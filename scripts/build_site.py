#!/usr/bin/env python3
"""
Turn a Claude Design "standalone" export into the deployable site.

The export is a single ~4 MB file: the page is a JSON-escaped string, and every
font, image and script is base64'd into one blob beside it. That is fine for
mailing a file around and bad for a page on kowaz.com, so this script unpacks it
into ordinary web assets, shrinks the two photographs, swaps the inlined font
files for Google Fonts, and wires the party numbers to data/polls.json.

Re-run it whenever you re-export from Claude Design:

    python3 scripts/build_site.py "~/Downloads/Knesset Coalition Builder - standalone.html"

Every edit below is asserted. If a future export renames something the patch
depends on, the build fails loudly and names the patch that no longer applies —
it never writes a page that is quietly missing its live data.
"""

import argparse
import base64
import gzip
import json
import os
import re
import shutil
import subprocess
import sys
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ASSETS = os.path.join(ROOT, "assets")

GOOGLE_FONTS = (
    "@import url('https://fonts.googleapis.com/css2?"
    "family=Newsreader:ital,opsz,wght@0,6..72,400;0,6..72,500;0,6..72,600;0,6..72,700;"
    "1,6..72,400&family=Libre+Franklin:ital,wght@0,400;0,500;0,600;0,700;0,800;1,400"
    "&display=swap');"
)

# Photographs render at 88px; 256px covers 2x displays with room to spare.
PHOTO_PX = 256

# Where readers actually land, and where scrapers fetch the share card from.
CANONICAL_URL = "https://kowaz.com/coalition"
OG_IMAGE_URL = "https://eliko90.github.io/coalition/assets/og-image.png"


class BuildError(Exception):
    pass


def patch(text, old, new, what, count=1):
    """Replace `old` exactly `count` times, or fail naming the patch.

    The count is asserted rather than assumed: a snippet that starts matching
    more places than intended is exactly how a build silently rewrites the
    wrong thing.
    """
    n = text.count(old)
    if n != count:
        raise BuildError(
            f"patch {what!r} expected exactly {count} match(es), found {n}.\n"
            f"The Claude Design export changed. Look for this snippet in the "
            f"export and update scripts/build_site.py:\n\n  {old[:200]}\n")
    return text.replace(old, new)


def decompress(entry):
    raw = base64.b64decode(entry["data"])
    if entry.get("compressed") in (None, False, "none"):
        return raw
    try:
        return gzip.decompress(raw)
    except Exception:
        return zlib.decompress(raw)


def unpack(export_path):
    text = open(export_path, encoding="utf-8").read()
    lines = text.split("\n")

    assets, page, ext = None, None, []
    for i, line in enumerate(lines):
        s = line.strip()
        if not s.startswith(("{", "[", '"')):
            continue
        try:
            val = json.loads(s)
        except Exception:
            continue
        if isinstance(val, dict) and val and all(
                isinstance(v, dict) and "data" in v for v in val.values()):
            assets = val
        elif isinstance(val, str) and "<x-dc>" in val:
            page = val
        elif isinstance(val, list) and val and isinstance(val[0], dict) \
                and "uuid" in val[0]:
            ext = val
    if assets is None or page is None:
        raise BuildError(
            "Could not find the asset blob and page inside the export. "
            "Is this the 'standalone' export from Claude Design?")
    return assets, page, ext


def shrink_png(src, dst, px):
    """Downscale with macOS sips, else copy. A 44px avatar does not need 2 MB."""
    if shutil.which("sips"):
        r = subprocess.run(
            ["sips", "-Z", str(px), src, "--out", dst],
            capture_output=True, text=True)
        if r.returncode == 0 and os.path.exists(dst):
            return True
    shutil.copyfile(src, dst)
    return False


# ---------------------------------------------------------------------------
# The live-data patches
# ---------------------------------------------------------------------------

LIVE_RUNTIME = r"""
/* ------------------------------------------------------------------
   Live polling data (data/polls.json, rebuilt by scripts/fetch_polls.py)

   PARTIES_BASE holds the editorial record — blocs, leaders, vetoes,
   positions. None of that can be scraped and none of it is touched here.
   Only the numbers are replaced: seats, the delta arrow, and the spread.
   If the fetch fails the page renders the baked-in snapshot instead, so a
   dead network degrades to "slightly stale" rather than a blank board.
   ------------------------------------------------------------------ */

const CANONICAL_URL = '__CANONICAL__';
const POLLS_URL = 'data/polls.json';
const EXTRA_URL = 'data/parties-extra.json';
const COMMENTARY_URL = 'data/commentary.json';
const STANCES_URL = 'data/stances.json';
const STALE_AFTER_DAYS = 14;

/* Shown when Ra'am is in a coalition with a non-Arab party. Overridden by
   `raamNote` in data/commentary.json; this is only the fallback. */
/* Fallback for the "wild card" line; overridden by wildCardNote in
   data/commentary.json. */
const WILD_CARD_DEFAULT = "The small parties between the blocs decide this. " +
  "Whichever of them clears the threshold picks the government.";

const RAAM_NOTE_DEFAULT = "Ra'am's willingness to sit with the Zionist " +
  "opposition, and theirs to rely on it, is the hinge the whole change bloc " +
  "turns on.";

/* The field moves faster than a redesign. data/parties-extra.json patches the
   board between Claude Design exports: an entry with a full party record adds
   one, and an entry of {"id": "...", "retired": true} takes one off after it
   merges away or quits the race. Seat totals stay whole either way. Fold the
   changes into the design when convenient and empty the file. */
let PARTIES_EXTRA = [];
let PARTIES_RAW = PARTIES_BASE;
/* PARTIES_RAW is the polling as reported; PARTIES_VIEW is the board as the
   reader has it — the same thing until a party is pushed under the threshold.
   Set once per render, read by byId and everything downstream. */
let PARTIES_VIEW = PARTIES_BASE;

function allBase() {
  const retired = new Set(PARTIES_EXTRA.filter(p => p.retired).map(p => p.id));
  const patches = new Map(PARTIES_EXTRA.filter(p => !p.retired).map(p => [p.id, p]));
  // An entry whose id already exists is merged over that party, so a leader,
  // a veto or a description can be corrected between design exports. One with
  // a new id is appended. Merging keeps the board's original ordering.
  const kept = PARTIES_BASE.filter(p => !retired.has(p.id)).map(p => {
    const patch = patches.get(p.id);
    if (!patch) return p;
    patches.delete(p.id);
    return Object.assign({}, p, patch);
  });
  return kept.concat(Array.from(patches.values()));
}

/* `override` is an alternate poll's seat map. The spread and the trend still
   come from the headline series — they describe the party, not this one poll —
   but every seat on the board follows whichever poll the reader picked. */
function applyLive(base, live, override) {
  if (!live || !live.parties) return base;
  return base.map(p => {
    const d = live.parties[p.id];
    if (!d) return p;
    const seats = (override && override[p.id] != null) ? override[p.id] : d.seats;
    const out = Object.assign({}, p, {
      seats: seats,
      delta: override ? 0 : (d.delta == null ? 0 : d.delta),
      pct: (override ? (seats === 0 ? d.pct : undefined) : d.pct) || undefined,
      nearThreshold: seats === 0 ||
        (d.spreadMin != null && d.spreadMin === 0 && seats > 0)
    });
    if (d.spreadMin != null && d.spreadMax != null) {
      // Keep the editor's commentary (the clause after the em dash) and let
      // the live range replace the numbers in front of it.
      const note = (p.spread || '').split(' — ').slice(1).join(' — ');
      const range = d.spreadMin === d.spreadMax
        ? `${d.spreadMin}`
        : `${d.spreadMin}–${d.spreadMax}`;
      const base_ = `${range} across ${d.spreadPolls} polls from every ` +
                    `pollster in the last ${live.spreadWindowDays} days`;
      out.spread = note ? `${base_} — ${note}` : base_;
    }
    return out;
  });
}

/* Reads `electionDate` from data/commentary.json. Returns '' when the date is
   absent or past, so the line simply disappears the morning after the vote
   rather than counting up into negatives. */
function electionCountdown(iso) {
  if (!iso) return '';
  const day = new Date(iso + 'T00:00:00Z');
  if (isNaN(day)) return '';
  const now = new Date();
  const today = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate());
  const days = Math.round((day.getTime() - today) / 86400000);
  if (days < 0) return '';
  if (days === 0) return 'Election day';
  if (days === 1) return '1 day to the election';
  return days + ' days to the election';
}

/* A party's seat trajectory over the trend window, in its own colour.

   Scaled to the party's own min and max rather than a shared 0-30 axis: at a
   common scale Ra'am moving 4 to 6 would be an invisible twitch, and the point
   of the line is direction, not magnitude. The seat count sits right beside it
   for magnitude. A flat series gets a flat line through the middle instead of
   a divide-by-zero. */
function sparkline(points, color) {
  if (!points || points.length < 3) return null;
  const W = 46, H = 14, PAD = 1.5;
  const vals = points.map(p => p.s);
  const lo = Math.min.apply(null, vals), hi = Math.max.apply(null, vals);
  const span = hi - lo;
  const x = i => (i / (points.length - 1)) * W;
  const y = v => span === 0 ? H / 2
    : PAD + (1 - (v - lo) / span) * (H - PAD * 2);
  const d = points.map((p, i) => (i ? 'L' : 'M') + x(i).toFixed(1) + ' ' +
                                 y(p.s).toFixed(1)).join(' ');
  const last = points[points.length - 1];
  return React.createElement('svg', {
      width: W, height: H, viewBox: `0 0 ${W} ${H}`,
      style: { display: 'block', overflow: 'visible' },
      'aria-hidden': 'true', focusable: 'false'
    },
    React.createElement('path', {
      d: d, fill: 'none', stroke: color, strokeWidth: 1.4,
      strokeLinecap: 'round', strokeLinejoin: 'round', opacity: 0.75
    }),
    React.createElement('circle', {
      cx: x(points.length - 1), cy: y(last.s), r: 1.9, fill: color
    })
  );
}

/* "23 → 20 over the last 9 weeks", for the party detail panel. */
function trendSentence(points, days) {
  if (!points || points.length < 3) return '';
  const first = points[0].s, last = points[points.length - 1].s;
  const weeks = Math.round(days / 7);
  const move = last === first ? 'level at ' + last
    : `${first} → ${last}`;
  return `${move} across ${points.length} polls over ${weeks} weeks ` +
         `(same pollster).`;
}

/* A coalition travels in the URL as ?c=likud.shas.utj, so a shared link opens
   the exact board the sender built instead of an empty one. Dots avoid the
   percent-encoding a comma would pick up. Ids are validated against the live
   board, so a link naming a party that has since folded degrades to the
   parties that still exist rather than breaking. */
/* Captured at load. The board only knows which ids are real once polls.json
   has arrived, but by then the first render has already called syncUrl and
   rewritten the address bar — so the parameter has to be read before anything
   else runs, not looked up again later. */
var URL_COALITION = (function () {
  try { return new URLSearchParams(window.location.search).get('c') || ''; }
  catch (e) { return ''; }
})();

function coalitionFromUrl(known) {
  if (!URL_COALITION) return null;
  var ids = URL_COALITION.split('.').filter(function (id) { return known.has(id); });
  return ids.length ? ids : null;
}

function coalitionUrl(base, ids) {
  return ids && ids.length ? base + '?c=' + ids.join('.') : base;
}

/* Would this coalition have governed in each of the headline house's recent
   weekly polls, or only in the latest one? A one-seat margin sits well inside
   sampling error, so "exactly 61" and "short by four" otherwise read as facts
   of the same kind when one of them is a coin toss.

   Only polls in which EVERY member of the coalition was measured are counted.
   A party polled for the first time last week would otherwise be scored as
   zero across the earlier polls and drag its coalition down for no reason. If
   that leaves too few polls to say anything, this says nothing. */
function robustness(history, ids, governsNow) {
  if (!history || !ids.length) return '';
  const usable = history.filter(h => ids.every(id => h.seats[id] != null));
  if (usable.length < 4) return '';
  const clears = usable.filter(h => ids.reduce((s, id) => s + h.seats[id], 0) >= 61);
  const when = iso => {
    const d = new Date(iso + 'T00:00:00Z');
    return d.getUTCDate() + ' ' + ['January','February','March','April','May','June','July',
      'August','September','October','November','December'][d.getUTCMonth()];
  };
  if (governsNow && clears.length === usable.length) {
    return 'Steady — this coalition has cleared 61 in every one of ' +
           `Kantar's weekly polls since ${when(usable[0].d)}.`;
  }
  if (governsNow) {
    const missed = usable.length - clears.length;
    return `Not settled — it clears 61 today, but fell short in ${missed} of ` +
           `Kantar's last ${usable.length} weekly polls.`;
  }
  if (clears.length) {
    return 'Close — it falls short today, but cleared 61 as recently as ' +
           when(clears[clears.length - 1].d) + '.';
  }
  return '';   // never close; "short by N" already says enough
}

/* "What if they miss the threshold?"

   Israel wastes every vote cast for a list under 3.25%, and the seats those
   votes would have bought are shared out among the lists that did clear it.
   That is why a party polling at four seats is not a small story: whether it
   survives moves seats to everyone else, usually across blocs.

   Seats here stand in for votes, which is an approximation. The real
   allocation is Bader-Ofer with surplus-vote agreements between named
   parties, and those agreements can pull a seat one way or the other. Good
   enough to show the shape of the change, not a projection. */
function applyThreshold(parties, dropped) {
  if (!dropped || !dropped.length) return parties;
  const drop = new Set(dropped);
  const freed = parties.reduce((s, p) => s + (drop.has(p.id) ? p.seats : 0), 0);
  const rest = parties.filter(p => !drop.has(p.id) && p.seats > 0);
  if (!freed || !rest.length) {
    return parties.map(p => drop.has(p.id) ? Object.assign({}, p, { seats: 0 }) : p);
  }
  const total = rest.reduce((s, p) => s + p.seats, 0);
  // Largest remainder, so the freed seats land whole and the house still sums
  // to 120 rather than drifting by a seat or two.
  const share = rest.map(p => {
    const exact = freed * p.seats / total;
    return { id: p.id, n: Math.floor(exact), rem: exact - Math.floor(exact) };
  });
  let left = freed - share.reduce((s, r) => s + r.n, 0);
  share.slice().sort((a, b) => b.rem - a.rem).forEach(r => {
    if (left > 0) { r.n++; left--; }
  });
  const add = {};
  share.forEach(r => { add[r.id] = r.n; });
  return parties.map(p => drop.has(p.id)
    ? Object.assign({}, p, { seats: 0, missedThreshold: true })
    : Object.assign({}, p, { seats: p.seats + (add[p.id] || 0) }));
}

/* Offer the toggle only where a pollster has actually shown the party missing,
   so it never invites a hypothetical the data does not support. */
function couldMiss(p) {
  // 3.25% is about four seats, so any party a pollster has put at four or
  // fewer is one survey away from missing — that includes Ra'am at 4-6.
  return !!p && p.seats > 0 &&
         ((p.spreadMinLive != null && p.spreadMinLive <= 4) || p.seats <= 5);
}

/* A pollster's measured divergence, phrased as a fact about the numbers rather
   than a label about the pollster. Only shown once it is big enough to matter,
   so the default view is not nagging the reader about a house that tracks the
   field. */
function leanSentence(a, live) {
  if (!a || a.lean == null || Math.abs(a.lean) < 2) return '';
  const dir = a.lean > 0 ? 'above' : 'below';
  const n = Math.abs(a.lean);
  return `${a.firm} has run ${n} ${n === 1 ? 'seat' : 'seats'} ${dir} the field ` +
         `median for ${live.leanBlocLabel || 'the largest bloc'}, across ` +
         `${a.leanPolls} polls in the last 30 days.`;
}

/* Compact labels for the quick strip. The full names are kept everywhere the
   reader has room to read them. */
const SHORT_NAMES = {
  'United Torah Judaism': 'UTJ',
  'Religious Zionism': 'Rel. Zionism',
  'Yisrael Beiteinu': 'Beiteinu',
  'The Democrats': 'Democrats',
  'Otzma Yehudit': 'Otzma',
  'Amcha Yisrael': 'Amcha',
  'The Reservists': 'Reservists',
  'Blue & White': 'Blue & White'
};
function shortName(name) {
  return SHORT_NAMES[name] || (name.length > 14 ? name.split(' ')[0] : name);
}

/* Where the reader's screen actually is, inside a frame that has no scroll of
   its own. position:fixed pins to the iframe's viewport, and the iframe is its
   full 8,000px height — so a "fixed, centred" panel lands in the middle of the
   whole document, thousands of pixels from whoever opened it. The parent page
   is the only one that knows, so it reports the visible slice and the overlay
   is positioned absolutely over that instead. */
let VIEWPORT = null;   // {offset, height} in this document's coordinates
function overlayBox(anchorY) {
  if (VIEWPORT && VIEWPORT.height) {
    return { top: Math.max(0, VIEWPORT.offset), height: VIEWPORT.height, centred: true };
  }
  // No word from the parent — either the embed predates the viewport messages
  // or this is a frame that never sends them. Fall back to the position of the
  // control that opened the panel, which is at least where the reader is
  // looking. A nominal screen height keeps the dimming from swallowing the
  // whole document.
  if (anchorY != null) {
    return { top: Math.max(0, anchorY - 24), height: 720, centred: false };
  }
  return null;
}

/* Colour by side, not by approval: two parties sharing a colour in a column
   are on the same side of that question. On annexation "a" is for it; on the
   draft "a" is drafting them. The neutral pair keeps the grid from reading as
   a scorecard. */
const STANCE_TONES = {
  a:    { bg: 'color-mix(in srgb, var(--navy) 13%, var(--paper-raised))',
          fg: 'var(--ink)', border: 'color-mix(in srgb, var(--navy) 34%, transparent)' },
  b:    { bg: 'color-mix(in srgb, var(--clay) 13%, var(--paper-raised))',
          fg: 'var(--ink)', border: 'color-mix(in srgb, var(--clay) 34%, transparent)' },
  mid:  { bg: 'var(--paper-raised)', fg: 'var(--ink-2)', border: 'var(--rule-strong)' },
  none: { bg: 'transparent', fg: 'var(--ink-3)', border: 'var(--rule)' }
};

function daysSince(iso) {
  if (!iso) return null;
  const then = new Date(iso + 'T00:00:00Z');
  if (isNaN(then)) return null;
  return Math.floor((Date.now() - then.getTime()) / 86400000);
}

/* The kingmaker search is a 2^n sweep over every viable coalition, so it is
   computed once per distinct set of seat counts rather than on every render. */
let __kmCache = null;
function kingmakerNow() {
  const sig = PARTIES_RAW.map(p => p.id + ':' + p.seats).join(',');
  if (!__kmCache || __kmCache.sig !== sig) {
    __kmCache = { sig: sig, val: computeKingmaker(PARTIES_RAW) };
  }
  return __kmCache.val;
}
"""

