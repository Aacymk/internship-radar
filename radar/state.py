"""What the tracker has already seen, and what is genuinely new.

This is the heart of the tool. Aggregating job listings is easy; the hard
part is that a naive tracker re-reports the same posting forever. A job that
has been open for three months is not news, and a tool that announces it
every morning trains you to ignore it — at which point you miss the one
posting that mattered.

So the alert gate is NOVELTY, not content. Listings are never filtered by
role or season to decide whether to alert: filters guess, and a wrong guess
means a missed opening, which is the one failure mode with no recovery. A
posting alerts exactly once, the first time it is seen, and is then
remembered forever.

State is plain JSON committed to the repo rather than a binary database, for
three reasons: a non-technical user can read it, the git diff of a run is
itself a human-readable changelog of what opened that day, and GitHub
Actions can commit it back with no external storage.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from .model import Listing

STATE_VERSION = 1


@dataclass
class Diff:
    """One run's outcome."""
    new: list[Listing]
    known: list[Listing]
    first_run: bool

    @property
    def has_news(self) -> bool:
        return bool(self.new)


class State:
    """The set of postings seen on previous runs, keyed by canonical URL."""

    def __init__(self, path: Path, seen: dict | None = None,
                 last_run: str | None = None):
        self.path = Path(path)
        self.seen: dict[str, dict] = seen or {}
        self.last_run = last_run

    # ---------------------------------------------------------- load/save

    @classmethod
    def load(cls, path: str | Path) -> State:
        """Read state, tolerating absence and corruption.

        A missing or unreadable state file must not crash a run: it means
        "nothing seen yet", which the first-run logic below handles safely.
        """
        path = Path(path)
        if not path.exists():
            return cls(path)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            print(f"    state file at {path} is unreadable — "
                  "treating this as a first run")
            return cls(path)
        return cls(path, seen=raw.get("seen") or {},
                   last_run=raw.get("last_run"))

    def save(self, when: date | None = None) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": STATE_VERSION,
            "last_run": (when or date.today()).isoformat(),
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "count": len(self.seen),
            # sorted so the git diff shows real changes, not key reshuffling
            "seen": dict(sorted(self.seen.items())),
        }
        self.path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8")

    # -------------------------------------------------------------- diff

    @property
    def is_empty(self) -> bool:
        return not self.seen

    def diff(self, listings: list[Listing]) -> Diff:
        """Split listings into never-seen-before and already-known."""
        new: list[Listing] = []
        known: list[Listing] = []
        for listing in listings:
            (known if listing.key in self.seen else new).append(listing)
        return Diff(new=new, known=known, first_run=self.is_empty)

    def record(self, listings: list[Listing],
               when: date | None = None) -> None:
        """Remember every listing, so today's news is tomorrow's history.

        `first_seen` is preserved for postings already known — it is the
        only durable record of when a role actually opened.
        """
        today = (when or date.today()).isoformat()
        for listing in listings:
            existing = self.seen.get(listing.key)
            entry = listing.to_json()
            entry["first_seen"] = (existing or {}).get("first_seen", today)
            entry["last_seen"] = today
            self.seen[listing.key] = entry

    def seen_listings(self) -> list[Listing]:
        return [Listing.from_json(v) for v in self.seen.values()]


def adopt_baseline(state: State, listings: list[Listing],
                   when: date | None = None) -> int:
    """Record listings WITHOUT alerting — used for the very first run.

    Without this, a new user's first run reports several hundred postings
    that have been open for months, which is noise rather than news. The
    first run establishes what "already open" means; every run after it
    reports only genuine change.
    """
    state.record(listings, when)
    state.save(when)
    return len(listings)
