from app.heuristics.address_reuse import detect_address_reuse
from app.models.finding import FindingType, Severity
from app.models.transaction import Input, Output, Transaction


def _tx(txid: str, *, inputs: list[str] | None = None, outputs: list[str] | None = None) -> Transaction:
    vin = [
        Input(previous_txid=f"prev-{txid}-{i}", previous_vout=0, address=addr, value=1000)
        for i, addr in enumerate(inputs or [])
    ]
    vout = [Output(vout=i, address=addr, value=900) for i, addr in enumerate(outputs or [])]
    return Transaction(txid=txid, inputs=vin, outputs=vout, fee=100, confirmed=True)


def test_reuse_across_independent_transactions() -> None:
    reused = "bc1qreusedaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    other = "bc1qotherbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    findings = detect_address_reuse(
        [
            _tx("tx1", outputs=[reused]),
            _tx("tx2", inputs=[reused], outputs=[other]),
            _tx("tx3", outputs=[reused]),
        ]
    )
    assert len(findings) == 1
    finding = findings[0]
    assert finding.type == FindingType.ADDRESS_REUSE
    assert finding.severity == Severity.HIGH
    assert finding.confidence == 1.0
    assert finding.affected_addresses == [reused]
    assert finding.affected_transactions == ["tx1", "tx2", "tx3"]
    assert finding.evidence[0].payload["occurrence_count"] == 3
    assert "real-world person" in finding.limitations[0]


def test_same_address_twice_in_one_transaction_is_not_reuse() -> None:
    addr = "bc1qonceccccccccccccccccccccccccccccccccc"
    findings = detect_address_reuse([_tx("tx1", inputs=[addr], outputs=[addr])])
    assert findings == []


def test_no_reuse_when_each_address_is_unique() -> None:
    findings = detect_address_reuse(
        [
            _tx("tx1", outputs=["bc1qa11111111111111111111111111111111111"]),
            _tx("tx2", outputs=["bc1qa22222222222222222222222222222222222"]),
        ]
    )
    assert findings == []


def test_two_transactions_are_medium_severity() -> None:
    addr = "bc1qtwiceddddddddddddddddddddddddddddddd"
    findings = detect_address_reuse(
        [
            _tx("tx1", outputs=[addr]),
            _tx("tx2", outputs=[addr]),
        ]
    )
    assert len(findings) == 1
    assert findings[0].severity == Severity.MEDIUM
    assert "2 transactions" in findings[0].inference
