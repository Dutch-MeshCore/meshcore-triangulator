"""Reads mc-spamdetector.nl for the triangulator (#107).

The spam detector publishes its incident list as JSON but renders an attack
page as HTML. The table under "Entry hops (RF signals)" lists the repeaters
that heard the sender directly, with packet counts, which is what Step 1
takes as `PREFIX:count`. This module turns that page into the small JSON the
page needs; server.py serves it under /proxy/spamdetector/.

Pure logic, stdlib only, so it is unit-testable (tests/test_spamdetector.py).
"""
from html.parser import HTMLParser
import re

ENTRY_HOPS_HEADING = "Entry hops (RF signals)"
HOP_PATTERN = re.compile(r"^[0-9A-F]{2,8}$")
HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}


class _EntryHopTableParser(HTMLParser):
    """Collects the rows of the first <table> after the entry-hops heading."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._heading_text = None      # accumulates while inside a heading
        self._armed = False            # heading seen, table not yet reached
        self._in_table = False
        self._cell = None              # accumulates while inside a td/th
        self._row = None
        self.rows = []                 # lists of cell strings, header included
        self.done = False

    def handle_starttag(self, tag, attrs):
        if self.done:
            return
        if tag in HEADINGS:
            self._heading_text = ""
        elif tag == "table" and self._armed and not self._in_table:
            self._in_table = True
        elif self._in_table and tag == "tr":
            self._row = []
        elif self._in_table and tag in ("td", "th") and self._row is not None:
            self._cell = ""

    def handle_endtag(self, tag):
        if self.done:
            return
        if tag in HEADINGS and self._heading_text is not None:
            if self._heading_text.strip() == ENTRY_HOPS_HEADING:
                self._armed = True
            self._heading_text = None
        elif self._in_table and tag in ("td", "th") and self._cell is not None:
            self._row.append(" ".join(self._cell.split()))
            self._cell = None
        elif self._in_table and tag == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None
        elif self._in_table and tag == "table":
            self._in_table = False
            self._armed = False
            self.done = True

    def handle_data(self, data):
        if self._heading_text is not None:
            self._heading_text += data
        if self._cell is not None:
            self._cell += data


def _packets(text):
    digits = text.replace(",", "").replace(".", "").strip()
    return int(digits) if digits.isdigit() else None


def _snr(text):
    try:
        return float(text.strip())
    except ValueError:
        return None


def parse_entry_hops(html_text):
    """The entry-hop table of an attack page as a list of dicts.

    Each row: {"hop": "1C", "repeater": "...", "packets": 4046, "snr": 3.9}.
    Heaviest first, as the page orders them. Rows whose hop is not a hex
    prefix or whose packet count does not parse are skipped, the header row
    among them. An empty list means the page has no such table.
    """
    parser = _EntryHopTableParser()
    parser.feed(html_text)
    parser.close()
    hops = []
    for row in parser.rows:
        if len(row) < 3:
            continue
        hop = row[0].upper()
        packets = _packets(row[2])
        if not HOP_PATTERN.match(hop) or packets is None:
            continue
        hops.append({
            "hop": hop,
            "repeater": row[1],
            "packets": packets,
            "snr": _snr(row[3]) if len(row) > 3 else None,
        })
    return hops


def step_one_value(hops):
    """The first-hop field text for these hops: `1C:4046, 35:3216`."""
    return ", ".join(f"{hop['hop']}:{hop['packets']}" for hop in hops)


def incident_payload(incident_id, html_text, source_url=None):
    hops = parse_entry_hops(html_text)
    return {
        "id": int(incident_id),
        "source": source_url,
        "hops": hops,
        "prefixes": step_one_value(hops),
    }
