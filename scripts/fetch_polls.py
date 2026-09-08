#!/usr/bin/env python3
"""
Rebuild data/polls.json from the Wikipedia opinion-polling table.

Headline seat numbers come from ONE house (Kantar/Kan 11 by default, see
mapping.json). The per-party spread is computed across every pollster in the
recent window, so the range stays honest without the headline drifting between
houses poll to poll.

The script refuses to write a file it does not fully understand: an unmapped
column, a missing headline poll, or a seat total far from 120 is a hard error.
An auto-updater that silently drops a newly formed party is worse than no
auto-updater at all.

Usage:
    python3 scripts/fetch_polls.py                 # write data/polls.json
    python3 scripts/fetch_polls.py --dry-run       # print, write nothing
    python3 scripts/fetch_polls.py --cache w.html  # reuse a saved page
"""

import argparse
import datetime as dt
import json
import os
import re
import sys
import urllib.request
from html.parser import HTMLParser

PAGE = "Opinion_polling_for_the_2026_Israeli_legislative_election"
REST = "https://en.wikipedia.org/api/rest_v1/page/html/" + PAGE
UA = "kowaz-coalition-builder/1.0 (https://kowaz.com/coalition; poll data sync)"

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
MAPPING = os.path.join(HERE, "mapping.json")
OUT = os.path.join(ROOT, "data", "polls.json")

MONTHS = {m: i + 1 for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])}


class Warn(Exception):
    """A condition that must stop the run rather than produce silent bad data."""


# --------------------------------------------------------------------------
# HTML table parsing (colspan-aware)
# --------------------------------------------------------------------------

class TableParser(HTMLParser):
    """Parse one <table> into rows of (text, is_span_continuation) cells.

    Columns shift as parties merge — when RZP and Zehut became a technical bloc
    the two columns became one cell with colspan=2. Expanding colspans keeps
    every row the same width as the header, so a column can be identified by
    position; the continuation flag lets the caller credit a merged cell's seats
    to one party instead of counting them twice.
    """

    def __init__(self):
        super().__init__()
        self.rows = []
        self._row = None
        self._cell = None
        self._span = 1
        self._skip = 0
        self._depth = 0

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._depth += 1
            return
        if self._depth != 1:
            return
        if tag == "tr":
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []
            a = dict(attrs)
            try:
                self._span = max(1, int(a.get("colspan", 1)))
            except ValueError:
                self._span = 1
        elif tag in ("sup", "style", "script"):
            self._skip += 1

    def handle_endtag(self, tag):
        if tag == "table":
            self._depth -= 1
            return
        if self._depth != 1:
            return
        if tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None
        elif tag in ("td", "th") and self._cell is not None:
            text = re.sub(r"\s+", " ", "".join(self._cell)).strip()
            self._row.append((text, False))
            for _ in range(self._span - 1):
                self._row.append((text, True))
            self._cell = None
            self._span = 1
        elif tag in ("sup", "style", "script") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if self._cell is not None and not self._skip:
            self._cell.append(data)


def top_level_tables(html):
    starts = [m.start() for m in re.finditer(r"<table\b", html)]
    ends = [m.start() for m in re.finditer(r"</table>", html)]
    events = sorted([(s, 1) for s in starts] + [(e, -1) for e in ends])
    depth, start, out = 0, None, []
    for pos, delta in events:
        if delta == 1:
            if depth == 0:
                start = pos
            depth += 1
        else:
            depth -= 1
            if depth == 0 and start is not None:
                out.append(html[start:pos + len("</table>")])
    return out


def find_poll_tables(html, required):
    """Every current-cycle poll table, with its own header.

    Two filters matter here. The page also carries poll tables from previous
    election cycles, so a table only counts as current if its header contains
    every column in `required`. And Wikipedia splits the current cycle across
    more than one table whose headers differ (RZP and Zehut are one column in
    the September table, two in the August one), so each table is mapped
    against its own header rather than assuming a shared layout.
    """
    found = []
    for tbl in top_level_tables(html):
        p = TableParser()
        p.feed(tbl)
        for row in p.rows:
            texts = [c[0] for c in row]
            if not any("Fieldwork" in t for t in texts):
                continue
            if all(req in texts for req in required):
                found.append((p.rows, texts))
            break
    if not found:
        raise Warn(
            "Found no current-cycle poll table. Every table must carry the "
            f"columns {required!r} — see 'current_cycle_header_must_contain' in "
            "scripts/mapping.json. If a lead party has renamed, update that list.")
    return found


