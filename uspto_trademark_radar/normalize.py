"""Filer-name normalization.

Attorney names in the raw XML are free text and appear in many variant forms
for the same person: case differences, punctuation, honorific suffixes,
middle name vs. middle initial, and surname-first ordering (common for
Chinese names, e.g. "GU, WEI" vs "Wei Gu"). Without consolidation a single
operator's portfolio fragments across rows, undercounting volume and hiding
cross-jurisdiction spread.

`canonical_attorney` collapses a name to a first+last key that is invariant
to those variations:

    "Dana K. Rivers", "DANA K. RIVERS", "Dana Rivers",
    "Dana K. Rivers, Esq."       -> "DANA RIVERS"
    "Alex T. Warren", "Alex Thomas Warren"  -> "ALEX WARREN"
    "KANG, MIN", "Min Kang"      -> "KANG MIN"    (surname-first ordering)

(All names in this documentation and in the tests are fictional placeholders.)

KNOWN LIMITATION (string normalization has a precision ceiling). Dropping
middle tokens is REQUIRED to merge initial-vs-full-name variants of ONE
person ("Dana K. Rivers" == "Dana Rivers"), but it necessarily over-merges
DIFFERENT people who share a first and last name ("Chris T. Baker" vs
"Chris N. Baker"). These two goals are mutually exclusive for string matching
alone — the middle initial is simultaneously noise (Rivers) and signal
(Baker). Robustly separating them needs multi-signal entity resolution
(corroborate with correspondent address, client set, docket-number prefix);
see the gold-layer roadmap. Until then this key is a candidate-grouping aid,
NOT a person identifier: always keep it auditable back to its raw variants,
and never attach a conclusion to a canonical key without inspecting the raw
names (and their addresses) behind it.

This module also guards the two unambiguous failure modes: non-name noise in
the attorney field (docket codes like "QO2281") and multi-attorney fields
("A and B"), which would otherwise produce garbage or chimeric keys.
"""

from __future__ import annotations

import re

# Honorifics / firm-type tokens that are not part of a person's name.
_SUFFIXES = {
    "ESQ", "ESQUIRE", "JR", "SR", "II", "III", "IV", "V",
    "PC", "PLLC", "LLP", "LLC", "LP", "PA", "APC", "LTD",
    "ATTORNEY", "ATTORNEYS", "ATTY", "LAW", "ESQU",
}
# Splits a field naming multiple attorneys; we keep only the first name.
_MULTI = re.compile(r"\bAND\b|&|;|/| , ")
_STRIP = re.compile(r"[^A-Z\s'-]")
_VOWEL = re.compile(r"[AEIOU]")


def canonical_attorney(name: str | None) -> str | None:
    """Return a first+last canonical key, or None if the field does not
    contain a usable single personal name.

    Order-invariant (sorts the two anchor tokens) so surname-first and
    surname-last spellings map together. Returns None for empty input,
    docket-code noise, and single-token names (not reliably resolvable).
    """
    if not name:
        return None
    first_segment = _MULTI.split(name.upper(), maxsplit=1)[0]
    s = _STRIP.sub(" ", first_segment)         # drop digits/punctuation
    toks = [t for t in s.split() if t and t not in _SUFFIXES]
    # Need a real first AND last name; drop codes ("QO2281" -> "QO") and
    # bare single-token names, which cannot be normalized with confidence.
    toks = [t for t in toks if len(t) >= 2 and _VOWEL.search(t)] or toks
    if len(toks) < 2:
        return None
    first, last = toks[0], toks[-1]            # ignore middle names/initials
    return " ".join(sorted((first, last)))
