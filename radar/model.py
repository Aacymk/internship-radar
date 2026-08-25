"""The Listing record and the normalization rules every source shares.

Two normalizers matter more than they look:

`canonical_url` decides a posting's IDENTITY. The whole tracker rests on
knowing whether it has seen a posting before, and job lists append tracking
params (`?utm_source=...`) that differ between lists and change over time.
Keying on the raw URL means one list tweaking its links makes every posting
look brand new and re-alerts the user about jobs from months ago.

`normalize_company` decides whether two names are the same employer, so
"JPMorganChase", "JPMorgan Chase", and "JP Morgan Chase" collapse to one row
instead of three.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# Tracking params bolted onto apply links. They are not part of a posting's
# identity, so they are stripped before the URL is used as a key.
_TRACKING_PARAM_RE = re.compile(
    r"^(utm_\w+|gh_src|gh_jid|source|src|ref|referrer|campaign|mc_\w+)$", re.I)


def canonical_url(url: str) -> str:
    """Stable identity key for a posting: URL minus tracking params/fragment."""
    raw = (url or "").strip()
    try:
        parts = urlsplit(raw)
    except ValueError:
        return raw.lower()
    if not parts.scheme:
        return raw.lower()
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query)
                       if not _TRACKING_PARAM_RE.match(k)])
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(),
                       parts.path.rstrip("/") or "/", query, ""))


def normalize_company(name: str) -> str:
    """Match key for an employer: 'JP Morgan Chase' == 'JPMorganChase'."""
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def season_pair(text: str, season: str, year: str) -> bool:
    """True if `season` and `year` appear ADJACENT in text.

    The gap may not contain digits, which is the whole point: it separates
    "Summer 2027 Internship" (a season) from "Summer 2026 internship for
    2027 graduates" (a graduation year). Getting this wrong is the single
    most common way an internship tracker reports a role a year early.
    """
    pattern = rf"{season}[^\d]{{0,20}}{year}|{year}[^\d]{{0,20}}{season}"
    return bool(re.search(pattern, text or "", re.I))


# Is this posting an internship AT ALL? Deliberately not a relevance filter:
# it does not ask whether a role is software, or the right season, or in the
# right country. Those are judgment calls where a wrong guess silently hides
# a real opening, so they are left to the reader.
#
# This exists because a company job board is not an internship list. Workday's
# search does SUBSTRING matching, so querying "intern" returns every posting
# containing "internal" or "international" — which is how a search for
# internships comes back with "Vice President, Global Partner Marketing".
# The word boundary after "intern" is what rejects those two words.
_INTERNSHIP_RE = re.compile(
    r"\bintern(ship|ships|s)?\b"
    r"|\bco-?op\b"
    r"|\bapprentice(ship)?\b"
    r"|\btrainee\b"
    r"|\bindustrial placement\b"
    r"|\bsummer analyst\b"
    r"|\bstudent\b",
    re.I)


def looks_like_internship(text: str) -> bool:
    """True if a job title plausibly describes an internship."""
    return bool(_INTERNSHIP_RE.search(text or ""))


@dataclass(frozen=True)
class Listing:
    """One job posting as reported by one source.

    `source` is the source's name, kept so the report can tell the user
    where a posting came from and so a noisy source can be identified and
    turned off in config rather than debugged in code.
    """
    source: str
    company: str
    role: str
    url: str
    location: str = ""
    posted: date | None = None
    extra: dict = field(default_factory=dict)

    @property
    def key(self) -> str:
        return canonical_url(self.url)

    @property
    def company_key(self) -> str:
        return normalize_company(self.company)

    def to_json(self) -> dict:
        return {
            "source": self.source,
            "company": self.company,
            "role": self.role,
            "url": self.url,
            "location": self.location,
            "posted": self.posted.isoformat() if self.posted else None,
        }

    @classmethod
    def from_json(cls, d: dict) -> Listing:
        posted = d.get("posted")
        return cls(
            source=d.get("source", ""),
            company=d.get("company", ""),
            role=d.get("role", ""),
            url=d.get("url", ""),
            location=d.get("location", ""),
            posted=date.fromisoformat(posted) if posted else None,
        )
