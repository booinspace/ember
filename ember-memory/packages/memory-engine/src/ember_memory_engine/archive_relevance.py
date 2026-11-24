"""Opt-in archive result gating; never treat rank or similarity as probability."""

from .word_recall import words

OPTION = "ember.archive_relevance"
PROFILE = "corroborated_v1"
_FILLER = frozenset(
    "a an and are about am as at be been but can could did do does for from had has have how "
    "i if in into is it its me my of on or our please should so that the their them there these "
    "they this through to up us use using was we were what when where which who why will with "
    "would you your s t m re ve ll d".split()
)


def topical_words(text: str) -> frozenset[str]:
    """Choose bounded literal query terms without interpreting source contents."""
    return frozenset(tuple(dict.fromkeys(term for term in words(text) if term not in _FILLER))[:64])


def corroborated(terms: frozenset[str], text: str, similarity: float | None, best: float) -> bool:
    """Keep topical agreement, strong semantics, or several distinct literal matches."""
    if not terms:
        return False
    # ponytail: fixed margins for this preview; qualify on held-out archives before rollout.
    gap = best - similarity if similarity is not None else float("inf")
    if similarity is not None and gap <= 0.025 and similarity >= 0.64:
        return True
    matches = len(terms.intersection(words(text)))
    return (gap <= 0.08 and matches > 0) or matches >= 3 or (matches >= 2 and len(terms) <= 3)
