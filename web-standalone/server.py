from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
import json
import os
import re
import socket
import sys
import threading
import time

import bag3d
import spamdetector


PORT = 8000
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

UPSTREAMS = {
    "/proxy/mc-radar/search": {
        "url": "https://mc-radar.woodwar.com/api/node-inspector/search",
        "method": "POST",
        "content_type": "application/json",
    },
    "/proxy/mc-radar/connected/": {
        "prefix": "https://mc-radar.woodwar.com/api/node-inspector/connected/",
        "method": "GET",
    },
    "/proxy/meshcore/nodes": {
        "url": "https://map.meshcore.io/api/v1/nodes?binary=1&short=1",
        "method": "GET",
    },
    "/proxy/pdok/ahn": {
        "prefix": "https://service.pdok.nl/rws/actueel-hoogtebestand-nederland/wms/v1_0",
        "method": "GET",
    },
}

# mc-spamdetector.nl (#107). The incident list is JSON; an attack page is
# 1-3 MB of HTML that spamdetector.py boils down to its entry-hop table.
# Both are cached for a while so a page reload or a second operator does not
# fetch the same page again (AGENTS.md: do not hammer upstream feeds).
SPAMDETECTOR_ALERTS_URL = "https://mc-spamdetector.nl/api/spam-alerts"
SPAMDETECTOR_ATTACK_URL = "https://mc-spamdetector.nl/attacks/{id}"
SPAMDETECTOR_ALERTS_TTL = 120
SPAMDETECTOR_INCIDENT_TTL = 600
SPAMDETECTOR_CACHE_SIZE = 20
INCIDENT_ID = re.compile(r"^[0-9]{1,9}$")

# 3D BAG (#74): the tallest building within BAG3D_RADIUS_M of a repeater,
# for its antenna height. The API pages at 100 features; a 100 m box holds
# up to about 100 buildings in a Dutch town centre, so up to BAG3D_MAX_PAGES
# are followed.
# Keyed on the rounded position; buildings do not move, so the cache lives a
# day.
BAG3D_ITEMS_URL = "https://api.3dbag.nl/collections/pand/items?bbox={bbox}&limit=100"
BAG3D_RADIUS_M = 50
BAG3D_MAX_PAGES = 4
BAG3D_TTL = 86400


def _bag3d_pages(url):
    """Every page of a 3D BAG items query, following rel=next, capped."""
    pages = []
    next_url = url
    while next_url and len(pages) < BAG3D_MAX_PAGES:
        page = json.loads(_fetch(next_url).decode("utf-8", "replace"))
        pages.append(page)
        next_url = None
        for link in page.get("links") or []:
            if link.get("rel") == "next" and link.get("href"):
                next_url = link["href"]
                break
    return pages

_cache = {}
_cache_lock = threading.Lock()