LOAD_POLLS = r"""
  componentDidMount() {
    this.loadPolls();
    if (window.parent && window.parent !== window) {
      this._onMsg = (e) => {
        const d = e.data;
        if (!d || d.type !== 'kcb-viewport') return;
        const next = { offset: +d.offset || 0, height: +d.height || 0 };
        if (!VIEWPORT || Math.abs(VIEWPORT.offset - next.offset) > 2 ||
            VIEWPORT.height !== next.height) {
          VIEWPORT = next;
          // Only a panel that is open needs repositioning.
          if (this.state.infoId || this.state.showIntro) this.forceUpdate();
        }
      };
      window.addEventListener('message', this._onMsg);
      window.parent.postMessage({ type: 'kcb-need-viewport' }, '*');
    }
  }

  componentWillUnmount() {
    if (this._onMsg) window.removeEventListener('message', this._onMsg);
  }

  loadPolls() {
    const json = url => fetch(url, { cache: 'no-cache' })
      .then(r => r.ok ? r.json() : Promise.reject(new Error(url + ': HTTP ' + r.status)));

    // Both side files are optional — a missing one is not an error.
    Promise.all([
      json(POLLS_URL),
      json(EXTRA_URL).catch(() => []),
      json(COMMENTARY_URL).catch(() => null),
      json(STANCES_URL).catch(() => null)
    ])
      .then(([live, extra, commentary, stances]) => {
        if (!live || !live.parties || !live.source) throw new Error('bad shape');
        PARTIES_EXTRA = Array.isArray(extra) ? extra : [];
        PARTIES_RAW = applyLive(allBase(), live);   // headline, until one is picked
        // Only now is the board known, so only now can a shared link be trusted.
        const shared = coalitionFromUrl(new Set(PARTIES_RAW.map(p => p.id)));
        this.setState(st => ({
          live: live, commentary: commentary, stances: stances, liveError: '',
          coalition: (shared && !st.coalition.length) ? shared : st.coalition
        }));
      })
      .catch(err => {
        // Leave PARTIES_RAW on the baked-in snapshot and say so in the byline.
        this.setState({ liveError: String((err && err.message) || err) });
      });
  }

  /* The warnings are notes to the editor, not to the reader — they name
     internal things like PARTIES_BASE. They show on localhost, or on the live
     site with ?check appended to the URL. Readers never see them. */
  showChecks() {
    if (typeof window === 'undefined') return false;
    const h = window.location.hostname;
    return h === 'localhost' || h === '127.0.0.1' || h === '' ||
           /[?&]check\b/.test(window.location.search);
  }

  dataWarnings(live) {
    if (!live || !this.showChecks()) return [];
    const out = [];
    const known = new Set(allBase().map(p => p.id));

    Object.keys(live.parties).forEach(id => {
      if (known.has(id)) return;
      const s = live.parties[id].seats;
      out.push({
        text: `“${id}” is in the latest poll${s ? ` on ${s} seats` : ''} but is not on this board — ` +
              `add it to PARTIES_BASE with its bloc and vetoes.`
      });
    });

    const missing = allBase()
      .filter(p => !live.parties[p.id])
      .map(p => p.name);
    if (missing.length) {
      out.push({ text: `Not in the latest poll: ${missing.join(', ')}. ` +
                       `Check whether they are still running.` });
    }

    const total = PARTIES_RAW.reduce((s, p) => s + p.seats, 0);
    if (total !== 120) {
      out.push({ text: `The board adds up to ${total} seats, not 120 — ` +
                       `a party in the poll is missing from the board.` });
    }

    const age = daysSince(live.source.fieldworkDate);
    if (age != null && age > STALE_AFTER_DAYS) {
      out.push({ text: `The most recent poll is ${age} days old.` });
    }
    return out;
  }
"""


OLD_HINT = '<sc-if value="{{ hasClosestHint }}" hint-placeholder-val="{{ false }}">'
NEW_HINT = '<sc-if value="{{ hasBlockedHint }}" hint-placeholder-val="{{ false }}"><div style="margin-top:14px;background:var(--gold-tint);border:1px solid var(--gold);border-radius:var(--r-md);padding:12px 14px;"><div class="ek-h4" style="color:var(--gold-deep);margin:0 0 6px;font-size:1.05rem;">What would have to change</div><p class="ek-body" style="font-size:0.92rem;margin:0 0 8px;color:var(--ink);">{{ blockedHeadline }}</p><p class="ek-small" style="margin:0 0 4px;color:var(--ink-2);font-weight:600;">{{ blockedSubhead }}</p><sc-for list="{{ blockedList }}" as="b" hint-placeholder-count="0"><div class="ek-small" style="margin:0 0 3px;color:var(--ink-2);">· <strong>{{ b.a }} and {{ b.b }}</strong> — {{ b.text }}</div></sc-for><button sc-camel-on-click="{{ applyBlockedHint }}" style="margin-top:10px;font-family:var(--font-sans);font-weight:700;font-size:0.85rem;padding:8px 14px;min-height:36px;background:var(--gold-deep);color:var(--paper);border:none;border-radius:var(--r-sm);cursor:pointer;">Load it anyway</button></div></sc-if><sc-if value="{{ hasImpossible }}" hint-placeholder-val="{{ false }}"><div style="margin-top:14px;background:var(--gold-tint);border:1px solid var(--gold);border-radius:var(--r-md);padding:12px 14px;"><div class="ek-h4" style="color:var(--gold-deep);margin:0 0 6px;font-size:1.05rem;">What would have to change</div><p class="ek-body" style="font-size:0.92rem;margin:0;color:var(--ink);">Nothing reaches 61 from here — even setting every stated red line aside, the parties still outside this coalition do not have the seats between them.</p></div></sc-if><sc-if value="{{ hasClosestHint }}" hint-placeholder-val="{{ false }}">'


