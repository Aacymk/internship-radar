"""Tests for the concurrent fetch layer.

Two things are being protected here, and they are the two things
concurrency is most likely to break:

1. ISOLATION - one source failing must not take down the run. That was true
   of the old sequential loop by construction (a try/except per iteration);
   under asyncio.gather it is a property that has to be tested for.
2. DETERMINISM - dedup keeps the first listing seen for a URL, so if
   completion order decided precedence the recorded source of a posting
   would change from run to run. Results must follow configured order, not
   whoever answered first.
"""
import asyncio
import json

import httpx
import pytest
import respx

from radar.model import Listing
from radar.sources import base
from radar.sources.base import Source, collect, session
from radar.sources.boards import GreenhouseBoard, LeverBoard, WorkdayBoard
from radar.sources.joblists import JobListSource


def quiet(*args, **kwargs):
    """A `log` that says nothing, so tests do not print."""


class FakeSource(Source):
    """A source with no network, so timing and ordering are controllable."""

    def __init__(self, name, listings=(), delay=0.0, fail=None):
        self.name = name
        self.listings = list(listings)
        self.delay = delay
        self.fail = fail

    async def fetch(self):
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail:
            raise self.fail
        return self.listings


def listing(url, company="Acme", source="s", role="SWE Intern"):
    return Listing(source=source, company=company, role=role, url=url)


class TestFailureIsolation:
    async def test_one_dead_source_does_not_lose_the_others(self):
        got, warnings = await collect([
            FakeSource("good-1", [listing("https://a.com/1")]),
            FakeSource("dead", fail=httpx.ConnectError("boom")),
            FakeSource("good-2", [listing("https://b.com/2")]),
        ], log=quiet)
        assert sorted(l.url for l in got) == ["https://a.com/1",
                                              "https://b.com/2"]
        assert len(warnings) == 1
        assert "dead" in warnings[0] and "ConnectError" in warnings[0]

    async def test_every_source_failing_is_still_not_a_crash(self):
        got, warnings = await collect([
            FakeSource("a", fail=ValueError("nope")),
            FakeSource("b", fail=httpx.ReadTimeout("slow")),
        ], log=quiet)
        assert got == []
        assert len(warnings) == 2

    async def test_a_slow_failure_does_not_cancel_work_in_flight(self):
        """The failing source finishes LAST, after the others are done."""
        got, warnings = await collect([
            FakeSource("dead", delay=0.05, fail=httpx.ConnectError("boom")),
            FakeSource("good", [listing("https://a.com/1")]),
        ], log=quiet)
        assert [l.url for l in got] == ["https://a.com/1"]
        assert len(warnings) == 1


class TestSourceDeadline:
    """Concurrency makes a run as slow as its slowest source, so one
    struggling upstream must not become the run's duration."""

    async def test_a_hanging_source_is_abandoned(self, monkeypatch):
        monkeypatch.setattr(base, "SOURCE_TIMEOUT", 0.05)
        got, warnings = await collect([
            FakeSource("hangs", [listing("https://slow.com/1")], delay=10),
            FakeSource("fine", [listing("https://a.com/1")]),
        ], log=quiet)
        assert [l.url for l in got] == ["https://a.com/1"]
        assert "too slow" in warnings[0]

    async def test_the_deadline_bounds_the_run_not_just_the_source(
            self, monkeypatch):
        monkeypatch.setattr(base, "SOURCE_TIMEOUT", 0.05)
        started = asyncio.get_running_loop().time()
        await collect([FakeSource("hangs", delay=10)], log=quiet)
        assert asyncio.get_running_loop().time() - started < 1.0


class TestDeterminism:
    async def test_first_configured_source_wins_a_duplicate_url(self):
        """Even when it is the SLOWEST source to answer.

        Completion order here is the reverse of configured order, which is
        exactly the case that would break if precedence were decided by
        whoever returned first.
        """
        got, _ = await collect([
            FakeSource("slow-first", [listing("https://x.com/1",
                                              source="slow-first")],
                       delay=0.05),
            FakeSource("fast-second", [listing("https://x.com/1",
                                               source="fast-second")]),
        ], log=quiet)
        assert len(got) == 1
        assert got[0].source == "slow-first"

    async def test_tracking_params_do_not_defeat_dedup(self):
        got, _ = await collect([
            FakeSource("a", [listing("https://x.com/1")]),
            FakeSource("b", [listing("https://x.com/1?utm_source=Simplify")]),
        ], log=quiet)
        assert len(got) == 1

    async def test_listings_without_a_url_are_dropped(self):
        got, _ = await collect([FakeSource("a", [listing("")])], log=quiet)
        assert got == []


