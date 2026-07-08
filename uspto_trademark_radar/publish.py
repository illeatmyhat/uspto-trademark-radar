"""Publish the live build to a Hugging Face dataset repo (ADR 0004).

upload_folder produces one commit per publish, so versioning and rollback
(revert the commit — RUNBOOK §4) come free. Only data/, profile/ and the
generated README.md dataset card are uploaded.
"""

from __future__ import annotations

import csv
import json
import os
import shutil
from datetime import datetime, UTC
from pathlib import Path

from huggingface_hub import HfApi

from .build import read_meta
from .lookups import LOOKUP_FILES, lookup_dir
from .schema import TABLES


def _viewer_configs() -> str:
    """YAML `configs:` block so the HF dataset viewer treats each silver
    table (and each lookup CSV) as its own selectable config. Without it the
    viewer tries to infer one schema across all tables and fails on this
    multi-table layout. case_file is listed first, so it is the default view.
    """
    lines = ["configs:"]
    for tbl in TABLES:
        lines += [f"  - config_name: {tbl}",
                  f"    data_files: data/{tbl}/*.parquet"]
    for name in LOOKUP_FILES:
        lines += [f"  - config_name: lookup_{name.removesuffix('.csv')}",
                  f"    data_files: lookups/{name}"]
    return "\n".join(lines)


_CARD = """\
---
license: other
license_name: us-government-work
license_link: https://www.usa.gov/government-works
pretty_name: USPTO Trademark Case Files (Silver)
tags:
  - trademarks
  - uspto
  - legal
{configs}
---

# USPTO Trademark Case Files — analysis-ready Parquet

Complete USPTO trademark application/registration corpus, rebuilt from the
official bulk XML products and normalized into relational Parquet tables.

- **Sources:** USPTO Open Data Portal products **TRTYRAP** (Trademark Full
  Text XML Data, No Images — Annual Applications; backfile from 1884) and
  **TRTDXFAP** (same, Daily Applications), US Trademark Applications v2.0 DTD.
- **Data current through:** `{data_current_through}` (annual snapshot cutoff
  `{annual_cutoff}` + daily replay).
- **License:** United States government work — public domain.
- **Schema lineage:** table/column names follow the USPTO Office of the Chief
  Economist (OCE) Trademark Case Files Dataset conventions, so existing
  research code ports directly. `correspondent` is an extension beyond OCE.

## Querying (DuckDB, no download needed)

```sql
SELECT filing_dt, mark_id_char
FROM 'hf://datasets/{repo_id}/data/case_file/*.parquet'
WHERE serial_no = '75930757';
```

## Tables

| table | rows | grain |
|---|---:|---|
{table_rows}

`serial_no` (8-char zero-padded string) joins everything. Row counts, per-column
null rates and build lineage are published under `profile/`. USPTO's code
tables (case status, prosecution-history event, statement type, party,
legal entity, mark drawing) ship under `lookups/`, with per-table source
URL and retrieval date in `lookups/_sources.json`.

## Research & reproducibility

This corpus was assembled to study, quantitatively, the well-documented
post-2015 surge in coined trademark filings tied to online-marketplace
brand-gating (e.g. Amazon Brand Registry) — the phenomenon that motivated the
Trademark Modernization Act of 2020. Dimensions of interest, all computable
directly from the tables above: filing volume by year, owner geography, and
marketplace-gated class (9/11/18/20/21/25/28); coined single-token marks;
attorney-of-record filing concentration; the marks-per-owner ratio that
distinguishes ongoing law practices from high-churn filing operations;
goods/services identification length; and correspondent-address-level
aggregation of filing operations.

A complete, runnable methodology — DuckDB queries plus a versioned scoring
module that composes these signals into a ranked table — is in the open-source
pipeline: **[docs/ANALYSIS.md]({repo_url}/blob/main/docs/ANALYSIS.md)**. Every
figure is a factual measurement of public records and reproducible in minutes.
Results are descriptive patterns, not determinations about any filer; see the
document's "Reading the results" note.

## Update semantics & limitations

- **Latest state only.** Each case file appears once, in its most recent
  known state (annual baseline + daily replay, latest-wins). Superseded field
  values are not retained; consumers wanting version archaeology should
  rebuild from USPTO dailies themselves.
- Owner names are **not normalized**; address country codes contain invalid
  values — documented, not cleaned.
- Pre-1980s records are sparse/backfilled by USPTO.
- Every row carries `file_dt_source`/`file_name_source` lineage back to the
  exact USPTO bulk file that produced it.

Built {built_at} by the [USPTO Trademark Radar pipeline]({repo_url}).
"""


