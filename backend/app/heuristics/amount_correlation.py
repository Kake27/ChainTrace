from __future__ import annotations

from collections import defaultdict
from typing import NamedTuple

from app.config import settings
from app.heuristics.address_reuse import detect_address_reuse
from app.heuristics.change_detection import detect_change_candidates
from app.heuristics.common_input import detect_common_input_ownership, looks_coinjoin_like
from app.heuristics.timing import detect_timing_correlations
from app.models.evidence import Evidence
from app.models.finding import Finding, FindingType, Severity
from app.models.transaction import Output, Transaction

ROUND_DENOMS_SATS = (100_000_000, 10_000_000, 1_000_000, 100_000)
COMMON_DENOM_SATS = 1_000_000
_MAX_CONFIDENCE = 0.70
_MIN_SCORE = 0.35
_MAX_FINDINGS = 25
_WEIGHTS = {
    "exact_match": 0.28,
    "near_match": 0.16,
    "uncommon_amount": 0.24,
    "shared_address": 0.12,
    "multi_heuristic_support": 0.10,
    "change_continuity": 0.12,
    "round_strength": 0.06,
}
_LIMITATION = (
    "Amount correlation is supporting evidence on top of other heuristics, not "
    "proof of common ownership. Round outputs are not automatically payments, "
    "and leftover amounts are not automatically change."
)


class _Candidate(NamedTuple):
    tx_left: str
    tx_right: str
    addresses: tuple[str, ...]
    reasons: frozenset[str]


class _Match(NamedTuple):
    left_txid: str
    left_vout: int
    left_address: str
    right_txid: str
    right_vout: int
    right_address: str
    left_value: int
    right_value: int
    difference: int
    exact: bool
    uncommon: bool
    shared_address: bool
    round_strength: float
    residual_hint: bool
    payment_hint: bool


def detect_amount_correlations(
    transactions: list[Transaction],
    *,
    address_reuse_findings: list[Finding] | None = None,
    common_input_findings: list[Finding] | None = None,
    change_findings: list[Finding] | None = None,
    timing_findings: list[Finding] | None = None,
) -> list[Finding]:
    """Compare amounts only on tx pairs already linked by other heuristics."""
    if len(transactions) < 2:
        return []
    by_id = {tx.txid: tx for tx in transactions}
    order = _tx_order(transactions)
    findings: list[Finding] = []
    for candidate in _candidates_from_heuristics(
        transactions,
        order,
        address_reuse_findings=address_reuse_findings,
        common_input_findings=common_input_findings,
        change_findings=change_findings,
        timing_findings=timing_findings,
    ):
        left = by_id.get(candidate.tx_left)
        right = by_id.get(candidate.tx_right)
        if left is None or right is None:
            continue
        match = _best_match(left, right, set(candidate.addresses))
        if match is None or not _meaningful(match, candidate.reasons):
            continue
        finding = _to_finding(match, candidate)
        if finding.confidence < _MIN_SCORE:
            continue
        findings.append(finding)
    findings.sort(key=lambda item: (-item.confidence, item.affected_transactions[0]))
    findings = findings[:_MAX_FINDINGS]
    for index, finding in enumerate(findings, start=1):
        finding.id = f"amount_correlation_{index:03d}"
    return findings


def round_denom_and_strength(sats: int) -> tuple[int | None, float]:
    if sats <= 0:
        return None, 0.0
    for index, denom in enumerate(ROUND_DENOMS_SATS):
        if sats % denom == 0 and sats >= denom:
            return denom, round(1.0 - index * 0.22, 2)
    return None, 0.0


def _tx_order(transactions: list[Transaction]) -> dict[str, tuple[int, int, str]]:
    ordered = sorted(
        transactions,
        key=lambda tx: (
            tx.block_height is None,
            tx.block_height or 0,
            tx.timestamp or 0,
            tx.txid,
        ),
    )
    return {tx.txid: (index, tx.timestamp or 0, tx.txid) for index, tx in enumerate(ordered)}


