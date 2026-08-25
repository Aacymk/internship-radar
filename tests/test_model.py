from datetime import date

from radar.model import (Listing, canonical_url, looks_like_internship,
                         normalize_company, season_pair)


class TestCanonicalUrl:
    """Identity keys must survive the noise real job lists add to links."""

    def test_strips_tracking_params(self):
        assert (canonical_url("https://x.com/j/1?utm_source=github&utm_medium=b")
                == "https://x.com/j/1")

    def test_keeps_meaningful_params(self):
        assert (canonical_url("https://x.com/search?jobId=42&utm_source=gh")
                == "https://x.com/search?jobId=42")

    def test_same_posting_from_two_lists_collapses(self):
        a = "https://jobs.apple.com/details/200664785?utm_source=list-a"
        b = "https://jobs.apple.com/details/200664785/?ref=list-b"
        assert canonical_url(a) == canonical_url(b)

    def test_ignores_case_and_fragment(self):
        assert (canonical_url("HTTPS://Jobs.Example.COM/a#apply")
                == "https://jobs.example.com/a")

    def test_distinct_jobs_stay_distinct(self):
        assert canonical_url("https://x.com/j/1") != canonical_url(
            "https://x.com/j/2")

    def test_tolerates_junk(self):
        assert canonical_url("") == ""
        assert canonical_url("not a url") == "not a url"


class TestNormalizeCompany:
    def test_collapses_spacing_and_case(self):
        assert (normalize_company("JP Morgan Chase")
                == normalize_company("JPMorganChase"))

    def test_distinct_companies_stay_distinct(self):
        assert normalize_company("Meta") != normalize_company("Meterian")


class TestSeasonPair:
    """The graduation-year trap: the most expensive mistake this kind of
    tracker makes is reporting a role a full year early."""

    def test_matches_adjacent_season_and_year(self):
        assert season_pair("Summer 2027 SWE Internship", "Summer", "2027")

    def test_matches_reversed_order(self):
        assert season_pair("2027 Summer Intern", "Summer", "2027")

    def test_rejects_graduation_year(self):
        text = "Summer 2026 internship for students graduating in 2027"
        assert not season_pair(text, "Summer", "2027")

    def test_rejects_class_of_phrasing(self):
        assert not season_pair("Summer 2026 Intern, Class of 2027",
                               "Summer", "2027")

    def test_handles_empty(self):
        assert not season_pair("", "Summer", "2027")


class TestListing:
    def test_round_trips_through_json(self):
        original = Listing(source="s", company="Acme", role="SWE Intern",
                           url="https://x.com/1", location="NYC",
                           posted=date(2026, 8, 3))
        assert Listing.from_json(original.to_json()) == original

    def test_key_is_canonical(self):
        listing = Listing(source="s", company="A", role="R",
                          url="https://x.com/1?utm_source=q")
        assert listing.key == "https://x.com/1"


class TestLooksLikeInternship:
    """Applied to company job boards only. Job lists are passed through
    untouched, so this must never become a relevance filter."""

    def test_accepts_ordinary_internship_titles(self):
        for title in ["Software Engineer Intern",
                      "Summer 2027 Internship - Backend",
                      "Data Science Interns",
                      "Engineering Co-op",
                      "Software Engineering Coop - Fall",
                      "Apprentice Developer",
                      "Graduate Trainee, Platform",
                      "Student Researcher, ML",
                      "Summer Analyst - Technology"]:
            assert looks_like_internship(title), title

    def test_rejects_the_workday_substring_trap(self):
        """"intern" is a substring of these, which is the actual bug: a
        Workday search for "intern" returns all of them."""
        for title in ["Vice President, Global Partner Marketing",
                      "Manager, Internal Audit",
                      "Director, International Markets",
                      "Internal Communications Lead",
                      "Senior Internal Auditor",
                      "Principal Software Engineer",
                      "Data Scientist II"]:
            assert not looks_like_internship(title), title

    def test_does_not_judge_relevance(self):
        """Non-software internships still pass: deciding what is relevant
        is the reader's job, not the filter's."""
        assert looks_like_internship("Marketing Intern")
        assert looks_like_internship("Mechanical Engineering Intern")

    def test_handles_empty(self):
        assert not looks_like_internship("")
        assert not looks_like_internship(None)
