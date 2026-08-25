from datetime import date

from radar.model import Listing
from radar.report import (END_MARKER, START_MARKER, digest, readme_block,
                          summary_line, update_readme)


def listing(company="Acme", role="SWE Intern", url="https://x.com/1"):
    return Listing(source="s", company=company, role=role, url=url,
                   location="NYC", posted=date(2026, 8, 3))


class TestSummaryLine:
    def test_names_the_companies(self):
        assert "Acme" in summary_line([listing()])

    def test_singular_and_plural(self):
        assert "1 new internship posting:" in summary_line([listing()])
        assert "2 new internship postings:" in summary_line(
            [listing(url="https://x.com/1"), listing(url="https://x.com/2")])

    def test_truncates_a_long_company_list(self):
        many = [listing(company=f"C{i}", url=f"https://x.com/{i}")
                for i in range(6)]
        assert "+3 more" in summary_line(many)

    def test_handles_nothing_new(self):
        assert summary_line([]) == "No new internship postings"


class TestDigest:
    def test_lists_the_new_postings(self):
        body = digest([listing()], "Summer 2027", date(2026, 8, 3))
        assert "Acme" in body and "https://x.com/1" in body

    def test_flags_postings_that_do_not_state_a_season(self):
        body = digest([listing()], "Summer 2027", date(2026, 8, 3))
        assert "do not state a season" in body

    def test_does_not_flag_when_the_season_is_explicit(self):
        explicit = Listing(source="s", company="A",
                           role="Summer 2027 SWE Intern", url="https://x.com/1")
        body = digest([explicit], "Summer 2027", date(2026, 8, 3))
        assert "do not state a season" not in body

    def test_empty_digest_is_still_readable(self):
        assert "No new" in digest([], "Summer 2027", date(2026, 8, 3))

    def test_pipes_in_a_role_do_not_break_the_table(self):
        odd = listing(role="SWE | Intern")
        body = digest([odd], "Summer 2027", date(2026, 8, 3))
        assert r"SWE \| Intern" in body


class TestReadme:
    def test_block_is_replaced_not_appended(self):
        readme = f"# Title\n\n{START_MARKER}\nold\n{END_MARKER}\n\n## Footer\n"
        block = f"{START_MARKER}\nnew\n{END_MARKER}"
        out = update_readme(readme, block)
        assert "old" not in out
        assert "new" in out
        assert out.count(START_MARKER) == 1

    def test_surrounding_content_is_preserved(self):
        readme = f"# Title\n\n{START_MARKER}\nold\n{END_MARKER}\n\n## Footer\n"
        out = update_readme(readme, f"{START_MARKER}\nnew\n{END_MARKER}")
        assert "# Title" in out and "## Footer" in out

    def test_block_is_appended_when_markers_are_absent(self):
        out = update_readme("# Title\n", f"{START_MARKER}\nnew\n{END_MARKER}")
        assert "# Title" in out and "new" in out

    def test_repeated_updates_are_stable(self):
        readme = f"# T\n\n{START_MARKER}\na\n{END_MARKER}\n"
        for text in ("b", "c", "d"):
            readme = update_readme(readme, f"{START_MARKER}\n{text}\n{END_MARKER}")
        assert readme.count(START_MARKER) == 1
        assert "d" in readme and "a" not in readme

    def test_block_reports_counts(self):
        block = readme_block([listing()], [listing()], "Summer 2027",
                             date(2026, 8, 3))
        assert "Tracking 1 open" in block and "1 new since the last check" in block

    def test_empty_state_says_so(self):
        block = readme_block([], [], "Summer 2027", date(2026, 8, 3))
        assert "Nothing open yet" in block

    def test_source_warnings_surface(self):
        block = readme_block([], [], "Summer 2027", date(2026, 8, 3),
                             warnings=["listA: unavailable"])
        assert "listA: unavailable" in block