def _candidates_from_heuristics(
    transactions: list[Transaction],
    order: dict[str, tuple[int, int, str]],
    *,
    address_reuse_findings: list[Finding] | None,
    common_input_findings: list[Finding] | None,
    change_findings: list[Finding] | None,
    timing_findings: list[Finding] | None,
) -> list[_Candidate]:
    merged: dict[tuple[str, str], tuple[set[str], set[str]]] = {}

    def add(tx_a: str, tx_b: str, addresses: list[str], reason: str) -> None:
        if not tx_a or not tx_b or tx_a == tx_b:
            return
        key = (tx_a, tx_b) if tx_a < tx_b else (tx_b, tx_a)
        addrs, reasons = merged.setdefault(key, (set(), set()))
        addrs.update(addr for addr in addresses if addr)
        reasons.add(reason)

    for finding in (
        detect_address_reuse(transactions)
        if address_reuse_findings is None
        else address_reuse_findings
    ):
        for tx_a, tx_b in _consecutive(finding.affected_transactions, order):
            add(tx_a, tx_b, finding.affected_addresses, "address_reuse")

    for finding in (
        detect_common_input_ownership(transactions)
        if common_input_findings is None
        else common_input_findings
    ):
        for tx_a, tx_b in _consecutive(finding.affected_transactions, order):
            add(tx_a, tx_b, finding.affected_addresses, "common_input_ownership")

    for finding in (
        detect_timing_correlations(transactions) if timing_findings is None else timing_findings
    ):
        payload = finding.evidence[0].payload if finding.evidence else {}
        representative_pairs = payload.get("representative_pairs") or []
        if representative_pairs:
            for item in representative_pairs:
                add(
                    item["left_txid"],
                    item["right_txid"],
                    finding.affected_addresses,
                    "timing_correlation",
                )
        else:
            for tx_a, tx_b in _consecutive(finding.affected_transactions, order):
                add(tx_a, tx_b, finding.affected_addresses, "timing_correlation")

    spends = _input_txids_by_address(transactions)
    for finding in (
        detect_change_candidates(transactions) if change_findings is None else change_findings
    ):
        origin = finding.affected_transactions[0] if finding.affected_transactions else ""
        origin_rank = order.get(origin, (10**9, 0, origin))[0]
        for address in finding.affected_addresses:
            later = [
                txid
                for txid in spends.get(address, [])
                if order.get(txid, (10**9, 0, txid))[0] > origin_rank
            ]
            later.sort(key=lambda txid: order.get(txid, (0, 0, txid)))
            for txid in later[:3]:
                add(origin, txid, [address], "change_candidate")

    return [
        _Candidate(left, right, tuple(sorted(addrs)), frozenset(reasons))
        for (left, right), (addrs, reasons) in merged.items()
    ]


def _consecutive(
    txids: list[str], order: dict[str, tuple[int, int, str]]
) -> list[tuple[str, str]]:
    unique = list(dict.fromkeys(txids))
    unique.sort(key=lambda txid: order.get(txid, (0, 0, txid)))
    return list(zip(unique, unique[1:]))


def _input_txids_by_address(transactions: list[Transaction]) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = defaultdict(list)
    for tx in transactions:
        seen: set[str] = set()
        for inp in tx.inputs:
            if inp.address and inp.address not in seen:
                seen.add(inp.address)
                grouped[inp.address].append(tx.txid)
    return grouped