KM_OLD = "There isn't one. Of the {{ totalCombos }} possible party combinations, only <strong>{{ viablePaths }}</strong> are minimal coalitions that clear 61 without crossing a stated refusal — and no small party is welcome in coalitions anchored by <em>both</em> sides. Every path runs through the big parties themselves."
KM_NEW = '{{ kingmakerNoneText }}'


STICKY = '<div style="position:sticky;top:0;z-index:5;background:color-mix('
MISS_BTN = '<sc-if value="{{ infoView.canMiss }}" hint-placeholder-val="{{ false }}"><button sc-camel-on-click="{{ infoView.toggleMiss }}" style="margin:0 0 16px;font-family:var(--font-sans);font-weight:700;font-size:0.85rem;padding:8px 14px;min-height:36px;background:transparent;color:var(--clay-deep);border:1.5px dashed var(--clay);border-radius:var(--r-sm);cursor:pointer;">{{ infoView.missLabel }}</button></sc-if>'
MISS_BANNER = '<sc-if value="{{ hasDropped }}" hint-placeholder-val="{{ false }}"><div style="margin-top:10px;background:var(--clay-tint);border:1px solid var(--clay);border-radius:var(--r-sm);padding:9px 12px;text-transform:none;letter-spacing:0;display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap;"><div><div class="ek-caption" style="margin:0 0 2px;color:var(--clay-deep);font-weight:700;text-transform:uppercase;letter-spacing:0.04em;">Hypothetical</div><div class="ek-small" style="margin:0;color:var(--ink-2);">{{ droppedNames }} misses the 3.25% threshold. Those {{ droppedSeats }} seats are shared out among the parties that cleared it, so every number below has moved.</div></div><button sc-camel-on-click="{{ clearDropped }}" style="font-family:var(--font-sans);font-weight:700;font-size:0.8rem;padding:7px 12px;min-height:34px;background:var(--clay);color:var(--paper);border:none;border-radius:var(--r-sm);cursor:pointer;flex:none;">Back to the polling</button></div></sc-if>'


CARD_MISS_OLD = '<sc-if value="{{ p.nearThreshold }}" hint-placeholder-val="{{ false }}">\n                  <div class="ek-caption" style="color:var(--clay-deep);margin-top:6px;font-weight:600;">near the threshold</div>'
CARD_MISS_NEW = '<sc-if value="{{ p.canMissCard }}" hint-placeholder-val="{{ false }}"><button sc-camel-on-click="{{ p.onMissToggle }}" style="margin-top:8px;font-family:var(--font-sans);font-weight:700;font-size:0.72rem;letter-spacing:0.02em;padding:5px 9px;min-height:30px;background:{{ p.missCardBg }};color:{{ p.missCardFg }};border:1.5px dashed var(--clay);border-radius:var(--r-pill);cursor:pointer;">{{ p.missCardLabel }}</button></sc-if><sc-if value="{{ p.nearThreshold }}" hint-placeholder-val="{{ false }}">\n                  <div class="ek-caption" style="color:var(--clay-deep);margin-top:6px;font-weight:600;">near the threshold</div>'
POLL_NOTE = '<sc-if value="{{ hasPollNote }}" hint-placeholder-val="{{ false }}"><div style="max-width:960px;margin:0 auto;padding:2px 20px 6px;"><div style="background:var(--gold-tint);border-left:3px solid var(--gold);border-radius:var(--r-sm);padding:8px 11px;"><span class="ek-small" style="color:var(--ink-2);"><strong style="color:var(--gold-deep);">About this pollster —</strong> {{ pollNote }}</span></div></div></sc-if>'
PICKER_ANCHOR = '<div style="position:sticky;top:0;z-index:5;background:color-mix('
PICKER = '<sc-if value="{{ hasPollPicker }}" hint-placeholder-val="{{ false }}"><div style="max-width:960px;margin:0 auto;padding:0 20px 4px;"><div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;"><span class="ek-meta" style="font-size:0.68rem;">Showing</span><sc-for list="{{ pollOptions }}" as="o" hint-placeholder-count="0"><button sc-camel-on-click="{{ o.pick }}" title="{{ o.title }}" style="font-family:var(--font-sans);font-weight:700;font-size:0.78rem;padding:6px 11px;min-height:32px;background:{{ o.bg }};color:{{ o.fg }};border:1px solid var(--rule-strong);border-radius:var(--r-pill);cursor:pointer;">{{ o.label }}</button></sc-for></div></div></sc-if>'


OVERLAY_OLD = 'style="position:fixed;inset:0;background:rgba(28,26,22,0.78);backdrop-filter:blur(2px);display:flex;align-items:flex-start;justify-content:center;padding:20px;padding-top:min(8vh,60px);z-index:50;"'
OVERLAY_NEW = 'style="position:fixed;inset:0;background:rgba(28,26,22,0.78);backdrop-filter:blur(2px);display:flex;align-items:center;justify-content:center;padding:20px;overflow-y:auto;z-index:50;"'
PANEL_OLD = 'style="max-width:420px;width:100%;background:var(--paper-raised);border-radius:var(--r-md);box-shadow:var(--shadow-lg);padding:22px;position:relative;"'
PANEL_NEW = 'style="max-width:min(640px,100%);width:100%;background:var(--paper-raised);border-radius:var(--r-md);box-shadow:var(--shadow-lg);padding:26px 28px;position:relative;max-height:calc(100vh - 40px);overflow-y:auto;"'


QUICK_STRIP = '<div style="max-width:1140px;margin:0 auto;padding:4px 20px 2px;"><div style="display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:7px;"><span class="ek-meta" style="font-size:0.68rem;">Tap to add or remove <span style="color:var(--clay);">·</span> dashed = near the threshold</span><sc-if value="{{ hasCoalition }}" hint-placeholder-val="{{ false }}"><span style="display:inline-flex;gap:6px;flex:none;"><sc-if value="{{ canUndo }}" hint-placeholder-val="{{ false }}"><button sc-camel-on-click="{{ undo }}" aria-label="Undo the last change" style="font-family:var(--font-sans);font-weight:700;font-size:0.72rem;padding:4px 9px;min-height:28px;background:none;color:var(--ink-3);border:1px solid var(--rule-strong);border-radius:var(--r-pill);cursor:pointer;">↺ Undo</button></sc-if><button sc-camel-on-click="{{ clearAll }}" aria-label="Clear the coalition" style="font-family:var(--font-sans);font-weight:700;font-size:0.72rem;padding:4px 9px;min-height:28px;background:none;color:var(--clay-deep);border:1px solid var(--clay);border-radius:var(--r-pill);cursor:pointer;">Clear</button></span></sc-if></div><div style="display:flex;flex-wrap:wrap;gap:6px;"><sc-for list="{{ quickParties }}" as="q" hint-placeholder-count="0"><span style="display:inline-flex;align-items:stretch;border:1.5px {{ q.borderStyle }} {{ q.border }};border-radius:var(--r-pill);background:{{ q.bg }};overflow:hidden;opacity:{{ q.opacity }};"><button sc-camel-on-click="{{ q.toggle }}" aria-pressed="{{ q.inCoalition }}" aria-label="{{ q.aria }}" title="{{ q.title }}" style="display:inline-flex;align-items:center;gap:6px;font-family:var(--font-sans);font-weight:700;font-size:0.78rem;padding:5px 4px 5px 10px;min-height:32px;background:none;color:{{ q.fg }};border:none;cursor:pointer;"><span style="width:8px;height:8px;border-radius:50%;background:{{ q.dot }};flex:none;"></span>{{ q.name }} <span style="font-weight:600;opacity:0.75;">{{ q.seats }}</span></button><button sc-camel-on-click="{{ q.info }}" aria-label="{{ q.infoAria }}" title="{{ q.infoAria }}" style="display:inline-flex;align-items:center;justify-content:center;width:26px;min-height:32px;background:none;border:none;border-left:1px solid {{ q.divider }};color:{{ q.infoFg }};font-family:var(--font-serif);font-style:italic;font-size:0.86rem;cursor:pointer;flex:none;">i</button></span></sc-for></div></div>'


ISSUES_ANCHOR = '<div style="max-width:1140px;margin:0 auto;padding:26px 20px 0;">'
ISSUES_GRID = '<sc-if value="{{ hasIssues }}" hint-placeholder-val="{{ false }}"><div style="max-width:1140px;margin:0 auto;padding:30px 20px 6px;"><div style="display:flex;align-items:baseline;gap:12px;margin-bottom:6px;"><div style="width:28px;height:2px;background:var(--clay);flex:none;transform:translateY(-4px);"></div><div class="ek-kicker" style="font-size:0.75rem;white-space:nowrap;">WHERE THEY STAND</div></div><p class="ek-caption" style="margin:0 0 14px;max-width:46rem;">The three questions that draw the red lines above. Parties sharing a colour in a column are on the same side of it — the colour marks the side, not a verdict. Tap any party for the full position.</p><div style="overflow-x:auto;"><div style="min-width:{{ issueMinWidth }};"><div style="display:grid;grid-template-columns:{{ issueGridCols }};gap:6px;align-items:end;margin-bottom:6px;"><div></div><sc-for list="{{ issueCols }}" as="col" hint-placeholder-count="0"><div class="ek-meta" style="font-size:0.68rem;">{{ col.label }}</div></sc-for></div><sc-for list="{{ issueRows }}" as="row" hint-placeholder-count="0"><div style="display:grid;grid-template-columns:{{ issueGridCols }};gap:6px;margin-bottom:6px;align-items:stretch;"><button sc-camel-on-click="{{ row.open }}" aria-label="{{ row.aria }}" style="display:flex;align-items:center;gap:7px;text-align:left;background:none;border:none;padding:6px 2px;cursor:pointer;font-family:var(--font-sans);font-weight:700;font-size:0.82rem;color:var(--ink);min-height:38px;"><span style="width:8px;height:8px;border-radius:50%;background:{{ row.color }};flex:none;"></span>{{ row.name }} <span style="font-weight:600;color:var(--ink-3);">{{ row.seats }}</span></button><sc-for list="{{ row.cells }}" as="c" hint-placeholder-count="0"><div title="{{ c.full }}" style="background:{{ c.bg }};color:{{ c.fg }};border:1px solid {{ c.border }};border-radius:var(--r-sm);padding:7px 9px;font-family:var(--font-sans);font-size:0.76rem;line-height:1.3;display:flex;align-items:center;">{{ c.label }}</div></sc-for></div></sc-for></div></div></div></sc-if>'


