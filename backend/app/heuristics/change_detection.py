from __future__ import annotations

from collections import defaultdict

from app.config import settings
from app.heuristics.common_input import looks_coinjoin_like
from app.models.evidence import Evidence
from app.models.finding import Finding, FindingType, Severity
from app.models.transaction import Output, Transaction

_LIMITATION = (
    "Change detection is a multi-feature heuristic, not a label in the transaction. "
    "Batch payments, PayJoin, and CoinJoin can make change look like a payment, or "
    "leave no unique change output."
)
_FAN_OUT_LIMITATION = "High output count makes change identification ambiguous."

_WEIGHTS = {
    "new_address": 0.24,
    "script_type_match": 0.18,
    "contrasts_round_payment": 0.16,
    "non_round_amount": 0.10,
    "later_spent": 0.12,
    "two_output_structure": 0.08,
    "input_address_reuse": 0.07,
    "later_vout": 0.05,
}
def detect_change_candidates(transactions: list[Transaction]) -> list[Finding]:
    """Return only uniquely distinguishable change candidates.

    A transaction with an ambiguous output ranking or fan-out/batch structure has
    no confident change candidate. It is safer to abstain than select an output.
    """
    ordered = _chronological(transactions)
    input_indexes = _input_indexes(ordered)
    seen: set[str] = set()
    ranked_rows: list[dict] = []
    abstentions: list[dict] = []

    for index, tx in enumerate(ordered):
        spendable = [output for output in tx.outputs if output.address and output.value > 0]
        structure = classify_transaction_structure(tx, spendable)
        if structure == "fan_out_batch":
            abstentions.append(
                {
                    "txid": tx.txid,
                    "structure_classification": structure,
                    "output_count": len(tx.outputs),
                    "ambiguous": True,
                    "reason": _FAN_OUT_LIMITATION,
                }
            )
            _remember(tx, seen)
            continue
        if len(spendable) < 2 or structure == "coinjoin_like":
            _remember(tx, seen)
            continue
        scored = [
            _score_output(tx, output, spendable, seen, input_indexes, index)
            for output in spendable
        ]
        scored.sort(key=lambda row: (-row["score"], row["vout"]))
        best = scored[0]
        second_best_score = scored[1]["score"] if len(scored) > 1 else None
        score_gap = (
            round(best["score"] - second_best_score, 2)
            if second_best_score is not None
            else best["score"]
        )
        ambiguous = bool(
            second_best_score is not None and score_gap < settings.change_min_score_gap
        )
        if ambiguous:
            abstentions.append(
                {
                    "txid": tx.txid,
                    "structure_classification": "ambiguous",
                    "output_count": len(tx.outputs),
                    "ambiguous": True,
                    "reason": "Top change candidates are not sufficiently separated.",
                    "best_score": best["score"],
                    "second_best_score": second_best_score,
                    "score_gap": score_gap,
                    "minimum_score": settings.change_min_score,
                    "minimum_score_gap": settings.change_min_score_gap,
                }
            )
            _remember(tx, seen)
            continue
        if best["score"] < settings.change_min_score:
            _remember(tx, seen)
            continue
        ranked_rows.append(
            {
                **best,
                "txid": tx.txid,
                "candidate_rank": 1,
                "second_best_score": second_best_score,
                "score_gap": score_gap,
                "ambiguous": False,
                "structure_classification": structure,
                "output_count": len(tx.outputs),
            }
        )
        _remember(tx, seen)

    ranked_rows.sort(key=lambda row: (-row["score"], row["txid"], row["vout"]))
    findings: list[Finding] = []
    for finding_id, row in enumerate([*ranked_rows, *abstentions], start=1):
        findings.append(_to_finding(finding_id, row))
    return findings


def classify_transaction_structure(tx: Transaction, spendable: list[Output] | None = None) -> str:
    """Classify only the transaction structure relevant to change confidence."""
    if looks_coinjoin_like(tx):
        return "coinjoin_like"
    if len(tx.outputs) >= settings.change_high_output_threshold:
        return "fan_out_batch"
    if len(spendable if spendable is not None else tx.outputs) < 2:
        return "ambiguous"
    return "normal_payment_like"


def _score_output(
    tx: Transaction,
    output: Output,
    spendable: list[Output],
    seen: set[str],
    input_indexes: dict[str, list[int]],
    tx_index: int,
) -> dict:
    address = output.address or ""
    input_scripts = {infer_script_type(inp.address) for inp in tx.inputs if inp.address}
    input_scripts.discard(None)
    input_addresses = {inp.address for inp in tx.inputs if inp.address}
    other_round = any(
        other.vout != output.vout and _is_round_amount(other.value) for other in spendable
    )
    max_vout = max(item.vout for item in spendable)
    features = {
        "new_address": address not in seen,
        "script_type_match": infer_script_type(address, output.script_type) in input_scripts,
        "contrasts_round_payment": (not _is_round_amount(output.value)) and other_round,
        "non_round_amount": not _is_round_amount(output.value),
        "later_spent": any(index > tx_index for index in input_indexes.get(address, [])),
        "two_output_structure": len(spendable) == 2,
        "input_address_reuse": address in input_addresses,
        "later_vout": output.vout == max_vout and len(spendable) > 1,
    }
    raw = sum(_WEIGHTS[name] for name, fired in features.items() if fired)
    score = round(raw / sum(_WEIGHTS.values()), 2)
    return {
        "vout": output.vout,
        "address": address,
        "value": output.value,
        "script_type": infer_script_type(address, output.script_type),
        "features": features,
        "score": score,
    }


