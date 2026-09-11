from __future__ import annotations

import httpx

from app.blockchain.parser import parse_transaction, parse_utxo
from app.config import settings
from app.models.transaction import Transaction
from app.models.utxo import UTXO


class BlockchainAPIError(Exception):
    """Raised when the public explorer returns an unexpected error."""


class AddressNotFoundError(BlockchainAPIError):
    """Raised when the explorer has no record for the address."""


class UtxoUnavailableError(BlockchainAPIError):
    """Raised when the explorer refuses to list UTXOs (common for dust-heavy addresses)."""


class MempoolBlockchainClient:
    """Esplora-compatible client for mempool.space (or any Esplora base URL)."""

    def __init__(
        self,
        base_url: str | None = None,
        *,
        timeout: float | None = None,
        max_transactions: int | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self._base_url = (base_url or settings.mempool_base_url).rstrip("/")
        self._timeout = timeout if timeout is not None else settings.mempool_timeout_seconds
        self._max_transactions = (
            max_transactions if max_transactions is not None else settings.max_transactions
        )
        self._owns_client = client is None
        self._client = client or httpx.Client(timeout=self._timeout)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> MempoolBlockchainClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def get_address_transactions(
        self, address: str, *, max_transactions: int | None = None
    ) -> list[Transaction]:
        limit = max_transactions if max_transactions is not None else self._max_transactions
        collected: list[Transaction] = []
        seen: set[str] = set()
        last_txid: str | None = None

        while len(collected) < limit:
            page = self._fetch_tx_page(address, last_txid=last_txid)
            if not page:
                break
            new_items = 0
            for raw in page:
                txid = raw["txid"]
                if txid in seen:
                    continue
                seen.add(txid)
                collected.append(parse_transaction(raw))
                new_items += 1
                if len(collected) >= limit:
                    break
            if new_items == 0:
                break
            last_txid = page[-1]["txid"]

        return collected

    def get_transaction(self, txid: str) -> Transaction:
        raw = self._get_json(f"/tx/{txid}")
        return parse_transaction(raw)

    def get_utxos(self, address: str) -> list[UTXO]:
        raw_utxos = self._get_json(f"/address/{address}/utxo")
        if not isinstance(raw_utxos, list):
            raise BlockchainAPIError("Unexpected UTXO payload from explorer")
        return [parse_utxo(item, address=address) for item in raw_utxos]

    def get_wallet_history(
        self, address: str, *, max_transactions: int | None = None
    ) -> tuple[list[Transaction], list[UTXO], list[str]]:
        transactions = self.get_address_transactions(address, max_transactions=max_transactions)
        warnings: list[str] = []
        try:
            utxos = self.get_utxos(address)
        except UtxoUnavailableError as exc:
            utxos = []
            warnings.append(str(exc))
        return transactions, utxos, warnings

    def _fetch_tx_page(self, address: str, *, last_txid: str | None) -> list[dict]:
        path = f"/address/{address}/txs" if last_txid is None else f"/address/{address}/txs/chain/{last_txid}"
        payload = self._get_json(path)
        if not isinstance(payload, list):
            raise BlockchainAPIError("Unexpected transaction payload from explorer")
        return payload

    def _get_json(self, path: str) -> object:
        url = f"{self._base_url}{path}"
        try:
            response = self._client.get(url)
        except httpx.HTTPError as exc:
            raise BlockchainAPIError(f"Explorer request failed: {exc}") from exc

        if response.status_code == 404:
            raise AddressNotFoundError(f"Address or transaction not found: {path}")
        if response.status_code >= 400:
            body = response.text[:300]
            if path.endswith("/utxo") and _is_utxo_limit_error(response.status_code, body):
                raise UtxoUnavailableError(
                    "Explorer UTXO list is unavailable for this address (public APIs cap "
                    "addresses with more than 500 unspent outputs). Transaction history was "
                    "still retrieved."
                )
            raise BlockchainAPIError(
                f"Explorer returned HTTP {response.status_code} for {path}: {body}"
            )
        return response.json()


def _is_utxo_limit_error(status_code: int, body: str) -> bool:
    lowered = body.lower()
    return status_code in {400, 403, 413, 422, 429} and (
        "too many unspent" in lowered or "unspent transaction outputs" in lowered
    )
