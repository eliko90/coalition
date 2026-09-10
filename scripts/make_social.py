#!/usr/bin/env python3
"""
Render an Instagram carousel promoting the coalition builder.

Slides are 1080x1350 (4:5) — the tallest ratio Instagram shows in feed, so it
takes the most screen. The numbers come from data/polls.json rather than being
typed in, so re-running after a new poll produces a correct carousel instead of
a stale one. The wording lives in SLIDES below; edit it there.

    python3 scripts/make_social.py            # writes assets/social/*.png
    python3 scripts/make_social.py --html     # just the HTML, to preview

Uses headless Chrome, like scripts/make_og.sh, so there is no image library to
install.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(ROOT, "assets", "social")
W, H = 1080, 1350
# X renders summary_large_image at 2:1 and small in a timeline, so this card
# carries fewer words and larger type than the 1200x630 Facebook one.
XW, XH = 1200, 628

NETANYAHU = ["likud", "rzp", "otzma", "shas", "utj", "amcha_yisrael"]
CHANGE = ["yashar", "together", "democrats", "beiteinu"]

PAPER, INK, INK2, CLAY, NAVY = "#F6F2E9", "#1C1A16", "#4A453C", "#B0492C", "#1C2C36"


def numbers():
    with open(os.path.join(ROOT, "data", "polls.json"), encoding="utf-8") as f:
        d = json.load(f)
    s = {k: v["seats"] for k, v in d["parties"].items()}
    n = {
        "source": d["source"]["label"],
        "date": d["source"]["displayDate"],
        "net": sum(s.get(i, 0) for i in NETANYAHU),
        "chg": sum(s.get(i, 0) for i in CHANGE),
        "hendel": s.get("reservists", 0),
        "raam": s.get("raam", 0),
    }
    n["chg_hendel"] = n["chg"] + n["hendel"]
    # Same test the board uses: 3.25% is about four seats, so a party a
    # pollster has put at four or fewer is one survey from missing.
    n["at_risk"] = sum(
        1 for v in d["parties"].values()
        if v["seats"] > 0 and ((v.get("spreadMin") is not None and v["spreadMin"] <= 4)
                               or v["seats"] <= 5))
    # How many of the polls in the picker put Hendel out. He is the partner the
    # change bloc's only majority runs through, so this is the live question.
    alts = d.get("alternates", [])
    n["alt_total"] = len(alts)
    n["hendel_out"] = sum(1 for a in alts if not a["seats"].get("reservists"))
    # And what his absence costs, using the board's own redistribution.
    no_hendel = apply_threshold({k: v["seats"] for k, v in d["parties"].items()},
                                ["reservists"])
    n["nh_net"] = sum(no_hendel.get(i, 0) for i in NETANYAHU)
    n["nh_chg"] = sum(no_hendel.get(i, 0) for i in CHANGE)
    return n


def apply_threshold(seats, dropped):
    """Largest-remainder redistribution — the same arithmetic applyThreshold()
    runs in the browser, so a slide can never claim a total the board denies."""
    drop = set(dropped)
    freed = sum(v for k, v in seats.items() if k in drop)
    rest = {k: v for k, v in seats.items() if k not in drop and v > 0}
    out = {k: (0 if k in drop else v) for k, v in seats.items()}
    if not freed or not rest:
        return out
    total = sum(rest.values())
    share = {k: (freed * v / total) for k, v in rest.items()}
    add = {k: int(v // 1) for k, v in share.items()}
    left = freed - sum(add.values())
    for k in sorted(share, key=lambda k: share[k] % 1, reverse=True):
        if left <= 0:
            break
        add[k] += 1
        left -= 1
    for k in add:
        out[k] += add[k]
    return out


def slides(n):
    """Copy for each slide. Edit freely; {placeholders} are filled from the poll."""
    return [
        # 1 — the hook. One claim, no furniture.
        dict(kind="hook", kicker="THE MIDDLE GROUND · INTERACTIVE",
             big="Nobody can\nform a\ngovernment.",
             sub="Israel votes on 27 October. On the current polling, "
                 "no combination of parties reaches 61 without someone "
                 "breaking a promise they have already made.",
             foot="Swipe →"),
        # 2 — the deadlock, as numbers.
        dict(kind="stat", kicker="THE DEADLOCK",
             rows=[("Netanyahu bloc", n["net"]), ("Change bloc", n["chg"]),
                   ("Needed to govern", 61)],
             note=f"{n['source']}, {n['date']}. Across two dozen polls from six "
                  f"houses in three weeks, neither bloc reaches 61 — not once.",
             foot="Swipe →"),
        # 3 — the mechanism the piece is built on.
        dict(kind="quote", kicker="THE REAL CONSTRAINT",
             big="Seats are not\nthe only\nconstraint.",
             sub="Parties have ruled each other out in public. A coalition can "
                 "clear 61 on paper and still be impossible — because Lieberman "
                 "will not sit with the Haredi parties, because Bennett has ruled "
                 "out Ra'am, because Eisenkot has ruled out Ben Gvir.",
             foot="Swipe →"),
        # 4 — where it actually gets decided.
        dict(kind="stat", kicker="WHERE IT IS DECIDED",
             rows=[("The electoral threshold", "3.25%"),
                   ("Worth about", "4 seats"),
                   ("Parties within reach of it", n["at_risk"])],
             note="Israel wastes every vote for a list under the threshold and "
                  "shares those seats among the lists that clear it. Whether one "
                  "small party survives moves seats across the whole map.",
             foot="Swipe →"),
        # 5 — the concrete illustration, from a real poll.
        dict(kind="split", kicker="ONE MAN, TWO OUTCOMES",
             left=("Hendel clears", f"{n['net']}", "Netanyahu bloc",
                   f"{n['chg_hendel']}", "Eisenkot + Hendel"),
             right=("Hendel misses", f"{n['nh_net']}", "Netanyahu bloc",
                    f"{n['nh_chg']}", "Eisenkot alone"),
             note=f"{n['hendel_out']} of the {n['alt_total']} newest polls put Yoaz "
                  "Hendel's Reservists under the threshold. He is the one partner "
                  "the change bloc can add without breaking a promise — so his four "
                  "seats are the whole margin, and they may not exist.",
             foot="Swipe →"),
        # 6 — the product, arriving at the number slide 5 just claimed.
        dict(kind="shot", kicker="THE TOOL", img="board.png",
             sub="Push Hendel under the threshold and the board answers.",
             note=f"Eisenkot lands on {n['nh_chg']} — short by "
                  f"{61 - n['nh_chg']}. Six pollsters, every stated red line, "
                  "seats redistributed the way Israeli law does.",
             foot="Swipe →"),
        # 7 — the ask.
        dict(kind="cta", kicker="BUILD IT YOURSELF",
             big="Road to 61",
             sub="Every party, every seat, every stated red line. Pick a pollster, "
                 "push a party under the threshold, and see who can actually govern. "
                 "Updated automatically with each new poll.",
             url="kowaz.com/coalitionbuilder",
             foot="Link in bio"),
    ]


CSS = f"""
@import url('https://fonts.googleapis.com/css2?family=Newsreader:ital,opsz,wght@0,6..72,400;0,6..72,600;1,6..72,400&family=Libre+Franklin:wght@400;600;700;800&display=swap');
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ width:{W}px; height:{H}px; overflow:hidden; background:{PAPER}; color:{INK};
  font-family:'Libre Franklin',-apple-system,sans-serif; }}