def _best_match(left: Transaction, right: Transaction, focus: set[str]) -> _Match | None:
    near = settings.amount_near_match_sats
    best: _Match | None = None
    best_key: tuple[int, int, int] | None = None
    for out_l in _relevant_outputs(left, focus):
        for out_r in _relevant_outputs(right, focus):
            delta = abs(out_l.value - out_r.value)
            if delta != 0 and delta > near:
                continue
            denom_l, strength_l = round_denom_and_strength(out_l.value)
            denom_r, strength_r = round_denom_and_strength(out_r.value)
            match = _Match(
                left_txid=left.txid,
                left_vout=out_l.vout,
                left_address=out_l.address or "",
                right_txid=right.txid,
                right_vout=out_r.vout,
                right_address=out_r.address or "",
                left_value=out_l.value,
                right_value=out_r.value,
                difference=delta,
                exact=delta == 0,
                uncommon=_uncommon(denom_l) and _uncommon(denom_r),
                shared_address=bool(out_l.address and out_l.address == out_r.address),
                round_strength=max(strength_l, strength_r),
                residual_hint=_residual_hint(out_l, left) or _residual_hint(out_r, right),
                payment_hint=_payment_hint(out_l, left) and _payment_hint(out_r, right),
            )
            # Prefer exact uncommon matches, then smaller deltas.
            key = (0 if match.uncommon else 1, 0 if match.exact else 1, delta)
            if best is None or key < best_key:
                best = match
                best_key = key
    return best


def _relevant_outputs(tx: Transaction, focus: set[str]) -> list[Output]:
    if looks_coinjoin_like(tx):
        return []
    min_sats = settings.amount_min_sats
    spendable = [out for out in tx.outputs if out.address and out.value >= min_sats]
    if not spendable:
        return []
    focused = [out for out in spendable if out.address in focus] if focus else spendable
    return focused or spendable


def _uncommon(denom: int | None) -> bool:
    return denom is None or denom < COMMON_DENOM_SATS


def _residual_hint(output: Output, tx: Transaction) -> bool:
    denom, _strength = round_denom_and_strength(output.value)
    if denom is not None:
        return False
    return any(
        round_denom_and_strength(other.value)[0]
        for other in tx.outputs
        if other.vout != output.vout
    )


def _payment_hint(output: Output, tx: Transaction) -> bool:
    denom, _strength = round_denom_and_strength(output.value)
    return denom is not None and not _residual_hint(output, tx)


def _meaningful(match: _Match, reasons: frozenset[str]) -> bool:
    if match.uncommon:
        return True
    if match.shared_address or "address_reuse" in reasons or "change_candidate" in reasons:
        return True
    return len(reasons) >= 2


def _to_finding(match: _Match, candidate: _Candidate) -> Finding:
    features = {
        "exact_match": match.exact,
        "near_match": not match.exact,
        "uncommon_amount": match.uncommon,
        "shared_address": match.shared_address,
        "multi_heuristic_support": len(candidate.reasons) >= 2,
        "change_continuity": "change_candidate" in candidate.reasons,
        "round_strength": match.round_strength,
    }
    score = 0.0
    for name, weight in _WEIGHTS.items():
        raw = features[name]
        score += weight * (raw if isinstance(raw, float) else (1.0 if raw else 0.0))
    score = round(min(_MAX_CONFIDENCE, score), 2)
    relation = "exact amount" if match.exact else f"near amount (Δ {match.difference} sats)"
    return Finding(
        id="amount_correlation",
        type=FindingType.AMOUNT_CORRELATION,
        severity=Severity.MEDIUM if match.uncommon and match.exact else Severity.LOW,
        confidence=score,
        affected_addresses=list(dict.fromkeys([match.left_address, match.right_address])),
        affected_transactions=[match.left_txid, match.right_txid],
        evidence=[
            Evidence(
                type="amount_comparison",
                payload={
                    "left": {
                        "txid": match.left_txid,
                        "vout": match.left_vout,
                        "address": match.left_address,
                        "value_sats": match.left_value,
                    },
                    "right": {
                        "txid": match.right_txid,
                        "vout": match.right_vout,
                        "address": match.right_address,
                        "value_sats": match.right_value,
                    },
                    "amount_difference_sats": match.difference,
                    "supported_by": sorted(candidate.reasons),
                    "features": features,
                },
            )
        ],
        inference=(
            f"{match.left_txid} ↔ {match.right_txid} via {relation} "
            f"({match.left_value} vs {match.right_value} sats), "
            f"supported by {', '.join(sorted(candidate.reasons))}. "
            "This is an amount-pattern signal, not ownership."
        ),
        limitations=[_LIMITATION],
        recommendation=(
            "Avoid repeating distinctive transfer amounts on already-linked addresses."
        ),
    )
