"""Filename semantics for the two source products (docs/DATA_SOURCES.md).

- TRTYRAP (annual backfile): one snapshot per completed year, split into many
  part zips named ``apc<START>-<END>-<PART>.zip`` with 8-digit dates, e.g.
  ``apc18840407-20241231-03.zip``. The END date is the snapshot's **coverage
  cutoff** (Dec 31 of the covered year) — normative for daily replay; do NOT
  confuse it with the file's publication date.
- TRTDXFAP (dailies): ``apcyymmdd.zip``; the 6-digit date is the transaction
  date (the day whose activity the file carries).

Both parsers return None for names they don't recognize — preflight treats a
manifest full of unparseable names as platform drift and stops.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

_ANNUAL_RE = re.compile(
    r"^apc(\d{8})-(\d{8})-(\d+)\.zip$", re.IGNORECASE
)
_DAILY_RE = re.compile(r"^apc(\d{6})\.zip$", re.IGNORECASE)


def _yyyymmdd(s: str) -> date | None:
    try:
        return date(int(s[:4]), int(s[4:6]), int(s[6:8]))
    except ValueError:
        return None


@dataclass(frozen=True)
class AnnualFile:
    name: str
    start: date
    cutoff: date  # coverage cutoff — drives daily replay boundary
    part: int


@dataclass(frozen=True)
class DailyFile:
    name: str
    transaction_dt: date


def parse_annual(name: str) -> AnnualFile | None:
    m = _ANNUAL_RE.match(name)
    if not m:
        return None
    start, cutoff = _yyyymmdd(m.group(1)), _yyyymmdd(m.group(2))
    if not start or not cutoff:
        return None
    return AnnualFile(name=name, start=start, cutoff=cutoff,
                      part=int(m.group(3)))


def parse_daily(name: str) -> DailyFile | None:
    m = _DAILY_RE.match(name)
    if not m:
        return None
    yy, mm, dd = m.group(1)[:2], m.group(1)[2:4], m.group(1)[4:6]
    # Dailies only exist for the ODP era; a two-digit year is unambiguous
    # this side of 2050.
    year = 2000 + int(yy) if int(yy) < 50 else 1900 + int(yy)
    try:
        dt = date(year, int(mm), int(dd))
    except ValueError:
        return None
    return DailyFile(name=name, transaction_dt=dt)


def file_dt_source(name: str) -> date | None:
    """Transaction date of a source zip, for latest-wins ordering: the
    coverage cutoff for annual parts, the transaction date for dailies.
    """
    a = parse_annual(name)
    if a:
        return a.cutoff
    d = parse_daily(name)
    if d:
        return d.transaction_dt
    return None


def latest_annual_set(names: list[str]) -> tuple[date, list[AnnualFile]] | None:
    """From a TRTYRAP manifest, pick the newest snapshot: the group of part
    files sharing the maximum coverage cutoff (ADR 0006 — only the latest
    annual, even after a multi-year gap).
    """
    parsed = [a for a in (parse_annual(n) for n in names) if a]
    if not parsed:
        return None
    cutoff = max(a.cutoff for a in parsed)
    parts = sorted((a for a in parsed if a.cutoff == cutoff),
                   key=lambda a: a.part)
    return cutoff, parts
