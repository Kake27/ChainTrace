from __future__ import annotations

import statistics
from math import sqrt
from collections import defaultdict
from itertools import combinations
from typing import NamedTuple

from app.config import settings
from app.models.evidence import Evidence
from app.models.finding import Finding, FindingType, Severity
from app.models.transaction import Transaction

_LIMITATION = (
    "Timing correlation is supporting evidence of a shared activity pattern, "
    "not proof that two addresses share an owner. Unrelated wallets can transact "
    "close together by chance."
)
_MAX_WINDOW_SECONDS = 300
_MAX_CONFIDENCE = 0.72
_MIN_SCORE = 0.30
_REPRESENTATIVE_PAIR_LIMIT = 3


class _Event(NamedTuple):
    txid: str
    timestamp: int
    block_height: int | None


def detect_timing_correlations(
    transactions: list[Transaction],
    *,
    window_seconds: int | None = None,
) -> list[Finding]:
    """Correlate distinct-tx activity between address pairs inside a short window."""
    window = _window(window_seconds)
    min_hits = settings.timing_min_occurrences
    events = _events_by_address(transactions)
    active = sorted(addr for addr, items in events.items() if len(items) >= min_hits)

    findings: list[Finding] = []
    for left, right in combinations(active, 2):
        matches, same_block_excluded = _pair_matches(events[left], events[right], window)
        if len(matches) < min_hits:
            continue
        stats = _timing_stats(
            matches,
            events[left],
            events[right],
            window,
            same_block_excluded,
        )
        if stats["score"] < _MIN_SCORE:
            continue
        findings.append(_to_finding(left, right, matches, stats))

    findings.sort(key=lambda item: (-item.confidence, item.affected_addresses[0]))
    for index, finding in enumerate(findings, start=1):
        finding.id = f"timing_correlation_{index:03d}"
    return findings


def _window(window_seconds: int | None) -> int:
    raw = settings.timing_window_seconds if window_seconds is None else window_seconds
    return max(1, min(int(raw), _MAX_WINDOW_SECONDS))


def _events_by_address(transactions: list[Transaction]) -> dict[str, list[_Event]]:
    grouped: dict[str, list[_Event]] = defaultdict(list)
    seen: dict[str, set[str]] = defaultdict(set)
    for tx in transactions:
        if not tx.confirmed or tx.timestamp is None:
            continue
        for address in {item.address for item in (*tx.inputs, *tx.outputs) if item.address}:
            if tx.txid in seen[address]:
                continue
            seen[address].add(tx.txid)
            grouped[address].append(_Event(tx.txid, tx.timestamp, tx.block_height))
    for address in grouped:
        grouped[address].sort(key=lambda event: (event.timestamp, event.txid))
    return grouped


def _pair_matches(left: list[_Event], right: list[_Event], window: int) -> tuple[list[dict], int]:
    """Greedily select distinct, cross-block, non-zero timing relationships."""
    matches: list[dict] = []
    same_block_excluded = 0
    i = j = 0
    while i < len(left) and j < len(right):
        a, b = left[i], right[j]
        if a.txid == b.txid:
            i += 1
            continue
        if a.block_height is not None and a.block_height == b.block_height:
            same_block_excluded += 1
            i, j = _advance_earlier(i, j, a, b)
            continue
        delta = b.timestamp - a.timestamp
        if delta == 0:
            i, j = _advance_earlier(i, j, a, b)
            continue
        if abs(delta) <= window:
            matches.append(
                {
                    "left_txid": a.txid,
                    "right_txid": b.txid,
                    "left_block_height": a.block_height,
                    "right_block_height": b.block_height,
                    "delta_seconds": abs(delta),
                    "ordering": "left_then_right" if delta > 0 else "right_then_left" if delta < 0 else "simultaneous",
                }
            )
            i += 1
            j += 1
        elif a.timestamp < b.timestamp:
            i += 1
        else:
            j += 1
    return matches, same_block_excluded


def _advance_earlier(i: int, j: int, a: _Event, b: _Event) -> tuple[int, int]:
    if (a.timestamp, a.txid) <= (b.timestamp, b.txid):
        return i + 1, j
    return i, j + 1


