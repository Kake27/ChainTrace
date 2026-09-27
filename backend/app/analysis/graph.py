from __future__ import annotations

from dataclasses import dataclass

from app.models.transaction import Transaction


@dataclass(frozen=True)
class TransactionGraph:
    """One immutable transaction/UTXO relationship index for an analysis run."""

    transactions: tuple[Transaction, ...]
    by_txid: dict[str, Transaction]
    spending_by_outpoint: dict[tuple[str, int], str]

    @classmethod
    def build(cls, transactions: list[Transaction]) -> TransactionGraph:
        by_txid = {tx.txid: tx for tx in transactions}
        spending_by_outpoint: dict[tuple[str, int], str] = {}
        for tx in transactions:
            for item in tx.inputs:
                outpoint = (item.previous_txid, item.previous_vout)
                # A conflicting supplied history is ambiguous; no consumer may use it.
                if outpoint in spending_by_outpoint and spending_by_outpoint[outpoint] != tx.txid:
                    spending_by_outpoint[outpoint] = ""
                else:
                    spending_by_outpoint[outpoint] = tx.txid
        return cls(
            transactions=tuple(transactions),
            by_txid=by_txid,
            spending_by_outpoint=spending_by_outpoint,
        )
