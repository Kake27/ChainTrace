from app.heuristics.change_detection import detect_change_candidates
from app.heuristics.common_input import looks_coinjoin_like
from app.models.finding import FindingType, Severity
from app.models.transaction import Input, Output, Transaction

IN = "bc1qaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa0"
PAY = "bc1qbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb1"
CHG = "bc1qccccccccccccccccccccccccccccccc2"
ALT = "bc1qddddddddddddddddddddddddddddddd3"


def _tx(
    txid: str,
    *,
    inputs: list[str] | None = None,
    outputs: list[tuple[str, int]] | None = None,
    height: int = 1,
) -> Transaction:
    vin = [
        Input(previous_txid=f"prev-{txid}-{i}", previous_vout=0, address=addr, value=50_000_000)
        for i, addr in enumerate(inputs or [])
    ]
    vout = [
        Output(vout=i, address=addr, value=value, script_type="v0_p2wpkh")
        for i, (addr, value) in enumerate(outputs or [])
    ]
    return Transaction(
        txid=txid,
        block_height=height,
        inputs=vin,
        outputs=vout,
        fee=250,
        confirmed=True,
    )


def test_skips_single_output_transactions() -> None:
    assert detect_change_candidates([_tx("tx1", inputs=[IN], outputs=[(PAY, 1_000)])]) == []


def test_round_payment_and_new_remainder_is_likely_change() -> None:
    prior = _tx("recv", outputs=[(PAY, 100_000_000)], height=1)
    spend = _tx(
        "spend",
        inputs=[IN],
        outputs=[(PAY, 100_000_000), (CHG, 12_345_678)],
        height=2,
    )
    findings = detect_change_candidates([spend, prior])
    assert len(findings) == 1
    finding = findings[0]
    assert finding.type == FindingType.CHANGE_CANDIDATE
    assert finding.affected_addresses == [CHG]
    assert finding.affected_transactions == ["spend"]
    payload = finding.evidence[0].payload
    assert payload["vout"] == 1
    assert payload["features"]["new_address"] is True
    assert payload["features"]["contrasts_round_payment"] is True
    assert payload["features"]["later_vout"] is True
    assert finding.confidence >= 0.55
    assert finding.severity in {Severity.MEDIUM, Severity.HIGH}


def test_last_output_alone_is_not_treated_as_certain_change() -> None:
    spend = _tx(
        "spend",
        inputs=[IN],
        outputs=[(PAY, 12_345_001), (CHG, 12_345_002)],
        height=1,
    )
    findings = detect_change_candidates([spend])
    assert len(findings) == 2
    assert all(item.evidence[0].payload["ambiguous"] for item in findings)
    assert all(item.severity == Severity.LOW for item in findings)


def test_coinjoin_like_transactions_have_no_change_candidate() -> None:
    equal_outs = [(f"bc1qrecv{i:0>31}", 100_000) for i in range(5)]
    tx = _tx("cj", inputs=[f"bc1qin{i:0>33}" for i in range(5)], outputs=equal_outs)
    assert looks_coinjoin_like(tx)
    assert detect_change_candidates([tx]) == []


def test_later_spent_output_is_a_positive_signal() -> None:
    spend = _tx(
        "spend",
        inputs=[IN],
        outputs=[(PAY, 100_000_000), (CHG, 12_345_678)],
        height=1,
    )
    later = _tx("later", inputs=[CHG], outputs=[(ALT, 12_345_000)], height=2)
    findings = detect_change_candidates([spend, later])
    change = next(item for item in findings if item.affected_addresses == [CHG])
    assert change.evidence[0].payload["features"]["later_spent"] is True
