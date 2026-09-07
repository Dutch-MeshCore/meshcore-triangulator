"""Run with: python3 -m pytest web-standalone/tests"""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import bag3d  # noqa: E402

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "bag3d-pand.json").read_text(encoding="utf-8"))


def test_rd_conversion_against_two_church_towers():
    # Westertoren, Amsterdam: RD (120700.7, 487525.5); Martinitoren, Groningen: RD (233883.1, 582065.2).
    for (lat, lon), (rx, ry) in (((52.37453, 4.88352), (120700.7, 487525.5)), ((53.21938, 6.56820), (233883.1, 582065.2))):
        x, y = bag3d.wgs84_to_rd(lat, lon)
        assert abs(x - rx) < 1 and abs(y - ry) < 1
        back_lat, back_lon = bag3d.rd_to_wgs84(rx, ry)
        assert abs(back_lat - lat) < 1e-5 and abs(back_lon - lon) < 1e-5


def test_bbox_is_rd_metres_around_the_point():
    parts = [float(p) for p in bag3d.bbox_around(52.3731, 4.8932, 12).split(",")]
    assert parts[2] - parts[0] == 24 and parts[3] - parts[1] == 24


def test_buildings_carry_heights_and_a_footprint_in_metres():
    b = bag3d.buildings(FIXTURE)
    assert len(b) == 1
    building = b[0]
    assert building["id"] == "NL.IMBAG.Pand.0363100012178469"
    assert round(building["roof_m"], 2) == round(36.62099838256836 - 1.1339999437332153, 2)
    ring = building["surfaces"][0][0]
    assert len(ring) == 105
    xs = [p[0] for p in ring]
    ys = [p[1] for p in ring]
    # The footprint sits where the transform puts it, in RD metres, not in scaled ints.
    assert 121600 < min(xs) < max(xs) < 121800
    assert 487400 < min(ys) < max(ys) < 487600


def test_point_inside_and_outside_the_footprint():
    building = bag3d.buildings(FIXTURE)[0]
    ring = building["surfaces"][0][0]
    cx = sum(p[0] for p in ring) / len(ring)
    cy = sum(p[1] for p in ring) / len(ring)
    assert bag3d.point_in_ring(cx, cy, ring)
    assert not bag3d.point_in_ring(cx + 500, cy, ring)
    lat, lon = bag3d.rd_to_wgs84(cx, cy)
    inside = bag3d.roof_for_point(FIXTURE, lat, lon)
    assert inside["building"] == building["id"]
    assert inside["roof_m"] == 35.5
    assert inside["buildings"] == 1
    lat2, lon2 = bag3d.rd_to_wgs84(cx + 500, cy)
    outside = bag3d.roof_for_point(FIXTURE, lat2, lon2)
    assert outside["building"] is None and outside["roof_m"] is None


def test_empty_response_means_no_building():
    assert bag3d.roof_for_point({"features": []}, 52.0, 5.0) == {"roof_m": None, "building": None, "buildings": 0}
