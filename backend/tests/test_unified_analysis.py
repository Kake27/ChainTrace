from __future__ import annotations

from fastapi.testclient import TestClient

from app.analysis.graph import TransactionGraph
from app.analysis.service import analyse_wallet
from app.main import create_app
from app.models.finding import FindingType
from app.models.transaction import Input, Output, Transaction

WALLET = "bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq"
A = "bc1qaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa0"
B = "bc1qbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb1"
CHANGE_1 = "bc1qchangeaaaaaaaaaaaaaaaaaaaaaaaaaa0"
CHANGE_2 = "bc1qchangebbbbbbbbbbbbbbbbbbbbbbbbbb1"
REUSED = "bc1qreusedddddddddddddddddddddddddddd2"


class CountingHistoryClient:
    def __init__(self, transactions: list[Transaction], warnings: list[str] | None = None) -> None:
        self.transactions = transactions
        self.warnings = warnings or []
        self.calls = 0

    def get_wallet_history(self, address: str, *, max_transactions: int | None = None):
        self.calls += 1
        return self.transactions, [], self.warnings


def _tx(
    txid: str,
    *,
    inputs: list[tuple[str, int, str]],
    outputs: list[tuple[str, int]],
    height: int,
    timestamp: int | None = None,
) -> Transaction:
    return Transaction(
        txid=txid,
        block_height=height,
        timestamp=timestamp if timestamp is not None else 1_700_000_000 + height,
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


def _valid_peel_chain() -> list[Transaction]:
    return [
        _tx(
            "tx1",
            inputs=[("funding", 0, A)],
            outputs=[("payment-1", 10_000_000), (CHANGE_1, 39_123_456)],
            height=1,
        ),
        _tx(
            "tx2",
            inputs=[("tx1", 1, CHANGE_1)],
            outputs=[("payment-2", 5_000_000), (CHANGE_2, 34_122_500)],
            height=2,
        ),
        _tx(
            "tx3",
            inputs=[("tx2", 1, CHANGE_2)],
            outputs=[("payment-3", 30_000_000)],
            height=3,
        ),
    ]


def test_empty_wallet_has_empty_unified_result() -> None:
    client = CountingHistoryClient([])
    result = analyse_wallet(WALLET, client)

    assert client.calls == 1
    assert result.summary.transaction_count == 0
    assert result.summary.total_findings == 0
    assert result.findings == []
    assert result.clusters == []


def test_pipeline_fetches_once_and_builds_the_graph_once(monkeypatch) -> None:
    client = CountingHistoryClient(_valid_peel_chain(), warnings=["source warning"])
    original_build = TransactionGraph.build
    graph_builds = 0

    def tracked_build(transactions: list[Transaction]) -> TransactionGraph:
        nonlocal graph_builds
        graph_builds += 1
        return original_build(transactions)

    monkeypatch.setattr(TransactionGraph, "build", tracked_build)
    result = analyse_wallet(WALLET, client)

    assert client.calls == 1
    assert graph_builds == 1
    assert result.warnings == ["source warning"]
    assert result.summary.findings_by_type[FindingType.PEEL_CHAIN] == 1


def test_pipeline_combines_reuse_common_input_change_and_peel_findings() -> None:
    transactions = [
        *_valid_peel_chain(),
        _tx("common", inputs=[("a", 0, "owner-a"), ("b", 0, "owner-b")], outputs=[("pay", 1_000)], height=4),
        _tx("reuse-1", inputs=[], outputs=[(REUSED, 4_000)], height=5),
        _tx("reuse-2", inputs=[], outputs=[(REUSED, 5_000)], height=6),
    ]
    result = analyse_wallet(WALLET, CountingHistoryClient(transactions))

    counts = result.summary.findings_by_type
    assert counts[FindingType.ADDRESS_REUSE] >= 1
    assert counts[FindingType.COMMON_INPUT_OWNERSHIP] >= 1
    assert counts[FindingType.CHANGE_CANDIDATE] >= 2
    assert counts[FindingType.PEEL_CHAIN] == 1
    assert result.summary.total_findings == len(result.findings)
    assert result.summary.cluster_count >= 1
    assert all(cluster.evidence for cluster in result.clusters)


def test_pipeline_runs_timing_and_amount_correlation() -> None:
    transactions = [
        _tx("a1", inputs=[("p-a1", 0, "timing-a")], outputs=[("pay-a1", 1_000)], height=1, timestamp=1_700_000_000),
        _tx("b1", inputs=[("p-b1", 0, "timing-b")], outputs=[("pay-b1", 1_000)], height=2, timestamp=1_700_000_020),
        _tx("a2", inputs=[("p-a2", 0, "timing-a")], outputs=[("pay-a2", 1_000)], height=3, timestamp=1_700_003_000),
        _tx("b2", inputs=[("p-b2", 0, "timing-b")], outputs=[("pay-b2", 1_000)], height=4, timestamp=1_700_003_020),
        _tx("amount-1", inputs=[("funding-1", 0, "amount-owner")], outputs=[("amount-pay-1", 12_345_678)], height=5),
        _tx("amount-2", inputs=[("funding-2", 0, "amount-owner")], outputs=[("amount-pay-2", 12_345_678)], height=6),
    ]
    result = analyse_wallet(WALLET, CountingHistoryClient(transactions))

    counts = result.summary.findings_by_type
    assert counts[FindingType.TIMING_CORRELATION] >= 1
    assert counts[FindingType.AMOUNT_CORRELATION] >= 1


def test_ambiguous_peel_paths_and_coinjoin_are_conservatively_abstained() -> None:
    ambiguous = [
        _tx("ambiguous-1", inputs=[("funding", 0, A)], outputs=[(CHANGE_1, 20_123_456), (CHANGE_2, 19_122_500)], height=1),
        _tx("ambiguous-2", inputs=[("ambiguous-1", 0, CHANGE_1), ("ambiguous-1", 1, CHANGE_2)], outputs=[("pay", 5_000_000), ("next", 34_000_000)], height=2),
        _tx("ambiguous-3", inputs=[("ambiguous-2", 1, "next")], outputs=[("pay-2", 20_000_000)], height=3),
    ]
    coinjoin = _tx(
        "coinjoin",
        inputs=[("one", 0, "cj-a"), ("two", 0, "cj-b"), ("three", 0, "cj-c")],
        outputs=[("cj-out-a", 100_000), ("cj-out-b", 100_000), ("cj-out-c", 100_000)],
        height=4,
    )
    result = analyse_wallet(WALLET, CountingHistoryClient([*ambiguous, coinjoin]))

    assert result.summary.findings_by_type[FindingType.PEEL_CHAIN] == 0
    assert all(
        not {"cj-a", "cj-b", "cj-c"}.intersection(cluster.addresses)
        for cluster in result.clusters
    )


def test_analyse_endpoint_is_thin_and_returns_the_complete_result() -> None:
    app = create_app()
    client = CountingHistoryClient(_valid_peel_chain())
    with TestClient(app) as test_client:
        app.state.blockchain_client = client
        response = test_client.post("/analyse", json={"wallet_address": WALLET})

    assert response.status_code == 200
    body = response.json()
    assert client.calls == 1
    assert body["address"] == WALLET
    assert body["summary"]["findings_by_type"]["peel_chain"] == 1
    assert body["clusters"]