def _cached(key, ttl, produce):
    now = time.time()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and hit[0] > now:
            return hit[1]
    value = produce()
    with _cache_lock:
        if len(_cache) >= SPAMDETECTOR_CACHE_SIZE:
            oldest = min(_cache, key=lambda k: _cache[k][0])
            del _cache[oldest]
        _cache[key] = (now + ttl, value)
    return value


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=BASE_DIR, **kwargs)

    def end_headers(self):
        # Every response revalidates. Without a Cache-Control header browsers
        # apply heuristic caching to index.html and changelog.json and served
        # yesterday's page for hours; Cloudflare in front honours this too
        # (#124). The proxy routes set their own no-store before this runs.
        if not any(name.lower() == "cache-control" for name, _ in self._headers_buffer_names()):
            self.send_header("Cache-Control", "no-cache")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        super().end_headers()

    def _headers_buffer_names(self):
        for line in getattr(self, "_headers_buffer", []):
            if isinstance(line, bytes) and b":" in line:
                name = line.split(b":", 1)[0].decode("latin-1", "replace").strip()
                yield name, None

    def do_OPTIONS(self):
        self.send_response(204)
        self.end_headers()

    def do_GET(self):
        if self.path.startswith("/proxy/mc-radar/connected/"):
            self._proxy_dynamic("/proxy/mc-radar/connected/")
            return
        if self.path == "/proxy/meshcore/nodes":
            self._proxy_static("/proxy/meshcore/nodes")
            return
        if self.path.startswith("/proxy/pdok/ahn"):
            self._proxy_query_passthrough("/proxy/pdok/ahn")
            return
        if self.path == "/proxy/spamdetector/alerts":
            self._spamdetector_alerts()
            return
        if self.path.startswith("/proxy/spamdetector/incident/"):
            self._spamdetector_incident(self.path[len("/proxy/spamdetector/incident/"):])
            return
        if self.path.startswith("/proxy/3dbag/roof?"):
            self._bag3d_roof(self.path.split("?", 1)[1])
            return
        super().do_GET()

    def _bag3d_roof(self, query):
        params = dict(part.split("=", 1) for part in query.split("&") if "=" in part)
        try:
            lat = float(params.get("lat", ""))
            lon = float(params.get("lon", ""))
        except ValueError:
            self._send_json({"error": "lat and lon must be numbers"}, 400)
            return
        if not (50.0 <= lat <= 54.0 and 3.0 <= lon <= 7.5):
            # Outside the Netherlands there is no 3D BAG; not an error, no roof.
            self._send_json({"roof_m": None, "building": None, "buildings": 0, "covered": False}, 200)
            return
        key = "bag3d:%.5f,%.5f" % (lat, lon)
        url = BAG3D_ITEMS_URL.format(bbox=bag3d.bbox_around(lat, lon, BAG3D_RADIUS_M))
        try:
            result = _cached(key, BAG3D_TTL, lambda: bag3d.roof_for_point(_bag3d_pages(url), lat, lon, BAG3D_RADIUS_M))
        except UpstreamError as error:
            self._send_json({"error": error.message}, error.status)
            return
        except ValueError:
            self._send_json({"error": "3D BAG answered something that is not JSON"}, 502)
            return
        self._send_json(dict(result, covered=True, radius_m=BAG3D_RADIUS_M), 200)

    def _spamdetector_alerts(self):
        try:
            body = _cached("alerts", SPAMDETECTOR_ALERTS_TTL, lambda: _fetch(SPAMDETECTOR_ALERTS_URL))
        except UpstreamError as error:
            self._send_json({"error": error.message}, error.status)
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _spamdetector_incident(self, incident_id):
        if not INCIDENT_ID.match(incident_id):
            self._send_json({"error": "incident id must be a number"}, 400)
            return
        url = SPAMDETECTOR_ATTACK_URL.format(id=incident_id)
        try:
            page = _cached(f"incident:{incident_id}", SPAMDETECTOR_INCIDENT_TTL, lambda: _fetch(url))
        except UpstreamError as error:
            self._send_json({"error": error.message}, error.status)
            return
        payload = spamdetector.incident_payload(incident_id, page.decode("utf-8", "replace"), url)
        if not payload["hops"]:
            self._send_json({"error": "no entry-hop table on that attack page", **payload}, 404)
            return
        self._send_json(payload, 200)

    def _send_json(self, payload, status):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path == "/proxy/mc-radar/search":
            self._proxy_static("/proxy/mc-radar/search")
            return
        self.send_error(404, "Unknown endpoint")

    def _proxy_static(self, key):
        config = UPSTREAMS[key]
        body = self._read_body() if config["method"] == "POST" else None
        self._forward(config["url"], method=config["method"], body=body, content_type=config.get("content_type"))

    def _proxy_dynamic(self, key):
        config = UPSTREAMS[key]
        suffix = self.path[len(key):]
        self._forward(f"{config['prefix']}{suffix}", method=config["method"])

    def _read_body(self):
        length = int(self.headers.get("Content-Length", "0"))
        return self.rfile.read(length) if length > 0 else b""

    def _proxy_query_passthrough(self, key):
        config = UPSTREAMS[key]
        query = ""
        if "?" in self.path:
            query = self.path.split("?", 1)[1]
        url = config["prefix"]
        if query:
            url = f"{url}?{query}"
        self._forward(url, method=config["method"])

    def _forward(self, url, method="GET", body=None, content_type=None):
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, text/plain, */*",
        }
        if content_type:
            headers["Content-Type"] = content_type

        request = Request(url, data=body, headers=headers, method=method)
        try:
            with urlopen(request, timeout=20) as response:
                data = response.read()
                self.send_response(response.status)
                self.send_header("Content-Type", response.headers.get("Content-Type", "application/octet-stream"))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)
        except HTTPError as error:
            data = error.read()
            self.send_response(error.code)
            self.send_header("Content-Type", error.headers.get("Content-Type", "application/json"))
            self.end_headers()
            self.wfile.write(data)
        except URLError as error:
            message = str(error.reason).encode("utf-8")
            self.send_response(502)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(message)
        except socket.timeout:
            message = b"PDOK request timed out"
            self.send_response(504)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(message)
        except Exception as error:
            message = str(error).encode("utf-8")
            self.send_response(502)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(message)


class UpstreamError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


# GET a URL and return its body, or raise UpstreamError with the status the
# proxy should answer. Same headers as _forward(); kept separate because the
# spam-detector routes transform the body instead of passing it through.
def _fetch(url):
    request = Request(url, headers={
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/html, */*",
    })
    try:
        with urlopen(request, timeout=30) as response:
            return response.read()
    except HTTPError as error:
        raise UpstreamError(error.code, f"spam detector answered {error.code}")
    except URLError as error:
        raise UpstreamError(502, f"spam detector unreachable: {error.reason}")
    except socket.timeout:
        raise UpstreamError(504, "spam detector timed out")


def main():
    port = PORT
    if len(sys.argv) > 1:
        port = int(sys.argv[1])
    # Bind to localhost by default; set HOST=0.0.0.0 to accept connections from
    # other machines (put a reverse proxy with TLS/access control in front — see README).
    host = os.environ.get("HOST", "127.0.0.1")
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Serving {BASE_DIR} on http://{host}:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
