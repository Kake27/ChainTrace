from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from itertools import combinations

from app.analysis.graph import TransactionGraph
from app.heuristics.change_detection import detect_change_candidates, infer_script_type
from app.heuristics.common_input import looks_coinjoin_like
from app.models.finding import Finding
from app.models.transaction import Transaction


@dataclass(frozen=True)
class OwnershipEdge:
    """An explainable candidate ownership relationship between two addresses."""

    left: str
    right: str
    relationship: str
    transaction: str
    confidence: float
    evidence: dict


@dataclass
class ClusterProfile:
    addresses: set[str] = field(default_factory=set)
    script_types: set[str] = field(default_factory=set)
    transaction_output_counts: Counter[int] = field(default_factory=Counter)
    change_positions: Counter[int] = field(default_factory=Counter)


class _UnionFind:
    def __init__(self) -> None:
        self._parent: dict[str, str] = {}

    def add(self, item: str) -> None:
        self._parent.setdefault(item, item)

    def find(self, item: str) -> str:
        self.add(item)
        parent = self._parent[item]
        if parent != item:
            self._parent[item] = self.find(parent)
        return self._parent[item]

    def union(self, left: str, right: str) -> None:
        left_root, right_root = self.find(left), self.find(right)
        if left_root != right_root:
            self._parent[right_root] = left_root


@dataclass
class OwnershipClusters:
    """Union-Find clusters plus the raw edges that justify every merge."""

    edges: list[OwnershipEdge]
    _address_to_cluster: dict[str, str]
    profiles: dict[str, ClusterProfile]

    def cluster_for(self, address: str | None) -> str | None:
        return self._address_to_cluster.get(address) if address else None

    def same_cluster(self, left: str | None, right: str | None) -> bool:
        left_cluster, right_cluster = self.cluster_for(left), self.cluster_for(right)
        return bool(left_cluster and right_cluster and left_cluster == right_cluster)

    def profile_for(self, address: str | None) -> ClusterProfile | None:
        cluster = self.cluster_for(address)
        return self.profiles.get(cluster) if cluster else None

    def has_probable_change_edge(self, transaction: str, change_address: str) -> bool:
        return any(
            edge.relationship == "probable_change"
            and edge.transaction == transaction
            and edge.right == change_address
            for edge in self.edges
        )


def build_ownership_clusters(
    transactions: list[Transaction],
    *,
    graph: TransactionGraph | None = None,
    change_findings: list[Finding] | None = None,
) -> OwnershipClusters:
    """Build conservative clusters from common-input and probable-change edges.

    CoinJoin-like transactions never create edges.  The returned edges retain the
    transaction, confidence, and feature evidence used for each Union-Find merge.
    """
    graph = graph or TransactionGraph.build(transactions)
    change_findings = (
        detect_change_candidates(transactions) if change_findings is None else change_findings
    )
    uf = _UnionFind()
    edges: list[OwnershipEdge] = []
    by_id = graph.by_txid

    for tx in transactions:
        if looks_coinjoin_like(tx):
            continue
        inputs = sorted({item.address for item in tx.inputs if item.address})
        for left, right in combinations(inputs, 2):
            edges.append(
                OwnershipEdge(
                    left=left,
                    right=right,
                    relationship="common_input_ownership",
                    transaction=tx.txid,
                    confidence=0.82,
                    evidence={"addresses": [left, right], "transaction": tx.txid},
                )
            )

    for finding in change_findings:
        if not finding.affected_transactions or not finding.affected_addresses:
            continue
        tx = by_id.get(finding.affected_transactions[0])
        if tx is None or looks_coinjoin_like(tx):
            continue
        payload = finding.evidence[0].payload if finding.evidence else {}
        change_address = finding.affected_addresses[0]
        for input_address in sorted({item.address for item in tx.inputs if item.address}):
            if input_address == change_address:
                continue
            edges.append(
                OwnershipEdge(
                    left=input_address,
                    right=change_address,
                    relationship="probable_change",
                    transaction=tx.txid,
                    confidence=finding.confidence,
                    evidence={
                        "transaction": tx.txid,
                        "vout": payload.get("vout"),
                        "features": payload.get("features", {}),
                        "ambiguous": payload.get("ambiguous", False),
                    },
                )
            )

    for edge in edges:
        uf.union(edge.left, edge.right)

    groups: dict[str, set[str]] = defaultdict(set)
    all_addresses = {
        item.address
        for tx in transactions
        for item in (*tx.inputs, *tx.outputs)
        if item.address
    }
    for address in all_addresses:
        groups[uf.find(address)].add(address)

    # Stable IDs make serialized evidence and tests reproducible.
    address_to_cluster: dict[str, str] = {}
    profiles: dict[str, ClusterProfile] = {}
    for index, addresses in enumerate(sorted(groups.values(), key=lambda group: sorted(group)[0]), start=1):
        cluster_id = f"cluster_{index:03d}"
        profile = ClusterProfile(addresses=addresses)
        profiles[cluster_id] = profile
        for address in addresses:
            address_to_cluster[address] = cluster_id

    _populate_profiles(profiles, address_to_cluster, transactions, change_findings)
    return OwnershipClusters(edges=edges, _address_to_cluster=address_to_cluster, profiles=profiles)


def _populate_profiles(
    profiles: dict[str, ClusterProfile],
    address_to_cluster: dict[str, str],
    transactions: list[Transaction],
    change_findings: list[Finding],
) -> None:
    for tx in transactions:
        tx_clusters = {
            address_to_cluster[item.address]
            for item in (*tx.inputs, *tx.outputs)
            if item.address in address_to_cluster
        }
        for cluster_id in tx_clusters:
            profiles[cluster_id].transaction_output_counts[len(tx.outputs)] += 1
        for output in tx.outputs:
            cluster_id = address_to_cluster.get(output.address or "")
            if cluster_id:
                script = infer_script_type(output.address, output.script_type)
                if script:
                    profiles[cluster_id].script_types.add(script)

    for finding in change_findings:
        if not finding.affected_addresses or not finding.evidence:
            continue
        cluster_id = address_to_cluster.get(finding.affected_addresses[0])
        vout = finding.evidence[0].payload.get("vout")
        if cluster_id and isinstance(vout, int):
            profiles[cluster_id].change_positions[vout] += 1
