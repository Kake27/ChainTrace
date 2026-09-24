from __future__ import annotations

from dataclasses import dataclass

from app.analysis.clustering import OwnershipClusters, build_ownership_clusters
from app.config import settings
from app.heuristics.change_detection import detect_change_candidates, infer_script_type
from app.heuristics.common_input import looks_coinjoin_like
from app.models.evidence import Evidence
from app.models.finding import Finding, FindingType, Severity
from app.models.transaction import Transaction

_MIN_CHAIN_TRANSACTIONS = 3
_MAX_CONFIDENCE = 0.82
_LIMITATION = (
    "A peel chain is a candidate continuation pattern inferred from change detection and "
    "cluster evidence. It does not prove that the same person or entity controlled every address."
)


@dataclass(frozen=True)
class _Hop:
    origin_txid: str
    origin_vout: int
    address: str
    value_sats: int
    input_total_sats: int
    payment_total_sats: int
    payment_outputs: list[dict]
    fee_sats: int | None
    next_txid: str
    change_confidence: float
    change_features: dict
    cluster_id: str | None
    profile_match: dict


class _PeelDetector:
    def __init__(self, transactions: list[Transaction]) -> None:
        self.by_id = {tx.txid: tx for tx in transactions}
        self.clusters: OwnershipClusters = build_ownership_clusters(transactions)
        self.spends = self._spending_index(transactions)
        self.change_by_txid = self._change_candidates(transactions)

    @staticmethod
    def _spending_index(transactions: list[Transaction]) -> dict[tuple[str, int], str]:
        index: dict[tuple[str, int], str] = {}
        for tx in transactions:
            for item in tx.inputs:
                key = (item.previous_txid, item.previous_vout)
                # Conflicting supplied histories are ambiguous; never select either spender.
                if key in index and index[key] != tx.txid:
                    index[key] = ""
                else:
                    index[key] = tx.txid
        return index

    @staticmethod
    def _change_candidates(transactions: list[Transaction]) -> dict[str, list[dict]]:
        grouped: dict[str, list[dict]] = {}
        for finding in detect_change_candidates(transactions):
            if not finding.affected_transactions or not finding.evidence:
                continue
            txid = finding.affected_transactions[0]
            payload = finding.evidence[0].payload
            if isinstance(payload.get("vout"), int) and finding.affected_addresses:
                grouped.setdefault(txid, []).append(
                    {
                        "vout": payload["vout"],
                        "address": finding.affected_addresses[0],
                        "value_sats": payload.get("value", 0),
                        "confidence": finding.confidence,
                        "features": payload.get("features", {}),
                    }
                )
        return grouped

    def find_next(self, tx: Transaction) -> _Hop | None:
        """Return the sole compatible spent change continuation, otherwise abstain."""
        if looks_coinjoin_like(tx):
            return None
        candidates: list[_Hop] = []
        for candidate in self.change_by_txid.get(tx.txid, []):
            next_txid = self.spends.get((tx.txid, candidate["vout"]))
            next_tx = self.by_id.get(next_txid or "")
            if next_tx is None or looks_coinjoin_like(next_tx):
                continue
            profile_match = self._profile_matches(tx, candidate, next_tx)
            if not profile_match["compatible"]:
                continue
            candidates.append(
                _Hop(
                    origin_txid=tx.txid,
                    origin_vout=candidate["vout"],
                    address=candidate["address"],
                    value_sats=candidate["value_sats"],
                    input_total_sats=sum(item.value for item in tx.inputs),
                    payment_total_sats=sum(
                        output.value for output in tx.outputs if output.vout != candidate["vout"]
                    ),
                    payment_outputs=[
                        {
                            "vout": output.vout,
                            "address": output.address,
                            "value_sats": output.value,
                        }
                        for output in tx.outputs
                        if output.vout != candidate["vout"]
                    ],
                    fee_sats=tx.fee,
                    next_txid=next_txid or "",
                    change_confidence=candidate["confidence"],
                    change_features=candidate["features"],
                    cluster_id=self.clusters.cluster_for(candidate["address"]),
                    profile_match=profile_match,
                )
            )

        next_txids = {hop.next_txid for hop in candidates}
        if len(next_txids) != 1:
            return None
        # If several candidate outputs lead to the same next transaction, select only
        # a uniquely highest-scored output. A score tie is still ambiguous.
        candidates.sort(key=lambda hop: (-hop.change_confidence, hop.origin_vout))
        if len(candidates) > 1 and candidates[0].change_confidence == candidates[1].change_confidence:
            return None
        return candidates[0]

    def find_previous(self, tx: Transaction) -> _Hop | None:
        """Return the sole compatible change hop ending at tx, otherwise abstain."""
        candidates: list[_Hop] = []
        for item in tx.inputs:
            previous = self.by_id.get(item.previous_txid)
            if previous is None or looks_coinjoin_like(previous):
                continue
            hop = self.find_next(previous)
            if hop and hop.next_txid == tx.txid and hop.origin_vout == item.previous_vout:
                candidates.append(hop)
        unique = {(hop.origin_txid, hop.origin_vout): hop for hop in candidates}
        return next(iter(unique.values())) if len(unique) == 1 else None

    def _profile_matches(self, origin_tx: Transaction, candidate: dict, next_tx: Transaction) -> dict:
        change_address = candidate["address"]
        cluster_id = self.clusters.cluster_for(change_address)
        profile = self.clusters.profile_for(change_address)
        input_addresses = {item.address for item in next_tx.inputs if item.address}
        same_cluster_inputs = sorted(
            address for address in input_addresses if self.clusters.same_cluster(change_address, address)
        )
        input_scripts = {
            infer_script_type(item.address)
            for item in next_tx.inputs
            if item.address and infer_script_type(item.address)
        }
        script_match = not profile or not profile.script_types or bool(input_scripts & profile.script_types)
        cluster_change_edge = self.clusters.has_probable_change_edge(origin_tx.txid, change_address)
        cluster_peer_count = len(profile.addresses) if profile else 0
        structure_match = bool(profile and profile.transaction_output_counts)
        return {
            # These are continuation-selection gates, not merely diagnostics.
            "compatible": bool(same_cluster_inputs)
            and cluster_change_edge
            and cluster_peer_count > 1
            and script_match
            and structure_match,
            "cluster_id": cluster_id,
            "cluster_change_edge": cluster_change_edge,
            "cluster_peer_count": cluster_peer_count,
            "structure_match": structure_match,
            "same_cluster_input_addresses": same_cluster_inputs,
            "input_script_types": sorted(input_scripts),
            "profile_script_types": sorted(profile.script_types) if profile else [],
            "known_output_counts": dict(profile.transaction_output_counts) if profile else {},
            "known_change_positions": dict(profile.change_positions) if profile else {},
        }

    def chain_from(self, seed: Transaction) -> tuple[list[str], list[_Hop]]:
        """Combine conservative backward and forward traversal around one seed."""
        backwards: list[_Hop] = []
        current = seed
        seen = {seed.txid}
        while len(backwards) < settings.peel_chain_max_hops:
            hop = self.find_previous(current)
            if hop is None or hop.origin_txid in seen:
                break
            backwards.append(hop)
            seen.add(hop.origin_txid)
            current = self.by_id[hop.origin_txid]

        forward: list[_Hop] = []
        current = seed
        while len(forward) < settings.peel_chain_max_hops:
            hop = self.find_next(current)
            if hop is None or hop.next_txid in seen:
                break
            forward.append(hop)
            seen.add(hop.next_txid)
            current = self.by_id[hop.next_txid]

        hops = list(reversed(backwards)) + forward
        if not hops:
            return [seed.txid], []
        txids = [hops[0].origin_txid, *[hop.next_txid for hop in hops]]
        return txids, hops