class TestConcurrency:
    async def test_sources_run_at_the_same_time_not_one_after_another(self):
        """The point of the whole exercise.

        Five sources that each wait 100ms take ~500ms sequentially and
        ~100ms concurrently. The 300ms bound is loose enough not to be
        flaky on a slow machine but far below the sequential cost.
        """
        sources = [FakeSource(f"s{i}", [listing(f"https://x.com/{i}")],
                              delay=0.1) for i in range(5)]
        started = asyncio.get_running_loop().time()
        got, _ = await collect(sources, log=quiet)
        elapsed = asyncio.get_running_loop().time() - started

        assert len(got) == 5
        assert elapsed < 0.3, f"took {elapsed:.2f}s - sources ran serially"

    @respx.mock
    async def test_requests_in_flight_stay_under_the_limit(self):
        """The semaphore is what keeps this a polite client."""
        in_flight = 0
        peak = 0

        async def slow_response(request):
            nonlocal in_flight, peak
            in_flight += 1
            peak = max(peak, in_flight)
            await asyncio.sleep(0.02)
            in_flight -= 1
            return httpx.Response(200, json={"jobs": []})

        respx.get(url__startswith="https://boards-api.greenhouse.io").mock(
            side_effect=slow_response)

        sources = [GreenhouseBoard(f"C{i}", f"c{i}") for i in range(25)]
        await collect(sources, log=quiet)

        from radar.sources.base import MAX_CONCURRENT_REQUESTS
        assert peak <= MAX_CONCURRENT_REQUESTS
        assert peak > 1, "nothing ran concurrently at all"


class TestBoardsOverHttp:
    @respx.mock
    async def test_greenhouse_board_is_parsed(self):
        respx.get("https://boards-api.greenhouse.io/v1/boards/stripe/jobs"
                  ).mock(return_value=httpx.Response(200, json={"jobs": [
                      {"title": "Software Engineering Intern",
                       "absolute_url": "https://stripe.com/j/1",
                       "location": {"name": "NYC"},
                       "first_published": "2026-08-01T00:00:00Z"},
                  ]}))
        got = await GreenhouseBoard("Stripe", "stripe").fetch()
        assert len(got) == 1
        assert got[0].company == "Stripe"
        assert got[0].location == "NYC"
        assert got[0].posted.isoformat() == "2026-08-01"

    @respx.mock
    async def test_non_internships_are_filtered_out_of_a_board(self):
        """A job board carries every role, not just internships."""
        respx.get(url__startswith="https://api.lever.co").mock(
            return_value=httpx.Response(200, json=[
                {"text": "Software Engineering Intern",
                 "hostedUrl": "https://l.co/1", "categories": {}},
                # the classic false positive: "internal" contains "intern"
                {"text": "VP, Internal Communications",
                 "hostedUrl": "https://l.co/2", "categories": {}},
            ]))
        got = await LeverBoard("Palantir", "palantir").fetch()
        assert [l.url for l in got] == ["https://l.co/1"]

    @respx.mock
    async def test_an_http_error_surfaces_as_a_warning_not_a_crash(self):
        respx.get(url__startswith="https://boards-api.greenhouse.io").mock(
            return_value=httpx.Response(500))
        got, warnings = await collect(
            [GreenhouseBoard("Stripe", "stripe")], log=quiet)
        assert got == []
        assert "Stripe" in warnings[0]


