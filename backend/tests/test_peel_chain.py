from __future__ import annotations

from app.analysis.clustering import build_ownership_clusters
from app.config import settings
from app.heuristics.address_reuse import detect_address_reuse
from app.heuristics.peel_chain import _PeelDetector, detect_peel_chains
from app.models.finding import FindingType
from app.models.transaction import Input, Output, Transaction

A = "bc1qaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa0"
B = "bc1qbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb1"
PAY_1 = "bc1qpaymentaaaaaaaaaaaaaaaaaaaaaaaa0"
PAY_2 = "bc1qpaymentbbbbbbbbbbbbbbbbbbbbbbbb1"
PAY_3 = "bc1qpaymentcccccccccccccccccccccccc2"
CHANGE_1 = "bc1qchangeaaaaaaaaaaaaaaaaaaaaaaaaaa0"
CHANGE_2 = "bc1qchangebbbbbbbbbbbbbbbbbbbbbbbbbb1"


def _tx(
    txid: str,
    *,
    inputs: list[tuple[str, int, str]],
    outputs: list[tuple[str, int]],
    height: int,
) -> Transaction:
    return Transaction(
        txid=txid,
        block_height=height,
        timestamp=1_700_000_000 + height,
        inputs=[
            Input(previous_txid=previous_txid, previous_vout=vout, address=address, value=50_000_000)
            for previous_txid, vout, address in inputs
        ],
        outputs=[
            Output(vout=index, address=address, value=value, script_type="v0_p2wpkh")
            for index, (address, value) in enumerate(outputs)
        ],
        fee=500,
        confirmed=True,
    )


def _valid_chain(*, extra_second_input: bool = False) -> list[Transaction]:
    tx1 = _tx(
        "tx1",
        inputs=[("funding", 0, A)],
        outputs=[(PAY_1, 10_000_000), (CHANGE_1, 39_123_456)],
        height=1,
    )
    tx2_inputs = [("tx1", 1, CHANGE_1)]
    if extra_second_input:
        tx2_inputs.append(("other-funding", 0, B))
    tx2 = _tx(
        "tx2",
        inputs=tx2_inputs,
        outputs=[(PAY_2, 5_000_000), (CHANGE_2, 34_122_500)],
        height=2,
    )
    tx3 = _tx(
        "tx3",
        inputs=[("tx2", 1, CHANGE_2)],
        outputs=[(PAY_3, 30_000_000)],
        height=3,
    )
    return [tx1, tx2, tx3]


def test_reports_a_three_transaction_decreasing_peel_chain() -> None:
    findings = detect_peel_chains(_valid_chain())

    assert len(findings) == 1
    finding = findings[0]
    assert finding.type == FindingType.PEEL_CHAIN
    assert finding.affected_transactions == ["tx1", "tx2", "tx3"]
    payload = finding.evidence[0].payload
    assert payload["hop_count"] == 2
    assert payload["decreasing_value_pattern"] is True
    assert payload["continuation_values_sats"] == [39_123_456, 34_122_500]
    assert all(hop["change_confidence"] >= 0.7 for hop in payload["hops"])
    assert payload["confidence_components"]["mean_change_confidence"] != finding.confidence
    assert all(hop["change_features"]["later_spent"] for hop in payload["hops"])
    assert all(hop["input_total_sats"] > hop["change_value_sats"] for hop in payload["hops"])
    assert all(hop["payment_outputs"] for hop in payload["hops"])
    assert all(hop["fee_sats"] == 500 for hop in payload["hops"])
    assert "not proven ownership" in finding.inference


def test_fresh_change_chain_needs_no_address_reuse_finding() -> None:
    txs = _valid_chain()
    assert detect_address_reuse(txs) == []
    findings = detect_peel_chains(txs)
    assert len(findings) == 1
    hops = findings[0].evidence[0].payload["hops"]
    assert all(hop["change_features"]["new_address"] for hop in hops)
    assert all(not hop["change_features"]["input_address_reuse"] for hop in hops)