.slide {{ width:{W}px; height:{H}px; padding:88px 88px 76px; display:none;
  flex-direction:column; }}
/* Centre the content and pin the footer, so a short slide does not leave a
   third of the frame empty. */
.body {{ flex:1; display:flex; flex-direction:column; justify-content:center; }}
.slide.on {{ display:flex; }}
.slide.dark {{ background:{NAVY}; color:{PAPER}; }}
.kicker {{ font-size:26px; font-weight:800; letter-spacing:.16em; text-transform:uppercase;
  color:{CLAY}; }}
.slide.dark .kicker {{ color:#E08A63; }}
.big {{ font-family:Newsreader,Georgia,serif; font-weight:600; font-size:118px; line-height:1.02;
  letter-spacing:-.02em; margin-top:34px; white-space:pre-line; }}
.sub {{ font-family:Newsreader,Georgia,serif; font-size:40px; line-height:1.42; color:{INK2};
  margin-top:34px; }}
.slide.dark .sub {{ color:rgba(246,242,233,.86); }}
.note {{ font-size:28px; line-height:1.5; color:{INK2}; margin-top:36px; }}
.shot {{ margin-top:30px; border:2px solid #DCD5C5; border-radius:18px;
  overflow:hidden; background:{PAPER}; }}
.shot img {{ display:block; width:100%; }}
.foot {{ font-size:26px; font-weight:700; color:{CLAY}; }}
.rows {{ margin-top:60px; }}
.row {{ display:flex; align-items:baseline; justify-content:space-between; gap:24px;
  padding:30px 0; border-bottom:2px solid #DCD5C5; }}
.row:first-child {{ border-top:2px solid #DCD5C5; }}
.row .lab {{ font-size:34px; font-weight:600; }}
.row .val {{ font-family:Newsreader,Georgia,serif; font-weight:600; font-size:78px;
  line-height:1; }}
.row.hi .val {{ color:{CLAY}; }}
.cols {{ display:flex; gap:34px; margin-top:56px; }}
.col {{ flex:1; border:2px solid #DCD5C5; border-radius:22px; padding:34px 30px; }}
.col.win {{ border-color:{CLAY}; background:rgba(176,73,44,.06); }}
.col h3 {{ font-size:28px; font-weight:800; letter-spacing:.06em; text-transform:uppercase;
  color:{INK2}; }}
.col.win h3 {{ color:{CLAY}; }}
.pair {{ margin-top:26px; }}
.pair .n {{ font-family:Newsreader,Georgia,serif; font-weight:600; font-size:84px; line-height:1; }}
.pair .t {{ font-size:25px; color:{INK2}; margin-top:6px; }}
.url {{ font-family:Newsreader,Georgia,serif; font-weight:600; font-size:52px; color:{CLAY};
  margin-top:40px; }}
.rule {{ width:96px; height:7px; background:{CLAY}; }}
"""


def render_html(n):
    out = [f"<!doctype html><meta charset='utf-8'><style>{CSS}</style>"]
    for i, s in enumerate(slides(n), 1):
        dark = "dark" if s["kind"] in ("quote", "cta") else ""
        out.append(f"<div class='slide {dark}' data-i='{i}'>")
        out.append("<div class='body'>")
        out.append("<div class='rule'></div>")
        out.append(f"<div class='kicker' style='margin-top:22px'>{s['kicker']}</div>")
        if s["kind"] in ("hook", "quote", "cta"):
            out.append(f"<div class='big'>{s['big']}</div>")
            out.append(f"<div class='sub'>{s['sub']}</div>")
            if s.get("url"):
                out.append(f"<div class='url'>{s['url']}</div>")
        elif s["kind"] == "stat":
            out.append("<div class='rows'>")
            for j, (lab, val) in enumerate(s["rows"]):
                hi = "hi" if j == len(s["rows"]) - 1 else ""
                out.append(f"<div class='row {hi}'><span class='lab'>{lab}</span>"
                           f"<span class='val'>{val}</span></div>")
            out.append("</div>")
            out.append(f"<div class='note'>{s['note']}</div>")
        elif s["kind"] == "shot":
            out.append(f"<div class='sub' style='margin-top:14px'>{s['sub']}</div>")
            out.append(f"<div class='shot'><img src='{s['img']}'></div>")
            out.append(f"<div class='note' style='margin-top:26px'>{s['note']}</div>")
        elif s["kind"] == "split":
            out.append("<div class='cols'>")
            for col, cls in ((s["left"], ""), (s["right"], "win")):
                title, a, at, b, bt = col
                out.append(f"<div class='col {cls}'><h3>{title}</h3>"
                           f"<div class='pair'><div class='n'>{a}</div><div class='t'>{at}</div></div>"
                           f"<div class='pair'><div class='n'>{b}</div><div class='t'>{bt}</div></div>"
                           "</div>")
            out.append("</div>")
            out.append(f"<div class='note'>{s['note']}</div>")
        out.append("</div>")
        out.append(f"<div class='foot'>{s['foot']}</div>")
        out.append("</div>")
    out.append("""<script>
      var i = new URLSearchParams(location.search).get('slide') || '1';
      var el = document.querySelector("[data-i='" + i + "']");
      if (el) el.classList.add('on');
    </script>""")
    return "\n".join(out)


X_CSS = f"""
@import url('https://fonts.googleapis.com/css2?family=Newsreader:ital,opsz,wght@0,6..72,400;0,6..72,600&family=Libre+Franklin:wght@600;700;800&display=swap');
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ width:{XW}px; height:{XH}px; overflow:hidden; background:{PAPER}; color:{INK};
  font-family:'Libre Franklin',sans-serif; padding:56px 64px 48px;
  display:flex; flex-direction:column; justify-content:space-between; }}
.rule {{ width:64px; height:6px; background:{CLAY}; }}
.kick {{ font-size:22px; font-weight:800; letter-spacing:.15em; text-transform:uppercase;
  color:{CLAY}; margin-top:18px; }}
h1 {{ font-family:Newsreader,Georgia,serif; font-weight:600; font-size:80px; line-height:1.04;
  letter-spacing:-.015em; margin-top:14px; }}
.blocs {{ display:flex; gap:18px; margin-top:26px; }}
.b {{ flex:1; border:2px solid #DCD5C5; border-radius:16px; padding:18px 20px; }}
.b.line {{ border-color:{CLAY}; background:rgba(176,73,44,.07); }}
.b .n {{ font-family:Newsreader,Georgia,serif; font-weight:600; font-size:56px; line-height:1; }}
.b.line .n {{ color:{CLAY}; }}
.b .t {{ font-size:20px; font-weight:600; color:{INK2}; margin-top:6px; }}
.foot {{ display:flex; justify-content:space-between; align-items:baseline;
  border-top:2px solid #DCD5C5; padding-top:18px; font-size:22px; font-weight:700; }}
.foot .m {{ color:#8A8275; font-weight:600; }}
"""


def x_card_html(n):
    return f"""<!doctype html><meta charset='utf-8'><style>{X_CSS}</style>
<div>
  <div class='rule'></div>
  <div class='kick'>Road to 61 · The Middle Ground</div>
  <h1>Nobody can form a government.</h1>
  <div class='blocs'>
    <div class='b'><div class='n'>{n['net']}</div><div class='t'>Netanyahu bloc</div></div>
    <div class='b'><div class='n'>{n['chg_hendel']}</div><div class='t'>Eisenkot + Hendel</div></div>
    <div class='b line'><div class='n'>61</div><div class='t'>Needed to govern</div></div>
  </div>
</div>
<div class='foot'>
  <span>kowaz.com/coalitionbuilder</span>
  <span class='m'>{n['source']}, {n['date']}</span>
</div>"""


BOARD_STATE = ("yashar.together.democrats.beiteinu.reservists"
               "._x_.reservists")


def capture_board(exe):
    """Screenshot the live board with Hendel pushed under the threshold.

    The carousel's claim and the tool's answer then come from one place: this
    is the product, arriving at the number the slides quote. ?shot=board lifts
    the board to the top of the frame, so the crop does not drift when the
    analysis paragraph changes length.
    """
    page = os.path.join(ROOT, "index.html")
    dst = os.path.join(OUT, "board.png")
    subprocess.run([exe, "--headless", "--disable-gpu", "--hide-scrollbars",
                    "--allow-file-access-from-files",
                    "--force-device-scale-factor=2",
                    # A narrower frame makes the board's own type larger inside the slide,
                    # which is what decides whether this reads on a phone.
                    "--window-size=880,752", "--virtual-time-budget=9000",
                    f"--screenshot={dst}",
                    f"file://{page}?shot=board&c={BOARD_STATE}"],
                   capture_output=True)
    ok = os.path.exists(dst)
    print(f"  board.png  {(os.path.getsize(dst)//1024) if ok else 0} KB",
          file=sys.stderr)
    return ok


def chrome():
    c = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    if os.path.exists(c):
        return c
    for name in ("google-chrome", "chromium"):
        p = shutil.which(name)
        if p:
            return p
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--html", action="store_true", help="write the HTML only")
    args = ap.parse_args()

    n = numbers()
    os.makedirs(OUT, exist_ok=True)
    src = os.path.join(OUT, "carousel.html")
    with open(src, "w", encoding="utf-8") as f:
        f.write(render_html(n))
    print(f"wrote {src}", file=sys.stderr)
    if args.html:
        return 0

    exe = chrome()
    if not exe:
        print("Chrome not found — open carousel.html and screenshot manually.",
              file=sys.stderr)
        return 1
    capture_board(exe)
    total = len(slides(n))
    for i in range(1, total + 1):
        dst = os.path.join(OUT, f"slide-{i}.png")
        subprocess.run([exe, "--headless", "--disable-gpu", "--hide-scrollbars",
                        "--force-device-scale-factor=1",
                        f"--window-size={W},{H}", "--virtual-time-budget=9000",
                        f"--screenshot={dst}", f"file://{src}?slide={i}"],
                       capture_output=True)
        size = os.path.getsize(dst) // 1024 if os.path.exists(dst) else 0
        print(f"  slide-{i}.png  {size} KB", file=sys.stderr)
    # The X card, rendered from the same numbers.
    xsrc = os.path.join(OUT, "twitter-card.html")
    with open(xsrc, "w", encoding="utf-8") as f:
        f.write(x_card_html(n))
    xdst = os.path.join(ROOT, "assets", "twitter-card.png")
    subprocess.run([exe, "--headless", "--disable-gpu", "--hide-scrollbars",
                    "--force-device-scale-factor=1",
                    f"--window-size={XW},{XH}", "--virtual-time-budget=9000",
                    f"--screenshot={xdst}", f"file://{xsrc}"], capture_output=True)
    print(f"  twitter-card.png  {os.path.getsize(xdst)//1024} KB", file=sys.stderr)
    print(f"\n{total} slides in {OUT}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
