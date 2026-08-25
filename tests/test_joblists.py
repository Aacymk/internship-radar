"""Parser tests use the real shapes these lists publish, because the
variation between them IS the difficulty."""
from radar.sources.joblists import parse_document

SIMPLE = """
| Company | Role | Location | Date | Link |
| --- | --- | --- | --- | --- |
| Stripe | SWE Intern | NYC | 2026-08-01 | [apply](https://s.com/1) |
| Figma | Design Eng Intern | SF | 2026-08-02 | [apply](https://f.com/2) |
"""

LINKED_NAMES = """
| Company | Position | Location | Apply |
| --- | --- | --- | --- |
| [**Google**](https://google.com) | SWE Intern | MTV | [apply](https://g.co/j/1) |
"""

BADGE_LINKS = """
| Company | Role | Location | Application |
| --- | --- | --- | --- |
| Meta | ML Intern | NYC | [![Apply](img/badge.png)](https://meta.com/j/9) |
"""

HTML_LINKS = """
| Company | Role | Location | Link |
| --- | --- | --- | --- |
| <a href="https://x.ai"><strong>xAI</strong></a> | AI Intern | SF | <a href="https://x.ai/careers/7">Apply</a> |
"""

CONTINUATION = """
| Company | Role | Location | Link |
| --- | --- | --- | --- |
| Oracle | SWE Intern | Austin | [apply](https://o.com/1) |
| ↳ | Data Intern | Seattle | [apply](https://o.com/2) |
"""

SECTIONED = """
## Software Engineering
| Company | Role | Location | Link |
| --- | --- | --- | --- |
| Stripe | SWE Intern | NYC | [apply](https://s.com/1) |

## Finance
| Company | Role | Location | Link |
| --- | --- | --- | --- |
| Goldman | Summer Analyst | NYC | [apply](https://gs.com/1) |
"""


class TestColumnMapping:
    def test_reads_a_plain_table(self):
        got = parse_document(SIMPLE, "t")
        assert [l.company for l in got] == ["Stripe", "Figma"]
        assert got[0].role == "SWE Intern"
        assert got[0].posted.isoformat() == "2026-08-01"

    def test_position_header_is_an_alias_for_role(self):
        assert parse_document(LINKED_NAMES, "t")[0].role == "SWE Intern"

    def test_unlinks_a_markdown_company_name(self):
        assert parse_document(LINKED_NAMES, "t")[0].company == "Google"

    def test_strips_html_from_a_company_name(self):
        assert parse_document(HTML_LINKS, "t")[0].company == "xAI"


class TestLinkExtraction:
    def test_takes_the_apply_link_not_the_badge_image(self):
        assert parse_document(BADGE_LINKS, "t")[0].url == "https://meta.com/j/9"

    def test_reads_an_html_href(self):
        assert parse_document(HTML_LINKS, "t")[0].url == "https://x.ai/careers/7"

    def test_rows_without_a_link_are_dropped(self):
        md = ("| Company | Role | Link |\n| --- | --- | --- |\n"
              "| Acme | SWE Intern | TBD |\n")
        assert parse_document(md, "t") == []


class TestRowSemantics:
    def test_continuation_marker_inherits_the_company_above(self):
        got = parse_document(CONTINUATION, "t")
        assert [l.company for l in got] == ["Oracle", "Oracle"]
        assert got[1].role == "Data Intern"

    def test_closed_postings_are_skipped(self):
        md = ("| Company | Role | Link |\n| --- | --- | --- |\n"
              "| Acme | SWE Intern \U0001F512 | [a](https://a.com/1) |\n")
        assert parse_document(md, "t") == []

    def test_separator_rows_are_not_listings(self):
        assert len(parse_document(SIMPLE, "t")) == 2


class TestSections:
    def test_only_requested_sections_are_parsed(self):
        got = parse_document(SECTIONED, "t", sections=["software engineering"])
        assert [l.company for l in got] == ["Stripe"]

    def test_no_section_filter_takes_everything(self):
        assert len(parse_document(SECTIONED, "t")) == 2


class TestRobustness:
    def test_empty_document(self):
        assert parse_document("", "t") == []

    def test_prose_without_tables(self):
        assert parse_document("# Hi\n\nSome text.\n", "t") == []

    def test_table_without_recognizable_headers_is_ignored(self):
        md = "| Foo | Bar |\n| --- | --- |\n| a | b |\n"
        assert parse_document(md, "t") == []

    def test_source_name_is_recorded(self):
        assert parse_document(SIMPLE, "my-list")[0].source == "my-list"


# SimplifyJobs' real shape: HTML tables, linked company names, badge-image
# apply links, and multi-location cells nested in <details>.
HTML_TABLE = """
<h2>Software Engineering</h2>
<table>
<thead><tr><th>Company</th><th>Role</th><th>Location</th><th>Application</th><th>Age</th></tr></thead>
<tbody>
<tr>
<td>&#128293; <strong><a href="https://simplify.jobs/c/SpaceX">SpaceX</a></strong></td>
<td>Software Engineering Intern/Co-op</td>
<td><details><summary><strong>3 locations</strong></summary>Palo Alto, CA<br>Irvine, CA<br>Hawthorne, CA</details></td>
<td><div align="center"><a href="https://boards.greenhouse.io/spacex/jobs/862?utm_source=Simplify&amp;ref=Simplify"><img src="https://i.imgur.com/x.png" alt="Apply"></a></div></td>
<td>0d</td>
</tr>
<tr>
<td>↳</td>
<td>Software Engineer Intern, Starlink</td>
<td>Redmond, WA</td>
<td><a href="https://boards.greenhouse.io/spacex/jobs/999">Apply</a></td>
<td>1d</td>
</tr>
</tbody>
</table>
"""


class TestHtmlTables:
    def test_parses_an_html_table(self):
        got = parse_document(HTML_TABLE, "SimplifyJobs")
        assert len(got) == 2

    def test_unwraps_a_linked_company_name(self):
        assert parse_document(HTML_TABLE, "s")[0].company == "SpaceX"

    def test_takes_the_apply_href_not_the_badge_image(self):
        url = parse_document(HTML_TABLE, "s")[0].url
        assert url.startswith("https://boards.greenhouse.io/spacex/jobs/862")
        assert "imgur" not in url

    def test_html_entities_are_decoded_in_urls(self):
        """&amp; in an href would otherwise change the identity key every
        run and can break the link itself."""
        assert "&amp;" not in parse_document(HTML_TABLE, "s")[0].url
        assert "&ref=Simplify" in parse_document(HTML_TABLE, "s")[0].url

    def test_nested_location_cells_do_not_run_together(self):
        loc = parse_document(HTML_TABLE, "s")[0].location
        assert "Palo Alto, CA" in loc
        assert "CAIrvine" not in loc

    def test_continuation_marker_works_in_html_too(self):
        assert parse_document(HTML_TABLE, "s")[1].company == "SpaceX"

    def test_section_filter_applies_to_html_tables(self):
        assert parse_document(HTML_TABLE, "s", sections=["finance"]) == []
        assert len(parse_document(HTML_TABLE, "s",
                                  sections=["software engineering"])) == 2

    def test_a_document_with_both_syntaxes_yields_both(self):
        assert len(parse_document(SIMPLE + HTML_TABLE, "s")) == 4
