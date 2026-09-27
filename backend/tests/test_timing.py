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
