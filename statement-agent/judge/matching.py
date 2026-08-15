"""Non-circular, description-only transaction matching."""

import difflib
import re

from contracts.models import Bank
from judge.normalization import norm_desc

THRESHOLDS = {Bank.HDFC: 0.55, Bank.ICICI: 0.60, Bank.SBI: 0.60, Bank.AXIS: 0.60}


def description_similarity(left: str | None, right: str | None) -> float:
    if not left or not right:
        return 1.0 if not left and not right else 0.0
    if left == right:
        return 1.0
    left_tokens, right_tokens = set(re.findall(r"[a-z0-9]+", left)), set(re.findall(r"[a-z0-9]+", right))
    jaccard = len(left_tokens & right_tokens) / len(left_tokens | right_tokens) if left_tokens or right_tokens else 0.0
    ratio = difflib.SequenceMatcher(None, left, right).ratio()
    prefix = 1.0 if left.startswith(right[:14]) or right.startswith(left[:14]) else 0.0
    flat = difflib.SequenceMatcher(None, left.replace(" ", ""), right.replace(" ", "")).ratio()
    return max(0.6 * jaccard + 0.4 * ratio,
               0.5 * ratio + 0.5 * prefix * min(1.0, ratio + 0.3),
               flat * 0.98)


def match_transactions(actual: object, expected: object, threshold: float):
    """Return (actual-index, expected-index, similarity) pairs and unmatched indices."""
    actual = actual if isinstance(actual, list) else []
    expected = expected if isinstance(expected, list) else []
    actual_descriptions = [norm_desc(row.get("description")) if isinstance(row, dict) else None for row in actual]
    expected_descriptions = [norm_desc(row.get("description")) if isinstance(row, dict) else None for row in expected]
    actual_span, expected_span = max(1, len(actual) - 1), max(1, len(expected) - 1)
    candidates = []
    for actual_index, left in enumerate(actual_descriptions):
        for expected_index, right in enumerate(expected_descriptions):
            similarity = description_similarity(left, right)
            if similarity >= threshold:
                position = abs(actual_index / actual_span - expected_index / expected_span)
                candidates.append((similarity, position, actual_index, expected_index))
    candidates.sort(key=lambda item: (-item[0], item[1], item[2], item[3]))
    used_actual, used_expected, pairs = set(), set(), []
    for similarity, _position, actual_index, expected_index in candidates:
        if actual_index in used_actual or expected_index in used_expected:
            continue
        used_actual.add(actual_index)
        used_expected.add(expected_index)
        pairs.append((actual_index, expected_index, similarity))
    pairs.sort(key=lambda pair: pair[1])
    return (pairs,
            [index for index in range(len(actual)) if index not in used_actual],
            [index for index in range(len(expected)) if index not in used_expected])