# --------------------------------------------------------------------------
# Cell values
# --------------------------------------------------------------------------

def parse_seats(raw):
    """Return (seats, pct, ok). '(2.6%)' means below threshold: 0 seats."""
    s = (raw or "").strip()
    if not s or s in {"—", "-", "?"} or "N/a" in s or "N/A" in s:
        return None, None, False
    m = re.fullmatch(r"\(?\s*<?\s*([\d.]+)\s*%\s*\)?", s)
    if m:
        return 0, m.group(1) + "%", True
    m = re.fullmatch(r"\(\s*(\d+)\s*\)", s)          # '(3)' = below threshold
    if m:
        return 0, None, True
    m = re.fullmatch(r"'''?(\d+)'''?|(\d+)", s)
    if m:
        return int(m.group(1) or m.group(2)), None, True
    return None, None, False


def parse_date(raw, year, today=None):
    """'3–4 Sep', '31 Aug', '31 Dec – 1 Jan' -> the date fieldwork ended.

    The table gives no year, so one is assumed and then corrected: a date that
    lands well in the future must belong to the previous year. Without that, a
    poll fielded across New Year ('31 Dec – 1 Jan') reads as December of the
    coming year and sorts ahead of every real poll.
    """
    s = (raw or "").replace("–", "-").replace("—", "-").strip()
    parts = [p.strip() for p in s.split("-") if p.strip()]
    if not parts:
        return None

    end = parts[-1]                      # fieldwork ends at the right-hand date
    m = re.search(r"(\d{1,2})\s*([A-Z][a-z]{2})", end)
    if m:
        day, mon = int(m.group(1)), m.group(2)
    else:
        dm = re.search(r"(\d{1,2})", end)
        mon = None
        for p in reversed(parts[:-1]):   # '3-4 Sep': month is on the left
            x = re.search(r"([A-Z][a-z]{2})", p)
            if x:
                mon = x.group(1)
                break
        if not dm or not mon:
            return None
        day = int(dm.group(1))

    if mon not in MONTHS:
        return None
    try:
        d = dt.date(year, MONTHS[mon], day)
    except ValueError:
        return None

    if (d - (today or dt.date.today())).days > 30:
        try:
            d = dt.date(year - 1, MONTHS[mon], day)
        except ValueError:
            return None
    return d


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def load_mapping():
    with open(MAPPING, encoding="utf-8") as f:
        return json.load(f)