_BRONZE_CARD = """\
---
license: other
license_name: us-government-work
license_link: https://www.usa.gov/government-works
pretty_name: USPTO Trademark Bulk XML (Bronze Mirror)
tags:
  - trademarks
  - uspto
  - legal
viewer: false
---

# USPTO Trademark Bulk XML — raw source mirror

Verbatim mirror of the USPTO Open Data Portal bulk products **TRTYRAP**
(Trademark Full Text XML Data, No Images — Annual Applications) and
**TRTDXFAP** (Daily Applications): zips of XML conforming to the US
Trademark Applications v2.0 DTD, exactly as served by USPTO.

Why this mirror exists: pulling these files from USPTO requires a personal,
ID.me-verified API key, and the portal does not retain superseded files.
This repo is **append-only** — annual snapshot editions and daily files
accumulate here even after USPTO rotates them out, so any historical build
of the derived dataset can be reproduced.

- `annual/` — complete backfile snapshots, one edition per completed year
  (`apc<START>-<END>-<PART>.zip`; the END date is the coverage cutoff).
- `daily/` — daily update files (`apcyymmdd.zip`), each carrying the
  complete current record of every case with activity that day.

The analysis-ready relational Parquet derived from these files lives in the
companion silver dataset. License: United States government work — public
domain.
"""


def write_bronze_card(bronze_root: Path) -> Path:
    path = bronze_root / "README.md"
    path.write_text(_BRONZE_CARD, encoding="utf-8")
    return path


def publish_bronze(bronze_root: Path, repo_id: str, log=print) -> None:
    """Mirror bronze/ (annual/ + daily/ zips) to a HF dataset repo.
    Append-only by design: nothing is ever deleted from the mirror, so it
    outlives both local pruning and USPTO's own file rotation.
    """
    token = os.environ.get("HF_TOKEN")
    if not token:
        raise SystemExit("HF_TOKEN is not set — required for publish.")
    write_bronze_card(bronze_root)
    api = HfApi(token=token)
    api.create_repo(repo_id, repo_type="dataset", exist_ok=True)
    log(f"[publish] mirroring {bronze_root} -> hf://datasets/{repo_id} "
        "(resumable; re-run on interruption)")
    api.upload_large_folder(
        repo_id=repo_id,
        folder_path=str(bronze_root),
        repo_type="dataset",
        allow_patterns=["annual/*.zip", "daily/*.zip", "README.md"],
    )
    log("[publish] bronze mirror up to date")


_FEATURES_CARD = """\
---
license: other
license_name: us-government-work
license_link: https://www.usa.gov/government-works
pretty_name: USPTO Trademark Mark Features (Derived)
tags:
  - trademarks
  - uspto
  - feature-store
configs:
  - config_name: mark_features
    data_files: "*.parquet"
---

# USPTO Trademark Mark Features (derived)

Objective per-mark feature columns derived from the companion silver corpus,
one row per application (`serial_no`, joins to
`illeatmyhat/uspto-trademarks`). Each column is a **measurement of the
filing**, not a judgement about any filer.

| column | meaning |
|---|---|
| `coined_mark_score` | character n-gram improbability of the whole mark, [0,1] (1 = most coined/random-looking) |
| `coined_token_max` | max n-gram improbability over the mark's tokens, [0,1] (catches a coined token padded with an ordinary word) |
| `single_token` | mark is a single word |
| `is_standard_char` | standard-character drawing |
| `is_marketplace_class` | filed in an Amazon-heavy class (9/11/18/20/21/25/28) |
| `filing_basis` | use / intent / foreign / madrid |
| `final_refusal_in` | a final refusal issued (public prosecution-history fact) |
| `abandoned_no_use_in` | abandoned for a missing/defective use statement |
| `owner_country` | applicant country code (public record; contains dirt) |
| `goods_item_count` | number of goods/services items listed |
| `filing_year` | filing year |

## Use

```sql
-- e.g. surface coined, marketplace-class, use-based recent marks
SELECT serial_no, mark_id_char, coined_mark_score
FROM 'hf://datasets/{repo_id}/*.parquet'
WHERE coined_mark_score > 0.85 AND is_marketplace_class
  AND filing_basis = 'use' AND filing_year >= 2015
ORDER BY coined_mark_score DESC;
```

`coined_mark_score` measures the statistical unusualness of a mark's letter
sequence; the other columns are direct properties of the filing. Methodology:
{repo_url}/blob/main/docs/ANALYSIS.md. License: US government work (public
domain).
"""


def write_features_card(features_dir: Path, repo_id: str,
                        repo_url: str = "https://github.com/illeatmyhat/uspto-trademark-radar") -> Path:
    path = features_dir / "README.md"
    path.write_text(_FEATURES_CARD.format(repo_id=repo_id, repo_url=repo_url),
                    encoding="utf-8")
    return path


