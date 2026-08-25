"""Company job boards, queried through their own public JSON APIs.

Greenhouse, Lever, Ashby, and Workday all expose the board behind a
company's careers page as unauthenticated JSON. No keys, no browser, no
scraping of rendered HTML — this is a company telling you directly what it
is hiring for, and it is the highest-trust signal in the system.

WORKDAY is the one that needs explaining, because most large employers use
it and its API is not obvious:

    POST /wday/cxs/{tenant}/{site}/jobs
    {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": "intern"}

`searchText` behaves badly in BOTH directions, which is why the query here
is broad and the filtering happens locally:

- Too narrow a query silently misses roles. "software intern 2027" only
  matches postings containing all three terms, so an internship whose title
  is just "Software Engineering Intern" never comes back.
- Too broad a query over-matches, because it is substring-based rather than
  word-based. "intern" matches "internal" and "international", which is how
  a search for internships returns "Vice President, Global Partner
  Marketing". `looks_like_internship` is what rejects those.

Location is the other Workday subtlety. The list endpoint reports
`locationsText`, which degrades to "2 Locations" when a posting spans
several — useless for filtering. The DETAIL endpoint returns a structured
`country` object, which is reliable. That distinction is what separates a
genuine US opening from the same job req posted only in another region.
"""
from __future__ import annotations

import abc
import asyncio
import html
import re
from datetime import datetime

import httpx

from ..model import Listing, looks_like_internship
from .base import Source, get_json, post_json

MAX_WORKDAY_PAGES = 5
WORKDAY_PAGE_SIZE = 20


class JobBoard(Source):
    """A company's own board. Subclasses implement `fetch_postings`.

    Unlike a curated internship list — which contains nothing BUT internships
    and is therefore passed through untouched — a job board carries every
    role the company is hiring for. `looks_like_internship` is the one gate
    applied here, and it only asks whether a posting is an internship at all,
    never whether it is relevant. See radar/model.py for why that line is
    drawn where it is.
    """

    @abc.abstractmethod
    async def fetch_postings(self) -> list[Listing]:
        """Every posting the board returns, unfiltered."""

    async def fetch(self) -> list[Listing]:
        return [l for l in await self.fetch_postings()
                if looks_like_internship(l.role)]