def infer_script_type(address: str | None, explicit: str | None = None) -> str | None:
    if explicit:
        return explicit
    if not address:
        return None
    lowered = address.lower()
    if lowered.startswith(("bc1p", "tb1p")):
        return "v1_p2tr"
    if lowered.startswith(("bc1q", "tb1q")):
        return "v0_p2wpkh" if len(address) <= 42 else "v0_p2wsh"
    if address.startswith(("3", "2")):
        return "p2sh"
    if address.startswith(("1", "m", "n")):
        return "p2pkh"
    return None


def _is_round_amount(sats: int) -> bool:
    if sats <= 0:
        return False
    for step in (100_000_000, 10_000_000, 1_000_000, 100_000):
        if sats % step == 0 and sats >= step:
            return True
    return False


def _chronological(transactions: list[Transaction]) -> list[Transaction]:
    return sorted(
        transactions,
        key=lambda tx: (
            tx.block_height is None,
            tx.block_height or 0,
            tx.timestamp or 0,
            tx.txid,
        ),
    )


def _input_indexes(transactions: list[Transaction]) -> dict[str, list[int]]:
    indexes: dict[str, list[int]] = defaultdict(list)
    for index, tx in enumerate(transactions):
        for inp in tx.inputs:
            if inp.address:
                indexes[inp.address].append(index)
    return indexes


def _remember(tx: Transaction, seen: set[str]) -> None:
    for item in (*tx.inputs, *tx.outputs):
        if item.address:
            seen.add(item.address)


def _to_finding(index: int, row: dict) -> Finding:
    if row["ambiguous"]:
        return _to_abstention_finding(index, row)
    txid = row["txid"]
    vout = row["vout"]
    address = row["address"]
    confidence = row["score"]
    limitations = [_LIMITATION]
    if row["structure_classification"] == "fan_out_batch":
        limitations.append(_FAN_OUT_LIMITATION)
    return Finding(
        id=f"change_candidate_{index:03d}",
        type=FindingType.CHANGE_CANDIDATE,
        severity=_severity(confidence),
        confidence=confidence,
        affected_addresses=[address],
        affected_transactions=[txid],
        evidence=[
            Evidence(
                type="change_features",
                payload={
                    "transaction": txid,
                    "vout": vout,
                    "address": address,
                    "value": row["value"],
                    "script_type": row["script_type"],
                    "score": row["score"],
                    "features": row["features"],
                    "candidate_rank": row["candidate_rank"],
                    "second_best_score": row["second_best_score"],
                    "score_gap": row["score_gap"],
                    "minimum_score": settings.change_min_score,
                    "minimum_score_gap": settings.change_min_score_gap,
                    "ambiguous": False,
                    "structure_classification": row["structure_classification"],
                    "output_count": row["output_count"],
                },
            )
        ],
        inference=(
            f"Tx {txid} → output {vout} ({address}) is a likely change output. "
            "This is a ranked candidate from explainable features, not a certainty."
        ),
        limitations=limitations,
        recommendation=(
            "Use a fresh change address and avoid making change distinguishable "
            "from the payment (script type, round amounts, or later reuse)."
        ),
    )


def _to_abstention_finding(index: int, row: dict) -> Finding:
    txid = row["txid"]
    structure = row["structure_classification"]
    limitations = [_LIMITATION]
    if structure == "fan_out_batch":
        limitations.append(_FAN_OUT_LIMITATION)
    else:
        limitations.append(
            "Transaction outputs have similarly plausible change scores; no specific output "
            "is reported as probable change."
        )
    return Finding(
        id=f"change_ambiguity_{index:03d}",
        type=FindingType.CHANGE_CANDIDATE,
        severity=Severity.LOW,
        confidence=0.0,
        affected_transactions=[txid],
        evidence=[
            Evidence(
                type="change_ambiguity",
                payload={
                    "transaction": txid,
                    "ambiguous": True,
                    "structure_classification": structure,
                    "output_count": row["output_count"],
                    "reason": row["reason"],
                    "best_score": row.get("best_score"),
                    "second_best_score": row.get("second_best_score"),
                    "score_gap": row.get("score_gap"),
                    "minimum_score": row.get("minimum_score", settings.change_min_score),
                    "minimum_score_gap": row.get(
                        "minimum_score_gap", settings.change_min_score_gap
                    ),
                },
            )
        ],
        inference=f"Tx {txid}: no confident change candidate identified. {row['reason']}",
        limitations=limitations,
        recommendation=(
            "Treat this transaction's outputs as ambiguous and avoid relying on a specific "
            "change-output inference."
        ),
    )


def _severity(confidence: float) -> Severity:
    if confidence < 0.50:
        return Severity.LOW
    if confidence >= 0.70:
        return Severity.HIGH
    return Severity.MEDIUM
