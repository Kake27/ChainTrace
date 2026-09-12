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
    """Flag addresses that appear in two or more distinct transactions."""
    address_to_txids: dict[str, set[str]] = defaultdict(set)
    for tx in transactions:
        for address in _addresses_in_transaction(tx):
            address_to_txids[address].add(tx.txid)

    reused = [
        (address, sorted(txids))
        for address, txids in address_to_txids.items()
        if len(txids) >= 2
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


def _addresses_in_transaction(tx: Transaction) -> set[str]:
    addresses: set[str] = set()
    for item in (*tx.inputs, *tx.outputs):
        if item.address:
            addresses.add(item.address)
    return addresses


def _severity(occurrence_count: int) -> Severity:
    if occurrence_count >= 3:
        return Severity.HIGH
    return Severity.MEDIUM
