"""A-share daily data maintenance CLI.

    python -m data.maintenance init                 # full history for every A-share (resumable)
    python -m data.maintenance update               # incremental update to the latest trading day
    python -m data.maintenance update --symbols 600000.SH,000001.SZ
    python -m data.maintenance repair --symbols 600000.SH   # re-download one symbol from scratch
    python -m data.maintenance status               # what is in the lake
    python -m data.maintenance check                # data-quality checks
    python -m data.maintenance sources              # probe every data source
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date

from data import config


def _symbols(arg: str | None) -> list[str] | None:
    if not arg:
        return None
    from data.symbols import normalize
    return [normalize(s) for s in arg.split(",") if s.strip()]


def _run(mode: str, fn) -> "SyncReport":  # noqa: F821
    """Run a write command under the lake lock, logging to meta/runs/<run_id>.log."""
    from data.maintenance.runs import LakeBusy, capture_log, lake_lock, new_run_id
    from data.store import Lake

    lake = Lake()
    run_id = new_run_id(mode)
    try:
        with lake_lock(lake), capture_log(lake, run_id):
            logging.getLogger(__name__).info("run %s started", run_id)
            rep = fn(run_id)
    except LakeBusy:
        print("another data update is running (from the web UI or another terminal); try again later",
              file=sys.stderr)
        sys.exit(4)
    print(json.dumps(rep.as_dict(), ensure_ascii=False, indent=2, default=str))
    return rep


def cmd_init(a) -> int:
    from data.maintenance.sync import full_download
    rep = _run("full", lambda rid: full_download(
        symbols=_symbols(a.symbols), workers=a.workers, include_delisted=not a.listed_only,
        resume=not a.no_resume, refresh_meta=not a.skip_meta, time_budget=a.time_budget, run_id=rid))
    if not rep.complete:
        return 3  # partial: run again to continue
    return 0 if rep.symbols_failed == 0 else 2


def cmd_update(a) -> int:
    from data.maintenance.sync import incremental_update
    end = date.fromisoformat(a.end) if a.end else None
    rep = _run("incremental", lambda rid: incremental_update(
        symbols=_symbols(a.symbols), workers=a.workers, end=end, refresh_meta=not a.skip_meta, run_id=rid))
    return 0 if rep.symbols_failed == 0 else 2


def cmd_repair(a) -> int:
    from data.maintenance.sync import repair
    rep = _run("repair", lambda rid: repair(symbols=_symbols(a.symbols), workers=a.workers, run_id=rid))
    return 0 if rep.symbols_failed == 0 else 2


def cmd_status(a) -> int:
    from data.maintenance.checks import status
    print(json.dumps(status(), ensure_ascii=False, indent=2, default=str))
    return 0


def cmd_check(a) -> int:
    from data.maintenance.checks import quality_report
    rep = quality_report()
    print(json.dumps(rep, ensure_ascii=False, indent=2, default=str))
    return 0 if rep.get("ok") else 1


def cmd_sources(a) -> int:
    from data.maintenance.checks import probe_sources
    print(json.dumps(probe_sources(), ensure_ascii=False, indent=2, default=str))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m data.maintenance", description="A-share daily data maintenance")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="full history download (resumable)")
    s.add_argument("--symbols", help="comma-separated subset, e.g. 600000.SH,000001.SZ")
    s.add_argument("--workers", type=int, default=config.WORKERS)
    s.add_argument("--listed-only", action="store_true", help="skip delisted stocks")
    s.add_argument("--no-resume", action="store_true", help="ignore previously staged symbols")
    s.add_argument("--skip-meta", action="store_true", help="reuse the stored stock list / calendar")
    s.add_argument("--time-budget", type=float, help="seconds; stop early, save progress, exit 3")
    s.set_defaults(fn=cmd_init)

    s = sub.add_parser("update", help="incremental update")
    s.add_argument("--symbols")
    s.add_argument("--workers", type=int, default=config.WORKERS)
    s.add_argument("--end", help="YYYY-MM-DD; default = latest completed trading day")
    s.add_argument("--skip-meta", action="store_true", help="do not refresh stock list / calendar")
    s.set_defaults(fn=cmd_update)

    s = sub.add_parser("repair", help="delete and re-download specific symbols")
    s.add_argument("--symbols", required=True)
    s.add_argument("--workers", type=int, default=config.WORKERS)
    s.set_defaults(fn=cmd_repair)

    sub.add_parser("status", help="lake summary").set_defaults(fn=cmd_status)
    sub.add_parser("check", help="data-quality checks").set_defaults(fn=cmd_check)
    sub.add_parser("sources", help="probe data sources").set_defaults(fn=cmd_sources)

    a = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for noisy in ("urllib3", "tdxpy", "pytdx", "mootdx"):
        logging.getLogger(noisy).setLevel(logging.ERROR)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