def test_ownership_clusters_keep_change_edge_provenance() -> None:
    clusters = build_ownership_clusters(_valid_chain())

    assert clusters.same_cluster(A, CHANGE_1)
    assert clusters.same_cluster(CHANGE_1, CHANGE_2)
    assert any(edge.relationship == "probable_change" and edge.transaction == "tx1" for edge in clusters.edges)
    profile = clusters.profile_for(CHANGE_1)
    assert profile is not None
    assert profile.script_types == {"v0_p2wpkh"}
    assert profile.change_positions[1] >= 1


def test_cluster_change_edge_is_required_for_continuation_selection() -> None:
    txs = _valid_chain()
    detector = _PeelDetector(txs)
    assert detector.find_next(txs[0]) is not None
    detector.clusters.edges = []
    assert detector.find_next(txs[0]) is None


def test_allows_a_multi_input_continuation_when_the_change_outpoint_is_unique() -> None:
    findings = detect_peel_chains(_valid_chain(extra_second_input=True))
    assert len(findings) == 1
    assert findings[0].affected_transactions == ["tx1", "tx2", "tx3"]


def test_abstains_when_multiple_equal_change_candidates_continue_to_the_same_tx() -> None:
    tx1 = _tx(
        "tx1",
        inputs=[("funding", 0, A)],
        outputs=[(CHANGE_1, 20_123_456), (CHANGE_2, 19_122_500)],
        height=1,
    )
    tx2 = _tx(
        "tx2",
        inputs=[("tx1", 0, CHANGE_1), ("tx1", 1, CHANGE_2)],
        outputs=[(PAY_1, 5_000_000), (PAY_2, 34_000_000)],
        height=2,
    )
    tx3 = _tx(
        "tx3",
        inputs=[("tx2", 1, PAY_2)],
        outputs=[(PAY_3, 20_000_000)],
        height=3,
    )
    assert detect_peel_chains([tx1, tx2, tx3]) == []


def test_skips_coinjoin_like_transactions() -> None:
    tx1, _tx2, tx3 = _valid_chain()
    coinjoin = _tx(
        "tx2",
        inputs=[
            ("tx1", 1, CHANGE_1),
            ("funding-b", 0, B),
            ("funding-c", 0, "bc1qccccccccccccccccccccccccccccccc2"),
        ],
        outputs=[
            ("bc1qequaloutputaaaaaaaaaaaaaaaaaaaa0", 10_000_000),
            ("bc1qequaloutputbbbbbbbbbbbbbbbbbbbb1", 10_000_000),
            ("bc1qequaloutputcccccccccccccccccccc2", 10_000_000),
        ],
        height=2,
    )
    assert detect_peel_chains([tx1, coinjoin, tx3]) == []


def test_broken_outpoints_do_not_form_chains() -> None:
    txs = _valid_chain()
    txs[2] = _tx(
        "tx3",
        inputs=[("not-tx2", 1, CHANGE_2)],
        outputs=[(PAY_3, 30_000_000)],
        height=3,
    )
    assert detect_peel_chains(txs) == []


def test_non_decreasing_remainders_do_not_form_chains() -> None:
    tx1 = _tx(
        "tx1",
        inputs=[("funding", 0, A)],
        outputs=[(PAY_1, 10_000_000), (CHANGE_1, 30_123_456)],
        height=1,
    )
    tx2 = _tx(
        "tx2",
        inputs=[("tx1", 1, CHANGE_1)],
        outputs=[(PAY_2, 5_000_000), (CHANGE_2, 35_122_500)],
        height=2,
    )
    tx3 = _tx(
        "tx3",
        inputs=[("tx2", 1, CHANGE_2)],
        outputs=[(PAY_3, 30_000_000)],
        height=3,
    )
    assert detect_peel_chains([tx1, tx2, tx3]) == []


def test_traversal_limit_stops_forward_expansion(monkeypatch) -> None:
    txs = _valid_chain()
    tx4 = _tx(
        "tx4",
        inputs=[("tx3", 0, PAY_3)],
        outputs=[("bc1qfinalaaaaaaaaaaaaaaaaaaaaaaaaaaa0", 20_000_000)],
        height=4,
    )
    monkeypatch.setattr(settings, "peel_chain_max_hops", 1)
    txids, hops = _PeelDetector([*txs, tx4]).chain_from(txs[0])
    assert txids == ["tx1", "tx2"]
    assert len(hops) == 1
