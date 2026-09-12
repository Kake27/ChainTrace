from __future__ import annotations

from collections import Counter, defaultdict
from itertools import combinations

from app.models.evidence import Evidence
from app.models.finding import Finding, FindingType, Severity
from app.models.transaction import Transaction

_LIMITATION = (
    "Common-input ownership is a heuristic, not proof. Collaborative transactions "
    "such as CoinJoin can put unrelated wallets in the same input set."
)

_BASE_CONFIDENCE = 0.82
_REPEAT_BONUS = 0.06
_MAX_CONFIDENCE = 0.95
_COINJOIN_CONFIDENCE = 0.28
_COINJOIN_MIX_FACTOR = 0.65


def detect_common_input_ownership(transactions: list[Transaction]) -> list[Finding]:
    """Link distinct input addresses that co-spend in the same transaction."""
    pair_txs: dict[tuple[str, str], list[str]] = defaultdict(list)
    pair_coinjoin_txs: dict[tuple[str, str], list[str]] = defaultdict(list)

    for tx in transactions:
        inputs = _unique_input_addresses(tx)
        if len(inputs) < 2:
            continue
        coinjoin = looks_coinjoin_like(tx)
        for left, right in combinations(sorted(inputs), 2):
            pair = (left, right)
            pair_txs[pair].append(tx.txid)
            if coinjoin:
                pair_coinjoin_txs[pair].append(tx.txid)

    ranked = sorted(
        pair_txs.items(),
        key=lambda item: (-len(item[1]), item[0]),
    )
    findings: list[Finding] = []
    for index, (pair, txids) in enumerate(ranked, start=1):
        left, right = pair
        coinjoin_txids = pair_coinjoin_txs.get(pair, [])
        confidence = _confidence(txids, coinjoin_txids)
        findings.append(
            Finding(
                id=f"common_input_{index:03d}",
                type=FindingType.COMMON_INPUT_OWNERSHIP,
                severity=_severity(confidence, coinjoin_only=len(coinjoin_txids) == len(txids)),
                confidence=confidence,
                affected_addresses=[left, right],
                affected_transactions=txids,
                evidence=[
                    Evidence(
                        type="shared_transaction_inputs",
                        payload={
                            "addresses": [left, right],
                            "transactions": txids,
                            "coappearance_count": len(txids),
                            "coinjoin_like_transactions": coinjoin_txids,
                        },
                    )
                ],
                inference=(
                    f"{left} ↔ {right} appeared together as inputs in {len(txids)} "
                    "transaction(s). An analyst would treat this as a candidate "
                    "common-ownership link, not a confirmed identity."
                ),
                limitations=_limitations(coinjoin_txids),
                recommendation=(
                    "Avoid combining coins from addresses you do not want linked. "
                    "Use single-input spends, or a collaborative construction, when "
                    "inputs should not imply one owner."
                ),
            )
        )
    return findings


def looks_coinjoin_like(tx: Transaction) -> bool:
    """Equal-output, many-input spends are treated as CoinJoin-like exceptions."""
    input_count = len(_unique_input_addresses(tx))
    if input_count < 3:
        return False
    values = [output.value for output in tx.outputs if output.value > 0]
    if len(values) < 3:
        return False
    _value, equal_count = Counter(values).most_common(1)[0]
    return equal_count >= 3 and equal_count >= (len(values) + 1) // 2


def _unique_input_addresses(tx: Transaction) -> set[str]:
    return {inp.address for inp in tx.inputs if inp.address}


def _confidence(txids: list[str], coinjoin_txids: list[str]) -> float:
    non_cj = len(txids) - len(coinjoin_txids)
    if non_cj > 0:
        score = min(_MAX_CONFIDENCE, _BASE_CONFIDENCE + _REPEAT_BONUS * (non_cj - 1))
        if coinjoin_txids:
            score *= _COINJOIN_MIX_FACTOR
        return round(score, 2)
    return round(min(0.40, _COINJOIN_CONFIDENCE + 0.03 * (len(coinjoin_txids) - 1)), 2)


def _severity(confidence: float, *, coinjoin_only: bool) -> Severity:
    if coinjoin_only or confidence < 0.45:
        return Severity.LOW
    if confidence >= 0.88:
        return Severity.HIGH
    return Severity.MEDIUM


def _limitations(coinjoin_txids: list[str]) -> list[str]:
    notes = [_LIMITATION]
    if coinjoin_txids:
        notes.append(
            "At least one supporting transaction looks CoinJoin-like "
            f"({', '.join(coinjoin_txids)}); those inputs may belong to different users."
        )
    return notes
