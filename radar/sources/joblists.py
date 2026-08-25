"""Curated GitHub internship lists (vanshb03, speedyapply, SimplifyJobs...).

These lists are maintained by bots and volunteers and are usually the first
place a new posting shows up — often hours before it is discoverable any
other way. Each publishes its postings as one big README table.

The parser is COLUMN-AWARE rather than positional: it reads each table's
header row and maps columns by name. That is what makes one parser work
across lists that disagree on everything else — "Role" vs "Position"
headers, `[apply](url)` vs `<a href>` vs `[![badge](img)](url)` links,
`[**Company**](url)` linked names, and the "arrow" marker some lists use to
mean "same company as the row above".

Two table SYNTAXES are supported, because the lists split roughly evenly
between them: markdown pipe tables, and raw HTML `<table>` (which is what
SimplifyJobs — the most widely used list — publishes). Both are reduced to
the same "row of cells" stream and then run through identical column
mapping, so the format a list happens to use never reaches the rest of the
system.

Adding a list is therefore a config edit, not a code change, as long as it
publishes a table with recognizable headers.
"""
from __future__ import annotations

import html as html_mod
import re
from datetime import datetime

from bs4 import BeautifulSoup

from ..model import Listing
from .base import Source, get_text

# Header text -> canonical field. Substring match, case-insensitive, so
# "Company Name" and "Apply Link" resolve without needing exact strings.
# Note "age" (e.g. "7d") maps to nothing: it is relative, so it would
# silently drift every run.
_HEADER_ALIASES = {
    "company": ["company"],
    "role": ["role", "position", "title"],
    "location": ["location"],
    "link": ["apply", "application", "link", "posting"],
    "date": ["added", "posted", "date"],
}

_HREF_RE = re.compile(r'href=["\']?(https?://[^"\'>\s]+)', re.I)
_URL_RE = re.compile(r"\((https?://[^)\s]+)\)")
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_MARKER_RE = re.compile("[\U0001F000-\U0001FAFF☀-➿️]")
_CLOSED_MARKER = "\U0001F512"  # lock emoji; several lists use it for "closed"
_CONTINUATION = {"↳", "»", "—", "same", ""}


def _parse_posted(cell: str):
    cell = cell.strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(cell, fmt).date()
        except ValueError:
            continue
    # "Jul 09" style, no year -> assume the current one
    for fmt in ("%b %d", "%B %d"):
        try:
            return datetime.strptime(cell, fmt).replace(
                year=datetime.now().year).date()
        except ValueError:
            continue
    return None


def _header_map(cells: list[str]) -> dict:
    out: dict = {}
    for i, c in enumerate(cells):
        c = c.strip().lower()
        for canon, aliases in _HEADER_ALIASES.items():
            if canon not in out and any(a in c for a in aliases):
                out[canon] = i
    return out


def _clean_text(cell: str) -> str:
    # Tags collapse to a SPACE, not to nothing. HTML cells nest their
    # content — <summary>3 locations</summary>Palo Alto, CA<br>Irvine, CA —
    # and deleting the tags outright welds the words into "CAIrvine".
    s = _HTML_TAG_RE.sub(" ", cell or "")
    s = _MD_LINK_RE.sub(r"\1", s)          # [**Google**](url) -> **Google**
    s = s.replace("*", "").replace("`", "")
    s = html_mod.unescape(s)
    return " ".join(_MARKER_RE.sub("", s).split())


def _extract_url(cell: str) -> str:
    """The apply link: an HTML href, else the LAST markdown (url) so that
    `[![badge](image)](applylink)` yields the apply link, not the badge."""
    m = _HREF_RE.search(cell or "")
    if m:
        return html_mod.unescape(m.group(1))
    urls = _URL_RE.findall(cell or "")
    return html_mod.unescape(urls[-1]) if urls else ""


def _section_name(line: str):
    """Section title from a <summary> or markdown heading, else None.

    Lists that mix categories (finance, hardware, software) into one README
    are restricted to chosen sections via the `sections` option.
    """
    if "<summary" in line.lower():
        return _HTML_TAG_RE.sub("", line).strip().lower()
    if line.startswith("#"):
        return line.lstrip("#").strip().lower()
    return None


def _iter_markdown_rows(text: str):
    """Yield (section, cells, raw) for every markdown pipe-table row."""
    section = ""
    in_table = False
    for line in text.splitlines():
        line = line.strip()
        sec = _section_name(line)
        if sec is not None:
            section = sec
        if not line.startswith("|"):
            if in_table:
                yield section, None, None  # table ended
                in_table = False
            continue
        in_table = True
        cells = [c.strip() for c in line.strip("|").split("|")]
        if all(set(c) <= {"-", " ", ":"} for c in cells if c):
            continue  # |---|---| separator carries no data
        yield section, cells, line


def _iter_html_rows(text: str):
    """Yield (section, cells, raw) for every <table> row.

    Cells keep their inner HTML, so the same link/date/name extractors used
    for markdown work unchanged on `<a href>` and `<img>` markup.
    """
    if "<table" not in text.lower():
        return
    soup = BeautifulSoup(text, "html.parser")
    for table in soup.find_all("table"):
        # nearest preceding heading or <summary> names the section
        section = ""
        for prev in table.find_all_previous(
                ["h1", "h2", "h3", "h4", "summary"]):
            section = prev.get_text(" ", strip=True).lower()
            break
        yield section, None, None  # a new table resets column mapping
        for tr in table.find_all("tr"):
            cells = [str(td) for td in tr.find_all(["td", "th"])]
            if cells:
                yield section, cells, str(tr)


def parse_document(text: str, source_name: str,
                   sections: list[str] | None = None) -> list[Listing]:
    """Every company/role row of every recognizable table in the document.

    Handles markdown and HTML tables, including documents containing both.
    """
    allowed = [s.lower() for s in sections] if sections else None
    listings: list[Listing] = []

    for rows in (_iter_markdown_rows(text), _iter_html_rows(text)):
        cols: dict | None = None
        last_company = ""
        for section, cells, raw in rows:
            if cells is None:          # table boundary
                cols, last_company = None, ""
                continue

            header = _header_map([_clean_text(c) for c in cells])
            if "company" in header and "role" in header:
                cols, last_company = header, ""
                continue
            if cols is None:
                continue
            if allowed is not None and not any(a in section for a in allowed):
                continue

            def cell(key: str) -> str:
                i = cols.get(key)
                return cells[i] if i is not None and i < len(cells) else ""

            company = _clean_text(cell("company"))
            if company.lower() in _CONTINUATION:
                company = last_company
            else:
                last_company = company
            if not company:
                continue
            if _CLOSED_MARKER in (raw or ""):
                continue  # the list itself marks this posting closed

            url = _extract_url(cell("link")) or _extract_url(raw or "")
            if not url:
                continue

            listings.append(Listing(
                source=source_name,
                company=company,
                role=_clean_text(cell("role")),
                url=url,
                location=_clean_text(cell("location")),
                posted=_parse_posted(_clean_text(cell("date"))),
            ))
    return listings



class JobListSource(Source):
    """One curated markdown list, identified by its raw README URL."""

    def __init__(self, name: str, url: str, sections=None):
        self.name = name
        self.url = url
        self.sections = sections

    async def fetch(self) -> list[Listing]:
        text = await get_text(self.url)
        return parse_document(text, self.name, self.sections)


def from_config(entries: list[dict]) -> list[Source]:
    return [
        JobListSource(
            name=e.get("name") or e["url"].split("/")[-3:-1][0],
            url=e["url"],
            sections=e.get("sections"),
        )
        for e in entries
    ]
