from pydantic import BaseModel, Field

from app.models.transaction import Transaction
from app.models.utxo import UTXO


class WalletHistoryResponse(BaseModel):
    address: str
    source: str
    truncated: bool
    transaction_count: int
    utxo_count: int
    total_utxo_value: int = Field(description="Sum of unspent outputs in satoshis")
    transactions: list[Transaction]
    utxos: list[UTXO]
    warnings: list[str] = Field(default_factory=list)
