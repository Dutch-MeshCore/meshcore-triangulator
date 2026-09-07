"""Run with: python3 -m pytest web-standalone/tests"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import spamdetector  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_one_byte_hops_with_ambiguous_labels():
    hops = spamdetector.parse_entry_hops(fixture("attack-2047.html"))
    assert [h["hop"] for h in hops] == ["1C", "35", "57", "BA", "64", "9B", "26", "F5"]
    assert hops[0] == {
        "hop": "1C",
        "repeater": "Ambiguous hop 1C (9 possible repeaters)",
        "packets": 4046,
        "snr": 3.9,
    }
    assert hops[-1]["packets"] == 2088


def test_two_byte_hops_with_resolved_names():
    hops = spamdetector.parse_entry_hops(fixture("attack-2041.html"))
    assert len(hops) == 8
    assert hops[0]["hop"] == "DE55"
    assert hops[0]["repeater"] == "NL-DDM De Ziep"
    assert hops[0]["packets"] == 42519
    assert hops[1]["hop"] == "A0A2"
    assert hops[2]["repeater"] == "Zevenaar-GH | NL-ZEV"


def test_only_the_table_under_the_heading_is_read():
    # Both fixtures carry a table before and after the entry-hop table.
    page = fixture("attack-2047.html")
    assert page.count("<table") >= 2
    hops = spamdetector.parse_entry_hops(page)
    assert all(h["packets"] >= 2088 for h in hops)


def test_page_without_the_heading_gives_nothing():
    page = "<html><body><h4>Route mix</h4><table><tr><td>1C</td><td>x</td><td>5</td></tr></table></body></html>"
    assert spamdetector.parse_entry_hops(page) == []


def test_missing_snr_and_bad_rows_are_tolerated():
    page = (
        '<h4 class="t">Entry hops (RF signals)</h4>'
        "<table><thead><tr><th>Hop</th><th>Repeater</th><th>Pkts</th><th>SNR</th></tr></thead>"
        "<tbody>"
        "<tr><td>ab</td><td>R&amp;D <b>site</b></td><td>1,200</td><td>—</td></tr>"
        "<tr><td>ZZ</td><td>not hex</td><td>3</td><td>1.0</td></tr>"
        "<tr><td>0C</td><td>no packets</td><td>n/a</td><td>1.0</td></tr>"
        "</tbody></table>"
    )
    hops = spamdetector.parse_entry_hops(page)
    assert hops == [{"hop": "AB", "repeater": "R&D site", "packets": 1200, "snr": None}]


def test_step_one_value_and_payload():
    hops = spamdetector.parse_entry_hops(fixture("attack-2047.html"))
    assert spamdetector.step_one_value(hops).startswith("1C:4046, 35:3216, 57:3078")
    payload = spamdetector.incident_payload("2047", fixture("attack-2047.html"), "https://x/attacks/2047")
    assert payload["id"] == 2047
    assert payload["source"] == "https://x/attacks/2047"
    assert len(payload["hops"]) == 8
    assert payload["prefixes"] == spamdetector.step_one_value(hops)