def _timing_stats(
    matches: list[dict],
    left: list[_Event],
    right: list[_Event],
    window: int,
    same_block_excluded: int,
) -> dict:
    n = len(matches)
    abs_deltas = [item["delta_seconds"] for item in matches]
    median_delta = int(statistics.median(abs_deltas))
    mean_delta = int(statistics.mean(abs_deltas))
    stdev = float(statistics.pstdev(abs_deltas)) if n > 1 else 0.0
    order_counts = {"left_then_right": 0, "right_then_left": 0}
    for item in matches:
        order_counts[item["ordering"]] += 1
    order_consistency = max(order_counts.values()) / n
    occurrence_strength = min(1.0, n / 12)
    delta_consistency = 1.0 if stdev == 0 else max(0.0, 1.0 - stdev / window)
    tightness = 1.0 - min(1.0, median_delta / window)
    coverage = n / min(len(left), len(right))
    cross_block_count = sum(
        1
        for item in matches
        if item["left_block_height"] is not None and item["right_block_height"] is not None
    )
    timestamp_fallback_count = n - cross_block_count
    cross_block_strength = (cross_block_count + 0.6 * timestamp_fallback_count) / n
    temporal_consistency = 0.60 * delta_consistency + 0.40 * tightness
    # Pattern strength measures the repeatability of the observed subset. Coverage
    # then moderates confidence without deleting a small but potentially useful pattern.
    pattern_strength = (
        0.55 * occurrence_strength
        + 0.15 * temporal_consistency
        + 0.20 * order_consistency
        + 0.10 * cross_block_strength
    )
    coverage_modifier = 0.60 + 0.40 * sqrt(min(1.0, coverage))
    score = pattern_strength * coverage_modifier
    return {
        "score": round(min(_MAX_CONFIDENCE, score), 2),
        "occurrence_count": n,
        "median_delta_seconds": median_delta,
        "mean_delta_seconds": mean_delta,
        "stdev_delta_seconds": round(stdev, 2),
        "order_counts": order_counts,
        "order_consistency": round(order_consistency, 2),
        "coverage": round(coverage, 2),
        "pattern_strength": round(pattern_strength, 2),
        "coverage_modifier": round(coverage_modifier, 2),
        "window_seconds": window,
        "block_height_relationship": {
            "cross_block_occurrence_count": cross_block_count,
            "timestamp_fallback_occurrence_count": timestamp_fallback_count,
            "same_block_pairs_excluded": same_block_excluded,
        },
    }


def _to_finding(left: str, right: str, matches: list[dict], stats: dict) -> Finding:
    representative_pairs = sorted(
        matches,
        key=lambda item: (item["delta_seconds"], item["left_txid"], item["right_txid"]),
    )[:_REPRESENTATIVE_PAIR_LIMIT]
    txids = []
    for item in representative_pairs:
        txids.extend([item["left_txid"], item["right_txid"]])
    unique_txids = list(dict.fromkeys(txids))
    confidence = stats["score"]
    return Finding(
        id="timing_correlation",
        type=FindingType.TIMING_CORRELATION,
        severity=_severity(confidence, stats["occurrence_count"]),
        confidence=confidence,
        affected_addresses=[left, right],
        affected_transactions=unique_txids,
        evidence=[
            Evidence(
                type="timing_correlation",
                payload={
                    "addresses": [left, right],
                    "representative_pairs": representative_pairs,
                    "representative_transaction_ids": unique_txids,
                    **stats,
                },
            )
        ],
        inference=(
            f"{left} ↔ {right} show {stats['occurrence_count']} close-in-time "
            f"activities (median gap {stats['median_delta_seconds']}s). "
            "This may indicate a shared activity pattern, not common ownership."
        ),
        limitations=[_LIMITATION],
        recommendation=(
            "Avoid transacting from related addresses in tight bursts if those "
            "addresses should not be linked by timing analysis."
        ),
    )


def _severity(confidence: float, occurrences: int) -> Severity:
    if occurrences >= 4 and confidence >= 0.55:
        return Severity.MEDIUM
    return Severity.LOW
