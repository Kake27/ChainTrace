from fastapi.testclient import TestClient

from app.main import create_app
from app.models.transaction import Input, Output, Transaction
from app.models.utxo import UTXO


class FakeBlockchainClient:
    def get_wallet_history(self, address: str, *, max_transactions: int | None = None):
        tx = Transaction(
            txid="tx1",
            timestamp=1700000000,
            block_height=800000,
            inputs=[Input(previous_txid="prev", previous_vout=0, address=address, value=1000)],
            outputs=[Output(vout=0, address=address, value=900)],
            fee=100,
            confirmed=True,
        )
        utxo = UTXO(txid="tx1", vout=0, address=address, value=900, confirmed=True)
        return [tx], [utxo], []


class ReuseFakeBlockchainClient:
    def get_wallet_history(self, address: str, *, max_transactions: int | None = None):
        txs = [
            Transaction(
                txid="tx1",
                inputs=[],
                outputs=[Output(vout=0, address=address, value=1000)],
                fee=0,
                confirmed=True,
            ),
            Transaction(
                txid="tx2",
                inputs=[Input(previous_txid="tx1", previous_vout=0, address=address, value=1000)],
                outputs=[Output(vout=0, address="bc1qotherbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", value=900)],
                fee=100,
                confirmed=True,
            ),
        ]
        return txs, [], []


def test_health() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_wallet_history_rejects_invalid_address() -> None:
    app = create_app()
    with TestClient(app) as client:
        response = client.get("/api/v1/wallet/not-an-address/history")
    assert response.status_code == 400


def test_wallet_history_returns_normalized_models() -> None:
    app = create_app()
    address = "bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq"
    with TestClient(app) as client:
        app.state.blockchain_client = FakeBlockchainClient()
        response = client.get(f"/api/v1/wallet/{address}/history")

    assert response.status_code == 200
    body = response.json()
    assert body["address"] == address
    assert body["transaction_count"] == 1
    assert body["utxo_count"] == 1
    assert body["total_utxo_value"] == 900
    assert body["transactions"][0]["outputs"][0]["value"] == 900
    assert body["warnings"] == []


def test_address_reuse_get_uses_wallet_history() -> None:
    app = create_app()
    address = "bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq"
    with TestClient(app) as client:
        app.state.blockchain_client = ReuseFakeBlockchainClient()
        response = client.get(f"/api/v1/wallet/{address}/heuristics/address-reuse")

    assert response.status_code == 200
    body = response.json()
    assert body["reused_address_count"] == 1
    assert body["findings"][0]["affected_addresses"] == [address]
    assert body["findings"][0]["affected_transactions"] == ["tx1", "tx2"]


def test_address_reuse_post_consumes_history_payload() -> None:
    history_tx = {
        "txid": "tx1",
        "inputs": [],
        "outputs": [{"vout": 0, "address": "bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq", "value": 1}],
        "fee": 0,
        "confirmed": True,
    }
    other = dict(history_tx)
    other["txid"] = "tx2"
    with TestClient(create_app()) as client:
        response = client.post(
            "/api/v1/heuristics/address-reuse",
            json={"transactions": [history_tx, other]},
        )
    assert response.status_code == 200
    assert response.json()["reused_address_count"] == 1


def test_common_input_post_consumes_history_payload() -> None:
    tx = {
        "txid": "tx1",
        "inputs": [
            {"previous_txid": "p0", "previous_vout": 0, "address": "addrA", "value": 1000},
            {"previous_txid": "p1", "previous_vout": 0, "address": "addrB", "value": 1000},
        ],
        "outputs": [{"vout": 0, "address": "addrC", "value": 1900}],
        "fee": 100,
        "confirmed": True,
    }
    with TestClient(create_app()) as client:
        response = client.post("/api/v1/heuristics/common-input", json={"transactions": [tx]})
    assert response.status_code == 200
    body = response.json()
    assert body["pair_count"] == 1
    assert body["findings"][0]["affected_addresses"] == ["addrA", "addrB"]
    assert body["findings"][0]["type"] == "common_input_ownership"


