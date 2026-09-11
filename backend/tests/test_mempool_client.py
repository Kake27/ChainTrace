import json

import httpx

from app.blockchain.mempool_client import MempoolBlockchainClient
from tests.esplora_samples import SAMPLE_TX, SAMPLE_UTXO


def _client_for(handler) -> MempoolBlockchainClient:
    transport = httpx.MockTransport(handler)
    http = httpx.Client(transport=transport)
    return MempoolBlockchainClient(
        base_url="https://mempool.space/api",
        client=http,
        max_transactions=10,
    )


def test_get_address_transactions_paginates_until_empty() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        path = request.url.path
        if path.endswith("/txs"):
            first = dict(SAMPLE_TX)
            first["txid"] = "tx-page-1"
            return httpx.Response(200, json=[first])
        if "/txs/chain/" in path:
            return httpx.Response(200, json=[])
        raise AssertionError(path)

    client = _client_for(handler)
    txs = client.get_address_transactions("bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq")
    assert [tx.txid for tx in txs] == ["tx-page-1"]
    assert any(url.endswith("/txs") for url in calls)


def test_get_address_transactions_respects_max() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        page = []
        for i in range(5):
            item = dict(SAMPLE_TX)
            item["txid"] = f"tx-{i}"
            page.append(item)
        return httpx.Response(200, json=page)

    client = _client_for(handler)
    txs = client.get_address_transactions(
        "bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq",
        max_transactions=3,
    )
    assert len(txs) == 3


def test_get_utxos_and_single_transaction() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/utxo"):
            return httpx.Response(200, json=[SAMPLE_UTXO])
        if path.endswith("/tx/abc123def456"):
            return httpx.Response(200, content=json.dumps(SAMPLE_TX))
        raise AssertionError(path)

    client = _client_for(handler)
    utxos = client.get_utxos("bc1qchangedddddddddddddddddddddddddddd")
    assert len(utxos) == 1
    tx = client.get_transaction("abc123def456")
    assert tx.txid == SAMPLE_TX["txid"]


def test_wallet_history_continues_when_utxo_list_is_capped() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/txs"):
            return httpx.Response(200, json=[SAMPLE_TX])
        if "/txs/chain/" in path:
            return httpx.Response(200, json=[])
        if path.endswith("/utxo"):
            return httpx.Response(
                400,
                text="Too many unspent transaction outputs (>500). Contact support to raise limits.",
            )
        raise AssertionError(path)

    client = _client_for(handler)
    txs, utxos, warnings = client.get_wallet_history(
        "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa",
        max_transactions=5,
    )
    assert len(txs) == 1
    assert utxos == []
    assert warnings
    assert "500" in warnings[0] or "UTXO" in warnings[0]
