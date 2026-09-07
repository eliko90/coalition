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


class BuildError(Exception):
    pass


def patch(text, old, new, what):
    """Replace `old` once, or fail naming the patch."""
    n = text.count(old)
    if n != 1:
        raise BuildError(
            f"patch {what!r} expected exactly one match, found {n}.\n"
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

const POLLS_URL = 'data/polls.json';
const EXTRA_URL = 'data/parties-extra.json';
const COMMENTARY_URL = 'data/commentary.json';
const STALE_AFTER_DAYS = 14;

/* Shown when Ra'am is in a coalition with a non-Arab party. Overridden by
   `raamNote` in data/commentary.json; this is only the fallback. */
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

function applyLive(base, live) {
  if (!live || !live.parties) return base;
  return base.map(p => {
    const d = live.parties[p.id];
    if (!d) return p;
    const out = Object.assign({}, p, {
      seats: d.seats,
      delta: d.delta == null ? 0 : d.delta,
      pct: d.pct || undefined,
      nearThreshold: d.seats === 0 ||
        (d.spreadMin != null && d.spreadMin === 0 && d.seats > 0)
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
  }

  loadPolls() {
    const json = url => fetch(url, { cache: 'no-cache' })
      .then(r => r.ok ? r.json() : Promise.reject(new Error(url + ': HTTP ' + r.status)));

    // Both side files are optional — a missing one is not an error.
    Promise.all([
      json(POLLS_URL),
      json(EXTRA_URL).catch(() => []),
      json(COMMENTARY_URL).catch(() => null)
    ])
      .then(([live, extra, commentary]) => {
        if (!live || !live.parties || !live.source) throw new Error('bad shape');
        PARTIES_EXTRA = Array.isArray(extra) ? extra : [];
        PARTIES_RAW = applyLive(allBase(), live);
        this.setState({ live: live, commentary: commentary, liveError: '' });
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
    html = patch(
        html, "<head>\n<meta charset=\"utf-8\">",
        "<head>\n<meta charset=\"utf-8\">\n"
        "<title>Road to 61 — Knesset Coalition Builder</title>\n"
        '<meta name="description" content="Build a Knesset coalition from the '
        'latest Israeli polling and see which ones can actually govern.">',
        "head-title")

    # ---- live data: rename the base list, add the runtime ----------------
    html = patch(html, "const PARTIES_RAW = [", "const PARTIES_BASE = [", "rename-base")

    html = patch(
        html,
        "const BLOC_META = {",
        LIVE_RUNTIME + "\nconst BLOC_META = {",
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
        "      pollingDate: (live && live.source.displayDate) || "
        "this.props.pollingDate || 'Aug 9, 2026',",
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
        "      pollingSource: (live && live.source.label) || "
        "this.props.pollingSource || 'Kantar/Kan 11',\n"
        "      dataNote,\n"
        "      hasDataNote: !!dataNote,\n"
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
