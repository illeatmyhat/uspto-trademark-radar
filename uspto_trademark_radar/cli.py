"""trademark-radar CLI. `run` executes the full update job in the normative
order in docs/ARCHITECTURE.md; every other command is one stage, for triage.
All stages are idempotent - recovery from any failure is re-running the
command. (Keep CLI-visible strings ASCII: Windows consoles still default to
cp1252 and rich dies on unencodable characters.)
"""

from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import NoReturn

import typer
from dotenv import load_dotenv

from . import build as build_mod
from . import download as download_mod
from . import explore as explore_mod
from . import parse_stage, publish as publish_mod, release, restore as restore_mod
from .gold import detector_lists as gold_detector
from .gold import mark_features as gold_mark_features
from .gold import operation_profile as gold_operation
from .config import PRODUCT_ANNUAL, PRODUCT_DAILY, Config, default_workers
from .ledger import Ledger
from .odp import OdpClient, PlatformDriftError, TransientError, preflight as odp_preflight
from .reconcile import ReconcileError, reconcile as run_reconcile

app = typer.Typer(no_args_is_help=True, add_completion=False,
                  help=__doc__)


def _ctx() -> tuple[Config, Ledger]:
    cfg = Config()
    cfg.ensure_dirs()
    return cfg, Ledger(cfg.ledger_path)


def _client(cfg: Config) -> OdpClient:
    return OdpClient(cfg.api_key())


def _fail(e: Exception) -> NoReturn:
    kind = ("PLATFORM DRIFT - see RUNBOOK section 2"
            if isinstance(e, PlatformDriftError)
            else "transient — retry later" if isinstance(e, TransientError)
            else type(e).__name__)
    typer.secho(f"[{kind}] {e}", fg=typer.colors.RED, err=True)
    raise typer.Exit(1)


@app.command()
def preflight() -> None:
    """Check ODP reachability, product presence, filename conventions."""
    cfg, _ = _ctx()
    try:
        for line in odp_preflight(_client(cfg), PRODUCT_ANNUAL, PRODUCT_DAILY):
            typer.echo(line)
    except (PlatformDriftError, TransientError) as e:
        _fail(e)


@app.command()
def plan() -> None:
    """Show what a run would download/integrate, without downloading."""
    cfg, ledger = _ctx()
    try:
        p = download_mod.plan(cfg, ledger, _client(cfg))
    except (PlatformDriftError, TransientError) as e:
        _fail(e)
    typer.echo(p.summary())


# Worker entry points must be module-level picklables: on Windows,
# ProcessPoolExecutor spawns fresh interpreters. Each worker opens its own
# ledger connection; the ledger's atomic claiming does the coordination.

def _download_worker(_i: int) -> tuple[int, int]:
    cfg = Config()
    ledger = Ledger(cfg.ledger_path)
    try:
        return download_mod.work(cfg, ledger, OdpClient(cfg.api_key()))
    finally:
        ledger.close()


def _parse_worker(_i: int) -> tuple[int, int]:
    cfg = Config()
    ledger = Ledger(cfg.ledger_path)
    try:
        return parse_stage.work(cfg, ledger)
    finally:
        ledger.close()


def _fan_out(worker, n: int) -> tuple[int, int]:
    with ProcessPoolExecutor(max_workers=n) as ex:
        results = list(ex.map(worker, range(n)))
    return sum(r[0] for r in results), sum(r[1] for r in results)


_WORKERS_HELP = ("parallel worker processes (each claims jobs from the "
                 "ledger); default auto-sizes to the machine, 1 disables")


@app.command()
def download(workers: int | None = typer.Option(None, help=_WORKERS_HELP)) -> None:
    """Work the download queue (plan + claim loop). Safe to run in parallel."""
    cfg, ledger = _ctx()
    client = _client(cfg)
    try:
        download_mod.plan(cfg, ledger, client)
    except (PlatformDriftError, TransientError) as e:
        _fail(e)
    workers = workers or default_workers("download")
    if workers > 1:
        typer.echo(f"downloading with {workers} workers")
        done, failed = _fan_out(_download_worker, workers)
    else:
        done, failed = download_mod.work(cfg, ledger, client, log=typer.echo)
    typer.echo(f"download stage: {done} done, {failed} failed")
    if failed:
        raise typer.Exit(1)