METHOD_P1_OLD = '<p class="ek-caption" style="margin:0 0 10px;max-width:44rem;text-wrap:pretty;">This tool uses the Kantar/Kan 11 poll rather than an all-pollster average. Channel 14\'s polls consistently show the Netanyahu bloc far ahead of every other pollster and are excluded. At the same time, Israeli polls have historically underestimated the right on election day — so rather than average toward the more optimistic-for-the-opposition surveys, I use Kan 11, which tends to sit slightly to the right of the pack. Treat it as one considered snapshot, not a forecast.</p>'
METHOD_P1_NEW = '<p class="ek-caption" style="margin:0 0 10px;max-width:44rem;text-wrap:pretty;">{{ methodologyNote }}</p>'
METHOD_P2_OLD = '<p class="ek-caption" style="margin:0 0 8px;max-width:44rem;text-wrap:pretty;">Seat projections from the Kantar/Kan 11 poll, fieldwork Aug 9, 2026, as recorded on Wikipedia\'s polling page for the 2026 Israeli legislative election. Change arrows compare against the previous polling round; pollster ranges span recent published polls. Refusal statements are simplified summaries of parties\' publicly stated coalition positions, for illustration.</p>'
METHOD_P2_NEW = '<p class="ek-caption" style="margin:0 0 8px;max-width:44rem;text-wrap:pretty;">{{ sourceNote }}</p>'


THRESH_ANCHOR = '<sc-if value="{{ hasIssues }}" hint-placeholder-val="{{ false }}">'
THRESH_BLOCK = '<sc-if value="{{ hasThresholdScenarios }}" hint-placeholder-val="{{ false }}"><div style="max-width:1140px;margin:0 auto;padding:26px 20px 30px;"><div style="display:flex;align-items:baseline;gap:12px;margin-bottom:6px;"><div style="width:28px;height:2px;background:var(--clay);flex:none;transform:translateY(-4px);"></div><div class="ek-kicker" style="font-size:0.75rem;white-space:nowrap;">IF THEY MISS THE THRESHOLD</div></div><p class="ek-caption" style="margin:0 0 14px;max-width:46rem;">Israel wastes every vote for a list under 3.25%, and shares those seats among the lists that clear it. This is where the election is actually decided.</p><div style="display:flex;flex-wrap:wrap;gap:8px;"><sc-for list="{{ thresholdScenarios }}" as="t" hint-placeholder-count="0"><button sc-camel-on-click="{{ t.apply }}" aria-pressed="{{ t.active }}" style="font-family:var(--font-sans);font-weight:700;font-size:0.85rem;padding:9px 14px;min-height:40px;background:{{ t.bg }};color:{{ t.fg }};border:1.5px {{ t.borderStyle }} {{ t.border }};border-radius:var(--r-sm);cursor:pointer;">{{ t.label }}</button></sc-for></div></div></sc-if>'


BLOCS_TAIL = '</div>\n            </sc-for>\n          </div>\n        </div>\n      </sc-for>\n    </div>'


def move_analysis(html):
    """Lift the state-of-play paragraph above the tool.

    It sat at 3,600px on desktop and 6,800px on mobile — eight screens down,
    past everything, for a piece whose readers arrive from a newsletter. Only
    that paragraph moves: the outlooks and the kingmaker are conclusions about
    a board the reader has not seen yet, and moving the whole section put a
    screen and a half of prose in front of the first thing you can touch.
    """
    i = html.index("THE STATE OF PLAY")
    nav = html.rindex('<div style="background:var(--navy);', 0, i)
    inner = html.index('<div style="max-width:1140px', nav)
    start = html.index("<div style=", inner + 40)
    depth, j = 0, start
    while True:
        o, c = html.find("<div", j), html.find("</div>", j)
        if c == -1:
            raise BuildError("could not find the end of the analysis section")
        if o != -1 and o < c:
            depth += 1
            j = o + 4
        else:
            depth -= 1
            j = c + 6
            if depth == 0:
                break
    block = html[start:j]
    if "{{ stateOfPlay }}" not in block:
        raise BuildError("state-of-play group no longer holds the paragraph")
    if 'list="{{ outlooks }}"' in block:
        raise BuildError("state-of-play group unexpectedly swallowed the outlooks")
    html = html[:start] + html[j:]
    # It leaves a dark section and lands on paper, so it carries its own band.
    wrapped = ('<div style="background:var(--navy);margin-bottom:8px;">'
               '<div style="max-width:1140px;margin:0 auto;padding:34px 20px 10px;">'
               + block + '</div></div>')
    return patch(html, STICKY, wrapped + STICKY, "analysis-moved")


def move_scenarios(html):
    """Relocate the "Try a scenario" block to just under the party strip.

    Cut rather than copied: the block is found by its own container, and the
    surrounding section keeps its background and rule, so only the position
    changes.
    """
    i = html.index("TRY A SCENARIO")
    start = html.rindex('<div style="max-width:1140px;margin:0 auto;padding:24px 20px 28px;">', 0, i)
    depth, j = 0, start
    while True:
        o, c = html.find("<div", j), html.find("</div>", j)
        if c == -1:
            raise BuildError("could not find the end of the scenarios block")
        if o != -1 and o < c:
            depth += 1
            j = o + 4
        else:
            depth -= 1
            j = c + 6
            if depth == 0:
                break
    block = html[start:j]
    if 'list="{{ presets }}"' not in block:
        raise BuildError("the scenarios block no longer holds the preset buttons")
    html = html[:start] + html[j:]
    # Sits after the strip and before the hemicycle, which QUICK_STRIP precedes.
    return patch(html, QUICK_STRIP, QUICK_STRIP + block, "scenarios-moved")


