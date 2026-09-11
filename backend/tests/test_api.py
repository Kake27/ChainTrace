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
