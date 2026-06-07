#!/usr/bin/env python3
"""
Fetch arrivals + departures for Desert Rock Airstrip from OpenSky and merge
them into data/flights.json (deduplicated, rolling window). Run on a schedule
by GitHub Actions; the static page reads the JSON.

OpenSky has only historical/computed events (no live board), and obscure or
lightly-covered fields may return little or nothing — that absence is expected,
not a bug. Verify the ICAO code below for your target.

Auth: set OPENSKY_CLIENT_ID / OPENSKY_CLIENT_SECRET as repo Secrets for usable
limits. Without them it falls back to anonymous (400 credits/day).
"""

import json, os, sys, time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

AIRPORT_ICAO = "KDRA"        # Desert Rock, Mercury NV — VERIFY this code.
LOOKBACK_HOURS = 24          # scanned each run (OpenSky window max: 7 days)
RETENTION_DAYS = 365         # it's an archive: keep a year
DATA_FILE = Path(__file__).parent / "data" / "flights.json"

TOKEN_URL = ("https://auth.opensky-network.org/auth/realms/"
             "opensky-network/protocol/openid-connect/token")
API = "https://opensky-network.org/api"


def get_token():
    cid, sec = os.environ.get("OPENSKY_CLIENT_ID"), os.environ.get("OPENSKY_CLIENT_SECRET")
    if not (cid and sec):
        print("No credentials set — anonymous access (throttled).")
        return None
    body = urlencode({"grant_type": "client_credentials",
                      "client_id": cid, "client_secret": sec}).encode()
    req = Request(TOKEN_URL, data=body,
                  headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urlopen(req, timeout=30) as r:
        return json.load(r)["access_token"]


def fetch(direction, begin, end, tok):
    qs = urlencode({"airport": AIRPORT_ICAO, "begin": begin, "end": end})
    url = f"{API}/flights/{direction}?{qs}"
    headers = {"Authorization": f"Bearer {tok}"} if tok else {}
    try:
        with urlopen(Request(url, headers=headers), timeout=60) as r:
            return json.load(r)
    except HTTPError as e:
        if e.code == 404:
            print(f"  {direction}: no events in window (404)")
            return []
        print(f"  {direction}: HTTP {e.code} {e.reason}", file=sys.stderr)
        return []
    except URLError as e:
        print(f"  {direction}: network error {e.reason}", file=sys.stderr)
        return []


def normalize(raw, direction):
    out = []
    for f in raw:
        out.append({
            "direction": direction,
            "icao24": (f.get("icao24") or "").strip(),
            "callsign": (f.get("callsign") or "").strip() or "—",
            "first_seen": f.get("firstSeen"),
            "last_seen": f.get("lastSeen"),
            "other_airport": (f.get("estDepartureAirport") if direction == "arrival"
                              else f.get("estArrivalAirport")) or "—",
        })
    return out


def load_existing():
    if DATA_FILE.exists():
        try:
            return json.loads(DATA_FILE.read_text())
        except json.JSONDecodeError:
            pass
    return {"airport": AIRPORT_ICAO, "updated": None, "events": []}


def key(e):
    return (e["icao24"], e["direction"], e.get("last_seen"))


def main():
    now = int(time.time())
    begin = now - LOOKBACK_HOURS * 3600
    tok = get_token()

    fresh = []
    for d in ("arrival", "departure"):
        print(f"Fetching {d}s {begin}..{now}")
        fresh += normalize(fetch(d, begin, now, tok), d)

    store = load_existing()
    seen = {key(e) for e in store["events"]}
    added = 0
    for e in fresh:
        if key(e) not in seen:
            store["events"].append(e); seen.add(key(e)); added += 1

    cutoff = now - RETENTION_DAYS * 86400
    store["events"] = [e for e in store["events"] if (e.get("last_seen") or 0) >= cutoff]
    store["events"].sort(key=lambda e: e.get("last_seen") or 0, reverse=True)
    store["airport"] = AIRPORT_ICAO
    store["updated"] = datetime.now(timezone.utc).isoformat()

    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(json.dumps(store, indent=2))
    print(f"Added {added}. Total stored: {len(store['events'])}.")


if __name__ == "__main__":
    main()
