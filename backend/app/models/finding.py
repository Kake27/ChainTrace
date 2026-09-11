from enum import StrEnum

from pydantic import BaseModel, Field

from app.models.evidence import Evidence


class FindingType(StrEnum):
    ADDRESS_REUSE = "address_reuse"
    COMMON_INPUT_OWNERSHIP = "common_input_ownership"
    CHANGE_CANDIDATE = "change_candidate"
    TIMING_CORRELATION = "timing_correlation"
    AMOUNT_CORRELATION = "amount_correlation"
    PEEL_CHAIN = "peel_chain"


class Severity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Finding(BaseModel):
    id: str
    type: FindingType
    severity: Severity
    confidence: float = Field(ge=0.0, le=1.0)
    affected_addresses: list[str] = Field(default_factory=list)
    affected_transactions: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    inference: str
    limitations: list[str] = Field(default_factory=list)
    recommendation: str | None = None
    explanation: str | None = None
