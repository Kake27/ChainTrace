from __future__ import annotations

from pydantic import BaseModel, Field

from app.models.evidence import Evidence
from app.models.finding import Finding, FindingType, Severity


class Cluster(BaseModel):
    """An explainable candidate ownership cluster, never a proven identity."""

    id: str
    addresses: list[str]
    script_types: list[str] = Field(default_factory=list)
    transaction_output_counts: dict[int, int] = Field(default_factory=dict)
    change_positions: dict[int, int] = Field(default_factory=dict)
    evidence: list[Evidence] = Field(default_factory=list)


class AnalysisSummary(BaseModel):
    transaction_count: int
    total_findings: int
    findings_by_type: dict[FindingType, int]
    findings_by_severity: dict[Severity, int]
    cluster_count: int


class AnalysisResult(BaseModel):
    address: str
    source: str
    warnings: list[str] = Field(default_factory=list)
    summary: AnalysisSummary
    findings: list[Finding] = Field(default_factory=list)
    clusters: list[Cluster] = Field(default_factory=list)
