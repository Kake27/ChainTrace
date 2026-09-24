from app.heuristics.amount_correlation import detect_amount_correlations, round_denom_and_strength
from app.models.finding import FindingType
from app.models.transaction import Input, Output, Transaction

UNUSUAL = 12_345_678
ROUND_001 = 1_000_000
SHARED = "bc1qsharedpay000000000000000000000000"


def _tx(
    txid: str,
    outputs: list[tuple[str, int]],
    *,
    inputs: list[str] | None = None,
    height: int = 1,
    timestamp: int = 1_700_000_000,
) -> Transaction:
    total = sum(value for _addr, value in outputs) + 250
    vin = [
        Input(previous_txid=f"prev-{txid}-{i}", previous_vout=0, address=addr, value=total)
        for i, addr in enumerate(inputs or [f"in-{txid}"])
    ]
    return Transaction(
        txid=txid,
        timestamp=timestamp,
        block_height=height,
        inputs=vin,
        outputs=[Output(vout=i, address=addr, value=value) for i, (addr, value) in enumerate(outputs)],
        fee=250,
        confirmed=True,
    )


def test_unrelated_unusual_amounts_are_not_globally_paired() -> None:
    findings = detect_amount_correlations(
        [
            _tx("tx1", [("pay-a", UNUSUAL)]),
            _tx("tx2", [("pay-b", UNUSUAL)]),
        ]
    )
    assert findings == []


def test_reuse_linked_unusual_amount_is_correlation() -> None:
    findings = detect_amount_correlations(
        [
            _tx("tx1", [("pay-a", UNUSUAL), ("chg-a", 50_000_123)], inputs=["wallet"]),
            _tx("tx2", [("pay-b", UNUSUAL), ("chg-b", 40_000_123)], inputs=["wallet"]),
        ]
    )
    assert len(findings) == 1
    finding = findings[0]
    assert finding.type == FindingType.AMOUNT_CORRELATION
    payload = finding.evidence[0].payload
    assert payload["left"]["value_sats"] == UNUSUAL
    assert payload["right"]["value_sats"] == UNUSUAL
    assert payload["amount_difference_sats"] == 0
    assert "legs" not in payload
    assert payload["supported_by"] == ["address_reuse"]
    assert finding.confidence <= 0.70
    assert "not ownership" in finding.inference


def test_unrelated_common_round_amounts_are_not_linked() -> None:
    assert (
        detect_amount_correlations(
            [
                _tx("tx1", [("pay-a", ROUND_001)]),
                _tx("tx2", [("pay-b", ROUND_001)]),
            ]
        )
        == []
    )


def test_repeated_round_amount_on_reused_address_is_kept() -> None:
    findings = detect_amount_correlations(
        [
            _tx("tx1", [(SHARED, 100_000_000)]),
            _tx("tx2", [(SHARED, 100_000_000)]),
        ]
    )
    assert len(findings) == 1
    payload = findings[0].evidence[0].payload
    assert payload["features"]["shared_address"] is True
    assert "address_reuse" in payload["supported_by"]


def test_near_match_requires_a_prior_link() -> None:
    linked = detect_amount_correlations(
        [
            _tx("tx1", [("pay-a", UNUSUAL)], inputs=["wallet"]),
            _tx("tx2", [("pay-b", UNUSUAL + 2_000)], inputs=["wallet"]),
        ]
    )
    assert linked
    assert linked[0].evidence[0].payload["amount_difference_sats"] == 2_000
    assert linked[0].evidence[0].payload["features"]["near_match"] is True

    unlinked = detect_amount_correlations(
        [
            _tx("tx1", [("pay-a", UNUSUAL)]),
            _tx("tx2", [("pay-b", UNUSUAL + 2_000)]),
        ]
    )
    assert unlinked == []


def test_change_continuity_compares_change_to_later_spend() -> None:
    change_addr = "bc1qccccccccccccccccccccccccccccccc2"
    findings = detect_amount_correlations(
        [
            _tx(
                "spend",
                [("bc1qbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb1", 100_000_000), (change_addr, UNUSUAL)],
                height=1,
            ),
            _tx("later", [("bc1qddddddddddddddddddddddddddddddd3", UNUSUAL - 250)], inputs=[change_addr], height=2),
        ]
    )
    assert findings
    payload = findings[0].evidence[0].payload
    assert "change_candidate" in payload["supported_by"]
    assert payload["left"]["address"] == change_addr or payload["right"]["address"] == change_addr


def test_round_strength_helper_and_dust() -> None:
    assert detect_amount_correlations([_tx("tx1", [("a", 100)]), _tx("tx2", [("a", 100)])]) == []
    denom, strength = round_denom_and_strength(100_000_000)
    assert denom == 100_000_000 and strength == 1.0
    assert round_denom_and_strength(UNUSUAL) == (None, 0.0)
