from app.heuristics.timing import detect_timing_correlations
from app.models.finding import FindingType, Severity
from app.models.transaction import Input, Output, Transaction

A = "addrA"
B = "addrB"
C = "addrC"




def _tx(
    txid: str,
    address: str,
    timestamp: int | None,
    *,
    height: int | None = 1,
    confirmed: bool = True,
) -> Transaction:
    return Transaction(
        txid=txid,
        timestamp=timestamp,
        block_height=height if confirmed else None,
        inputs=[Input(previous_txid=f"p-{txid}", previous_vout=0, address=address, value=1000)],
        outputs=[Output(vout=0, address=f"pay-{txid}", value=900)],
        fee=100,
        confirmed=confirmed,
    )


def _repeated_pattern(
    deltas: list[int],
    *,
    total_events: int | None = None,
    alternating_order: bool = False,
) -> list[Transaction]:
    """Build cross-block matches plus optional same-block padding for coverage tests."""
    transactions: list[Transaction] = []
    for index, delta in enumerate(deltas):
        timestamp = 1_700_000_000 + index * 1_000
        signed_delta = -delta if alternating_order and index % 2 else delta
        transactions.extend(
            [
                _tx(f"a-{index}", A, timestamp, height=10_000 + index * 2),
                _tx(
                    f"b-{index}",
                    B,
                    timestamp + signed_delta,
                    height=10_001 + index * 2,
                ),
            ]
        )
    for index in range(len(deltas), total_events or len(deltas)):
        timestamp = 1_800_000_000 + index * 1_000
        # Same-block padding counts toward address activity but must never become evidence.
        transactions.extend(
            [
                _tx(f"pad-a-{index}", A, timestamp, height=20_000 + index),
                _tx(f"pad-b-{index}", B, timestamp + 30, height=20_000 + index),
            ]
        )
    return transactions


def test_repeated_close_activity_is_timing_correlation() -> None:
    txs = [
        _tx("a1", A, 1_700_000_000),
        _tx("b1", B, 1_700_000_030, height=2),
        _tx("a2", A, 1_700_003_000, height=3),
        _tx("b2", B, 1_700_003_025, height=4),
    ]
    findings = detect_timing_correlations(txs, window_seconds=120)
    assert len(findings) == 1
    finding = findings[0]
    assert finding.type == FindingType.TIMING_CORRELATION
    assert finding.affected_addresses == [A, B]
    payload = finding.evidence[0].payload
    assert payload["occurrence_count"] == 2
    assert payload["median_delta_seconds"] in {25, 27, 30}
    assert payload["mean_delta_seconds"] in {25, 27, 30}
    assert payload["stdev_delta_seconds"] >= 0
    assert payload["coverage"] == 1.0
    assert payload["window_seconds"] == 120
    assert len(payload["representative_pairs"]) <= 3
    assert "occurrences" not in payload
    assert payload["block_height_relationship"]["cross_block_occurrence_count"] == 2
    assert finding.confidence <= 0.72
    assert finding.severity in {Severity.LOW, Severity.MEDIUM}
    assert "not proof" in finding.limitations[0]


def test_isolated_match_is_ignored() -> None:
    txs = [
        _tx("a1", A, 1_700_000_000),
        _tx("b1", B, 1_700_000_020),
        _tx("a2", A, 1_700_010_000),
        _tx("b2", B, 1_700_020_000),
    ]
    assert detect_timing_correlations(txs, window_seconds=120) == []


def test_confirmation_lag_is_not_the_window() -> None:
    txs = [
        _tx("a1", A, 1_700_000_000),
        _tx("b1", B, 1_700_000_600),
        _tx("a2", A, 1_700_010_000),
        _tx("b2", B, 1_700_010_600),
    ]
    assert detect_timing_correlations(txs, window_seconds=120) == []


def test_same_timestamp_never_contributes_timing_evidence() -> None:
    txs = [
        _tx("a1", A, 1_700_000_000, height=1),
        _tx("b1", B, 1_700_000_000, height=2),
        _tx("a2", A, 1_700_003_000, height=3),
        _tx("b2", B, 1_700_003_000, height=4),
    ]
    assert detect_timing_correlations(txs, window_seconds=120) == []


