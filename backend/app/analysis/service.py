from __future__ import annotations

from collections import Counter
from typing import Protocol

from app.analysis.clustering import OwnershipClusters, build_ownership_clusters
from app.analysis.graph import TransactionGraph
from app.blockchain.address import is_valid_bitcoin_address
from app.heuristics.address_reuse import detect_address_reuse
from app.heuristics.amount_correlation import detect_amount_correlations
from app.heuristics.change_detection import detect_change_candidates
from app.heuristics.common_input import detect_common_input_ownership
from app.heuristics.peel_chain import detect_peel_chains
from app.heuristics.timing import detect_timing_correlations
from app.models.analysis import AnalysisResult, AnalysisSummary, Cluster
from app.models.evidence import Evidence
from app.models.finding import Finding, FindingType, Severity
from app.models.transaction import Transaction
from app.models.utxo import UTXO


class WalletHistoryClient(Protocol):
    def get_wallet_history(
        self, address: str, *, max_transactions: int | None = None
    ) -> tuple[list[Transaction], list[UTXO], list[str]]: ...


def analyse_wallet(
    address: str,
    client: WalletHistoryClient,
    *,
    max_transactions: int | None = None,
) -> AnalysisResult:
    """Fetch a wallet once and return all deterministic analysis from one snapshot."""
    if not is_valid_bitcoin_address(address):
        raise ValueError("Invalid Bitcoin address")

    transactions, _utxos, warnings = client.get_wallet_history(
        address, max_transactions=max_transactions
    )
    graph = TransactionGraph.build(transactions)

    address_reuse = detect_address_reuse(transactions)
    common_input = detect_common_input_ownership(transactions)
    change = detect_change_candidates(transactions)
    timing = detect_timing_correlations(transactions)
    amount = detect_amount_correlations(
        transactions,
        address_reuse_findings=address_reuse,
        common_input_findings=common_input,
        change_findings=change,
        timing_findings=timing,
    )
    ownership = build_ownership_clusters(transactions, graph=graph, change_findings=change)
    peel_chains = detect_peel_chains(
        transactions,
        graph=graph,
        clusters=ownership,
        change_findings=change,
    )

    findings = _sort_findings(
        [*address_reuse, *common_input, *change, *timing, *amount, *peel_chains]
    )
    clusters = _serialize_clusters(ownership)
    return AnalysisResult(
        address=address,
        source="mempool",
        warnings=warnings,
        summary=_summary(transactions, findings, clusters),
        findings=findings,
        clusters=clusters,
    )


def _sort_findings(findings: list[Finding]) -> list[Finding]:
    return sorted(findings, key=lambda finding: (finding.type, finding.id))


def _summary(
    transactions: list[Transaction], findings: list[Finding], clusters: list[Cluster]
) -> AnalysisSummary:
    by_type = Counter(finding.type for finding in findings)
    by_severity = Counter(finding.severity for finding in findings)
    return AnalysisSummary(
        transaction_count=len(transactions),
        total_findings=len(findings),
        findings_by_type={kind: by_type[kind] for kind in FindingType},
        findings_by_severity={severity: by_severity[severity] for severity in Severity},
        cluster_count=len(clusters),
    )


def _serialize_clusters(ownership: OwnershipClusters) -> list[Cluster]:
    edges_by_cluster: dict[str, list[Evidence]] = {}
    for edge in ownership.edges:
        cluster_id = ownership.cluster_for(edge.left)
        if cluster_id is None:
            continue
        edges_by_cluster.setdefault(cluster_id, []).append(
            Evidence(
                type="ownership_edge",
                payload={
                    "relationship": edge.relationship,
                    "left_address": edge.left,
                    "right_address": edge.right,
                    "transaction": edge.transaction,
                    "confidence": edge.confidence,
                    "details": edge.evidence,
                },
            )
        )

    clusters: list[Cluster] = []
    for cluster_id, profile in sorted(ownership.profiles.items()):
        # Singletons do not represent an inferred ownership linkage.
        if len(profile.addresses) < 2:
            continue
        clusters.append(
            Cluster(
                id=cluster_id,
                addresses=sorted(profile.addresses),
                script_types=sorted(profile.script_types),
                transaction_output_counts=dict(profile.transaction_output_counts),
                change_positions=dict(profile.change_positions),
                evidence=edges_by_cluster.get(cluster_id, []),
            )
        )
    return clusters