def publish_features(features_dir: Path, repo_id: str, log=print) -> str:
    """Publish the derived mark-features table (objective per-filing columns).
    Deliberate opt-in — separate from the silver publish."""
    token = os.environ.get("HF_TOKEN")
    if not token:
        raise SystemExit("HF_TOKEN is not set — required for publish.")
    write_features_card(features_dir, repo_id)
    api = HfApi(token=token)
    api.create_repo(repo_id, repo_type="dataset", exist_ok=True)
    log(f"[publish] uploading {features_dir.name} -> hf://datasets/{repo_id}")
    info = api.upload_folder(
        folder_path=str(features_dir), repo_id=repo_id, repo_type="dataset",
        allow_patterns=["*.parquet", "README.md"],
        commit_message="Publish mark_features",
    )
    log("[publish] features done")
    return str(info)


_DETECTOR_CARD = """\
---
license: other
license_name: us-government-work
license_link: https://www.usa.gov/government-works
pretty_name: USPTO Trademark Detector Lists
tags:
  - trademarks
  - uspto
  - brand-protection
configs:
  - config_name: tier1_adjudicated
    data_files: tier1.txt
    default: true
  - config_name: tier2_screened
    data_files: tier2.txt
  - config_name: tier3_operation
    data_files: tier3.txt{origin_configs}
---

# USPTO Trademark Detector Lists

Ready-to-ship lists for a downstream detector (e.g. a browser extension),
consumed exactly like an ad-blocker filter list: **fetch, cache, membership-
test.** Each file is plain text, one normalized mark key per line. Rebuilt
monthly from the companion corpus `illeatmyhat/uspto-trademarks`.

## Source

Generated by **[USPTO Trademark Radar]({repo_url})** - an open-source pipeline
that builds these lists from the public USPTO corpus. Method, thresholds, ADRs,
and the consumer contract live there.

[![{gh_slug}](https://opengraph.githubassets.com/1/{gh_slug})]({repo_url})

| file | what it is | how to treat a hit |
|---|---|---|
| `tier1.txt` | marks on a filing the **USPTO itself sanctioned** (order-for-sanctions events / sanctions-terminated status) | a public-record flag; link the user to the USPTO record |
| `tier2.txt` | marks whose per-filing features clear a recommended screening threshold | a hint to inspect, **not** a verdict |
| `tier3.txt` | marks filed by a **large, high-churn filing operation** (a structural fact about filing volume, keyed on the filing) | a stronger prior to inspect, still **not** a verdict; no person is named |{origin_rows}

Current build: **tier1 = {n1} marks**, **tier2 = {n2} marks** (threshold
{threshold}), **tier3 = {n3} marks**. See `_manifest.json` for build lineage.
{origin_note}

## Key normalization (match this exactly)

Both lists are keyed on a normalized mark string. Normalize the brand text you
read off a page the same way before testing membership ({key_rule}):

```js
const key = (s) => s.replace(/[^A-Za-z0-9]/g, "").toUpperCase();
```

## Consume

```js
const res  = await fetch("https://huggingface.co/datasets/{repo_id}/resolve/main/tier1.txt");
const tier1 = new Set((await res.text()).split("\\n").filter(Boolean));
// cache in IndexedDB; refresh monthly. At runtime:
if (tier1.has(key(listingBrandText))) flagAsAdjudicated(key(listingBrandText));
```

A mark string maps to many filings, so a hit means *"one or more filings under
this mark were flagged,"* not "this exact listing." Surface the USPTO record
and let the user judge; never blacklist a person. Design contract and rationale:
{repo_url}/blob/main/CONSUMERS.md. License: US government work (public domain).
"""