def detect_peel_chains(transactions: list[Transaction]) -> list[Finding]:
    """Find 3+ transaction chains with a unique, decreasing change continuation."""
    detector = _PeelDetector(transactions)
    deduplicated: set[tuple[str, ...]] = set()
    findings: list[Finding] = []
    for seed in sorted(transactions, key=lambda item: item.txid):
        txids, hops = detector.chain_from(seed)
        if len(txids) < _MIN_CHAIN_TRANSACTIONS or tuple(txids) in deduplicated:
            continue
        values = [hop.value_sats for hop in hops]
        if len(values) < 2 or not all(left > right for left, right in zip(values, values[1:])):
            continue
        deduplicated.add(tuple(txids))
        findings.append(_to_finding(txids, hops))

    findings.sort(key=lambda finding: (-finding.confidence, finding.affected_transactions))
    for index, finding in enumerate(findings, start=1):
        finding.id = f"peel_chain_{index:03d}"
    return findings


def _to_finding(txids: list[str], hops: list[_Hop]) -> Finding:
    confidence, confidence_components = _confidence(hops)
    addresses = list(dict.fromkeys(hop.address for hop in hops))
    hop_evidence = [
        {
            "from_transaction": hop.origin_txid,
            "change_vout": hop.origin_vout,
            "change_address": hop.address,
            "continuation_value_sats": hop.value_sats,
            "change_value_sats": hop.value_sats,
            "input_total_sats": hop.input_total_sats,
            "payment_total_sats": hop.payment_total_sats,
            "payment_outputs": hop.payment_outputs,
            "fee_sats": hop.fee_sats,
            "to_transaction": hop.next_txid,
            "change_confidence": hop.change_confidence,
            "change_features": hop.change_features,
            "cluster_id": hop.cluster_id,
            "profile_match": hop.profile_match,
        }
        for hop in hops
    ]
    return Finding(
        id="peel_chain",
        type=FindingType.PEEL_CHAIN,
        severity=_severity(confidence),
        confidence=confidence,
        affected_addresses=addresses,
        affected_transactions=txids,
        evidence=[
            Evidence(
                type="peel_chain_path",
                payload={
                    "transactions": txids,
                    "hop_count": len(hops),
                    "hops": hop_evidence,
                    "continuation_values_sats": [hop.value_sats for hop in hops],
                    "decreasing_value_pattern": True,
                    "confidence_components": confidence_components,
                },
            )
        ],
        inference=(
            f"{' → '.join(txids)} forms a {len(txids)}-transaction candidate peel chain. "
            "Each reported continuation is a uniquely selected, spent change candidate with "
            "a decreasing remainder value. This is linkage evidence, not proven ownership."
        ),
        limitations=[_LIMITATION],
        recommendation=(
            "Avoid making a repeated change-continuation pattern easy to follow; use wallet "
            "coin selection and payment structures that do not expose a distinctive remainder path."
        ),
    )


