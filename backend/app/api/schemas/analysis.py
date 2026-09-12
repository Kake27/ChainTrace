from pydantic import BaseModel, Field

from app.models.finding import Finding
from app.models.transaction import Transaction


class AddressReuseRequest(BaseModel):
    transactions: list[Transaction] = Field(
        description="Normalized transactions, e.g. the `transactions` array from wallet history"
    )


class AddressReuseResponse(BaseModel):
    source: str
    address: str | None = None
    transaction_count: int
    reused_address_count: int
    findings: list[Finding]
    warnings: list[str] = Field(default_factory=list)


class CommonInputRequest(AddressReuseRequest):
    pass


class CommonInputResponse(BaseModel):
    source: str
    address: str | None = None
    transaction_count: int
    pair_count: int
    findings: list[Finding]
    warnings: list[str] = Field(default_factory=list)
