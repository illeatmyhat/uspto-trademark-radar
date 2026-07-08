"""Character n-gram coined-mark scorer.

Models letter-sequence probability in trademark wordmarks as a character-level
n-gram distribution fit on a reference set, then scores any mark by how
improbable its letters are under that model. Random-looking strings (QVOWII,
BRCKD) score high; pronounceable English-like marks (EASY WEDDING, TITAN)
score low. Self-contained (no external wordlist); measures a property of the
mark string. Fitting on a pre-2015 baseline gives a stable reference for
ordinary trademark wording.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import duckdb

_NON_ALPHA = re.compile(r"[^a-z]")

FIT_SAMPLE = 300_000             # pre-surge wordmarks to fit the scorer on


class CoinedScorer:
    def __init__(self, order: int = 3):
        self.order = order
        self._ngram: Counter = Counter()   # (context, next_char) -> count
        self._context: Counter = Counter()  # context -> count
        self._vocab: set[str] = set()
        self.lo: float | None = None        # calibration: avg-logprob bounds
        self.hi: float | None = None

    @staticmethod
    def _prep(s: str | None) -> str:
        return _NON_ALPHA.sub("", (s or "").lower())

    def _avg_logprob(self, word: str | None) -> float | None:
        """Mean add-1-smoothed log P(char | context) over the padded string.
        Higher (closer to 0) = more ordinary; more negative = more coined."""
        w = self._prep(word)
        if len(w) < 2:
            return None
        v = len(self._vocab) + 1
        padded = "^" * (self.order - 1) + w + "$"
        total, n = 0.0, 0
        for i in range(len(padded) - self.order + 1):
            ctx = padded[i:i + self.order - 1]
            nxt = padded[i + self.order - 1]
            prob = (self._ngram[(ctx, nxt)] + 1) / (self._context[ctx] + v)
            total += math.log(prob)
            n += 1
        return total / n if n else None

    def fit(self, words) -> CoinedScorer:
        words = list(words)
        for word in words:
            w = self._prep(word)
            if len(w) < 2:
                continue
            self._vocab.update(w)
            padded = "^" * (self.order - 1) + w + "$"
            for i in range(len(padded) - self.order + 1):
                ctx = padded[i:i + self.order - 1]
                nxt = padded[i + self.order - 1]
                self._ngram[(ctx, nxt)] += 1
                self._context[ctx] += 1
        # Calibrate the score range on the fitting distribution so the output
        # is a stable [0,1] relative to ordinary marks.
        lps = sorted(lp for word in words
                     if (lp := self._avg_logprob(word)) is not None)
        if lps:
            self.lo = lps[int(0.02 * (len(lps) - 1))]   # ~most coined
            self.hi = lps[int(0.98 * (len(lps) - 1))]   # ~most ordinary
        return self

    def score(self, word: str | None) -> float | None:
        """Coinedness in [0,1]: 1.0 = maximally improbable/coined, 0.0 =
        ordinary. None for un-scoreable input (empty / single letter / unfit).
        """
        lp = self._avg_logprob(word)
        if lp is None or self.lo is None or self.hi is None or self.hi == self.lo:
            return None
        s = (self.hi - lp) / (self.hi - self.lo)   # low logprob -> high score
        return round(max(0.0, min(1.0, s)), 4)

    def score_token_max(self, mark: str | None) -> float | None:
        """Highest coinedness among the mark's whitespace tokens.

        A whole-mark score is diluted by padding a coined token with an
        ordinary word ('ZORVEX GEAR'); the per-token max stays high because
        the coined token still scores high on its own. None when no token is
        scoreable.
        """
        if not mark:
            return None
        scores = [s for tok in mark.split()
                  if (s := self.score(tok)) is not None]
        return max(scores) if scores else None


def fit_scorer(con: duckdb.DuckDBPyConnection, cf_glob: str,
               sample_rows: int = FIT_SAMPLE) -> CoinedScorer:
    """Fit on a sample of PRE-2015 wordmarks — an ordinary-trademark-English
    baseline that predates the studied surge. Falls back to all wordmarks
    when the pre-2015 baseline is too thin (tiny corpora / tests).

    The sample is the `sample_rows` marks with the smallest `hash(serial_no)`
    — a deterministic pseudo-random subset. DuckDB's reservoir `USING SAMPLE`
    is not reproducible under multithreaded scans (observed: the fit's
    calibration bounds drifted between identical rebuilds), which would make
    the published coined score itself non-reproducible; hash-ordering fixes
    both the membership and the fit. (Stable within a DuckDB major version;
    the hash function is the only cross-version caveat.)
    """
    def sample(where: str):
        return con.execute(
            f"SELECT mark_id_char FROM read_parquet('{cf_glob}') "
            f"WHERE mark_id_char IS NOT NULL {where} "
            f"ORDER BY hash(serial_no), serial_no LIMIT {sample_rows}"
        ).fetchall()

    rows = sample("AND filing_dt < DATE '2015-01-01'")
    if len(rows) < 500:                     # thin baseline -> use all marks
        rows = sample("")
    return CoinedScorer(order=3).fit(r[0] for r in rows)