@app.command()
def parse(workers: int | None = typer.Option(None, help=_WORKERS_HELP)) -> None:
    """Parse downloaded zips into silver Parquet parts (parse-once cache)."""
    cfg, ledger = _ctx()
    parse_stage.plan(cfg, ledger)
    workers = workers or default_workers("parse")
    if workers > 1:
        typer.echo(f"parsing with {workers} workers")
        done, failed = _fan_out(_parse_worker, workers)
    else:
        done, failed = parse_stage.work(cfg, ledger, log=typer.echo)
    typer.echo(f"parse stage: {done} done, {failed} failed")
    if failed:
        raise typer.Exit(1)


@app.command()
def build() -> None:
    """Merge parsed parts into a candidate build (never touches live)."""
    cfg, _ = _ctx()
    cutoff, zips = parse_stage.input_zips(cfg)
    candidate = build_mod.build_candidate(cfg, cutoff, zips)
    typer.echo(f"candidate ready: {candidate}")


def _latest_candidate(cfg: Config) -> Path:
    builds = sorted(p for p in cfg.builds.glob("build-*") if p.is_dir())
    if not builds:
        raise SystemExit("no builds exist — run `trademark-radar build` first")
    return builds[-1]


@app.command()
def reconcile(build_dir: Path | None = typer.Argument(None)) -> None:
    """Run publish gates against a build (default: newest)."""
    cfg, ledger = _ctx()
    target = build_dir or _latest_candidate(cfg)
    try:
        run_reconcile(target, ledger, previous_build=release.live_build(cfg),
                      log=typer.echo)
    except ReconcileError as e:
        typer.secho(str(e), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from None
    typer.echo(f"{target.name}: gates passed")


@app.command("release")
def release_cmd(build_dir: Path | None = typer.Argument(None)) -> None:
    """Atomic swap to a reconciled build, advance watermarks, prune dailies."""
    cfg, ledger = _ctx()
    target = build_dir or _latest_candidate(cfg)
    if not (target / "profile" / "build_lineage.json").exists():
        typer.secho(f"{target.name} never passed reconcile - refusing.",
                    fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    release.swap(cfg, target)
    release.advance_watermarks(cfg, ledger)
    n = release.prune_dailies(cfg, log=typer.echo)
    typer.echo(f"live -> {target.name}; pruned {n} superseded dailies")


@app.command()
def publish(repo: str | None = typer.Option(
                None, help="HF dataset repo for silver, e.g. user/uspto-trademarks"),
            bronze_repo: str | None = typer.Option(
                None, help="HF dataset repo mirroring the raw bronze zips "
                           "(append-only archive)")) -> None:
    """Upload the live silver build and/or the bronze zip mirror to
    Hugging Face. Silver is one commit per publish; the bronze mirror is
    resumable and never deletes."""
    if not repo and not bronze_repo:
        raise typer.BadParameter("give --repo and/or --bronze-repo")
    cfg, _ = _ctx()
    if repo:
        live = release.live_build(cfg)
        if live is None:
            raise SystemExit("no live build - run the pipeline first")
        publish_mod.publish(live, repo, log=typer.echo)
    if bronze_repo:
        publish_mod.publish_bronze(cfg.data_root / "bronze", bronze_repo,
                                   log=typer.echo)


@app.command()
def restore(from_mirror: str = typer.Option(
        ..., help="HF bronze mirror repo to restore from, "
                  "e.g. user/uspto-trademarks-bronze")) -> None:
    """Restore bronze from the HF mirror (no USPTO key needed). Lets a
    stateless runner rebuild silver — and derived gold/features — from
    exactly the mirror's data, without downloading anything new from USPTO."""
    cfg, ledger = _ctx()
    restore_mod.restore_bronze(cfg, ledger, from_mirror, log=typer.echo)


@app.command()
def run(repo: str | None = typer.Option(
            None, help="HF dataset repo; if set, publish silver after release"),
        bronze_repo: str | None = typer.Option(
            None, help="HF dataset repo; if set, refresh the bronze mirror "
                       "after release"),
        from_mirror: str | None = typer.Option(
            None, help="restore bronze from this HF mirror repo first "
                       "(for stateless/cloud runners)"),
        workers: int | None = typer.Option(None, help=_WORKERS_HELP)) -> None:
    """Full update job: [restore,] preflight, download, parse, build,
    reconcile, release (swap + watermarks + prune), optional publish.
    See docs/ARCHITECTURE.md."""
    cfg, ledger = _ctx()
    client = _client(cfg)
    if from_mirror:
        restore_mod.restore_bronze(cfg, ledger, from_mirror, log=typer.echo)
    try:
        for line in odp_preflight(client, PRODUCT_ANNUAL, PRODUCT_DAILY):
            typer.echo(line)
        dl_plan = download_mod.plan(cfg, ledger, client)
    except (PlatformDriftError, TransientError) as e:
        _fail(e)
    typer.echo(dl_plan.summary())

    dl_workers = workers or default_workers("download")
    if dl_workers > 1:
        _, failed = _fan_out(_download_worker, dl_workers)
    else:
        _, failed = download_mod.work(cfg, ledger, client, log=typer.echo)
    if failed:
        typer.secho("downloads failed - fix and re-run; nothing was built.",
                    fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    parse_stage.plan(cfg, ledger)
    parse_workers = workers or default_workers("parse")
    if parse_workers > 1:
        _, failed = _fan_out(_parse_worker, parse_workers)
    else:
        _, failed = parse_stage.work(cfg, ledger, log=typer.echo)
    if failed:
        typer.secho("parses failed - fix and re-run; nothing was built.",
                    fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    cutoff, zips = parse_stage.input_zips(cfg)
    candidate = build_mod.build_candidate(cfg, cutoff, zips)
    typer.echo(f"candidate: {candidate.name}")

    try:
        run_reconcile(candidate, ledger,
                      previous_build=release.live_build(cfg), log=typer.echo)
    except ReconcileError as e:
        typer.secho(str(e), fg=typer.colors.RED, err=True)
        typer.secho("candidate kept for triage; live build untouched; "
                    "dailies NOT pruned.", err=True)
        raise typer.Exit(1) from None

    release.swap(cfg, candidate)
    release.advance_watermarks(cfg, ledger)
    release.prune_dailies(cfg, log=typer.echo)
    typer.echo(f"live -> {candidate.name} "
               f"(data current through "
               f"{ledger.get_watermark('data_current_through')})")

    if repo:
        publish_mod.publish(candidate, repo, log=typer.echo)
    if bronze_repo:
        publish_mod.publish_bronze(cfg.data_root / "bronze", bronze_repo,
                                   log=typer.echo)


def _explore_target(cfg: Config) -> Path:
    live = release.live_build(cfg)
    if live is not None:
        return live
    typer.secho("no live build yet - pointing views at the newest candidate",
                fg=typer.colors.YELLOW)
    return _latest_candidate(cfg)


@app.command()
def explore(tui: bool = typer.Option(
        True, "--tui/--no-tui",
        help="open the Harlequin SQL IDE on the data (default), or just "
             "refresh the views and print connection snippets")) -> None:
    """Browse the silver data in a terminal SQL IDE. Refreshes
    data/silver.duckdb views over the current build, writes
    data/starter_queries.sql, and launches Harlequin on it."""
    import shutil
    import subprocess

    cfg, _ = _ctx()
    target = _explore_target(cfg)
    db = cfg.data_root / "silver.duckdb"
    views = explore_mod.make_views_db(target, db)
    queries = cfg.data_root / "starter_queries.sql"
    queries.write_text(explore_mod.STARTER_QUERIES, encoding="utf-8")
    typer.echo(f"{db.name} -> {target.name} ({len(views)} views); "
               f"starter queries in {queries}")
    if not tui:
        typer.echo("\nquery it with any DuckDB client, e.g.:")
        typer.echo(f'  uv run python -c "import duckdb; '
                   f"duckdb.connect(r'{db}').sql('FROM case_file LIMIT 5').show()\"")
        typer.echo("\nstarter queries:\n" + explore_mod.STARTER_QUERIES)
        return
    exe = shutil.which("harlequin")
    if exe is None:
        typer.secho("harlequin is not installed - run: uv pip install -e . "
                    "(or: trademark-radar explore --no-tui)",
                    fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    typer.echo("launching Harlequin (Ctrl+Q quits; F1 for help; open "
               "starter_queries.sql with Ctrl+O)...")
    raise typer.Exit(subprocess.call([exe, str(db)]))


@app.command()
def sql(query: str = typer.Argument(..., help="SQL over the silver views")) -> None:
    """One-off query against the current build (creates views if needed)."""
    import duckdb as _duckdb

    cfg, _ = _ctx()
    db = cfg.data_root / "silver.duckdb"
    if not db.exists():
        explore_mod.make_views_db(_explore_target(cfg), db)
    _duckdb.connect(str(db), read_only=True).sql(query).show(max_rows=100)


@app.command()
def gold() -> None:
    """Build the gold analysis tables from the live build (to data/gold/):
    mark_features (per-filing columns, publishable) and operation_profile
    (per-address aggregates, local-only). See docs/adr/0008."""
    import duckdb as _duckdb

    from .gold import mark_features, operation_profile
    from .gold.coined_scorer import fit_scorer

    cfg, _ = _ctx()
    target = _explore_target(cfg)
    con = _duckdb.connect()
    scorer = fit_scorer(
        con, (target / "data" / "case_file").as_posix() + "/*.parquet")
    con.close()
    typer.echo("[gold] coined scorer fit (shared across gold tables)")
    mark_features.build(target, cfg.data_root / "gold", scorer=scorer,
                        log=typer.echo)
    operation_profile.build(target, cfg.data_root / "gold", scorer=scorer,
                            log=typer.echo)


@app.command()
def evaluate() -> None:
    """Build outcome labels from USPTO adjudications (sanctions vs TMA
    nonuse vs survivors) and measure signal quality against them. Writes
    data/gold/evaluation_metrics.json. Needs `uv sync --group analysis`."""
    from .gold import evaluation

    cfg, _ = _ctx()
    target = _explore_target(cfg)
    gold_root = cfg.data_root / "gold"
    evaluation.build_labels(target, gold_root, log=typer.echo)
    metrics = evaluation.evaluate(target, gold_root, log=typer.echo)
    if "auc" in metrics:
        typer.echo(f"AUCs: {metrics['auc']}")
    if "model" in metrics and "test_auc" in metrics.get("model", {}):
        typer.echo(f"model test AUC: {metrics['model']['test_auc']}")
    if "scorer_comparison" in metrics:
        c = metrics["scorer_comparison"]
        typer.echo(f"scorer comparison (ADR 0016): transparent "
                   f"{c['transparent_test_auc']} vs fitted "
                   f"{c['fitted_test_auc']} (equal-weight "
                   f"{c['equal_weight_test_auc']}); fitted Brier "
                   f"{c['fitted_brier']}")
        if "cross_cohort" in c:
            cc = c["cross_cohort"]
            typer.echo(f"  cross-cohort coef corr {cc['coef_correlation']}, "
                       f"cross AUC {cc['early_fit_late_test_auc']}/"
                       f"{cc['late_fit_early_test_auc']}")


@app.command("validate-resolution")
def validate_resolution(
    audit: bool = typer.Option(
        True, "--audit/--no-audit",
        help="write the stratified manual-review sample (local-only)"),
    sensitivity: bool = typer.Option(
        True, "--sensitivity/--no-sensitivity",
        help="regroup under alternative keying rules and compare"),
    colocation: bool = typer.Option(
        True, "--colocation/--no-colocation",
        help="rank co-located addresses as an inspection list (asserts "
             "nothing; local-only)"),
) -> None:
    """Validate the operation grouping (ADR 0011): write a deterministic
    manual-audit sample, a keying sensitivity report, and a co-location
    inspection list under data/gold/. All local-only. Needs the gold
    tables (trademark-radar gold)."""
    import duckdb as _duckdb

    from .gold import resolution_audit

    cfg, _ = _ctx()
    target = _explore_target(cfg)
    gold_root = cfg.data_root / "gold"
    if not (gold_root / gold_mark_features.TABLE / "_lineage.json").exists():
        raise SystemExit("mark_features missing - run `trademark-radar gold` "
                         "first (coined scores are reused, not refit)")
    # Scan the corpus once and share it across both artifacts.
    con = _duckdb.connect()
    resolution_audit.load_base(con, target, gold_root)
    rows = resolution_audit.signal_rows(con)
    if audit:
        resolution_audit.build_audit_sample(target, gold_root, con=con,
                                            rows=rows, log=typer.echo)
    if sensitivity:
        report = resolution_audit.sensitivity(target, gold_root, con=con,
                                              rows=rows, log=typer.echo)
        typer.echo(f"{'variant':<12} {'ops':>8} {'filings':>12} "
                   f"{'max op':>8} {'p90 score':>10} {'spearman':>9}")
        for name, v in report["variants"].items():
            rho = v.get("vs_shipped", {}).get("filing_score_spearman")
            typer.echo(f"{name:<12} {v['n_operations']:>8,} "
                       f"{v['filings_profiled']:>12,} "
                       f"{v['max_op_size']:>8,} "
                       f"{v['op_score_p90'] if v['op_score_p90'] is not None else '-':>10} "
                       f"{rho if rho is not None else '-':>9}")
    if colocation:
        rep = resolution_audit.colocation(target, gold_root, con=con,
                                          log=typer.echo)
        typer.echo(f"co-location: {rep['qualifying_addresses']:,} candidate "
                   f"addresses flagged for inspection (top "
                   f"{rep['reported']:,} listed; asserts nothing)")
    con.close()


@app.command("resolution-score")
def resolution_score(
    verdicts: Path = typer.Argument(
        ..., help="verdicts.json exported by the review app (review.html)"),
) -> None:
    """Score a completed grouping audit: read the name-free verdicts.json
    and print precision (overall and per size stratum) with Wilson 95%
    intervals. Precision counts only over-merge as an error (ADR 0014)."""
    from .gold import resolution_audit

    cfg, _ = _ctx()
    gold_root = cfg.data_root / "gold"
    if not (gold_root / resolution_audit.AUDIT_TABLE / "audit_sample.json").exists():
        raise SystemExit("no audit sample found - run "
                         "`trademark-radar validate-resolution` first")
    rep = resolution_audit.score_verdicts(gold_root, verdicts, log=typer.echo)
    c = rep["counts"]
    typer.echo(f"reviewed: {c['coherent']} coherent, {c['mixed']} mixed, "
               f"{c['unclear']} unclear, {c['unreviewed']} unreviewed")
    typer.echo(f"{'stratum':<12} {'coherent':>9} {'adjud.':>7} "
               f"{'precision':>10} {'95% CI':>18}")

    def line(label: str, p: dict) -> str:
        prec = "-" if p["precision"] is None else f"{p['precision']:.3f}"
        ci = f"[{p['wilson95'][0]:.3f}, {p['wilson95'][1]:.3f}]"
        return (f"{label:<12} {p['coherent']:>9} {p['adjudicated']:>7} "
                f"{prec:>10} {ci:>18}")

    for label, p in rep["by_stratum"].items():
        typer.echo(line(label, p))
    typer.echo(line("overall", rep["overall"]))


@app.command("event-study")
def event_study() -> None:
    """Policy-discontinuity analysis of the surge (ADR 0015): build the
    monthly composition series, run a descriptive interrupted-time-series at
    the three policy dates, and a difference-in-differences for the Aug-2019
    US-counsel rule. Writes data/gold/event_study_metrics.json. Needs
    `uv sync --group analysis`."""
    from .gold import event_study as es

    cfg, _ = _ctx()
    target = _explore_target(cfg)
    gold_root = cfg.data_root / "gold"
    m = es.analyze(target, gold_root, log=typer.echo)
    typer.echo(f"series: {m['n_months']} months {m['span'][0]}..{m['span'][1]}")
    did = m["us_counsel_did"]
    typer.echo(f"US-counsel rule DiD (attorney presence, CN vs US): "
               f"{did['did_estimate']:+.3f} (SE {did['did_se']}, p {did['did_p']}); "
               f"CN {did['cn_pre']}->{did['cn_post']} vs US "
               f"{did['us_pre']}->{did['us_post']}; "
               f"pre-deadline rush x{did['pre_deadline_rush_ratio']}")
    typer.echo(f"{'metric':<18}{'break':<16}{'level':>9}{'p':>8}")
    for metric, r in m["behavioral_its"].items():
        for key, b in r["breaks"].items():
            typer.echo(f"{metric:<18}{key:<16}{b['level_shift']:>9}{b['level_p']:>8}")


@app.command("publish-features")
def publish_features(repo: str = typer.Option(
        ..., help="HF dataset repo for the derived mark-features table")) -> None:
    """Publish the mark-features table (opt-in; separate from the silver
    publish)."""
    cfg, _ = _ctx()
    feat = cfg.data_root / "gold" / f"mark_features_v{gold_mark_features.VERSION}"
    if not (feat / "_lineage.json").exists():
        raise SystemExit("build it first: trademark-radar gold")
    publish_mod.publish_features(feat, repo, log=typer.echo)


@app.command("detector-lists")
def detector_lists(
    threshold: float = typer.Option(
        gold_detector.TIER2_THRESHOLD, help="tier-2 screening threshold; "
        "raise for a smaller, stricter candidate list"),
    min_cluster: int = typer.Option(
        gold_detector.TIER3_MIN_SIZE, help="tier-3 minimum operation size"),
    min_churn: float = typer.Option(
        gold_detector.TIER3_MIN_CHURN, help="tier-3 minimum owner-turnover"),
) -> None:
    """Build the downstream-detector lists under data/gold/: tier1.txt
    (adjudicated blocklist), tier2.txt (screened candidates), and tier3.txt
    (large-operation membership, ADR 0017). Needs the gold tables
    (trademark-radar gold). Core deps only; see CONSUMERS.md."""
    cfg, _ = _ctx()
    target = _explore_target(cfg)
    gold = cfg.data_root / "gold"
    feat = gold / f"mark_features_v{gold_mark_features.VERSION}"
    if not (feat / "_lineage.json").exists():
        raise SystemExit("build it first: trademark-radar gold")
    prof = gold / gold_operation.TABLE
    mem = gold / gold_operation.MEMBERSHIP_TABLE
    if not (prof / "_lineage.json").exists() or not any(mem.glob("*.parquet")):
        raise SystemExit("operation grouping missing (needed for tier3) - "
                         "run `trademark-radar gold`")
    gold_detector.build(target, feat, gold, tier2_threshold=threshold,
                        profile_dir=prof, membership_dir=mem,
                        tier3_min_size=min_cluster, tier3_min_churn=min_churn,
                        log=typer.echo)


@app.command("publish-detector")
def publish_detector(repo: str = typer.Option(
        ..., help="HF dataset repo for the detector lists")) -> None:
    """Publish the detector lists (opt-in; separate from silver/features)."""
    cfg, _ = _ctx()
    det = cfg.data_root / "gold" / f"detector_v{gold_detector.VERSION}"
    if not (det / "_manifest.json").exists():
        raise SystemExit("build it first: trademark-radar detector-lists")
    publish_mod.publish_detector(det, repo, log=typer.echo)


@app.command()
def status() -> None:
    """Ledger job counts, watermarks, live build."""
    cfg, ledger = _ctx()
    counts = ledger.status_counts()
    if counts:
        typer.echo("jobs:")
        for (stage, st), n in sorted(counts.items()):
            typer.echo(f"  {stage:10s} {st:8s} {n}")
    else:
        typer.echo("jobs: none")
    marks = ledger.watermarks()
    typer.echo("watermarks:")
    for k, v in sorted(marks.items()) or []:
        typer.echo(f"  {k} = {v}")
    if not marks:
        typer.echo("  (none)")
    live = release.live_build(cfg)
    typer.echo(f"live build: {live.name if live else '(none)'}")


@app.command()
def retry(stage: str | None = typer.Argument(None)) -> None:
    """Flip failed jobs back to pending (optionally for one stage)."""
    _, ledger = _ctx()
    n = ledger.retry_failed(stage)
    typer.echo(f"{n} job(s) reset to pending")


def main() -> None:
    # .env at the repo root supplies USPTO_API_KEY / HF_TOKEN /
    # TRADEMARK_RADAR_DATA_ROOT; real shell env vars still win.
    load_dotenv(Config().repo_root / ".env")
    load_dotenv()  # also honor a .env in the current working directory
    app()


if __name__ == "__main__":
    main()
