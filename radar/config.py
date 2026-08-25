"""Loads config.yml — the only file a user is ever expected to edit.

Everything here is designed to be typed into GitHub's web editor by someone
who has never opened a terminal, so the loader is deliberately forgiving:
missing keys fall back to defaults, and a company can be written either as a
bare name or as a name with a job-board attached.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = ROOT / "config.yml"
DEFAULT_STATE_PATH = ROOT / "data" / "state.json"

DEFAULT_SEASON = "Summer 2027"


class ConfigError(Exception):
    """config.yml is unusable. Message is aimed at a non-technical reader."""


@dataclass
class Company:
    """A company to watch, and optionally where its job board lives.

    `board` is None for most companies — they are matched by name against
    the curated job lists. When it IS set, the company's own ATS is queried
    directly, which is the highest-trust signal available.
    """
    name: str
    board: tuple | None = None  # (kind, board_config)


@dataclass
class Config:
    season: str = DEFAULT_SEASON
    companies: list[Company] = field(default_factory=list)
    job_lists: list[dict] = field(default_factory=list)
    check_boards: bool = True
    state_path: Path = DEFAULT_STATE_PATH

    @property
    def season_parts(self) -> tuple[str, str]:
        """('Summer', '2027'). Used by every season check."""
        bits = self.season.split()
        if len(bits) != 2 or not bits[1].isdigit():
            raise ConfigError(
                f'season must look like "Summer 2027", got "{self.season}"')
        return bits[0], bits[1]

    def company_names(self) -> dict[str, Company]:
        from .model import normalize_company
        return {normalize_company(c.name): c for c in self.companies}


def _parse_company(entry) -> Company:
    """Accept either `- Google` or a mapping with a board attached."""
    if isinstance(entry, str):
        # YAML silently folds a mis-indented list into ONE string:
        #     companies:
        #      - Google
        #       - Meta      ->  ["Google - Meta"]
        # Left alone this watches a company that does not exist and reports
        # nothing forever, with no error. Indentation is the single most
        # likely mistake for the audience editing this file, so catch it.
        name = entry.strip()
        if " - " in name or "\n" in name:
            raise ConfigError(
                f'The company "{name}" looks like several companies that ran '
                "together.\nThis is almost always an indentation mistake. "
                "Each company needs its own\nline, and every line must start "
                'with exactly two spaces then "- ":\n\n'
                "companies:\n  - Google\n  - Meta\n")
        return Company(name=name)
    if isinstance(entry, dict):
        name = (entry.get("name") or "").strip()
        if not name:
            raise ConfigError(f"a company entry is missing a name: {entry!r}")
        board = None
        kind = (entry.get("board") or "").strip().lower()
        if kind:
            board = (kind, entry.get("board_id") or entry.get("slug"))
        return Company(name=name, board=board)
    raise ConfigError(f"could not read the company entry: {entry!r}")


def load(path: str | Path | None = None) -> Config:
    """Read config.yml. Raises ConfigError with a human-readable message."""
    path = Path(path or DEFAULT_CONFIG_PATH)
    if not path.exists():
        raise ConfigError(
            f"No config file found at {path}.\n"
            "Copy config.example.yml to config.yml and edit it.")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise ConfigError(
            f"{path.name} is not valid YAML — check the indentation.\n{e}"
        ) from e
    if not isinstance(raw, dict):
        raise ConfigError(f"{path.name} should be a list of settings.")

    companies = [_parse_company(c) for c in (raw.get("companies") or [])]
    if not companies:
        raise ConfigError(
            f"{path.name} lists no companies — add at least one under "
            '"companies:".')

    job_lists = [j for j in (raw.get("job_lists") or []) if j.get("url")]

    return Config(
        season=str(raw.get("season") or DEFAULT_SEASON).strip(),
        companies=companies,
        job_lists=job_lists,
        check_boards=bool(raw.get("check_boards", True)),
        state_path=Path(raw.get("state_path") or DEFAULT_STATE_PATH),
    )


def load_or_exit(path: str | Path | None = None) -> Config:
    """load(), but turn a config mistake into a clear message, not a stack
    trace — the target user reads this in a GitHub Actions log."""
    try:
        cfg = load(path)
        _ = cfg.season_parts  # validate early, while we can still explain why
        return cfg
    except ConfigError as e:
        print(f"\nThere is a problem with your configuration:\n\n{e}\n",
              file=sys.stderr)
        raise SystemExit(2) from e