def write_detector_card(detector_dir: Path, repo_id: str,
                        repo_url: str = "https://github.com/illeatmyhat/uspto-trademark-radar") -> Path:
    m = json.loads((detector_dir / "_manifest.json").read_text())
    path = detector_dir / "README.md"
    n3 = f"{m['tier3']['n_marks']:,}" if "tier3" in m else "n/a"
    # owner/repo slug for the GitHub social-card image + link back to the repo.
    gh_slug = repo_url.rstrip("/").split("github.com/")[-1]

    # Origin lists (opt-in, not a junk signal): add a viewer config + a table row
    # per country, and an explanatory section, all driven off the manifest so the
    # card stays in sync with what actually shipped.
    origins = (m.get("origins") or {}).get("lists") or {}
    origin_configs = "".join(
        f"\n  - config_name: origin_{cc.lower()}\n    data_files: {info['file']}"
        for cc, info in origins.items())
    origin_rows = "".join(
        f"\n| `{info['file']}` | **{info['label']}** - marks whose registered owner "
        f"country of record is {cc} | an **origin filter**, not a junk signal; "
        f"opt-in, off by default |"
        for cc, info in origins.items())
    origin_note = ""
    if origins:
        origin_note = (
            "\n## Origin filters (opt-in, not a junk signal)\n\n"
            "`origin_<cc>.txt` lists marks by **registered owner country of record** - "
            "filing origin, not manufacture (a genuinely foreign brand that files "
            "through a domestic entity reads as domestic). Owner-country is deliberately "
            "**excluded from the tier gates** (it is evadable via a foreign shell); it "
            "ships only as a separate, clearly-labeled origin preference, **off by "
            "default**, never a junk verdict.\n")

    path.write_text(_DETECTOR_CARD.format(
        repo_id=repo_id, repo_url=repo_url,
        n1=f"{m['tier1']['n_marks']:,}", n2=f"{m['tier2']['n_marks']:,}",
        n3=n3, threshold=m["tier2"]["threshold"], key_rule=m["key_rule"],
        gh_slug=gh_slug, origin_configs=origin_configs, origin_rows=origin_rows,
        origin_note=origin_note),
        encoding="utf-8")
    return path


def publish_detector(detector_dir: Path, repo_id: str, log=print) -> str:
    """Publish the downstream-detector lists (tier1/tier2 txt + manifest).
    Deliberate opt-in — separate from the silver and features publishes."""
    token = os.environ.get("HF_TOKEN")
    if not token:
        raise SystemExit("HF_TOKEN is not set — required for publish.")
    write_detector_card(detector_dir, repo_id)
    api = HfApi(token=token)
    api.create_repo(repo_id, repo_type="dataset", exist_ok=True)
    log(f"[publish] uploading {detector_dir.name} -> hf://datasets/{repo_id}")
    info = api.upload_folder(
        folder_path=str(detector_dir), repo_id=repo_id, repo_type="dataset",
        allow_patterns=["*.txt", "*.json", "README.md"],
        commit_message="Publish detector lists",
    )
    log("[publish] detector done")
    return str(info)


def write_card(build_dir: Path, repo_id: str,
               repo_url: str = "https://github.com/illeatmyhat/uspto-trademark-radar") -> Path:
    meta = read_meta(build_dir)
    rows = []
    counts_csv = build_dir / "profile" / "row_counts.csv"
    with open(counts_csv, encoding="utf-8") as f:
        for rec in csv.DictReader(f):
            rows.append(f"| {rec['table']} | {int(rec['rows']):,} | "
                        f"{'1 per case file' if rec['table'] in ('case_file', 'correspondent') else '1:M per serial_no'} |")
    card = _CARD.format(
        configs=_viewer_configs(),
        data_current_through=meta["data_current_through"],
        annual_cutoff=meta["annual_cutoff"],
        repo_id=repo_id,
        table_rows="\n".join(rows),
        built_at=datetime.now(UTC).strftime("%Y-%m-%d"),
        repo_url=repo_url,
    )
    path = build_dir / "README.md"
    path.write_text(card, encoding="utf-8")
    return path


def stage_lookups(build_dir: Path) -> Path:
    """Copy the packaged code lookup CSVs (+ provenance) into the build dir
    so they upload alongside the data."""
    dest = build_dir / "lookups"
    dest.mkdir(exist_ok=True)
    for name in (*LOOKUP_FILES, "_sources.json"):
        shutil.copyfile(lookup_dir() / name, dest / name)
    return dest


def publish(build_dir: Path, repo_id: str, log=print) -> str:
    token = os.environ.get("HF_TOKEN")
    if not token:
        raise SystemExit("HF_TOKEN is not set — required for publish.")
    if not (build_dir / "profile" / "build_lineage.json").exists():
        raise SystemExit(
            "refusing to publish a build that never passed reconciliation"
        )
    write_card(build_dir, repo_id)
    stage_lookups(build_dir)
    api = HfApi(token=token)
    api.create_repo(repo_id, repo_type="dataset", exist_ok=True)
    meta = read_meta(build_dir)
    log(f"[publish] uploading {build_dir.name} -> hf://datasets/{repo_id}")
    info = api.upload_folder(
        folder_path=str(build_dir),
        repo_id=repo_id,
        repo_type="dataset",
        allow_patterns=["data/**", "profile/**", "lookups/**", "README.md"],
        commit_message=(
            f"{meta['build_id']}: data current through "
            f"{meta['data_current_through']}"
        ),
    )
    log(f"[publish] done: {info.commit_url if hasattr(info, 'commit_url') else info}")
    return str(info)
