# MeshCore Triangulator

[![Build web-standalone image](https://github.com/Dutch-MeshCore/meshcore-triangulator/actions/workflows/docker-build.yml/badge.svg)](https://github.com/Dutch-MeshCore/meshcore-triangulator/actions/workflows/docker-build.yml)

Localises repeaters/nodes/traffic sources on MeshCore from the mesh topology
(who heard what, from where).

**Live demo:** [triangulator.dutchmeshcore.nl](https://triangulator.dutchmeshcore.nl/)

This repo holds the standalone map tool. The data-collection pipeline it grew out of is a separate private repo (see the end of this page).

## `web-standalone/` — start here

Self-contained, no database (`index.html` + `server.py` proxy).
Resolves a node live against the public mc-radar / map.meshcore.io
feeds — nothing to install beyond Python 3.7+ stdlib, nothing to keep running
except the one process. **This is almost certainly what you want to host** —
lower maintenance, no persistent state to lose.

### Run with Docker (recommended)

See the [docker-compose.yml](web-standalone/docker-compose.yml) for how to use it.

A quick start is:

```bash
mkdir ~/triangulator
cd ~/triangulator
wget https://raw.githubusercontent.com/Dutch-MeshCore/meshcore-triangulator/refs/heads/main/web-standalone/docker-compose.yml
wget https://raw.githubusercontent.com/Dutch-MeshCore/meshcore-triangulator/refs/heads/main/web-standalone/.env.example
cp .env.example .env
```

`cloudflared` is built into the stack for ease of use:

1. In the Cloudflare Zero Trust dashboard (Networks → Tunnels), create a
   tunnel and copy its token into `TUNNEL_TOKEN` in `.env`.
2. Point the tunnel's public hostname at the service URL
   `http://triangulator:8000` (Docker DNS resolves the service name on the
   internal network shared with cloudflared).
3. `docker compose up -d`

### Run directly

```bash
cd web-standalone
python3 server.py          # http://127.0.0.1:8000
```

Bind for remote access with `HOST=0.0.0.0` — put a reverse proxy with TLS/access
control in front if you do; the proxy has no auth of its own.

### Data sources (proxied by server.py)

| Source | Use |
|---|---|
| mc-radar Node Inspector | Resolve prefixes; proven links. |
| map.meshcore.io | Global node feed (fallback). |
| PDOK AHN | Terrain/surface elevation (NL). |
| mc-spamdetector.nl | Incident list, and an incident's entry hops as Step 1 clues. |
| 3D BAG (api.3dbag.nl) | The tallest building within 50 m of an observer, for its antenna height in the line-of-sight check (NL). |

Loaded by the page directly, not through the proxy: OpenFreeMap vector tiles (the Liberty basemap, no key) and AWS Terrain Tiles (relief in the 3D view, for looking at; line of sight is measured on AHN).

### Tests and accuracy

```bash
python3 -m pytest web-standalone/tests      # pure-logic tests (spam-detector parser)
node web-standalone/tools/accuracy.mjs      # estimator error against 92 known targets
```

The accuracy harness runs offline against a committed fixture and is how every estimator change in this repo is judged; see [web-standalone/tools/README.md](web-standalone/tools/README.md).

### Deep-linking (for other sites/tools)

External sites can link straight into a query via URL query parameters —
useful if you're building a sibling tool (a spam detector, an incident page,
a bot) and want a "locate this node" link that lands the visitor on a live
result instead of a blank form.

| Param | Required | Meaning |
|---|---|---|
| `prefixes` | yes | First-hop clue prefixes — same syntax as the Step 1 field: `PREFIX` or `PREFIX:count`, comma-separated (e.g. `db:30,23,db11:12`). Presence of this param is what triggers auto-discovery. |
| `prefixes2` | no | Second-hop clue prefixes, same syntax. |
| `hop2` | no | Overrides the "2nd-hop km" input (assumed RF reach for 2nd-hop/relayed clues). |
| `cluster` | no | Overrides the "Cluster km" input (search radius / hull buffer). |
| `incident` | no | A [mc-spamdetector.nl](https://mc-spamdetector.nl/) attack number, e.g. `2047`. Loads its entry hops as the first-hop clues and runs discovery. Used on its own, without `prefixes`. |
| `region` | no | `lat,lon` of a candidate region's centre. After discovery the nearest candidate (within 15 km) is chosen and its proven links pulled. The Share button emits it once a region is locked. |
| `obs` | no | Active observers with weights after edits, `KEY8:weight,…` (first 8 hex of the node key). Applied after the lock; nodes no longer in the cluster are named in the status. |
| `moved` | no | Dragged observers, `KEY8@lat,lon;…`. |
| `droplink` | no | Removed proven links, `KEY8-KEY8;…`. |
| `est` | no | `observer` or `terrain`: which estimate to run after the lock. |
| `layers` | no | Layer switch, e.g. `layers=heat`; `none` for all off. |
| `relief` | no | Relief exaggeration for the 3D view. |

The Share button emits all of these, each only when it differs from what the app does on its own, and the address bar follows the state so copying it gives the same link.

If `prefixes` is present, the page pre-fills Step 1 from these params and
automatically clicks "Find Best Matching Region" on load — no further
interaction needed to land on candidate clusters.

```
https://triangulator.dutchmeshcore.nl/?prefixes=db:30,23,db11:12&prefixes2=a0:8,fc
```

There's no `hop1` param — the "1st-hop km" input was removed; 1st-hop
observers' range is now derived from proven-link data / node activity rather
than a fixed assumed value.

## The full data-collection pipeline

The MQTT collector, local observations database, hourly export, GPS-track
"movers" view and database-backed dashboards now live in a **separate private
repo**. This repo covers the standalone map tool only.

If you're standing up the whole pipeline rather than just the map, ask Kasper
for access.

## Background

The original deployment ran the full pipeline on a local NUC. The
`web-standalone` tool was built as a lighter, database-free alternative with
the same core triangulation logic (clue parsing, clustering, weighted
estimate, terrain refine, 2D/3D rendering), meant to run somewhere steadier —
and it's what this repo is for. The pipeline additionally has the
database-backed features (verified positions, augment/correct overlay,
Nodes/Targets/Movers/Monitoring) that `web-standalone` doesn't need or have.
