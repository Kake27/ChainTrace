from pydantic import BaseModel, Field


class UTXO(BaseModel):
    txid: str
    vout: int = Field(ge=0)
    address: str | None = None
    value: int = Field(ge=0, description="Value in satoshis")
    spent_by_txid: str | None = None
    confirmed: bool = False
    block_height: int | None = None
