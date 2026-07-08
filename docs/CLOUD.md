# Running the monthly update in the cloud

The pipeline runs statelessly on any ephemeral runner: `run --from-mirror`
restores bronze from our own HF mirror at datacenter speed, pulls only new
files from USPTO, then parses, builds, reconciles, and publishes — with the
publish leg (the bottleneck on residential upstream) running at datacenter
bandwidth.

Two hosting options are set up:

## Option A (free): GitHub Actions — `.github/workflows/monthly-update.yml`

Runs on the private-repo free tier (one ~3-4 h run/month against a 2000
min/month allowance). Setup: add `USPTO_API_KEY` and `HF_TOKEN` as repo
secrets (Settings -> Secrets and variables -> Actions), then either wait for
the monthly cron or trigger via Actions -> monthly-update -> Run workflow.
For the very first run (HF mirror still empty), dispatch manually with
`from_mirror: false`. The workflow caches parsed Parquet between runs, so
routine monthlies only parse the new dailies.

## Option B (pay-per-use, faster): Hugging Face Jobs

No subscription needed — Jobs are available to any account with a positive
credit balance (top up under Settings -> Billing). Bills per minute of
hardware; a full from-scratch run on `cpu-upgrade` is roughly $0.10, a
routine monthly sync a few cents. Best network locality to the Hub.

## One-time setup

1. `pip install -U "huggingface_hub[cli]"` and `hf auth login` locally.
2. Job secrets (passed per-run or stored on the job): `USPTO_API_KEY`
   (data.uspto.gov key; see RUNBOOK §1) and `HF_TOKEN` (write access to the
   dataset repos). The code repo is public (2026-07-08), so no GitHub token
   is needed to install it.

## Manual cloud run

**Run on the container's local disk, not a mounted bucket.** A Storage Bucket
mounted at `/data` sounds appealing for crash-resume, but it is a FUSE +
network filesystem: the parse stage does millions of tiny reads/writes and
each pays network latency, so parsing to a bucket is unusably slow (a real
run managed **zero** completed parses in 3.5 h before timing out). Buckets are
durable *storage*, not a working *directory*. Use fast local disk and lean on
a big flavor + generous timeout so the whole run finishes in one pass —
resume is unnecessary when the run is fast, and the only cost of a rare
restart is re-downloading bronze from USPTO (~15 min at datacenter speed).

```bash
hf jobs run \
  --flavor cpu-xl \
  --timeout 8h \
  --env TRADEMARK_RADAR_DATA_ROOT=/data \
  --secrets USPTO_API_KEY --secrets HF_TOKEN \
  python:3.12 /bin/bash -c '\
    pip install -q "git+https://github.com/illeatmyhat/uspto-trademark-radar" && \
    trademark-radar run \
      --from-mirror illeatmyhat/uspto-trademarks-bronze \
      --repo illeatmyhat/uspto-trademarks \
      --bronze-repo illeatmyhat/uspto-trademarks-bronze'
```

With no `-v` mount, `/data` is the container's local disk (fast). The full
from-scratch annual rebuild runs in well under the 8 h budget on `cpu-xl`;
routine monthly runs (new dailies only, restored from the mirror) finish in
minutes.

Notes:
- The **very first** run must omit `--from-mirror` (the mirror is empty
  until that run's final step creates it).
- If a run does time out, just re-launch the same command — `--from-mirror`
  restores already-downloaded bronze from the mirror, so only parse/build
  repeat (fast on local disk). Monitor with `hf jobs logs <id>` and
  `hf jobs ps -a`.

## Monthly schedule

**ARMED 2026-07-09:** scheduled job `6a4fc234b90c99045740e3d2`, cron
`0 6 2 * *` (06:00 UTC on the 2nd — gives USPTO time to post month-end
dailies), flavor `cpu-xl`, timeout 8h, secrets `USPTO_API_KEY` / `HF_TOKEN`
stored on the job (repo is public, so no GitHub token). Manage with
`hf jobs scheduled ps | inspect | suspend | resume | delete <id>`.

The scheduled command chains the silver run with the derived-layer build so
all three detector tiers refresh monthly (note: the server rejects the CLI's
`monthly` alias — use an explicit cron):

```bash
hf jobs scheduled run "0 6 2 * *" \
  --flavor cpu-xl --timeout 8h \
  --env TRADEMARK_RADAR_DATA_ROOT=/data \
  --secrets USPTO_API_KEY=... --secrets HF_TOKEN=... \
  python:3.12 /bin/bash -c 'pip install -q "git+https://github.com/illeatmyhat/uspto-trademark-radar" \
    && trademark-radar run --from-mirror illeatmyhat/uspto-trademarks-bronze \
         --repo illeatmyhat/uspto-trademarks --bronze-repo illeatmyhat/uspto-trademarks-bronze \
    && trademark-radar gold \
    && trademark-radar detector-lists \
    && trademark-radar publish-detector --repo illeatmyhat/uspto-trademarks-detector'
```

**Windows gotcha:** creating this from Git Bash mangles the literal `/bin/bash`
argument into a `C:/Program Files/Git/...` path (MSYS path conversion), which
fails in the container. Prefix the command with `MSYS_NO_PATHCONV=1
MSYS2_ARG_CONV_EXCL="*"` and verify with `hf jobs scheduled inspect <id>` that
`command[0]` is literally `/bin/bash`.

## Sizing notes

- `cpu-xl` finishes a full annual rebuild comfortably; local disk (no bucket
  mount) is essential — see the warning above. Parse fans out across
  all cores automatically; the DuckDB build spills gracefully past RAM
  (spill directory is set by the pipeline).
- A normal monthly sync (new dailies only, restored from the mirror) finishes
  in minutes. A January re-baseline (new annual: full re-download + re-parse)
  is the long case but still well inside an 8 h budget on `cpu-xl`.
- Job disk is ephemeral local storage (fast). Each run restores bronze from
  the mirror, so nothing needs to survive between runs; the ledger/lineage is
  rebuilt per run. A rare timeout costs only a re-run (bronze restores from
  the mirror; parse/build repeat on fast local disk).

## What the run publishes

- `illeatmyhat/uspto-trademarks` — silver Parquet + profile/ + dataset card
  (one commit per publish; roll back by reverting the commit).
- `illeatmyhat/uspto-trademarks-bronze` — appends any newly downloaded
  USPTO files to the mirror. Nothing is ever deleted from the mirror.
- `illeatmyhat/uspto-trademarks-detector` — the tier1/tier2/tier3 detector
  lists. The scheduled command chains `run` with `gold` (which builds
  `mark_features` and the local operation grouping), `detector-lists`, and
  `publish-detector`, so all three tiers refresh each month (all core deps —
  the analysis group is not installed in the job).

Still separate: `publish-features` (the `mark_features` HF repo) is a manual
opt-in, and the operation grouping / `peculiarity_score` stay local and are
never published (ADR 0008); only the per-filing detector mark keys leave.
