from pydantic import BaseModel, Field


class Input(BaseModel):
    previous_txid: str
    previous_vout: int = Field(ge=0)
    address: str | None = None
    value: int = Field(ge=0, description="Value in satoshis")


class Output(BaseModel):
    vout: int = Field(ge=0)
    address: str | None = None
    value: int = Field(ge=0, description="Value in satoshis")
    script_type: str | None = None


class Transaction(BaseModel):
    txid: str
    timestamp: int | None = None
    block_height: int | None = None
    inputs: list[Input]
    outputs: list[Output]
    fee: int | None = Field(default=None, ge=0, description="Fee in satoshis")
    confirmed: bool = False