def test_same_block_pairs_are_not_behavioral_timing_evidence() -> None:
    txs = [
        _tx("a1", A, 1_700_000_000, height=100),
        _tx("b1", B, 1_700_000_030, height=100),
        _tx("a2", A, 1_700_003_000, height=101),
        _tx("b2", B, 1_700_003_025, height=101),
    ]
    assert detect_timing_correlations(txs, window_seconds=120) == []


def test_missing_block_heights_fall_back_to_nonzero_timestamps() -> None:
    txs = [
        _tx("a1", A, 1_700_000_000, height=None),
        _tx("b1", B, 1_700_000_030, height=None),
        _tx("a2", A, 1_700_003_000, height=None),
        _tx("b2", B, 1_700_003_025, height=None),
    ]
    findings = detect_timing_correlations(txs, window_seconds=120)
    assert len(findings) == 1
    relationship = findings[0].evidence[0].payload["block_height_relationship"]
    assert relationship["timestamp_fallback_occurrence_count"] == 2


def test_twelve_strong_cross_block_occurrences_with_low_coverage_are_moderated() -> None:
    findings = detect_timing_correlations(
        _repeated_pattern([18] * 4 + [50] * 4 + [109] * 4, total_events=400),
        window_seconds=120,
    )
    assert len(findings) == 1
    finding = findings[0]
    payload = finding.evidence[0].payload
    assert payload["occurrence_count"] == 12
    assert payload["median_delta_seconds"] == 50
    assert payload["mean_delta_seconds"] == 59
    assert payload["stdev_delta_seconds"] == 37.69
    assert payload["order_consistency"] == 1.0
    assert payload["coverage"] == 0.03
    assert payload["block_height_relationship"]["cross_block_occurrence_count"] == 12
    assert payload["block_height_relationship"]["same_block_pairs_excluded"] > 0
    assert payload["pattern_strength"] > finding.confidence
    assert 0.50 < finding.confidence < 0.71


def test_twelve_occurrences_with_inconsistent_order_score_lower() -> None:
    consistent = detect_timing_correlations(
        _repeated_pattern([50] * 12, total_events=400), window_seconds=120
    )[0]
    inconsistent = detect_timing_correlations(
        _repeated_pattern([50] * 12, total_events=400, alternating_order=True),
        window_seconds=120,
    )[0]
    assert inconsistent.evidence[0].payload["order_consistency"] == 0.5
    assert inconsistent.confidence < consistent.confidence


def test_three_perfect_occurrences_remain_low_severity() -> None:
    finding = detect_timing_correlations(_repeated_pattern([50] * 3), window_seconds=120)[0]
    assert finding.evidence[0].payload["occurrence_count"] == 3
    assert finding.evidence[0].payload["order_consistency"] == 1.0
    assert finding.severity == Severity.LOW


def test_thirty_consistent_high_coverage_occurrences_reach_the_confidence_cap() -> None:
    finding = detect_timing_correlations(_repeated_pattern([50] * 30), window_seconds=120)[0]
    payload = finding.evidence[0].payload
    assert payload["occurrence_count"] == 30
    assert payload["coverage"] == 1.0
    assert finding.confidence == 0.72
    assert finding.severity == Severity.MEDIUM


def test_random_unrelated_activity_inside_the_window_does_not_make_a_finding() -> None:
    txs = [
        _tx("a-close", A, 1_700_000_000, height=1),
        _tx("b-close", B, 1_700_000_040, height=2),
        _tx("a-later", A, 1_700_100_000, height=3),
        _tx("b-later", B, 1_700_200_000, height=4),
    ]
    assert detect_timing_correlations(txs, window_seconds=120) == []


def test_missing_timestamps_and_same_tx_are_ignored() -> None:
    same = Transaction(
        txid="shared",
        timestamp=1_700_000_000,
        inputs=[Input(previous_txid="p0", previous_vout=0, address=A, value=500)],
        outputs=[Output(vout=0, address=B, value=400)],
        fee=100,
        confirmed=True,
    )
    txs = [
        same,
        _tx("a1", A, None, height=2),
        _tx("b1", B, 1_700_000_010, height=3),
        _tx("a2", A, 1_700_000_015, confirmed=False),
    ]
    assert detect_timing_correlations(txs, window_seconds=120) == []
