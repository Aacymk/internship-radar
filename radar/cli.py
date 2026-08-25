"""Entry point. One run = fetch every source, report what is new, remember.

    python -m radar                 # normal run
    python -m radar --dry-run       # show what would happen, write nothing
    python -m radar --baseline      # record everything, alert on nothing
    python -m radar --digest out.md # also write the notification body

Exit codes are meaningful because CI reads them:
    0  ran fine, nothing new
    10 ran fine, found something new   (the workflow opens an issue)
    1  the run failed
    2  the configuration is wrong
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from datetime import date
from pathlib import Path

from . import config as config_module
from . import report, sources
from .model import normalize_company
from .state import State, adopt_baseline

EXIT_OK = 0
EXIT_NEWS = 10
EXIT_ERROR = 1


def _parse_args(argv=None):
    p = argparse.ArgumentParser(
        prog="radar", description=__doc__.splitlines()[0])
    p.add_argument("--config", metavar="PATH", help="path to config.yml")
    p.add_argument("--dry-run", action="store_true",
                   help="report findings without writing state or README")
    p.add_argument("--baseline", action="store_true",
                   help="record every current posting without alerting; "
                        "use once when adopting an already-open season")
    p.add_argument("--digest", metavar="PATH",
                   help="write the notification body to this file")
    p.add_argument("--title-file", metavar="PATH",
                   help="write the one-line notification title to this file")
    p.add_argument("--readme", metavar="PATH", default="README.md",
                   help="README to update (default: README.md)")
    p.add_argument("--no-readme", action="store_true",
                   help="do not touch the README")
    return p.parse_args(argv)


def _tracked_only(listings, cfg):
    """Keep listings whose company is one the user asked to watch.

    This is the ONE filter applied, and it is the user's own explicit list —
    not a guess about roles or seasons. Everything else is reported.
    """
    wanted = set(cfg.company_names())
    return [l for l in listings if normalize_company(l.company) in wanted]


def run(argv=None) -> int:
    args = _parse_args(argv)
    cfg = config_module.load_or_exit(args.config)
    today = date.today()

    print(f"Internship radar - {cfg.season} - {today.isoformat()}")
    print(f"Watching {len(cfg.companies)} company(ies).\n")

    src_list = sources.build_all(cfg)
    if not src_list:
        print("No sources configured. Add entries under \"job_lists:\" in "
              "your config file.", file=sys.stderr)
        return EXIT_ERROR

    print(f"Checking {len(src_list)} source(s) concurrently...")
    started = time.perf_counter()
    # The only async part of the program: everything downstream of the fetch
    # is pure computation over the listings it returned.
    found, warnings = asyncio.run(sources.collect(src_list))
    elapsed = time.perf_counter() - started
    tracked = _tracked_only(found, cfg)
    print(f"\n{len(found)} unique posting(s) found in {elapsed:.1f}s; "
          f"{len(tracked)} at companies you are watching.")

    state = State.load(cfg.state_path)

    # A first run against an in-progress season would otherwise announce
    # every long-open posting as "new". Establish the baseline instead.
    # --dry-run takes this path too, so a preview shows what a real run
    # would actually do rather than a misleading wall of "new" postings.
    if state.is_empty or args.baseline:
        if args.dry_run:
            print(f"\n[dry-run] no history yet - would record "
                  f"{len(tracked)} posting(s) as the baseline and report "
                  "nothing as new.")
            return EXIT_OK
        if state.is_empty:
            print("\nNo history yet - recording the current postings as the "
                  "baseline.\nNothing is reported as new on a first run; "
                  "the next run will report real changes.")
        count = adopt_baseline(state, tracked, today)
        print(f"\nBaseline recorded: {count} posting(s) marked as already "
              "seen.")
        _write_readme(args, cfg, tracked, [], warnings, today)
        return EXIT_OK

    diff = state.diff(tracked)
    if diff.new:
        print(f"\n{len(diff.new)} NEW posting(s) since the last run:")
        for l in diff.new:
            print(f"  + {l.company}: {l.role or '(untitled)'}")
            print(f"      {l.url}")
    else:
        print("\nNothing new since the last run.")

    if args.digest:
        body = report.digest(diff.new, cfg.season, today)
        Path(args.digest).write_text(body, encoding="utf-8")
        print(f"\nDigest written to {args.digest}")
    if args.title_file:
        Path(args.title_file).write_text(
            report.summary_line(diff.new) + "\n", encoding="utf-8")

    if args.dry_run:
        print("\n[dry-run] state and README left unchanged.")
        return EXIT_NEWS if diff.new else EXIT_OK

    state.record(tracked, today)
    state.save(today)
    print(f"\nState updated ({len(state.seen)} posting(s) remembered).")

    _write_readme(args, cfg, tracked, diff.new, warnings, today)
    return EXIT_NEWS if diff.new else EXIT_OK


def _write_readme(args, cfg, tracked, new, warnings, today) -> None:
    if args.no_readme or args.dry_run:
        return
    path = Path(args.readme)
    if not path.exists():
        return
    block = report.readme_block(tracked, new, cfg.season, today, warnings)
    updated = report.update_readme(path.read_text(encoding="utf-8"), block)
    path.write_text(updated, encoding="utf-8")
    print(f"{path} updated.")


def _force_utf8_output() -> None:
    """Windows consoles default to cp1252, which cannot encode the CJK and
    full-width punctuation that routinely appears in real job titles.
    Printing one would otherwise end the run in a UnicodeEncodeError."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue  # already wrapped (pytest capture, a pipe) - fine
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except ValueError:
            pass


def main(argv=None) -> int:
    _force_utf8_output()
    try:
        return run(argv)
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