def _strip_html(s: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", html.unescape(s or "")).split())


def _iso_date(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).date()
    except (ValueError, TypeError):
        return None


class GreenhouseBoard(JobBoard):
    kind = "greenhouse"

    def __init__(self, company: str, slug: str):
        self.company, self.slug = company, slug
        self.name = f"{company} (greenhouse)"

    async def fetch_postings(self) -> list[Listing]:
        data = await get_json("https://boards-api.greenhouse.io/v1/boards/"
                              f"{self.slug}/jobs")
        return [Listing(
            source=self.name, company=self.company, role=j.get("title", ""),
            url=j.get("absolute_url", ""),
            location=(j.get("location") or {}).get("name", ""),
            posted=_iso_date(j.get("first_published")),
        ) for j in data.get("jobs", [])]


class LeverBoard(JobBoard):
    kind = "lever"

    def __init__(self, company: str, slug: str):
        self.company, self.slug = company, slug
        self.name = f"{company} (lever)"

    async def fetch_postings(self) -> list[Listing]:
        data = await get_json(f"https://api.lever.co/v0/postings/{self.slug}"
                              "?mode=json")
        out = []
        for p in data:
            posted = None
            if p.get("createdAt"):
                posted = datetime.fromtimestamp(p["createdAt"] / 1000).date()
            out.append(Listing(
                source=self.name, company=self.company,
                role=p.get("text", ""), url=p.get("hostedUrl", ""),
                location=(p.get("categories") or {}).get("location", ""),
                posted=posted,
            ))
        return out


class AshbyBoard(JobBoard):
    kind = "ashby"

    def __init__(self, company: str, slug: str):
        self.company, self.slug = company, slug
        self.name = f"{company} (ashby)"

    async def fetch_postings(self) -> list[Listing]:
        data = await get_json("https://api.ashbyhq.com/posting-api/job-board/"
                              f"{self.slug}")
        return [Listing(
            source=self.name, company=self.company, role=j.get("title", ""),
            url=j.get("jobUrl") or j.get("applyUrl") or "",
            location=j.get("location", ""),
            posted=_iso_date(j.get("publishedAt")),
        ) for j in data.get("jobs", [])]


class WorkdayBoard(JobBoard):
    """Workday. `board_id` is "tenant:N:site", read off the careers URL:

        https://salesforce.wd12.myworkdayjobs.com/External_Career_Site
                 tenant^     ^N   site^

    so the board_id is "salesforce:12:External_Career_Site".
    """
    kind = "workday"

    def __init__(self, company: str, board_id: str, search: str = "intern"):
        self.company = company
        try:
            tenant, n, site = str(board_id).split(":")
        except ValueError as e:
            raise ValueError(
                f'{company}: workday board_id should look like '
                f'"tenant:12:Site_Name", got "{board_id}"') from e
        self.tenant, self.n, self.site = tenant, n, site
        self.search = search
        self.base = f"https://{tenant}.wd{n}.myworkdayjobs.com"
        self.name = f"{company} (workday)"

    async def _post(self, offset: int) -> dict:
        return await post_json(
            f"{self.base}/wday/cxs/{self.tenant}/{self.site}/jobs",
            {"appliedFacets": {}, "limit": WORKDAY_PAGE_SIZE,
             "offset": offset, "searchText": self.search})

    def _listings_in(self, data: dict) -> list[Listing]:
        out = []
        for p in data.get("jobPostings") or []:
            path = p.get("externalPath", "")
            out.append(Listing(
                source=self.name, company=self.company,
                role=p.get("title", ""),
                url=f"{self.base}/en-US/{self.site}{path}",
                location=p.get("locationsText", ""),
                extra={"detail_path": path},
            ))
        return out

    async def fetch_postings(self) -> list[Listing]:
        """Page 1 first, then the rest at once.

        Workday paginates by offset and reports a `total`, so after one
        round trip the remaining offsets are all known — there is no reason
        to walk them one at a time. This is the only source whose pages have
        an ordering constraint at all, and it only applies to the first one.
        """
        first = await self._post(0)
        out = self._listings_in(first)

        total = first.get("total") or 0
        if not total:
            # No total reported: fall back to walking until a short page.
            page = 1
            while len(out) and page < MAX_WORKDAY_PAGES:
                data = await self._post(page * WORKDAY_PAGE_SIZE)
                found = self._listings_in(data)
                out += found
                if len(found) < WORKDAY_PAGE_SIZE:
                    break
                page += 1
            return out

        pages = min(MAX_WORKDAY_PAGES,
                    -(-total // WORKDAY_PAGE_SIZE))  # ceil division
        if pages > 1:
            rest = await asyncio.gather(
                *(self._post(p * WORKDAY_PAGE_SIZE)
                  for p in range(1, pages)))
            for data in rest:
                out += self._listings_in(data)
        return out

    async def country_of(self, listing: Listing) -> str:
        """Structured country for one posting, or "" if unavailable.

        Only the detail endpoint has this; see the module docstring for why
        `locationsText` cannot be trusted for multi-location postings.
        """
        path = listing.extra.get("detail_path")
        if not path:
            return ""
        try:
            data = await get_json(f"{self.base}/wday/cxs/{self.tenant}"
                                  f"/{self.site}{path}")
        except (httpx.HTTPError, ValueError):
            return ""
        info = data.get("jobPostingInfo") or {}
        country = info.get("country") or {}
        return country.get("descriptor", "") or ""


_KINDS = {
    "greenhouse": GreenhouseBoard,
    "lever": LeverBoard,
    "ashby": AshbyBoard,
    "workday": WorkdayBoard,
}


def build(company_name: str, kind: str, board_id) -> Source | None:
    """Instantiate a board source, or None if the config is unusable."""
    cls = _KINDS.get((kind or "").lower())
    if not cls or not board_id:
        return None
    try:
        return cls(company_name, board_id)
    except ValueError as e:
        print(f"    {e}")
        return None


def from_config(companies) -> list[Source]:
    out = []
    for c in companies:
        if not c.board:
            continue
        src = build(c.name, c.board[0], c.board[1])
        if src:
            out.append(src)
    return out