def build(export_path, out_html):
    assets, page, ext = unpack(export_path)
    os.makedirs(ASSETS, exist_ok=True)

    ext_by_uuid = {e["uuid"]: e["id"] for e in ext}

    # ---- write scripts and images ---------------------------------------
    written = {}
    tmp = os.path.join(ASSETS, ".tmp.png")
    for uuid, entry in assets.items():
        mime = entry.get("mime", "")
        if uuid not in page and uuid not in ext_by_uuid:
            continue
        if "javascript" in mime:
            url = ext_by_uuid.get(uuid, "")
            if "react-dom" in url:
                name = "react-dom.production.min.js"
            elif "react" in url:
                name = "react.production.min.js"
            elif len(entry["data"]) > 60000:
                name = "dc-app.js"
            else:
                name = "dc-runtime.js"
            with open(os.path.join(ASSETS, name), "wb") as f:
                f.write(decompress(entry))
            written[uuid] = "assets/" + name
        elif mime.startswith("image/"):
            with open(tmp, "wb") as f:
                f.write(decompress(entry))
            before = os.path.getsize(tmp)
            # Name the photo after its alt text in the page.
            m = re.search(r'<img src="' + re.escape(uuid) + r'" alt="([^"]+)"', page)
            slug = re.sub(r"[^a-z0-9]+", "-", (m.group(1) if m else uuid).lower()).strip("-")
            name = slug + ".png"
            dst = os.path.join(ASSETS, name)
            shrink_png(tmp, dst, PHOTO_PX)
            after = os.path.getsize(dst)
            print(f"  {name}: {before//1024} KB -> {after//1024} KB", file=sys.stderr)
            written[uuid] = "assets/" + name
    if os.path.exists(tmp):
        os.remove(tmp)

    html = page

    # ---- fonts: drop ~1.2 MB of inlined woff2 for Google Fonts ----------
    faces = re.findall(r"/\*[^*]*\*/\s*@font-face\s*\{[^}]*\}", html)
    if len(faces) < 10:
        raise BuildError(f"expected the inlined @font-face block, found {len(faces)} rules")
    first = html.index(faces[0])
    last = html.index(faces[-1]) + len(faces[-1])
    html = html[:first] + GOOGLE_FONTS + html[last:]

    # ---- rewrite asset urls ---------------------------------------------
    for uuid, path in written.items():
        html = html.replace(uuid, path)
    leftover = [u for u in assets if u in html]
    if leftover:
        raise BuildError(f"asset ids still referenced but not written: {leftover}")

    # ---- react must load before the runtime ------------------------------
    html = patch(
        html,
        '<script src="assets/dc-runtime.js"></script>',
        '<script src="assets/react.production.min.js"></script>\n'
        '<script src="assets/react-dom.production.min.js"></script>\n'
        '<script src="assets/dc-runtime.js"></script>',
        "react-before-runtime")

    # ---- title and description ------------------------------------------
    # Canonical reader-facing page is the WordPress one; the Pages copy carries
    # the same cards so a directly shared Pages link previews properly too.
    desc = ("Build Israel's next coalition from the latest Knesset polling — "
            "and see which combinations the parties have already ruled out.")
    html = patch(
        html, "<head>\n<meta charset=\"utf-8\">",
        "<head>\n<meta charset=\"utf-8\">\n"
        "<title>Road to 61 — Knesset Coalition Builder</title>\n"
        f'<meta name="description" content="{desc}">\n'
        f'<link rel="canonical" href="{CANONICAL_URL}">\n'
        '<meta property="og:type" content="website">\n'
        '<meta property="og:site_name" content="The Middle Ground">\n'
        '<meta property="og:title" content="Road to 61 — build Israel\'s next coalition">\n'
        f'<meta property="og:description" content="{desc}">\n'
        f'<meta property="og:url" content="{CANONICAL_URL}">\n'
        f'<meta property="og:image" content="{OG_IMAGE_URL}">\n'
        '<meta property="og:image:width" content="1200">\n'
        '<meta property="og:image:height" content="630">\n'
        '<meta property="og:image:alt" content="Road to 61 — a Knesset seat bar '
        'against the 61-seat line needed to govern">\n'
        '<meta name="twitter:card" content="summary_large_image">\n'
        '<meta name="twitter:title" content="Road to 61 — build Israel\'s next coalition">\n'
        f'<meta name="twitter:description" content="{desc}">\n'
        f'<meta name="twitter:image" content="{OG_IMAGE_URL}">',
        "head-title")

    # ---- live data: rename the base list, add the runtime ----------------
    html = patch(html, "const PARTIES_RAW = [", "const PARTIES_BASE = [", "rename-base")

    html = patch(
        html,
        "const BLOC_META = {",
        LIVE_RUNTIME.replace('__CANONICAL__', CANONICAL_URL) + "\nconst BLOC_META = {",
        "live-runtime")

    # ---- kingmaker: computed once at load -> recomputed when seats change
    html = patch(
        html,
        "const KINGMAKER = (function () {\n  const live = PARTIES_RAW.filter(p => p.seats > 0);",
        "function computeKingmaker(PARTIES_RAW) {\n  const live = PARTIES_RAW.filter(p => p.seats > 0);",
        "kingmaker-signature")
    html = patch(
        html,
        "  return { viablePaths, combos: (1 << n) - 1, party: top ? "
        "{ name: top.name, leader: top.leader.split(' & ')[0], count: counts[top.id] } : null };\n})();",
        "  return { viablePaths, combos: (1 << n) - 1, party: top ? "
        "{ name: top.name, leader: top.leader.split(' & ')[0], count: counts[top.id] } : null };\n}",
        "kingmaker-close")
    html = patch(html, "const kingmaker = KINGMAKER;",
                 "const kingmaker = kingmakerNow();", "kingmaker-call")

    # ---- a retired party must not leave dangling references ---------------
    # The presets and outlooks are written against the party list baked into
    # the design, so retiring one (or a poll dropping it) leaves ids that
    # byId() no longer resolves. Filtering on load keeps a stale id from ever
    # entering the coalition.
    html = patch(
        html,
        """  loadPreset(ids, note) {
    this.pushHistory(this.state.coalition);
    this.setState({ coalition: [...ids], presetNote: note || '' });
  }""",
        """  loadPreset(ids, note) {
    const known = new Set(allBase().map(p => p.id));
    this.pushHistory(this.state.coalition);
    this.setState({ coalition: ids.filter(id => known.has(id)), presetNote: note || '' });
  }""",
        "preset-filter")

    # And belt-and-braces: never dereference an unresolved id while rendering.
    html = patch(
        html,
        """          for (const seatedId of coalition) {
            const seated = this.byId(seatedId);
            if ((p.refusals || []).some(r => r.id === seatedId)) return `Refuses to sit with ${seated.name}`;""",
        """          for (const seatedId of coalition) {
            const seated = this.byId(seatedId);
            if (!seated) continue;
            if ((p.refusals || []).some(r => r.id === seatedId)) return `Refuses to sit with ${seated.name}`;""",
        "conflict-hint-guard")

    # ---- fetch on mount ---------------------------------------------------
    html = patch(
        html,
        "  setView(mode) { this.setState({ viewMode: mode }); }",
        LOAD_POLLS + "\n  setView(mode) { this.setState({ viewMode: mode }); }",
        "load-polls")

    # ---- byline reads from the live file ---------------------------------
    html = patch(
        html,
        "      pollingDate: this.props.pollingDate ?? 'Aug 9, 2026',",
        "      pollingDate: (chosen && chosen.displayDateLong) || "
        "(live && live.source.displayDate) || this.props.pollingDate || 'Aug 9, 2026',",
        "polling-date")

    # The standing-analysis paragraph is the fastest thing on the page to go
    # stale, so it can be rewritten in data/commentary.json without re-exporting
    # the design.
    html = patch(
        html,
        "      stateOfPlay: this.props.stateOfPlay ?? \"",
        "      stateOfPlay: (commentary && commentary.stateOfPlay) || "
        "this.props.stateOfPlay || \"",
        "state-of-play")
    html = patch(
        html,
        "      pollingSource: this.props.pollingSource ?? 'Kantar/Kan 11',",
        "      pollingSource: chosen ? `${chosen.firm}/${chosen.publisher}` : "
        "((live && live.source.label) || this.props.pollingSource || 'Kantar/Kan 11'),\n"
        "      dataNote,\n"
        "      hasDataNote: !!dataNote,\n"
        "      wildCardNote: (commentary && commentary.wildCardNote) || WILD_CARD_DEFAULT,\n"
        "      countdown,\n"
        "      hasCountdown: !!countdown,\n"
        "      raamNote: (commentary && commentary.raamNote) || RAAM_NOTE_DEFAULT,\n"
        "      hasDataWarning: warnings.length > 0,\n"
        "      dataWarnings: warnings,",
        "polling-source")

    html = patch(
        html,
        "  renderVals() {\n    const coalition = this.state.coalition;",
        "  renderVals() {\n"
        "    const live = this.state.live;\n"
        "    const commentary = this.state.commentary;\n"
        "    const countdown = electionCountdown(commentary && commentary.electionDate);\n"
        "    const warnings = this.dataWarnings(live);\n"
        "    const age = live ? daysSince(live.source.fieldworkDate) : null;\n"
        "    // The source line above already names the pollster and the date;\n"
        "    // this second line only speaks up when something is wrong.\n"
        "    const dataNote = live\n"
        "      ? ''\n"
        "      : (this.state.liveError\n"
        "          ? 'Showing the built-in snapshot — live polling data could not be loaded.'\n"
        "          : 'Loading the latest polling…');\n"
        "    const coalition = this.state.coalition;",
        "render-live-head")

    # ---- embedding: let the frame measure its real content ---------------
    # The page reports its own scrollHeight to the parent so a WordPress embed
    # can size the iframe. But the root element is min-height:100vh, so the
    # document always stretches to fill whatever height the iframe already has
    # and the measurement can only ever grow. Making that 100vh overridable,
    # and zeroing it when framed, lets the document collapse to its content.
    html = patch(
        html,
        '<div style="min-height:100vh;width:100%;max-width:100%;background:var(--paper);',
        '<div style="min-height:var(--kcb-minh, 100vh);width:100%;max-width:100%;'
        'background:var(--paper);',
        "root-minheight")

    html = patch(
        html,
        """    (function () {
      function postHeight() {
        var h = document.documentElement.scrollHeight;
        if (window.parent && window.parent !== window) {
          window.parent.postMessage({ type: 'kcb-resize', height: h }, '*');
        }
      }
      window.addEventListener('load', postHeight);
      window.addEventListener('resize', postHeight);
      setInterval(postHeight, 1500);
    })();""",
        """    (function () {
      var framed = window.parent && window.parent !== window;
      if (!framed) return;

      // Collapse the full-height root so scrollHeight reflects the content
      // rather than the height the parent last gave us.
      var s = document.createElement('style');
      s.textContent = ':root{--kcb-minh:0px}' +
                      'html,body{height:auto;min-height:0}';
      document.head.appendChild(s);

      var last = 0;
      function postHeight() {
        // Measure the body box, not documentElement.scrollHeight: an iframe
        // with scrolling="no" clamps scrollHeight to the height the parent
        // already set, so the frame could never shrink and would never grow
        // past its initial guess. A layout box has no such ceiling.
        var h = Math.ceil(document.body.getBoundingClientRect().height);
        if (!h || Math.abs(h - last) < 2) return;   // don't spam on 1px jitter
        last = h;
        window.parent.postMessage({ type: 'kcb-resize', height: h }, '*');
      }
      window.addEventListener('load', postHeight);
      window.addEventListener('resize', postHeight);
      if (window.ResizeObserver) {
        // Fires the moment a party is added or a modal opens, so the frame
        // resizes with the interaction instead of up to 1.5s later.
        new ResizeObserver(postHeight).observe(document.documentElement);
      }
      setInterval(postHeight, 1500);
    })();""",
        "post-height")

    # The Ra'am note is the most perishable sentence on the page — it turns on
    # one MK's decision — so it comes from data/commentary.json too.
    html = patch(
        html,
        """<p class="ek-body" style="font-size:0.92rem;margin:0;color:var(--ink);line-height:1.4;">Rumors that Ra'am may add a Jewish MK — Yoav Segalovitz, formerly of Yesh Atid — and formally accept Israel as a Jewish state would make it a far more legitimate partner for the Zionist opposition.</p>""",
        """<p class="ek-body" style="font-size:0.92rem;margin:0;color:var(--ink);line-height:1.4;">{{ raamNote }}</p>""",
        "raam-note")

    # Outlooks and the countdown are editorial and perishable, so both come
    # from data/commentary.json with the design's version as the fallback.
    html = patch(
        html,
        "    const outlooks = OUTLOOKS.map(o => {",
        "    const outlooks = ((commentary && commentary.outlooks) || OUTLOOKS).map(o => {",
        "outlooks-source")

    html = patch(
        html,
        "    const presets = PRESETS.map(preset => ({",
        "    const presets = ((commentary && commentary.presets) || PRESETS).map(preset => ({",
        "presets-source")

    html = patch(
        html,
        "      Polling as of {{ pollingDate }} <span style=\"color:var(--clay);\">\u00b7</span> {{ pollingSource }}",
        "      Polling as of {{ pollingDate }} <span style=\"color:var(--clay);\">\u00b7</span> {{ pollingSource }}"
        "<sc-if value=\"{{ hasCountdown }}\" hint-placeholder-val=\"{{ false }}\">"
        "<span> <span style=\"color:var(--clay);\">\u00b7</span> "
        "<strong style=\"color:var(--ink-2);\">{{ countdown }}</strong></span></sc-if>",
        "countdown-markup")

    # Sparkline: carry the trend series onto each party, render it in the card
    # beside the change arrow, and spell it out in the detail panel.
    html = patch(
        html,
        """      nearThreshold: seats === 0 ||
        (d.spreadMin != null && d.spreadMin === 0 && seats > 0)
    });""",
        """      nearThreshold: seats === 0 ||
        (d.spreadMin != null && d.spreadMin === 0 && seats > 0),
      spreadMinLive: d.spreadMin,
      trend: d.trend || null,
      trendDays: live.trendDays
    });""",
        "carry-trend")

    html = patch(
        html,
        "        seatBlocks: Array.from({ length: p.seats }, (_, i) => ({ key: i, color: p.color })),",
        "        seatBlocks: Array.from({ length: p.seats }, (_, i) => ({ key: i, color: p.color })),\n"
        "        trendSvg: sparkline(p.trend, p.color),\n"
        "        hasTrend: !!(p.trend && p.trend.length >= 3),",
        "party-trend-fields")

    html = patch(
        html,
        """                  <sc-if value="{{ p.hasDelta }}" hint-placeholder-val="{{ false }}">
                    <span class="ek-meta" style="color:{{ p.deltaColor }};margin-left:auto;font-weight:700;">{{ p.deltaLabel }}</span>
                  </sc-if>""",
        """                  <sc-if value="{{ p.hasTrend }}" hint-placeholder-val="{{ false }}">
                    <span style="margin-left:auto;display:flex;align-items:center;">{{ p.trendSvg }}</span>
                  </sc-if>
                  <sc-if value="{{ p.hasDelta }}" hint-placeholder-val="{{ false }}">
                    <span class="ek-meta" style="color:{{ p.deltaColor }};margin-left:{{ p.deltaMargin }};font-weight:700;">{{ p.deltaLabel }}</span>
                  </sc-if>""",
        "card-sparkline")

    html = patch(
        html,
        "        deltaColor: p.delta > 0 ? 'var(--success)' : 'var(--danger)',",
        "        deltaColor: p.delta > 0 ? 'var(--success)' : 'var(--danger)',\n"
        "        deltaMargin: (p.trend && p.trend.length >= 3) ? '8px' : 'auto',",
        "delta-margin")

    html = patch(
        html,
        "      spread: infoParty.spread",
        "      spread: infoParty.spread,\n"
        "      trendText: trendSentence(infoParty.trend, infoParty.trendDays),\n"
        "      hasTrendText: !!trendSentence(infoParty.trend, infoParty.trendDays),\n"
        "      trendSvgBig: sparkline(infoParty.trend, infoParty.color)",
        "info-trend")

    html = patch(
        html,
        '''<div class="ek-caption" style="margin:0 0 12px;color:var(--ink-3);">Pollster range: {{ infoView.spread }}</div>''',
        '''<div class="ek-caption" style="margin:0 0 6px;color:var(--ink-3);">Pollster range: {{ infoView.spread }}</div>'''
        '''<sc-if value="{{ infoView.hasTrendText }}" hint-placeholder-val="{{ false }}">'''
        '''<div class="ek-caption" style="margin:0 0 12px;color:var(--ink-3);display:flex;align-items:center;gap:8px;">'''
        '''<span>Trend: {{ infoView.trendText }}</span>{{ infoView.trendSvgBig }}</div></sc-if>''',
        "info-trend-markup")

    # The one sentence of static prose that names a party. It outlived Zionist
    # Home, so it reads from commentary.json now.
    html = patch(
        html,
        """The wild card is <strong>Yoaz Hendel's Zionist Home</strong> — <em>if</em> it clears the threshold, a big if. It sits squarely between the blocs and could join either one, but its refusal to sit with the ultra-Orthodox parties closes off the most natural path on the right.""",
        """{{ wildCardNote }}""",
        "wild-card-note")

    # Keep the address bar in step with the board, in this window and in the
    # parent page when embedded, so the URL is always copyable.
    html = patch(
        html,
        "  componentDidUpdate(_prevProps) {\n  }",
        "  componentDidUpdate(_prevProps) {\n  }",
        "noop") if False else html

    html = patch(
        html,
        """  setView(mode) { this.setState({ viewMode: mode }); }""",
        """  setView(mode) { this.setState({ viewMode: mode }); }

  /* The canonical page is the WordPress one, so that is what gets shared —
     never this iframe's github.io address. */
  shareUrl() {
    return coalitionUrl(CANONICAL_URL, this.state.coalition);
  }

  /* Reflect the coalition in the address bar. Inside an iframe the visible URL
     belongs to the parent, so the parent is asked to update it; standalone,
     this window updates its own. replaceState, not pushState — building a
     coalition should not fill the back button with every click. */
  syncUrl() {
    const ids = this.state.coalition;
    try {
      if (window.parent && window.parent !== window) {
        window.parent.postMessage({ type: 'kcb-coalition', ids: ids }, '*');
      } else {
        history.replaceState(null, '', coalitionUrl(window.location.pathname, ids));
      }
    } catch (e) { /* a sandboxed frame may refuse; the board still works */ }
  }

  componentDidUpdate() { this.syncUrl(); }""",
        "share-url-sync")

    # Share links and the share image now carry the coalition.
    for old, new, name, *cnt in [
        ("this.shareText(coalitionPartiesRaw, totalSeats, governs) + ' https://kowaz.com/coalition'",
         "this.shareText(coalitionPartiesRaw, totalSeats, governs) + ' ' + this.shareUrl()",
         "share-text-url", 2),
        ("encodeURIComponent('https://kowaz.com/coalition')",
         "encodeURIComponent(this.shareUrl())",
         "share-x-url"),
    ]:
        html = patch(html, old, new, name, cnt[0] if cnt else 1)

    # Keyboard access: the party cards carry the whole interaction and were
    # plain divs, so the board could not be used without a pointer at all.
    OLD_CARD = ('<div draggable="true" sc-camel-on-drag-start="{{ p.onDragStart }}" '
                'sc-camel-on-click="{{ p.onTap }}" class="kb-card"')
    NEW_CARD = ('<div draggable="true" role="button" tabindex="0" '
                'aria-pressed="{{ p.inCoalition }}" aria-label="{{ p.ariaLabel }}" '
                'sc-camel-on-key-down="{{ p.onKey }}" '
                'sc-camel-on-drag-start="{{ p.onDragStart }}" '
                'sc-camel-on-click="{{ p.onTap }}" class="kb-card"')
    html = patch(html, OLD_CARD, NEW_CARD, "card-keyboard")

    html = patch(
        html,
        "        onTap: () => this.toggleAdd(p.id),",
        "        onTap: () => this.toggleAdd(p.id),\n"
        "        ariaLabel: `${p.name}, ${p.seats} seats` +\n"
        "          (coalitionSet.has(p.id) ? ', in your coalition' : '') +\n"
        "          '. Press to ' + (coalitionSet.has(p.id) ? 'remove' : 'add') + '.',\n"
        "        onKey: (e) => {\n"
        "          if (e.key !== 'Enter' && e.key !== ' ' && e.key !== 'Spacebar') return;\n"
        "          e.preventDefault();   // Space would otherwise scroll the page\n"
        "          this.toggleAdd(p.id);\n"
        "        },",
        "card-keyboard-handlers")

    # A focus ring the design never needed when nothing was focusable.
    html = patch(
        html,
        "    .kb-card:hover {",
        "    .kb-card:focus-visible { outline: 3px solid var(--clay); outline-offset: 2px; }\n"
        "    .kb-card:hover {",
        "focus-ring")

    # "What would have to change": when no combination reaches 61 without
    # crossing a stated red line, the interesting question stops being "who
    # else could join" and becomes "whose promise would have to break". The
    # existing hint only searches compatible parties and goes silent exactly
    # when the answer matters most.
    html = patch(
        html,
        "    this._hintCache = { key: hintKey, value: closestHint };",
        """    if (!governs && coalition.length > 0 && !closestHint) {
      const available = PARTIES_RAW
        .filter(p => !coalitionSet.has(p.id) && p.seats > 0)
        .slice(0, 14);
      const deficit = 61 - totalSeats;

      // Every refusal the coalition would cross, named once per pair.
      const brokenBy = (picked) => {
        const seatedPlus = coalitionPartiesRaw.concat(picked);
        const out = new Map();
        seatedPlus.forEach(a => {
          (a.refusals || []).forEach(r => {
            const b = seatedPlus.find(x => x.id === r.id);
            if (!b) return;
            const key = [a.id, r.id].sort().join('|');
            if (!out.has(key)) out.set(key, { a: a.name, b: b.name, text: r.text });
          });
        });
        return Array.from(out.values());
      };

      let best = null;
      const n = available.length;
      for (let mask = 1; mask < (1 << n); mask++) {
        let seats = 0;
        const picked = [];
        for (let i = 0; i < n; i++) {
          if (mask & (1 << i)) { seats += available[i].seats; picked.push(available[i]); }
        }
        if (seats < deficit) continue;
        const broken = brokenBy(picked);
        // Fewest promises broken wins; then fewest parties, then fewest seats.
        if (!best ||
            broken.length < best.broken.length ||
            (broken.length === best.broken.length &&
              (picked.length < best.picked.length ||
                (picked.length === best.picked.length && seats < best.seats)))) {
          best = { picked, seats, broken };
        }
      }
      blockedHint = best
        ? { names: best.picked.map(p => p.name).join(' + '),
            ids: best.picked.map(p => p.id),
            total: totalSeats + best.seats,
            broken: best.broken,
            count: best.broken.length }
        : { impossible: true };
    }
    this._hintCache = { key: hintKey, value: closestHint, blocked: blockedHint };""",
        "blocked-hint")

    html = patch(
        html,
        "      closestHint = this._hintCache.value;",
        "      closestHint = this._hintCache.value;\n"
        "      blockedHint = this._hintCache.blocked;",
        "blocked-hint-cache")

    html = patch(
        html,
        "    let closestHint = null;",
        "    let closestHint = null;\n    let blockedHint = null;",
        "blocked-hint-decl")

    html = patch(
        html,
        "      hasClosestHint: !!closestHint,",
        """      hasBlockedHint: !!(blockedHint && !blockedHint.impossible),
      hasImpossible: !!(blockedHint && blockedHint.impossible),
      blockedHeadline: blockedHint && !blockedHint.impossible
        ? `Nothing reaches 61 from here without a stated red line breaking. `
          + `The nearest: add ${blockedHint.names} to reach ${blockedHint.total}.`
        : '',
      blockedSubhead: blockedHint && !blockedHint.impossible
        ? (blockedHint.count === 1
            ? 'That needs one promise to break:'
            : `That needs ${blockedHint.count} promises to break:`)
        : '',
      blockedList: blockedHint && blockedHint.broken ? blockedHint.broken : [],
      applyBlockedHint: (blockedHint && blockedHint.ids)
        ? (() => this.addMany(blockedHint.ids)) : (() => {}),
      hasClosestHint: !!closestHint,""",
        "blocked-hint-view")

    html = patch(html, OLD_HINT, NEW_HINT, "blocked-hint-markup")

    # How well the coalition holds up across the headline house's recent polls.
    html = patch(
        html,
        "      hasPresetNote: !!this.state.presetNote,",
        "      // Arithmetic only. A coalition blocked by a stated refusal is not\n"
        "      // \"steady\" however often it clears 61, so the red lines win.\n"
        "      robustnessText: conflicts.length ? '' : robustness(\n"
        "        live && live.history, coalition, governs),\n"
        "      hasRobustness: !conflicts.length && !!robustness(\n"
        "        live && live.history, coalition, governs),\n"
        "      hasPresetNote: !!this.state.presetNote,",
        "robustness-view")

    html = patch(
        html,
        '<sc-if value="{{ hasPresetNote }}" hint-placeholder-val="{{ false }}">',
        '<sc-if value="{{ hasRobustness }}" hint-placeholder-val="{{ false }}">'
        '<div style="margin-top:14px;background:var(--paper-sunken);'
        'border-left:3px solid var(--ink-3);border-radius:var(--r-sm);padding:10px 12px;">'
        '<p class="ek-small" style="margin:0;color:var(--ink-2);">{{ robustnessText }}</p>'
        '</div></sc-if>'
        '<sc-if value="{{ hasPresetNote }}" hint-placeholder-val="{{ false }}">',
        "robustness-markup")

    # With every red line counted the viable-path total can reach zero, and the
    # sentence was built assuming it never would — "only 0 are minimal
    # coalitions ... every path runs through the big parties themselves".
    html = patch(html, KM_OLD, KM_NEW, "kingmaker-none-text")

    html = patch(
        html,
        "      viablePaths: kingmaker.viablePaths,",
        "      viablePaths: kingmaker.viablePaths,\n"
        "      kingmakerNoneText: kingmaker.viablePaths === 0\n"
        "        ? `Nobody. Of the ${kingmaker.combos.toLocaleString('en-US')} possible party `\n"
        "          + `combinations, not one reaches 61 without crossing a line some party has `\n"
        "          + `already drawn. Every route to a government now runs through a broken promise.`\n"
        "        : `There isn't one. Of the ${kingmaker.combos.toLocaleString('en-US')} possible `\n"
        "          + `party combinations, only ${kingmaker.viablePaths} `\n"
        "          + `${kingmaker.viablePaths === 1 ? 'is a minimal coalition' : 'are minimal coalitions'} `\n"
        "          + `that clears 61 without crossing a stated refusal — and no small party is `\n"
        "          + `welcome in coalitions anchored by both sides.`,",
        "kingmaker-none-view")

    # Route the whole board through PARTIES_VIEW so a dropped party's seats
    # redistribute everywhere at once — bloc totals, the solver, the kingmaker.
    html = patch(
        html,
        "    const commentary = this.state.commentary;",
        "    const commentary = this.state.commentary;\n"
        "    const dropped = this.state.dropped || [];\n"
        "    PARTIES_VIEW = applyThreshold(PARTIES_RAW, dropped);",
        "parties-view-set")

    for old, new, name in [
        ("  byId(id) { return PARTIES_RAW.find(p => p.id === id); }",
         "  byId(id) { return PARTIES_VIEW.find(p => p.id === id); }", "byid-view"),
        ("      const available = PARTIES_RAW.filter(p => !coalitionSet.has(p.id) && p.seats > 0);",
         "      const available = PARTIES_VIEW.filter(p => !coalitionSet.has(p.id) && p.seats > 0);",
         "available-view"),
        ("      const available = PARTIES_RAW\n        .filter(p => !coalitionSet.has(p.id) && p.seats > 0)",
         "      const available = PARTIES_VIEW\n        .filter(p => !coalitionSet.has(p.id) && p.seats > 0)",
         "available-blocked-view"),
        ("    const parties = PARTIES_RAW.map(p => {",
         "    const parties = PARTIES_VIEW.map(p => {", "parties-map-view"),
        ("      const blocParties = parties.filter(p => PARTIES_RAW.find(rp => rp.id === p.id).bloc === key)",
         "      const blocParties = parties.filter(p => PARTIES_VIEW.find(rp => rp.id === p.id).bloc === key)",
         "blocs-view"),
        ("    const infoParty = this.state.infoId ? PARTIES_RAW.find(p => p.id === this.state.infoId) : null;",
         "    const infoParty = this.state.infoId ? PARTIES_VIEW.find(p => p.id === this.state.infoId) : null;",
         "info-view"),
        ("    const total = PARTIES_RAW.reduce((s, p) => s + p.seats, 0);",
         "    const total = PARTIES_VIEW.reduce((s, p) => s + p.seats, 0);", "warn-total-view"),
        ("  const sig = PARTIES_RAW.map(p => p.id + ':' + p.seats).join(',');",
         "  const sig = PARTIES_VIEW.map(p => p.id + ':' + p.seats).join(',');", "km-sig-view"),
        ("    __kmCache = { sig: sig, val: computeKingmaker(PARTIES_RAW) };",
         "    __kmCache = { sig: sig, val: computeKingmaker(PARTIES_VIEW) };", "km-compute-view"),
    ]:
        html = patch(html, old, new, name)

    # The toggle lives in the party panel, and a banner makes the hypothetical
    # impossible to mistake for the polling.
    html = patch(
        html,
        "  closeInfo() { this.setState({ infoId: null }); }",
        """  closeInfo() { this.setState({ infoId: null }); }

  /* The parent reports on scroll, but a panel can open before any scroll has
     happened. Ask at the moment it matters. */
  /* Where on the page the control that opened the panel sits. Only used when
     the parent has not reported a viewport. */
  anchorFrom(e) {
    try {
      const el = e && (e.currentTarget || e.target);
      if (!el || !el.getBoundingClientRect) return null;
      return el.getBoundingClientRect().top + (window.scrollY || 0);
    } catch (err) { return null; }
  }

  askViewport() {
    try {
      if (window.parent && window.parent !== window) {
        window.parent.postMessage({ type: 'kcb-need-viewport' }, '*');
      }
    } catch (e) { /* nothing to do */ }
  }

  toggleThreshold(id) {
    this.setState(st => {
      const dropped = (st.dropped || []).includes(id)
        ? st.dropped.filter(x => x !== id)
        : [...(st.dropped || []), id];
      // A party that misses the threshold cannot also be in the coalition.
      return { dropped, coalition: st.coalition.filter(x => !dropped.includes(x)) };
    });
  }

  clearThreshold() { this.setState({ dropped: [] }); }""",
        "threshold-methods")

    html = patch(
        html,
        "      spread: infoParty.spread,",
        "      spread: infoParty.spread,\n"
        "      canMiss: couldMiss(infoParty) || dropped.includes(infoParty.id),\n"
        "      isMissing: dropped.includes(infoParty.id),\n"
        "      missLabel: dropped.includes(infoParty.id)\n"
        "        ? 'Put them back above the threshold'\n"
        "        : 'What if they miss the threshold?',\n"
        "      toggleMiss: () => this.toggleThreshold(infoParty.id),",
        "info-threshold-view")

    html = patch(
        html,
        "      hasDataNote: !!dataNote,",
        "      hasDataNote: !!dataNote,\n"
        "      hasDropped: dropped.length > 0,\n"
        "      droppedVerb: dropped.length === 1 ? 'misses' : 'miss',\n"
        "      droppedNames: dropped.map(id => {\n"
        "        const p = PARTIES_RAW.find(x => x.id === id);\n"
        "        return p ? p.name : id;\n"
        "      }).join(' and '),\n"
        "      droppedSeats: dropped.reduce((s, id) => {\n"
        "        const p = PARTIES_RAW.find(x => x.id === id);\n"
        "        return s + (p ? p.seats : 0);\n"
        "      }, 0),\n"
        "      clearDropped: () => this.clearThreshold(),",
        "banner-view")

    html = patch(
        html,
        '<p class="ek-body" style="font-size:1rem;margin:0 0 16px;">{{ infoView.desc }}</p>',
        MISS_BTN + '<p class="ek-body" style="font-size:1rem;margin:0 0 16px;">{{ infoView.desc }}</p>',
        "miss-button-markup")

    html = patch(
        html,
        STICKY, MISS_BANNER + STICKY, "miss-banner-markup")

    # Reader-chosen poll. Recomputed each render so it flows through the
    # threshold hypothetical, the solver and the kingmaker alike.
    html = patch(
        html,
        "    PARTIES_VIEW = applyThreshold(PARTIES_RAW, dropped);",
        "    const alternates = (live && live.alternates) || [];\n"
        "    const chosen = alternates.find(a => a.id === this.state.pollId) || null;\n"
        "    if (live) PARTIES_RAW = applyLive(allBase(), live, chosen && chosen.seats);\n"
        "    PARTIES_VIEW = applyThreshold(PARTIES_RAW, dropped);",
        "chosen-poll")

    html = patch(
        html,
        "  clearThreshold() { this.setState({ dropped: [] }); }",
        """  clearThreshold() { this.setState({ dropped: [] }); }

  /* Switching poll clears any threshold hypothetical: the two together would
     be a guess stacked on a guess, and the banner could only explain one. */
  pickPoll(id) { this.setState({ pollId: id, dropped: [] }); }""",
        "pick-poll-method")

    html = patch(
        html,
        "      hasDropped: dropped.length > 0,",
        "      hasPollPicker: alternates.length > 1,\n"
        "      pollOptions: alternates.map(a => ({\n"
        "        id: a.id,\n"
        "        label: a.label,\n"
        "        title: `${a.firm} for ${a.publisher}, ${a.displayDate}`,\n"
        "        active: chosen ? a.id === chosen.id : !!a.isHeadline,\n"
        "        bg: (chosen ? a.id === chosen.id : !!a.isHeadline)\n"
        "          ? 'var(--ink)' : 'transparent',\n"
        "        fg: (chosen ? a.id === chosen.id : !!a.isHeadline)\n"
        "          ? 'var(--paper)' : 'var(--ink-2)',\n"
        "        pick: () => this.pickPoll(a.isHeadline ? null : a.id)\n"
        "      })),\n"
        "      hasDropped: dropped.length > 0,",
        "poll-picker-view")

    # The threshold hypothetical was buried behind the info button. It belongs
    # on the card, next to the seat count it changes.
    html = patch(
        html,
        "        trendSvg: sparkline(p.trend, p.color),",
        "        trendSvg: sparkline(p.trend, p.color),\n"
        "        canMissCard: couldMiss(p) || dropped.includes(p.id),\n"
        "        missCardLabel: dropped.includes(p.id)\n"
        "          ? 'Put them back' : 'What if they miss?',\n"
        "        missCardBg: dropped.includes(p.id) ? 'var(--clay)' : 'transparent',\n"
        "        missCardFg: dropped.includes(p.id) ? 'var(--paper)' : 'var(--clay-deep)',\n"
        "        onMissToggle: (e) => {\n"
        "          // The card itself adds the party to the coalition; this must not.\n"
        "          if (e) { e.stopPropagation(); e.preventDefault(); }\n"
        "          this.toggleThreshold(p.id);\n"
        "        },",
        "card-miss-fields")

    html = patch(html, CARD_MISS_OLD, CARD_MISS_NEW, "card-miss-markup")

    html = patch(html, PICKER_ANCHOR, PICKER + PICKER_ANCHOR, "poll-picker-markup")

    # A pollster's ownership or house effect, disclosed when their poll is on
    # screen rather than left for the reader to discover.
    html = patch(html, PICKER_ANCHOR, POLL_NOTE + PICKER_ANCHOR, "poll-note-markup")

    html = patch(
        html,
        "      hasPollPicker: alternates.length > 1,",
        "      hasPollPicker: alternates.length > 1,\n"
        "      pollNote: (() => {\n"
        "        const a = chosen || alternates.find(x => x.isHeadline);\n"
        "        if (!a) return '';\n"
        "        return [leanSentence(a, live), a.note].filter(Boolean).join(' ');\n"
        "      })(),\n"
        "      hasPollNote: (() => {\n"
        "        const a = chosen || alternates.find(x => x.isHeadline);\n"
        "        return !!a && !!(leanSentence(a, live) || a.note);\n"
        "      })(),",
        "poll-note-view")

    # The panel was pinned to the top with no scrolling, so anything taller than
    # the viewport was clipped and unreachable — which is where the threshold
    # button lives. Centre it, let it scroll, and let it use the width it has.
    html = patch(html, OVERLAY_OLD, OVERLAY_NEW, "modal-centre")
    html = patch(html, PANEL_OLD, PANEL_NEW, "modal-width")

    # The picker sat flush against the rule above it.
    html = patch(
        html,
        '<div style="max-width:960px;margin:0 auto;padding:0 20px 4px;">',
        '<div style="max-width:960px;margin:0 auto;padding:14px 20px 8px;">',
        "picker-spacing")

    # The card toggle was easy to miss at 0.72rem in a dashed outline.
    html = patch(
        html,
        "font-weight:700;font-size:0.72rem;"
        "letter-spacing:0.02em;padding:5px 9px;min-height:30px;",
        "font-weight:700;font-size:0.78rem;"
        "letter-spacing:0.02em;padding:6px 11px;min-height:32px;",
        "card-miss-size")

    # "Why this won't work" was capped at 160px with its own scrollbar. With
    # the vetoes now carrying their sourcing, that hid both the heading and
    # whichever refusal came last — the one thing on the page a reader most
    # needs to read in full.
    html = patch(
        html,
        "border-radius:var(--r-md);padding:12px 14px;max-height:160px;overflow-y:auto;",
        "border-radius:var(--r-md);padding:12px 14px;",
        "conflicts-no-clip")

    # The design centres everything in a 960px column. Embedded in a theme that
    # offers 1140, that left a wide empty margin either side and pushed the
    # party grid further down the page than it needed to be. The embed wrapper
    # no longer caps width at all, so this number is the only one to change.
    html = patch(html, "max-width:960px", "max-width:1140px", "container-width", count=9)

    # More width means more party cards per row, which is the part that
    # actually shortens the scroll.
    html = patch(
        html,
        "grid-template-columns:repeat(auto-fit, minmax(210px, 1fr))",
        "grid-template-columns:repeat(auto-fit, minmax(250px, 1fr))",
        "bloc-grid-width")

    # A complete party strip inside the first screenful. position:sticky is
    # inert in the embed — the iframe is its own full height, so there is no
    # scroll container for a header to pin to — and the page runs to 8,000px.
    # Without this, adding or removing a party means scrolling to the grid and
    # back to the total every time.
    html = patch(
        html,
        '<sc-if value="{{ pmTiedVisible }}" hint-placeholder-val="{{ false }}">',
        QUICK_STRIP + '<sc-if value="{{ pmTiedVisible }}" hint-placeholder-val="{{ false }}">',
        "quick-strip-markup")

    html = patch(
        html,
        "      hasClosestHint: !!closestHint,",
        "      quickParties: parties.filter(p => p.seats > 0).map(p => ({\n"
        "        id: p.id,\n"
        "        name: shortName(p.name),\n"
        "        seats: p.seats,\n"
        "        inCoalition: p.inCoalition,\n"
        "        dot: p.blocColor,\n"
        "        bg: p.inCoalition ? p.blocColor : 'var(--paper-raised)',\n"
        "        fg: p.inCoalition ? 'var(--paper)' : 'var(--ink)',\n"
        "        border: p.inCoalition ? p.blocColor : 'var(--rule-strong)',\n"
        "        opacity: p.hasConflictHint ? '0.55' : '1',\n"
        "        nearThreshold: p.canMissCard,\n"
        "        borderStyle: p.canMissCard ? 'dashed' : 'solid',\n"
        "        divider: p.inCoalition ? 'rgba(246,242,233,0.35)' : 'var(--rule)',\n"
        "        infoFg: p.inCoalition ? 'var(--paper)'\n"
        "          : (p.canMissCard ? 'var(--clay-deep)' : 'var(--ink-3)'),\n"
        "        title: p.canMissCard\n"
        "          ? `${p.name} sits near the 3.25% threshold — open it to test them missing`\n"
        "          : p.name,\n"
        "        infoAria: `More about ${p.name}` +\n"
        "          (p.canMissCard ? ', which is near the threshold' : ''),\n"
        "        info: (e) => { if (e) e.stopPropagation(); this.openInfo(p.id, e); },\n"
        "        aria: `${p.name}, ${p.seats} seats` +\n"
        "          (p.inCoalition ? ', in your coalition' : '') +\n"
        "          '. Press to ' + (p.inCoalition ? 'remove' : 'add') + '.',\n"
        "        toggle: () => this.toggleAdd(p.id)\n"
        "      })),\n"
        "      hasClosestHint: !!closestHint,",
        "quick-strip-view")

    # Move the scenario buttons up beside the party strip, so every control
    # sits together above the hemicycle instead of below it.
    html = move_scenarios(html)
    html = move_analysis(html)

    # Pin the modal to the reader's visible slice when embedded.
    html = patch(
        html,
        OVERLAY_NEW,
        'style="{{ overlayStyle }}"',
        "modal-overlay-dynamic")

    html = patch(
        html,
        "      hasInfo: !!infoView,",
        "      overlayStyle: (() => {\n"
        "        const box = overlayBox(this.state.anchorY);\n"
        "        const base = 'background:rgba(28,26,22,0.78);backdrop-filter:blur(2px);'\n"
        "          + 'display:flex;align-items:center;justify-content:center;padding:20px;'\n"
        "          + 'overflow-y:auto;z-index:50;';\n"
        "        return box\n"
        "          ? `position:absolute;left:0;right:0;top:${box.top}px;height:${box.height}px;`\n"
        "            + base.replace('align-items:center', box.centred ? 'align-items:center' : 'align-items:flex-start')\n"
        "          : `position:fixed;inset:0;${base}`;\n"
        "      })(),\n"
        "      hasInfo: !!infoView,",
        "modal-overlay-view")

    for old, new, name in [
        ("  openInfo(id, e) { if (e) e.stopPropagation(); this.setState({ infoId: id }); }",
         "  openInfo(id, e) { if (e) e.stopPropagation(); this.askViewport(); "
         "this.setState({ infoId: id, anchorY: this.anchorFrom(e) }); }", "openinfo-viewport"),
        ("  openIntro() { this.setState({ showIntro: true }); }",
         "  openIntro() { this.askViewport(); this.setState({ showIntro: true }); }",
         "openintro-viewport"),
    ]:
        html = patch(html, old, new, name)

    # Breathing room: the strip and the scenarios were packed tight against
    # each other and against the rule above them.
    html = patch(
        html,
        '<div style="max-width:1140px;margin:0 auto;padding:4px 20px 2px;">',
        '<div style="max-width:1140px;margin:0 auto;padding:16px 20px 6px;">',
        "strip-padding")
    html = patch(
        html,
        '<div style="display:flex;flex-wrap:wrap;gap:6px;">',
        '<div style="display:flex;flex-wrap:wrap;gap:9px;">',
        "strip-gap")
    html = patch(
        html,
        'justify-content:space-between;gap:10px;margin-bottom:7px;">',
        'justify-content:space-between;gap:10px;margin-bottom:11px;">',
        "strip-label-gap")
    html = patch(
        html,
        '<div style="max-width:1140px;margin:0 auto;padding:24px 20px 28px;">',
        '<div style="max-width:1140px;margin:0 auto;padding:20px 20px 26px;">',
        "scenarios-padding")

    # "Where they stand": the three questions that generate the red lines,
    # side by side. The full sourced sentence stays in each party's panel.
    html = patch(html, ISSUES_ANCHOR, ISSUES_GRID + ISSUES_ANCHOR, "issues-grid-markup")

    html = patch(
        html,
        "      hasClosestHint: !!closestHint,",
        "      hasIssues: !!(this.state.stances && this.state.stances.stances),\n"
        "      issueCols: ((this.state.stances || {})._issues || []),\n"
        "      issueGridCols: (() => {\n"
        "        const n = (((this.state.stances || {})._issues) || []).length;\n"
        "        return `minmax(126px,0.95fr) repeat(${n}, minmax(96px,1fr))`;\n"
        "      })(),\n"
        "      issueMinWidth: (() => {\n"
        "        const n = (((this.state.stances || {})._issues) || []).length;\n"
        "        return (140 + n * 104) + 'px';   // keeps columns legible; the wrapper scrolls\n"
        "      })(),\n"
        "      issueRows: (() => {\n"
        "        const st = this.state.stances;\n"
        "        if (!st || !st.stances) return [];\n"
        "        const cols = st._issues || [];\n"
        "        return parties.filter(p => p.seats > 0).map(p => {\n"
        "          const raw = PARTIES_VIEW.find(x => x.id === p.id) || {};\n"
        "          const pos = raw.positions || {};\n"
        "          const mine = st.stances[p.id] || {};\n"
        "          return {\n"
        "            id: p.id, name: p.name, seats: p.seats, color: p.blocColor,\n"
        "            aria: `More about ${p.name}`,\n"
        "            open: (e) => this.openInfo(p.id, e),\n"
        "            cells: cols.map(col => {\n"
        "              const cell = mine[col.key] || ['none', '—'];\n"
        "              const tone = STANCE_TONES[cell[0]] || STANCE_TONES.none;\n"
        "              return {\n"
        "                label: cell[1],\n"
        "                full: pos[col.key] || cell[1],\n"
        "                bg: tone.bg, fg: tone.fg, border: tone.border\n"
        "              };\n"
        "            })\n"
        "          };\n"
        "        });\n"
        "      })(),\n"
        "      hasClosestHint: !!closestHint,",
        "issues-grid-view")

    # "Why these numbers" hard-coded a fieldwork date and claimed Channel 14
    # was excluded — no longer true now that any recent poll can be selected.
    # Both paragraphs become live text, overridable from commentary.json.
    html = patch(html, METHOD_P1_OLD, METHOD_P1_NEW, "methodology-1")
    html = patch(html, METHOD_P2_OLD, METHOD_P2_NEW, "methodology-2")

    html = patch(
        html,
        "      hasDataNote: !!dataNote,",
        "      methodologyNote: (commentary && commentary.methodologyNote) ||\n"
        "        (`The headline numbers are one pollster — ${live ? live.source.pollster : 'Kantar'} `\n"
        "         + `for ${live ? live.source.publisher : 'Kan 11'} — rather than an average across `\n"
        "         + `houses that disagree by up to eleven seats on the size of the Netanyahu bloc. `\n"
        "         + `Israeli polls have historically understated the right, and this house sits `\n"
        "         + `slightly to the right of the pack. Other recent polls can be selected above, `\n"
        "         + `including Channel 14's, which is shown with a note on who owns it. Treat any of `\n"
        "         + `them as one considered snapshot, not a forecast.`),\n"
        "      sourceNote: (commentary && commentary.sourceNote) ||\n"
        "        (live\n"
        "          ? `Seat projections from the ${live.source.label} poll, fieldwork `\n"
        "            + `${live.source.displayDate}, as recorded on Wikipedia's polling page for the `\n"
        "            + `2026 Israeli legislative election and refreshed automatically each morning. `\n"
        "            + `Change arrows compare with that house's previous poll; the range on each `\n"
        "            + `party spans every pollster over ${live.spreadWindowDays} days. Positions and `\n"
        "            + `refusals are summaries of parties' publicly stated coalition positions.`\n"
        "          : 'Seat projections as recorded on Wikipedia\\'s polling page for the 2026 Israeli '\n"
        "            + 'legislative election.'),\n"
        "      hasDataNote: !!dataNote,",
        "methodology-view")

    # "If they miss the threshold": the same redistribution the party toggles
    # use, offered as the scenarios that actually decide the election.
    html = patch(html, THRESH_ANCHOR, THRESH_BLOCK + THRESH_ANCHOR, "threshold-block-markup")

    html = patch(
        html,
        "      hasDropped: dropped.length > 0,",
        "      hasThresholdScenarios: !!(commentary && commentary.thresholdScenarios),\n"
        "      thresholdScenarios: ((commentary && commentary.thresholdScenarios) || []).map(t => {\n"
        "        const drop = t.drop || [];\n"
        "        const same = drop.length === dropped.length &&\n"
        "          drop.every(id => dropped.includes(id));\n"
        "        // Recompute against this scenario, so each button carries its own answer.\n"
        "        return {\n"
        "          label: t.label,\n"
        "          active: same,\n"
        "          bg: same ? 'var(--clay)' : 'transparent',\n"
        "          fg: same ? 'var(--paper)' : 'var(--ink)',\n"
        "          border: same ? 'var(--clay)' : 'var(--rule-strong)',\n"
        "          borderStyle: drop.length ? 'dashed' : 'solid',\n"
        "          apply: () => this.setState({ dropped: drop.slice(), thresholdNote: t.note || '' })\n"
        "        };\n"
        "      }),\n"
        "      thresholdNote: this.state.thresholdNote || '',\n"
        "      hasThresholdNote: !!this.state.thresholdNote && dropped.length > 0,\n"
        "      hasDropped: dropped.length > 0,",
        "threshold-block-view")

    # The hypothetical banner carries the scenario's reading.
    html = patch(
        html,
        'out among the parties that cleared it, so every number below has moved.</div>',
        'out among the parties that cleared it, so every number below has moved.</div>'
        '<sc-if value="{{ hasThresholdNote }}" hint-placeholder-val="{{ false }}">'
        '<div class="ek-small" style="margin:6px 0 0;color:var(--ink);font-weight:600;">'
        '{{ thresholdNote }}</div></sc-if>',
        "threshold-note-markup")

    # The party grid runs 3,600px on a phone — a third of the page — and the
    # quick strip now covers adding and removing. Collapse it by default on
    # small screens; on a desktop there is room, so it stays open.
    html = patch(
        html,
        '<div class="kb-blocs" style="display:grid;',
        '<sc-if value="{{ partiesOpen }}" hint-placeholder-val="{{ true }}">'
        '<div class="kb-blocs" style="display:grid;',
        "parties-collapse-open")

    html = patch(
        html,
        BLOCS_TAIL,
        BLOCS_TAIL + '</sc-if>'
        '<button sc-camel-on-click="{{ togglePartiesOpen }}" aria-expanded="{{ partiesOpen }}" '
        'style="margin-top:14px;font-family:var(--font-sans);font-weight:700;font-size:0.85rem;'
        'padding:10px 16px;min-height:42px;background:transparent;color:var(--ink-2);'
        'border:1px solid var(--rule-strong);border-radius:var(--r-sm);cursor:pointer;">'
        '{{ partiesToggleLabel }}</button>',
        "parties-collapse-toggle")

    html = patch(
        html,
        "  setView(mode) { this.setState({ viewMode: mode }); }",
        """  togglePartiesOpen() {
    this.setState(st => ({ partiesOpen: !st.partiesOpen }));
  }

  setView(mode) { this.setState({ viewMode: mode }); }""",
        "parties-toggle-method")

    html = patch(
        html,
        "    this.loadPolls();\n    if (window.parent",
        "    this.loadPolls();\n"
        "    // Room for the full grid on a desktop; on a phone it is four screens.\n"
        "    if (typeof window !== 'undefined' && window.innerWidth < 760) {\n"
        "      this.setState({ partiesOpen: false });\n"
        "    }\n"
        "    if (window.parent",
        "parties-collapse-default")

    html = patch(
        html,
        "      hasClosestHint: !!closestHint,",
        "      partiesOpen: this.state.partiesOpen !== false,\n"
        "      partiesToggleLabel: this.state.partiesOpen !== false\n"
        "        ? 'Hide the full party list'\n"
        "        : `Show all ${parties.filter(p => p.seats > 0).length} parties in detail`,\n"
        "      togglePartiesOpen: () => this.togglePartiesOpen(),\n"
        "      hasClosestHint: !!closestHint,",
        "parties-collapse-view")

    # --ink-3 is used for .ek-meta at 13px, which measured 4.38:1 on the paper
    # background — under the 4.5 WCAG AA needs for normal-size text. This is
    # the same hue, darkened until it passes.
    html = patch(html, '--ink-3:        #79705F', '--ink-3:        #6B6252', "ink3-contrast", count=1)

    # ---- byline markup ----------------------------------------------------
    html = patch(
        html,
        '<div class="ek-caption" style="margin-top:4px;color:var(--ink-3);">'
        'Updated weekly — sooner when something big breaks.</div>',
        '<sc-if value="{{ hasDataNote }}" hint-placeholder-val="{{ false }}">'
        '<div class="ek-caption" style="margin-top:4px;color:var(--ink-3);">{{ dataNote }}</div>'
        '</sc-if>\n'
        '      <sc-if value="{{ hasDataWarning }}" hint-placeholder-val="{{ false }}">\n'
        '        <div style="margin-top:8px;background:var(--gold-tint);border:1px solid var(--gold);'
        'border-radius:var(--r-sm);padding:8px 10px;text-transform:none;letter-spacing:0;">\n'
        '          <div class="ek-caption" style="margin:0 0 4px;color:var(--gold-deep);'
        'font-weight:700;text-transform:uppercase;letter-spacing:0.04em;">Needs an editor’s eye</div>\n'
        '          <sc-for list="{{ dataWarnings }}" as="w" hint-placeholder-count="0">\n'
        '            <div class="ek-small" style="margin:0 0 2px;color:var(--ink-2);">· {{ w.text }}</div>\n'
        '          </sc-for>\n'
        '        </div>\n'
        '      </sc-if>',
        "byline-markup")

    with open(out_html, "w", encoding="utf-8") as f:
        f.write(html)
    return html


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("export", help="the Claude Design 'standalone' .html export")
    ap.add_argument("-o", "--out", default=os.path.join(ROOT, "index.html"))
    args = ap.parse_args()
    try:
        html = build(os.path.expanduser(args.export), args.out)
    except BuildError as e:
        print(f"\nBUILD FAILED: {e}\n", file=sys.stderr)
        return 1
    print(f"\nWrote {args.out}  ({len(html)//1024} KB)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
