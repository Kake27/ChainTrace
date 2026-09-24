from __future__ import annotations

from collections import defaultdict

from app.models.evidence import Evidence
from app.models.finding import Finding, FindingType, Severity
from app.models.transaction import Transaction

_LIMITATION = (
    "Address reuse is an on-chain observation. It links transactions, "
    "but it does not identify a real-world person or prove wallet ownership."
)


def detect_address_reuse(transactions: list[Transaction]) -> list[Finding]:
    """Flag addresses reused for multiple receives or independent spends.

    An address appearing once as an output and once later as the input that spends
    that output is normal UTXO lifecycle, not address reuse.  Keeping those cases
    separate lets downstream heuristics evaluate fresh change paths on their own.
    """
    output_txids: dict[str, set[str]] = defaultdict(set)
    input_txids: dict[str, set[str]] = defaultdict(set)
    for tx in transactions:
        for output in tx.outputs:
            if output.address:
                output_txids[output.address].add(tx.txid)
        for item in tx.inputs:
            if item.address:
                input_txids[item.address].add(tx.txid)

    reused = [
        (address, sorted(output_txids[address] | input_txids[address]))
        for address in output_txids.keys() | input_txids.keys()
        if max(len(output_txids[address]), len(input_txids[address])) >= 2
    ]
    reused.sort(key=lambda item: (-len(item[1]), item[0]))

    findings: list[Finding] = []
    for index, (address, txids) in enumerate(reused, start=1):
        count = len(txids)
        findings.append(
            Finding(
                id=f"address_reuse_{index:03d}",
                type=FindingType.ADDRESS_REUSE,
                severity=_severity(count),
                confidence=1.0,
                affected_addresses=[address],
                affected_transactions=txids,
                evidence=[
                    Evidence(
                        type="address_occurrences",
                        payload={
                            "address": address,
                            "occurrence_count": count,
                            "transactions": txids,
                        },
                    )
                ],
                inference=(
                    f"Address {address} is reused across {count} transactions. "
                    "An analyst can therefore link those transactions to one another."
                ),
                limitations=[_LIMITATION],
                recommendation=(
                    "Avoid sending future payments to or from this address; "
                    "use a fresh address for each receive."
                ),
            )
        )
    return findings


def _severity(occurrence_count: int) -> Severity:
    if occurrence_count >= 3:
        return Severity.HIGH
    return Severity.MEDIUM
