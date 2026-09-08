# Road to 61 — Knesset Coalition Builder

The interactive for kowaz.com/coalition. The seat numbers refresh themselves
from Wikipedia's poll tracker every morning; everything that requires judgment
stays hand-written.

## The split that makes this work

| | Where it lives | Who maintains it |
|---|---|---|
| Seats, change arrows, spread | `data/polls.json` | A GitHub Action, daily |
| Blocs, leaders, vetoes, positions | inside `index.html` | You, in Claude Design |
| A party formed since your last export | `data/parties-extra.json` | You, in a minute |
| The standing-analysis paragraph | `data/commentary.json` | You, whenever it turns |

Only numbers are scraped. Who refuses to sit with whom is the whole point of the
piece and cannot be scraped from anywhere — so nothing automated ever touches it.

## Putting it on the WordPress site

WordPress can't run this as a page of its own — it's a React app with its own
scripts and a JSON file it refreshes. The reliable way is to **host the app on
GitHub Pages and embed it in a WordPress page**. The daily Action keeps working
untouched, because it is updating the GitHub copy.

**1. Push this folder to a GitHub repo** (public, or private on a paid plan).

**2. Turn on Pages.** Repo → Settings → Pages → Source: *Deploy from a branch* →
`main`, folder `/ (root)`. After a minute it is live at
`https://eliko90.github.io/coalition/`. Open it and check the board says 120 seats.

**3. Embed it.** Edit the WordPress page at `kowaz.com/coalition`, add a
**Custom HTML** block, and paste the contents of `wordpress-embed.html`.
The URLs in it already point at `https://eliko90.github.io/coalition/`. Publish.

The iframe sizes itself — the app measures its content and posts the height out,
so there is no inner scrollbar and no guessed fixed height. The `allow` attribute
in the snippet is what keeps the Share button working from inside a frame; drop
it and the share menu fails silently.

**On WordPress.com**, Custom HTML blocks only run scripts on the Business plan
and above. On a lower plan the resize script is stripped and the embed will be a
fixed-height box with its own scrollbar, which reads badly on a page this tall —
better to link out to the Pages URL until you upgrade. Self-hosted WordPress
(wordpress.org) has no such limit.

Local check before you publish:

```bash
python3 -m http.server 8000
```

### The alternative, if you'd rather not use an iframe

On self-hosted WordPress you can SFTP this folder into
`wp-content/uploads/coalition/` and point the page there. It is same-origin and
there is no frame — but the daily Action then has to SFTP `data/polls.json` up
to the server on every run, which means storing deploy credentials as GitHub
secrets. More moving parts for a visual difference nobody sees. Only worth it if
the iframe causes you a problem.

## Refreshing the polls by hand

```bash
python3 scripts/fetch_polls.py
```

`--dry-run` prints without writing; `--summary` prints the markdown table the
Action posts to its run summary.

The script **fails rather than guesses**. If Wikipedia adds a column it does not
recognise, or the headline house stops polling, or the seats stop adding to ~120,
it exits non-zero and tells you what changed. The daily Action then emails you.
That is deliberate: a board silently missing a party that just won four seats is
worse than a board that is a day stale.

When it does stop, the fix is almost always one line in `scripts/mapping.json` —
that file maps Wikipedia's column headers onto this project's party ids, and it
carries notes on every merge and withdrawal so far.

## Which poll the page shows

The headline is a single house — **Kantar** — because the houses disagree far
too much to mix. In the current window Filber has Likud at 31 and Midgam at 19;
averaging those would invent a number no pollster published.

The newest Kantar poll wins regardless of which outlet published it (they
publish through both Kan 11 and Israel Hayom). Change the house in the
`headline` block of `scripts/mapping.json`.

The **range** shown on each party card is drawn from every pollster in the last
21 days, so the spread stays honest even though the headline does not move
between houses.

## Adding a party that just formed

Drop it into `data/parties-extra.json` — the board picks it up on the next page
load, seat totals stay whole, and you do not have to re-export the design. Fold
it into Claude Design when convenient and delete it from the file.

It currently holds **Amcha Yisrael** (Ofer Winter, launched 25 Aug 2026, ~4
seats). Its bloc placement and red lines are marked unconfirmed — worth your
review.

## Rebuilding after a Claude Design edit

```bash
python3 scripts/build_site.py "~/Downloads/Knesset Coalition Builder - standalone.html"
```

This unpacks the export into an ordinary web page: it pulls the fonts out to
Google Fonts, shrinks the two portraits, splits the scripts into `assets/`, and
re-applies the live-data wiring. It took the original 4.0 MB export down to
about 740 KB, most of that React.

Every edit it makes is asserted. If a future export renames something a patch
depends on, the build fails and names the patch instead of quietly producing a
page with no live data.

## Pushing a party under the threshold

Open any party whose recent polling has shown it missing 3.25% and the panel
offers "What if they miss the threshold?". Its seats are shared out among the
parties that cleared, and the whole board recomputes — bloc totals, the solver,
the kingmaker. A banner marks the hypothetical until you reset it.

Seats stand in for votes there, which is an approximation: the real allocation
is Bader-Ofer with surplus-vote agreements between named parties, and those can
pull a seat either way. It shows the shape of the change, not a projection.

## Checking your own work

Load the page with `?check` appended — or on localhost, where it is always on —
and an editor's panel appears under the byline flagging anything that needs a
human: a party in the polls but not on the board, a party on the board that has
stopped being polled, seats not adding to 120, a poll gone stale. Readers never
see it.

## What the live data has changed since the design

The design was built on a 9 August snapshot. Since then, from the live feed and
a fact-check of the red lines:

- **Yashar leads Likud**, 23 to 20. The baked-in text said Likud led by one.
- **Zionist Home dissolved** on 6 Sep — Tropper joined Yashar, Hendel kept the
  party and merged with the New Economic Party as **The Reservists**.
- **Amcha Yisrael** (Ofer Winter) did not exist in August and now polls four.
- **Erdan-Edelstein withdrew**; Balad is no longer polled separately.
- **Three Ra'am refusals were missing** — Yashar, Beyachad and Yisrael Beiteinu
  have all declared they will not rely on it, as has every change-bloc leader
  except Yair Golan. Recorded as soft: Bennett signed that pledge in 2021 and
  broke it within months.
- **Yashar refusing Likud was missing too.** Without it the solver offered a
  Netanyahu-led government containing Eisenkot as the only path to 61.

With the red lines complete, no combination reaches 61 without crossing one.

Party data lives in `data/parties-extra.json`, prose in `data/commentary.json` —
both editable on GitHub without a rebuild.

### Still open

- Eisenkot is preparing a **minority government** of 58-59 seats, governing with
  Arab parties supporting from outside rather than joining. The board only knows
  61 in-coalition seats, so it cannot show the route currently most likely.
- The vetoes are still unsourced on the page itself. Each one is now accurate,
  but a reader cannot check it without taking your word.