class TestWorkdayPagination:
    def _page(self, offset, total, n):
        return {"total": total, "jobPostings": [
            {"title": "SWE Intern", "externalPath": f"/job/{offset + i}"}
            for i in range(n)]}

    @respx.mock
    async def test_all_pages_are_collected(self):
        """45 postings = 3 pages of 20. Pages 2 and 3 go out together."""
        offsets = []

        def respond(request):
            offset = json.loads(request.content)["offset"]
            offsets.append(offset)
            return httpx.Response(
                200, json=self._page(offset, 45, min(20, 45 - offset)))

        respx.post(url__startswith="https://nvidia.wd5.myworkdayjobs.com"
                   ).mock(side_effect=respond)

        board = WorkdayBoard("NVIDIA", "nvidia:5:NVIDIAExternalCareerSite")
        got = await board.fetch()
        assert len(got) == 45
        assert sorted(offsets) == [0, 20, 40]

    @respx.mock
    async def test_page_cap_is_respected(self):
        """A board reporting thousands of hits must not page forever."""
        def respond(request):
            offset = json.loads(request.content)["offset"]
            return httpx.Response(200, json=self._page(offset, 10_000, 20))

        route = respx.post(
            url__startswith="https://nvidia.wd5.myworkdayjobs.com"
        ).mock(side_effect=respond)

        board = WorkdayBoard("NVIDIA", "nvidia:5:NVIDIAExternalCareerSite")
        got = await board.fetch()
        from radar.sources.boards import MAX_WORKDAY_PAGES, WORKDAY_PAGE_SIZE
        assert route.call_count == MAX_WORKDAY_PAGES
        assert len(got) == MAX_WORKDAY_PAGES * WORKDAY_PAGE_SIZE

    @respx.mock
    async def test_a_board_that_reports_no_total_still_pages(self):
        """Falls back to walking until a short page comes back."""
        def respond(request):
            offset = json.loads(request.content)["offset"]
            n = 20 if offset == 0 else 5
            return httpx.Response(200, json={
                "jobPostings": [{"title": "SWE Intern",
                                 "externalPath": f"/job/{offset + i}"}
                                for i in range(n)]})

        respx.post(url__startswith="https://ebay.wd5.myworkdayjobs.com"
                   ).mock(side_effect=respond)

        got = await WorkdayBoard("eBay", "ebay:5:apply").fetch()
        assert len(got) == 25

    def test_a_malformed_board_id_is_rejected_with_a_readable_message(self):
        with pytest.raises(ValueError, match="tenant:12:Site_Name"):
            WorkdayBoard("Bad", "just-a-slug")


class TestJobListOverHttp:
    @respx.mock
    async def test_a_list_is_fetched_and_parsed(self):
        respx.get("https://example.com/README.md").mock(
            return_value=httpx.Response(200, text=(
                "| Company | Role | Location | Link |\n"
                "| --- | --- | --- | --- |\n"
                "| Stripe | SWE Intern | NYC | [apply](https://s.com/1) |\n"
            )))
        got = await JobListSource("demo", "https://example.com/README.md"
                                  ).fetch()
        assert [l.company for l in got] == ["Stripe"]
        assert got[0].source == "demo"

    @respx.mock
    async def test_a_redirect_is_followed(self):
        """httpx does not follow redirects by default; requests did."""
        respx.get("https://example.com/a").mock(
            return_value=httpx.Response(301, headers={"Location":
                                                      "/b"}))
        respx.get("https://example.com/b").mock(
            return_value=httpx.Response(200, text=(
                "| Company | Role | Link |\n| --- | --- | --- |\n"
                "| Figma | SWE Intern | [apply](https://f.com/1) |\n")))
        got = await JobListSource("demo", "https://example.com/a").fetch()
        assert [l.company for l in got] == ["Figma"]


class TestSessionLifecycle:
    @respx.mock
    async def test_a_source_works_outside_a_session(self):
        """A unit test calling fetch() directly should not have to set up
        the ambient client."""
        respx.get(url__startswith="https://boards-api.greenhouse.io").mock(
            return_value=httpx.Response(200, json={"jobs": []}))
        assert await GreenhouseBoard("Stripe", "stripe").fetch() == []

    async def test_the_client_is_closed_when_the_session_ends(self):
        from radar.sources import base
        async with session():
            http = base._http.get()
            assert http is not None
        assert http.client.is_closed
        assert base._http.get() is None
