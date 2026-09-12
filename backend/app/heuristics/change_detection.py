from __future__ import annotations

from app.heuristics.common_input import looks_coinjoin_like
from app.models.evidence import Evidence
from app.models.finding import Finding, FindingType, Severity
from app.models.transaction import Output, Transaction

_LIMITATION = (
    "Change detection is a multi-feature heuristic, not a label in the transaction. "
    "Batch payments, PayJoin, and CoinJoin can make change look like a payment, or "
    "leave no unique change output."
)

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
_MIN_SCORE = 0.40
_AMBIGUOUS_MARGIN = 0.08


def detect_change_candidates(transactions: list[Transaction]) -> list[Finding]:
    """Score likely change outputs; never treat last-vout as sufficient on its own."""
    ordered = _chronological(transactions)
    first_input_index = _first_input_indexes(ordered)
    seen: set[str] = set()
    ranked_rows: list[tuple[float, int, dict]] = []

    for index, tx in enumerate(ordered):
        spendable = [output for output in tx.outputs if output.address and output.value > 0]
        if len(spendable) < 2 or looks_coinjoin_like(tx):
            _remember(tx, seen)
            continue
        scored = [
            _score_output(tx, output, spendable, seen, first_input_index, index)
            for output in spendable
        ]
        scored.sort(key=lambda row: (-row["score"], row["vout"]))
        top = scored[0]["score"]
        if top < _MIN_SCORE:
            _remember(tx, seen)
            continue
        unique = [
            row
            for row in scored
            if row["score"] >= _MIN_SCORE and top - row["score"] <= _AMBIGUOUS_MARGIN
        ]
        ambiguous = len(unique) > 1
        for rank, row in enumerate(unique, start=1):
            ranked_rows.append((row["score"], rank, {**row, "ambiguous": ambiguous, "txid": tx.txid}))
        _remember(tx, seen)

    ranked_rows.sort(key=lambda item: (-item[0], item[2]["txid"], item[2]["vout"]))
    findings: list[Finding] = []
    for finding_id, (_score, _rank, row) in enumerate(ranked_rows, start=1):
        findings.append(_to_finding(finding_id, row))
    return findings


def _score_output(
    tx: Transaction,
    output: Output,
    spendable: list[Output],
    seen: set[str],
    first_input_index: dict[str, int],
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
        "later_spent": first_input_index.get(address, -1) > tx_index,
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


def _first_input_indexes(transactions: list[Transaction]) -> dict[str, int]:
    indexes: dict[str, int] = {}
    for index, tx in enumerate(transactions):
        for inp in tx.inputs:
            if inp.address and inp.address not in indexes:
                indexes[inp.address] = index
    return indexes


def _remember(tx: Transaction, seen: set[str]) -> None:
    for item in (*tx.inputs, *tx.outputs):
        if item.address:
            seen.add(item.address)


def _to_finding(index: int, row: dict) -> Finding:
    txid = row["txid"]
    vout = row["vout"]
    address = row["address"]
    confidence = row["score"]
    ambiguous = row["ambiguous"]
    if ambiguous:
        confidence = round(min(confidence, 0.55), 2)
    limitations = [_LIMITATION]
    if ambiguous:
        limitations.append(
            f"Transaction {txid} has more than one similarly scored change candidate; "
            "none of them should be treated as certain."
        )
    return Finding(
        id=f"change_candidate_{index:03d}",
        type=FindingType.CHANGE_CANDIDATE,
        severity=_severity(confidence, ambiguous=ambiguous),
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
                    "ambiguous": ambiguous,
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


def _severity(confidence: float, *, ambiguous: bool) -> Severity:
    if ambiguous or confidence < 0.50:
        return Severity.LOW
    if confidence >= 0.70:
        return Severity.HIGH
    return Severity.MEDIUM