def fetch_html(cache=None):
    if cache and os.path.exists(cache):
        with open(cache, encoding="utf-8") as f:
            return f.read()
    req = urllib.request.Request(REST, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        html = r.read().decode("utf-8")
    if cache:
        with open(cache, "w", encoding="utf-8") as f:
            f.write(html)
    return html


def build(cache=None, year=None, verbose=True):
    cfg = load_mapping()
    cols_cfg = cfg["columns"]
    ignore = set(cfg.get("ignore_columns", []))
    year = year or dt.date.today().year

    required = cfg.get("current_cycle_header_must_contain", ["Likud"])
    tables = find_poll_tables(fetch_html(cache), required)

    unmapped = set()
    all_parties = set()
    polls = []
    history_days = int(cfg.get("history_days", 45))
    trend_days = int(cfg.get("trend_days", 63))
    trend_polls = []
    newest = None

    # Tables run newest-first. Reading all of them would drag in tables whose
    # columns are parties that no longer exist, so stop once a table is entirely
    # older than the history window.
    for rows, header in tables:
        width = len(header)

        # ---- validate every column is accounted for ---------------------
        col_party = {}             # header index -> party id
        table_unmapped = set()
        for i, name in enumerate(header):
            clean = name.strip()
            if clean in ignore or not clean:
                continue
            entry = cols_cfg.get(clean)
            if entry is None:
                table_unmapped.add(clean)
            elif isinstance(entry, str):
                col_party[i] = entry
            elif isinstance(entry, dict) and "merged_into" in entry:
                col_party[i] = entry["merged_into"]
            # withdrawn / note-only entries map to nothing, deliberately

        # ---- collect poll rows ------------------------------------------
        table_polls = []
        for row in rows:
            if len(row) < width - 1 or len(row) > width + 1:
                continue                                # event / artifact rows
            cells = [c[0] for c in row]
            cont = [c[1] for c in row]
            if cells[0].strip() in ("Fieldworkdate", "Fieldwork date"):
                continue
            date = parse_date(cells[0], year)
            firm = cells[1].strip() if len(cells) > 1 else ""
            if not date or not firm or firm in ("?", ""):
                continue
            pub = cells[2].strip() if len(cells) > 2 else ""

            seats, pcts, credited = {}, {}, set()
            good = 0
            for i, pid in col_party.items():
                if i >= len(cells):
                    continue
                # A merged cell (colspan) is credited once, to the first column.
                if cont[i] and pid in credited:
                    continue
                n, pct, ok = parse_seats(cells[i])
                if not ok:
                    continue
                credited.add(pid)
                seats[pid] = seats.get(pid, 0) + n
                if pct:
                    pcts[pid] = pct
                good += 1
            if good < 6:
                continue
            table_polls.append({"date": date, "firm": firm, "publisher": pub,
                                "seats": seats, "pcts": pcts})

        if not table_polls:
            continue
        table_newest = max(p["date"] for p in table_polls)
        if newest is None:
            newest = table_newest

        cutoff = newest - dt.timedelta(days=history_days)
        kept = [p for p in table_polls if p["date"] >= cutoff]
        if kept:
            # A table the headline and spread are drawn from must be fully
            # understood — an unknown column there could be a party missing
            # from the board.
            unmapped |= table_unmapped
            all_parties |= set(col_party.values())
            polls.extend(kept)

        # The sparklines reach further back than the spread window, into tables
        # whose columns are parties that have since merged or folded. Those are
        # read leniently: a column this project does not recognise contributes
        # nothing to a trend line, which is the correct outcome, not an error.
        trend_cutoff = newest - dt.timedelta(days=trend_days)
        older = [p for p in table_polls
                 if trend_cutoff <= p["date"] < cutoff]
        trend_polls.extend(kept + older)
        if not kept and not older:
            break                      # this table and everything after it is too old

    if unmapped:
        raise Warn(
            "Wikipedia has column(s) this project does not know about: "
            + ", ".join(repr(u) for u in sorted(unmapped))
            + ".\nA new party may have formed. Add it to scripts/mapping.json "
              "(and to the party list in index.html if it should appear) before "
              "the numbers can be trusted.")

    # De-duplicate polls that appear in more than one table.
    seen, deduped = set(), []
    for p in polls:
        key = (p["date"], p["firm"], p["publisher"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(p)
    polls = deduped

    if not polls:
        raise Warn("Parsed the table but found no usable poll rows.")
    polls.sort(key=lambda p: p["date"], reverse=True)

    # ---- pick the headline poll ----------------------------------------
    h = cfg["headline"]
    house = [p for p in polls if p["firm"].startswith(h["firm"])]
    if not house:
        raise Warn(
            f"No recent poll from {h['firm']} found. Either the house stopped "
            "polling or its name changed — update 'headline' in scripts/mapping.json.")

    # strict_publisher keeps the headline on one pollster AND one commissioning
    # outlet, so the series and its change arrows compare like with like. The
    # same house polling for a second outlet is a different sample on a
    # different date, not the next point in the same series.
    if h.get("strict_publisher"):
        pinned = [p for p in house if p["publisher"] == h["publisher"]]
        if not pinned:
            raise Warn(
                f"No recent {h['firm']} poll for {h['publisher']} in the last "
                f"{cfg.get('history_days', 45)} days, and 'strict_publisher' is on. "
                f"{h['firm']} has polled for: "
                + ", ".join(sorted({p['publisher'] for p in house}))
                + ".\nEither widen history_days, switch the publisher, or set "
                  "strict_publisher to false to accept any outlet.")
        house = pinned

    newest_date = max(p["date"] for p in house)
    same_day = [p for p in house if p["date"] == newest_date]
    pick = next((p for p in same_day if p["publisher"] == h["publisher"]), same_day[0])
    exact = pick["publisher"] == h["publisher"]

    total = sum(pick["seats"].values())
    if not (110 <= total <= 125):
        raise Warn(
            f"Headline poll ({pick['firm']}/{pick['publisher']}, {pick['date']}) "
            f"sums to {total} seats, not ~120. Refusing to publish; the column "
            "mapping is probably wrong.")

    # ---- previous poll from the same house, for the delta arrows --------
    prev = next((p for p in polls
                 if p["date"] < pick["date"]
                 and p["firm"] == pick["firm"]
                 and (p["publisher"] == pick["publisher"] or not exact)), None)

    # ---- spread across all houses in the window -------------------------
    window = int(cfg.get("spread_window_days", 21))
    recent = [p for p in polls if (pick["date"] - p["date"]).days <= window
              and p["date"] <= pick["date"]]
    spread = {}
    for pid in all_parties:
        vals = [p["seats"][pid] for p in recent if pid in p["seats"]]
        if vals:
            spread[pid] = {"min": min(vals), "max": max(vals), "n": len(vals)}

    # ---- app parties with no source column -------------------------------
    missing = {pid: why for pid, why in cfg.get("app_parties_without_column", {}).items()}

    # ---- per-party trend, headline house only ----------------------------
    # One house makes a real series; mixing houses would show the pollsters'
    # disagreement as if it were movement over time.
    seen_t = set()
    series = []
    for p in sorted(trend_polls, key=lambda q: q["date"]):
        if p["firm"] != pick["firm"] or p["publisher"] != pick["publisher"]:
            continue
        if p["date"] in seen_t:
            continue
        seen_t.add(p["date"])
        series.append(p)

    trend = {}
    for pid in all_parties:
        pts = [{"d": p["date"].isoformat(), "s": p["seats"][pid]}
               for p in series if pid in p["seats"]]
        # Two points is a line between two dots, not a trend worth drawing.
        if len(pts) >= 3:
            trend[pid] = pts

    parties = {}
    for pid in sorted(all_parties):
        s = pick["seats"].get(pid)
        if s is None:
            continue
        d = None
        if prev and pid in prev["seats"]:
            d = s - prev["seats"][pid]
        sp = spread.get(pid, {})
        parties[pid] = {
            "seats": s,
            "pct": pick["pcts"].get(pid),
            "delta": d,
            "spreadMin": sp.get("min"),
            "spreadMax": sp.get("max"),
            "spreadPolls": sp.get("n"),
            "trend": trend.get(pid),
        }

    # ---- alternate polls the reader can switch to -------------------------
    # Only polls that measure every party on the board are offered. One that
    # does not test a party would show it at zero and leave the house short of
    # 120, which reads as a collapse rather than as "this house did not ask".
    board = set(parties)
    excluded = set(cfg.get("exclude_pollsters", []))
    notes = cfg.get("pollster_notes", {})
    alts = []
    for p in polls:
        if not board.issubset(set(p["seats"])):
            continue
        if p["firm"] in excluded:
            continue
        alts.append({
            "id": f"{p['date'].isoformat()}-{re.sub(r'[^a-z0-9]+', '', p['firm'].lower())}",
            "date": p["date"].isoformat(),
            "displayDate": p["date"].strftime("%-d %b"),
            "displayDateLong": p["date"].strftime("%b %-d, %Y"),
            "firm": p["firm"],
            "publisher": p["publisher"],
            "label": f"{p['firm']} · {p['date'].strftime('%-d %b')}",
            "isHeadline": p is pick,
            "note": notes.get(p["firm"], ""),
            "seats": {k: v for k, v in p["seats"].items() if k in board},
        })
        if len(alts) >= int(cfg.get("alternate_polls", 6)):
            break
    if pick is not None and not any(a["isHeadline"] for a in alts):
        alts.insert(0, {
            "id": f"{pick['date'].isoformat()}-{re.sub(r'[^a-z0-9]+', '', pick['firm'].lower())}",
            "date": pick["date"].isoformat(),
            "displayDate": pick["date"].strftime("%-d %b"),
            "displayDateLong": pick["date"].strftime("%b %-d, %Y"),
            "firm": pick["firm"], "publisher": pick["publisher"],
            "label": f"{pick['firm']} · {pick['date'].strftime('%-d %b')}",
            "isHeadline": True,
            "note": notes.get(pick["firm"], ""),
            "seats": {k: v for k, v in pick["seats"].items() if k in board},
        })

    out = {
        "generated": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "source": {
            "pollster": pick["firm"],
            "publisher": pick["publisher"],
            "label": f"{pick['firm']}/{pick['publisher']}" if pick["publisher"] else pick["firm"],
            "fieldworkDate": pick["date"].isoformat(),
            "displayDate": pick["date"].strftime("%b %-d, %Y"),
            "exactHouseMatch": exact,
            "url": f"https://en.wikipedia.org/wiki/{PAGE}",
        },
        "spreadWindowDays": window,
        "trendDays": trend_days,
        "trendPolls": len(series),
        # Every headline-house poll in the window, so the page can ask whether a
        # coalition would have governed in each of them rather than only in the
        # latest one. One house, so it is a weekly series, not a mix of methods.
        "history": [{"d": p["date"].isoformat(), "seats": p["seats"]} for p in series],
        "alternates": alts,
        "spreadPollCount": len(recent),
        "totalSeats": total,
        "parties": parties,
        "unmappedAppParties": missing,
    }

    if verbose:
        note = "" if exact else f"  (via {pick['publisher']}, not {h['publisher']})"
        print(f"Headline: {out['source']['label']}  {pick['date']}  "
              f"total {total}{note}", file=sys.stderr)
        print(f"Spread from {len(recent)} polls in {window}d window", file=sys.stderr)
        for pid, v in sorted(parties.items(), key=lambda kv: -kv[1]["seats"]):
            d = f"{v['delta']:+d}" if v["delta"] is not None else "  "
            rng = (f"{v['spreadMin']}-{v['spreadMax']}"
                   if v["spreadMin"] is not None else "")
            print(f"  {pid:18s} {v['seats']:3d} {d:>3s}  [{rng}]", file=sys.stderr)
        if missing:
            print("\nNOTE — app parties with no Wikipedia column:", file=sys.stderr)
            for pid, why in missing.items():
                print(f"  {pid}: {why}", file=sys.stderr)
    return out


def summarise():
    """Markdown table of whatever is currently in data/polls.json."""
    if not os.path.exists(OUT):
        print("### Polling data\n\nNo `data/polls.json` was produced — "
              "see the fetch step above.")
        return 0
    with open(OUT, encoding="utf-8") as f:
        d = json.load(f)
    s = d["source"]
    print("### Polling data\n")
    print(f"**{s['label']}** — fieldwork {s['displayDate']} — "
          f"{d['totalSeats']} seats, spread across {d['spreadPollCount']} polls\n")
    print("| Party | Seats | Change | Range |")
    print("|---|---:|---:|---|")
    for pid, v in sorted(d["parties"].items(), key=lambda kv: -kv[1]["seats"]):
        delta = f"{v['delta']:+d}" if v["delta"] is not None else ""
        rng = (f"{v['spreadMin']}–{v['spreadMax']}"
               if v["spreadMin"] is not None else "")
        print(f"| {pid} | {v['seats']} | {delta} | {rng} |")
    if d.get("unmappedAppParties"):
        print("\n**Not in the latest poll:** "
              + ", ".join(d["unmappedAppParties"]))
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--summary", action="store_true",
                    help="print a markdown summary of the existing polls.json")
    ap.add_argument("--cache")
    ap.add_argument("--year", type=int)
    args = ap.parse_args()
    if args.summary:
        return summarise()
    try:
        data = build(cache=args.cache, year=args.year)
    except Warn as e:
        print(f"\nERROR: {e}\n", file=sys.stderr)
        return 1
    if args.dry_run:
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return 0
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(f"\nWrote {OUT}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