def _confidence(hops: list[_Hop]) -> tuple[float, dict]:
    """Score the chain itself, rather than copying a change-candidate score."""
    mean_change = sum(hop.change_confidence for hop in hops) / len(hops)
    cluster_support = sum(
        1.0
        for hop in hops
        if hop.profile_match["cluster_change_edge"] and hop.profile_match["cluster_peer_count"] > 1
    ) / len(hops)
    fresh_change_ratio = sum(
        1.0 for hop in hops if hop.change_features.get("new_address", False)
    ) / len(hops)
    decreases = [
        (left.value_sats - right.value_sats) / left.value_sats
        for left, right in zip(hops, hops[1:])
        if left.value_sats > right.value_sats and left.value_sats > 0
    ]
    decrease_strength = min(1.0, (sum(decreases) / len(decreases)) * 5) if decreases else 0.0
    path_maturity = min(1.0, len(hops) / 3)
    raw_score = (
        0.45 * mean_change
        + 0.25 * cluster_support
        + 0.15 * decrease_strength
        + 0.10 * fresh_change_ratio
        + 0.05 * path_maturity
    )
    return round(min(_MAX_CONFIDENCE, raw_score), 2), {
        "mean_change_confidence": round(mean_change, 2),
        "cluster_support": round(cluster_support, 2),
        "fresh_change_ratio": round(fresh_change_ratio, 2),
        "decrease_strength": round(decrease_strength, 2),
        "path_maturity": round(path_maturity, 2),
        "raw_score": round(raw_score, 2),
    }


def _severity(confidence: float) -> Severity:
    if confidence >= 0.75:
        return Severity.HIGH
    if confidence >= 0.55:
        return Severity.MEDIUM
    return Severity.LOW
