"""The contract every source implements, and the HTTP layer beneath it.

A source is anything that can produce Listings: a curated job list, a
company's ATS, an RSS feed. Keeping them behind one small interface is what
lets the runner treat "5 GitHub lists and 30 job boards" as one stream, and
it is the seam a future user-defined source plugs into.

Sources MUST NOT raise. A single unreachable list must never take down a
run, so `collect()` traps exceptions and reports them as warnings — a
partial result is always more useful than a crash.

CONCURRENCY. A run is almost entirely time spent waiting on other people's
servers: ~35 sources, each one or more round trips, none of which depend on
each other. Fetched one at a time that wait is additive. `collect()` runs
every source at once instead and waits for all of them together, so a run
costs about as long as its SLOWEST source rather than the sum of all of
them.

Two constraints shape how that is done:

- Politeness. Unbounded concurrency would open ~35+ simultaneous
  connections, and Workday alone would fire 5 pages at one host at once.
  `MAX_CONCURRENT_REQUESTS` caps requests in flight so this stays a well
  behaved client of APIs that are being offered for free.
- Determinism. Concurrency must not make the OUTPUT depend on which server
  answered first: dedup keeps the first listing seen for a URL, so if
  completion order decided precedence, the recorded `source` of a posting
  would vary run to run and produce spurious diffs. Results are therefore
  reassembled in configured order, not completion order.
"""
from __future__ import annotations

import abc
import asyncio
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass

import httpx

from ..model import Listing

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
              "AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/126.0 Safari/537.36")
HEADERS = {"User-Agent": USER_AGENT}
TIMEOUT = 20

#: Requests allowed in flight at once, across all sources.
MAX_CONCURRENT_REQUESTS = 10

#: Wall-clock budget for ONE source, covering all of its requests.
#
# Fetching concurrently makes a run as slow as its slowest source, which
# turns one struggling upstream into the run's entire duration — a single
# board taking 80s is an 80s run, however fast the other 31 were. TIMEOUT
# above cannot prevent that: it bounds each individual request, so a source
# that pages, or that answers every request slowly but not slowly enough to
# trip it, blows past it. This bounds the source as a whole.
SOURCE_TIMEOUT = 25


class Source(abc.ABC):
    """Produces Listings. Subclasses implement `fetch`."""

    #: shown in reports so a noisy source can be identified and disabled
    name: str = "source"

    @abc.abstractmethod
    async def fetch(self) -> list[Listing]:
        """Return every listing this source currently advertises.

        Filtering by season or role is deliberately NOT done here. Sources
        report what they see; the runner decides what is worth alerting on.
        """

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.name}>"


# --------------------------------------------------------------- http layer

@dataclass
class _Http:
    """The shared client and concurrency limit for one run."""
    client: httpx.AsyncClient
    semaphore: asyncio.Semaphore


# Set for the duration of a `session()`. A ContextVar rather than a global so
# sources can stay ignorant of it: they call `get_json(url)` and the ambient
# session supplies the pooled connection and the rate limit.
_http: ContextVar[_Http | None] = ContextVar("_http", default=None)


@asynccontextmanager
async def session(limit: int = MAX_CONCURRENT_REQUESTS):
    """Open one pooled HTTP client for every source in a run to share.

    Sharing matters: a client reuses connections, so repeat calls to the
    same host (Workday's five pages, Greenhouse's twenty boards) skip
    reconnecting and re-doing the TLS handshake every time.
    """
    client = httpx.AsyncClient(
        headers=HEADERS,
        timeout=TIMEOUT,
        # httpx does NOT follow redirects by default; requests did, and the
        # raw.githubusercontent and ATS URLs rely on it.
        follow_redirects=True,
        limits=httpx.Limits(max_connections=limit),
    )
    token = _http.set(_Http(client, asyncio.Semaphore(limit)))
    try:
        yield
    finally:
        _http.reset(token)
        await client.aclose()


async def request(method: str, url: str, **kwargs) -> httpx.Response:
    """One HTTP call against the ambient session, respecting its limit.

    Outside a `session()` — a direct unit test, say — this opens a throwaway
    client rather than failing, so a Source is usable on its own.
    """
    http = _http.get()
    if http is None:
        async with session():
            return await request(method, url, **kwargs)
    async with http.semaphore:
        r = await http.client.request(method, url, **kwargs)
    r.raise_for_status()
    return r


async def get_json(url: str, **kwargs):
    r = await request("GET", url, headers={"Accept": "application/json"},
                      **kwargs)
    return r.json()


async def post_json(url: str, payload: dict, **kwargs):
    r = await request("POST", url, json=payload,
                      headers={"Accept": "application/json",
                               "Content-Type": "application/json"},
                      **kwargs)
    return r.json()


async def get_text(url: str, **kwargs) -> str:
    r = await request("GET", url, **kwargs)
    return r.text


# ------------------------------------------------------------------ collect

async def _fetch_one(src: Source) -> tuple[list[Listing], str | None]:
    """Run one source, under a deadline. Never raises: warns instead.

    Trapping here rather than at the gather() call is deliberate — one
    source's failure must not cancel the ~34 already in flight beside it.
    """
    try:
        async with asyncio.timeout(SOURCE_TIMEOUT):
            return await src.fetch(), None
    except TimeoutError:
        # Losing one source is a smaller cost than a run slow enough that
        # the scheduled job starts overlapping itself.
        return [], f"{src.name}: too slow (gave up after {SOURCE_TIMEOUT}s)"
    except Exception as e:  # noqa: BLE001 - see module docstring
        return [], f"{src.name}: unavailable ({type(e).__name__}: {e})"


async def collect(sources: list[Source],
                  log=print) -> tuple[list[Listing], list[str]]:
    """Run every source concurrently, tolerating failures.

    Returns (listings, warnings). Duplicates are collapsed by canonical URL,
    because the same posting routinely appears on several lists with
    different tracking params.
    """
    async with session():
        results = await asyncio.gather(*(_fetch_one(s) for s in sources))

    listings: dict[str, Listing] = {}
    warnings: list[str] = []
    # gather() returns results in the order the tasks were PASSED, not the
    # order they finished, so this loop — and therefore which source wins a
    # duplicate URL — is identical to the old sequential behaviour.
    for src, (found, warning) in zip(sources, results, strict=True):
        if warning:
            warnings.append(warning)
            log(f"    {warning} - skipped")
            continue
        new = 0
        for listing in found:
            if not listing.url:
                continue
            if listing.key not in listings:
                listings[listing.key] = listing
                new += 1
        log(f"    {src.name}: {len(found)} listing(s), {new} unique")
    return list(listings.values()), warnings
