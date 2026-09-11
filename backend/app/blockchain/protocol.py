from typing import Protocol

from app.models.transaction import Transaction
from app.models.utxo import UTXO


class BlockchainClient(Protocol):
    def get_address_transactions(self, address: str, *, max_transactions: int | None = None) -> list[Transaction]: ...

    def get_transaction(self, txid: str) -> Transaction: ...

    def get_utxos(self, address: str) -> list[UTXO]: ...
