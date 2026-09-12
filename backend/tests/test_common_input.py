from app.heuristics.common_input import detect_common_input_ownership, looks_coinjoin_like
from app.models.finding import FindingType, Severity
from app.models.transaction import Input, Output, Transaction

A = "addrA"
B = "addrB"
C = "addrC"


def _tx(
    txid: str,
    *,
    inputs: list[str],
    outputs: list[tuple[str, int]] | None = None,
    input_value: int = 10_000,
) -> Transaction:
    vin = [
        Input(previous_txid=f"prev-{txid}-{i}", previous_vout=0, address=addr, value=input_value)
        for i, addr in enumerate(inputs)
    ]
    vout = [
        Output(vout=i, address=addr, value=value)
        for i, (addr, value) in enumerate(outputs or [("out", 9_000)])
    ]
    return Transaction(txid=txid, inputs=vin, outputs=vout, fee=100, confirmed=True)


def test_single_input_creates_no_pair() -> None:
    assert detect_common_input_ownership([_tx("tx1", inputs=[A])]) == []


def test_two_inputs_create_one_candidate_pair() -> None:
    findings = detect_common_input_ownership([_tx("tx1", inputs=[A, B])])
    assert len(findings) == 1
    finding = findings[0]
    assert finding.type == FindingType.COMMON_INPUT_OWNERSHIP
    assert finding.affected_addresses == [A, B]
    assert finding.affected_transactions == ["tx1"]
    assert finding.confidence == 0.82
    assert finding.severity == Severity.MEDIUM
    assert finding.evidence[0].payload["coappearance_count"] == 1
    assert "heuristic" in finding.limitations[0]


def test_repeated_pair_increases_confidence() -> None:
    findings = detect_common_input_ownership(
        [
            _tx("tx1", inputs=[B, A]),
            _tx("tx2", inputs=[A, B]),
        ]
    )
    assert len(findings) == 1
    assert findings[0].affected_transactions == ["tx1", "tx2"]
    assert findings[0].confidence == 0.88
    assert findings[0].severity == Severity.HIGH


def test_three_inputs_create_three_pairs() -> None:
    findings = detect_common_input_ownership([_tx("tx1", inputs=[A, B, C])])
    pairs = {tuple(item.affected_addresses) for item in findings}
    assert pairs == {(A, B), (A, C), (B, C)}


def test_coinjoin_like_lowers_confidence_and_is_flagged() -> None:
    equal_outs = [(f"recv{i}", 100_000) for i in range(5)]
    tx = _tx("cj1", inputs=[f"in{i}" for i in range(5)], outputs=equal_outs, input_value=100_000)
    assert looks_coinjoin_like(tx) is True
    findings = detect_common_input_ownership([tx])
    assert findings
    assert all(item.confidence <= 0.40 for item in findings)
    assert all(item.severity == Severity.LOW for item in findings)
    assert findings[0].evidence[0].payload["coinjoin_like_transactions"] == ["cj1"]
    assert "CoinJoin-like" in findings[0].limitations[1]
