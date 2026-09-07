"""3D BAG lookups for the triangulator (#74).

A repeater on a roof has its antenna at roof height plus a mast, not at the
12 m the estimator assumed for every node. 3D BAG (TU Delft, CC BY 4.0)
publishes per-building footprints with roof and ground heights. This module
turns one response of its OGC API Features endpoint into "the building this
point stands on, and how high its roof is above the ground".

Pure logic, stdlib only, so it is unit-testable (tests/test_bag3d.py).
server.py fetches the payload and serves the result under /proxy/3dbag/.

The API takes bounding boxes in EPSG:7415 (RD New + NAP), so lat/lon is
converted with the usual polynomial approximation, good to about 25 cm.
"""
import math

# Schreutelkops-Adema approximation, WGS84 -> RD New.
_RD_X0, _RD_Y0 = 155000.0, 463000.0
_LAT0, _LON0 = 52.15517440, 5.38720621

_RD_X_TERMS = [(0, 1, 190094.945), (1, 1, -11832.228), (2, 1, -114.221), (0, 3, -32.391),
               (1, 0, -0.705), (3, 1, -2.340), (1, 3, -0.608), (0, 2, -0.008), (2, 3, 0.148)]
_RD_Y_TERMS = [(1, 0, 309056.544), (0, 2, 3638.893), (2, 0, 73.077), (1, 2, -157.984),
               (3, 0, 59.788), (0, 1, 0.433), (2, 2, -6.439), (1, 1, -0.032), (0, 4, 0.092),
               (1, 4, -0.054)]
_WGS_LAT_TERMS = [(0, 1, 3235.65389), (2, 0, -32.58297), (0, 2, -0.24750), (2, 1, -0.84978),
                  (0, 3, -0.06550), (2, 2, -0.01709), (1, 0, -0.00738), (4, 0, 0.00530),
                  (2, 3, -0.00039), (4, 1, 0.00033), (1, 1, -0.00012)]
_WGS_LON_TERMS = [(1, 0, 5260.52916), (1, 1, 105.94684), (1, 2, 2.45656), (3, 0, -0.81885),
                  (1, 3, 0.05594), (3, 1, -0.05607), (0, 1, 0.01199), (3, 2, -0.00256),
                  (1, 4, 0.00128), (0, 2, 0.00022), (2, 0, -0.00022), (5, 0, 0.00026)]


def wgs84_to_rd(lat, lon):
    """RD New x, y in metres for a WGS84 position."""
    dlat = 0.36 * (lat - _LAT0)
    dlon = 0.36 * (lon - _LON0)
    x = _RD_X0 + sum(c * dlat ** p * dlon ** q for p, q, c in _RD_X_TERMS)
    y = _RD_Y0 + sum(c * dlat ** p * dlon ** q for p, q, c in _RD_Y_TERMS)
    return x, y


def rd_to_wgs84(x, y):
    """WGS84 lat, lon for an RD New position. Used by the tests to build a
    lat/lon inside a known footprint; the app only goes the other way."""
    dx = (x - _RD_X0) * 1e-5
    dy = (y - _RD_Y0) * 1e-5
    lat = _LAT0 + sum(c * dx ** p * dy ** q for p, q, c in _WGS_LAT_TERMS) / 3600.0
    lon = _LON0 + sum(c * dx ** p * dy ** q for p, q, c in _WGS_LON_TERMS) / 3600.0
    return lat, lon


def bbox_around(lat, lon, radius_m):
    """An RD bbox string for the API, radius_m either side of the point."""
    x, y = wgs84_to_rd(lat, lon)
    return "%.0f,%.0f,%.0f,%.0f" % (x - radius_m, y - radius_m, x + radius_m, y + radius_m)


def _transform(payload, feature):
    meta = payload.get("metadata") or {}
    transform = meta.get("transform") or feature.get("transform") or {}
    scale = transform.get("scale") or [1, 1, 1]
    translate = transform.get("translate") or [0, 0, 0]
    return scale, translate


def buildings(payload):
    """Every Building in a response: id, roof height above ground, the raw
    heights, and its footprint rings in RD metres (outer ring first per
    surface, inner rings are holes)."""
    out = []
    for feature in payload.get("features") or []:
        scale, translate = _transform(payload, feature)
        vertices = feature.get("vertices") or []

        def xy(index):
            v = vertices[index]
            return (v[0] * scale[0] + translate[0], v[1] * scale[1] + translate[1])

        for object_id, obj in (feature.get("CityObjects") or {}).items():
            if obj.get("type") != "Building":
                continue
            attrs = obj.get("attributes") or {}
            roof = attrs.get("b3_h_dak_max")
            ground = attrs.get("b3_h_maaiveld")
            surfaces = []
            for geometry in obj.get("geometry") or []:
                if str(geometry.get("lod")) != "0":
                    continue
                for surface in geometry.get("boundaries") or []:
                    rings = [[xy(i) for i in ring] for ring in surface if ring]
                    if rings:
                        surfaces.append(rings)
            out.append({
                "id": object_id,
                "roof_m": (roof - ground) if isinstance(roof, (int, float)) and isinstance(ground, (int, float)) else None,
                "h_dak_max": roof,
                "h_maaiveld": ground,
                "h_nok": attrs.get("b3_h_nok"),
                "surfaces": surfaces,
            })
    return out


def point_in_ring(x, y, ring):
    """Ray casting; a point on the edge counts as inside."""
    inside = False
    n = len(ring)
    for i in range(n):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            cross = (x2 - x1) * (y - y1) / (y2 - y1) + x1
            if x <= cross:
                inside = not inside
    return inside


def point_in_surface(x, y, rings):
    if not rings or not point_in_ring(x, y, rings[0]):
        return False
    return not any(point_in_ring(x, y, hole) for hole in rings[1:])


def building_at(payload, lat, lon):
    """The building whose footprint contains the point, or None."""
    x, y = wgs84_to_rd(lat, lon)
    for building in buildings(payload):
        if any(point_in_surface(x, y, rings) for rings in building["surfaces"]):
            return building
    return None


def roof_for_point(payload, lat, lon):
    """What the page asks for: the roof height above ground of the building
    the point stands on, or null when it stands on none."""
    building = building_at(payload, lat, lon)
    return {
        "roof_m": round(building["roof_m"], 1) if building and building["roof_m"] is not None else None,
        "building": building["id"] if building else None,
        "buildings": len(buildings(payload)),
    }
