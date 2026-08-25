"""Turning a run into things a human reads: the README table and a digest.

The README is rewritten in place between two HTML comment markers, so a user
can put whatever they like around the generated block and never lose it. The
digest is the body of the GitHub Issue that gets opened when something new
appears — which is what actually reaches the user, since GitHub emails you
about issues in your own repository by default.
"""
from __future__ import annotations

import re
from datetime import date

from .model import Listing, season_pair

START_MARKER = "<!-- RADAR:START -->"
END_MARKER = "<!-- RADAR:END -->"

_BLOCK_RE = re.compile(
    re.escape(START_MARKER) + r".*?" + re.escape(END_MARKER), re.S)


def _escape(text: str) -> str:
    """Keep a role title with a pipe in it from breaking the table."""
    return (text or "").replace("|", "\\|").strip()


def _row(listing: Listing) -> str:
    role = _escape(listing.role) or "(untitled role)"
    link = f"[Apply]({listing.url})" if listing.url else ""
    posted = listing.posted.strftime("%b %d") if listing.posted else "-"
    return (f"| {_escape(listing.company)} | {role} | "
            f"{_escape(listing.location) or '-'} | {posted} | {link} |")


def _table(listings: list[Listing]) -> str:
    header = ("| Company | Role | Location | Posted | |\n"
              "| --- | --- | --- | --- | --- |")
    rows = [_row(l) for l in sorted(
        listings, key=lambda l: (l.company.lower(), l.role.lower()))]
    return "\n".join([header, *rows])


def _flag_season_mismatch(listings: list[Listing], season: str) -> list[str]:
    """Notes about postings that do not visibly name the target season.

    Deliberately advisory, never a filter. A posting whose title omits the
    season is usually still relevant, and dropping it would risk the one
    failure this tool exists to prevent.
    """
    parts = season.split()
    if len(parts) != 2:
        return []
    name, year = parts
    unnamed = [l for l in listings
               if not season_pair(f"{l.role} {l.location}", name, year)]
    if not unnamed:
        return []
    return [f"{len(unnamed)} of these do not state a season in the title — "
            f"worth a click to confirm they are {season}."]


def digest(new: list[Listing], season: str, when: date | None = None) -> str:
    """Markdown body for the "something opened" notification."""
    when = when or date.today()
    if not new:
        return (f"No new postings as of {when.isoformat()} "
                f"(tracking {season}).")

    by_company: dict[str, list[Listing]] = {}
    for l in new:
        by_company.setdefault(l.company, []).append(l)

    lines = [
        f"**{len(new)} new posting(s)** across "
        f"{len(by_company)} company(ies), first seen {when.isoformat()}.",
        "",
        _table(new),
        "",
    ]
    for note in _flag_season_mismatch(new, season):
        lines.append(f"> {note}")
        lines.append("")
    lines.append("_Each posting is reported once, the first time it is "
                 "seen. Nothing here was open on the previous run._")
    return "\n".join(lines)


def summary_line(new: list[Listing]) -> str:
    """One-line title for the notification issue."""
    if not new:
        return "No new internship postings"
    companies = sorted({l.company for l in new})
    shown = ", ".join(companies[:3])
    if len(companies) > 3:
        shown += f" +{len(companies) - 3} more"
    noun = "posting" if len(new) == 1 else "postings"
    return f"{len(new)} new internship {noun}: {shown}"


def readme_block(all_listings: list[Listing], new: list[Listing],
                 season: str, when: date | None = None,
                 warnings: list[str] | None = None) -> str:
    """The generated section of the README."""
    when = when or date.today()
    # Deliberately does NOT claim these are all `season` postings. Nothing
    # is filtered by season (see the design note in state.py), so asserting
    # the season here would be a claim the tool has not checked.
    lines = [
        START_MARKER,
        "",
        f"### Tracking {len(all_listings)} open internship posting(s)",
        "",
        f"_Target season: {season}. Last checked {when.isoformat()}. "
        "Postings are not filtered by season or role — you see everything "
        "at the companies you follow._",
        "",
    ]
    if new:
        lines += [f"**{len(new)} new since the last check:**", "",
                  _table(new), ""]
    if all_listings:
        lines += ["<details>",
                  f"<summary>All {len(all_listings)} tracked postings"
                  "</summary>", "", _table(all_listings), "", "</details>",
                  ""]
    else:
        lines += ["Nothing open yet. This updates automatically.", ""]
    for w in warnings or []:
        lines.append(f"> Source unavailable this run - {w}")
    if warnings:
        lines.append("")
    lines.append(END_MARKER)
    return "\n".join(lines)


def update_readme(readme_text: str, block: str) -> str:
    """Replace the generated block, appending it if not present yet."""
    if _BLOCK_RE.search(readme_text):
        return _BLOCK_RE.sub(lambda _: block, readme_text, count=1)
    return readme_text.rstrip() + "\n\n" + block + "\n"
