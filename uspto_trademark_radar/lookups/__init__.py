"""Packaged USPTO code lookup tables (silver-schema §6).

Each CSV pairs a code as it appears in the bulk XML with USPTO's published
description (`code,description` at minimum; some tables carry extra columns).
`_sources.json` records, per table, the authoritative USPTO document it was
retrieved from and the retrieval date. The publish step ships this directory
under `lookups/` in the dataset repo so consumers can decode status, event
and statement codes without leaving the dataset.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

_DIR = Path(__file__).parent

LOOKUP_FILES = (
    "status_codes.csv",
    "event_codes.csv",
    "statement_types.csv",
    "party_types.csv",
    "entity_codes.csv",
    "drawing_codes.csv",
)


def lookup_dir() -> Path:
    return _DIR


def load(name: str) -> dict[str, str]:
    """code -> description for one of LOOKUP_FILES. Note event_codes.csv is
    keyed on (code, event_type_cd) pairs; here later rows win, which is fine
    for display but joins should use both columns."""
    if name not in LOOKUP_FILES:
        raise ValueError(f"unknown lookup table {name!r}; expected one of {LOOKUP_FILES}")
    with open(_DIR / name, encoding="utf-8", newline="") as f:
        return {row["code"]: row["description"] for row in csv.DictReader(f)}


def sources() -> dict:
    """Per-table retrieval provenance: source document URL + retrieval date."""
    return json.loads((_DIR / "_sources.json").read_text(encoding="utf-8"))
