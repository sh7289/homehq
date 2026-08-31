from difflib import SequenceMatcher

DEFAULT_CUTOFF = 0.72


def _normalize(name):
    return " ".join(name.lower().split())


def find_best_match(name, candidates, cutoff=DEFAULT_CUTOFF):
    """Return the candidate dict whose 'name' best fuzzy-matches `name`.

    Tolerates case, whitespace, and minor typos. Returns None if nothing
    scores at or above `cutoff` (ratio 0..1).
    """
    target = _normalize(name)
    best_candidate = None
    best_ratio = 0.0

    for candidate in candidates:
        ratio = SequenceMatcher(None, target, _normalize(candidate["name"])).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_candidate = candidate

    if best_candidate is not None and best_ratio >= cutoff:
        return best_candidate
    return None
