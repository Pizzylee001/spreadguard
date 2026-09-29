# SpreadGuard Control Room: DESIGN.md

Read-only monitor for the SpreadGuard Hummingbot controller on Bitget BTC-USDT perpetuals.
Four routes driven by `agent_state.json` and `journal.jsonl`. The design is the interface.
It shows makers what the desk is doing and why, in calm language that never shouts.

## 1. Visual Theme & Atmosphere

Mood: precise, warm, and quiet. A desk instrument, not a cockpit. Dark theme from the
brief and the ledger: the previous two builds were light and bone, so this one explores dark.
Density dial 6, standard app with room to breathe. Energy 2, Rhythm 2, Motion 1.

Reading this as: an operate desk for a solo quant running a Hummingbot market maker,
in a warm black-and-tan instrument language, leaning toward readable depth over spectacle.

Surface mode: Operate. Background treatment: layered radial glow in the tan accent at
5 percent over the off-black base, fixed. A flat fill ships nowhere here.

## 2. Color Palette & Roles

| Token | Hex | Role |
|---|---|---|
| `--bg` | #171310 | page base, true off-black, warm |
| `--panel` | #201914 | card surface |
| `--panel-2` | #2A211A | tracks, hover fills, skeleton |
| `--line` | #3A2E25 | structural borders |
| `--line-2` | #4A3B2F | control borders |
| `--text` | #F2EAdf | primary text, 14.6:1 on panel |
| `--text-2` | #C9BCA9 | secondary text, 9.3:1 on panel |
| `--muted` | #A2937E | tertiary text, 5.8:1 on panel |
| `--tan` | #C8945A | the one accent, live signal only, 6.5:1 on panel |
| `--danger` | #C4623F | feed loss, errors |
| `--tan-soft` | rgba(200,148,90,.20) | selected state wash |

One accent per page: tan. Danger red appears only in error and feed-loss states.
Neutral fills use panel and line, never tan, so the accent always means the desk is acting.

## 3. Typography Rules

Font pairing: Corporate Trust, Lexend headings plus Source Sans 3 body.
IBM Plex Mono for figures only. Mono never carries prose.

| Role | Face | Size | Weight | Tracking |
|---|---|---|---|---|
| Page title | Lexend | 34px | 600 | -0.02em |
| State name | Lexend | 36px | 600 | -0.02em |
| Section label | Source Sans 3 | 12.5px | 400 | 0 |
| Body | Source Sans 3 | 15px | 400/500 | 0 |
| Figure | IBM Plex Mono | 11-13px | 400/500 | 0, tabular-nums |
| Eyebrow | IBM Plex Mono | 11px | 400 | +0.12em |

Headlines wrap with `text-wrap: balance`. Body prose and notes use `text-wrap: pretty`.
No em dashes anywhere. No uppercase labels except eyebrows and table headers.

## 4. Component Stylings

Nav pill: sticky floating bar, pill radius, blur backdrop, routes as text chips,
active route filled tan on off-black. One nav, shared on all four pages.

State card: panel with 1px line border, 12px radius, plus the Shine Border signature:
a tan sweep travels the border on a 6.5s loop, signalling the live controller.
Reduced-motion stops the sweep.

State switcher: radiogroup chips, outlined voice, checked chip bordered tan with a soft
wash. Full keyboard model: arrow keys rove, Home and End jump, selection announces
through a polite live region.

Depth profile: cumulative resting size by price, bids tan to the left of the mid,
asks neutral to the right, our quotes as short ticks. The step-back state pushes
the fenced area out so the guard is visible in the shape of the book.

Meters: 6px tracks with threshold marks, fill transitions 550ms on the ease curve.
Loading: skeleton shimmer plus spinner and one explanatory sentence.
Empty journal: dashed outline, a plain title, and the sentence that tells when the
first entry will land.

Tables: text left, figures right and mono, first column padded from the edge,
horizontal padding on every cell, scrolls inside its card under 414px. No clipped columns.

## 5. Layout Principles

Spacing scale 8px. Cards 12px radius, 22 to 34px padding. Page max width 1000px.
Grid: single editorial column on content pages, two-column only for the signal pair
on the desk. Three route shapes: instrument on the desk, list on the journal,
tables on limits, explainer on strategy.

Routes: desk.html (live instrument), journal.html (filterable audit record),
limits.html (safety envelope), strategy.html (how it decides). Real files linked
by the pill, each marked with aria-current. No tabs standing in for pages.

## 6. Depth & Elevation

Elevation from borders, not shadows, except one 12px soft shadow under the nav pill.
Surface order: base, panel, panel-2. No glow, no floating cards, no glass except
the nav blur. The shine border is the single permitted luminous element because
it marks the live state card, and its reason is written here.

## 7. Do's and Don'ts

Do: one accent, figures in mono, quotes rounded to cents, every number labeled
with its unit, every interactive control with a real behavior, every data surface
with loading and empty states.

Do not: Financial green and red (spent by the genre), Forest palette (spent by the
ledger), bone backgrounds (spent), Bebas or Syne (spent), Number Ticker or Text
Highlighter signatures (spent), oversized solid buttons (spent), bento grids,
3D surfaces, fake terminals, capsule badges, gradient text, em dashes, buzzwords.

## 8. Responsive Behavior

Rail collapses under 900px. Three-column content grids collapse to one under 760px.
Journal rows stack under 620px. Tables keep 18px edge padding, 14px cells under 620px,
and scroll inside their card under 414px. Tap targets at least 44px. No horizontal
page overflow at any width from 360 to 1600, verified by headless measurement.

## 9. Signature, Motion & Depth

Signature: Shine Border on the live state card (Magic UI special effects family).
Reason: the desk is a monitor, so one travelling highlight says it is watching,
and it costs no screen space.

Motion tier: subtle. Entrance none, hover 150 to 180ms on the shared ease,
meter fills 550ms, shine 6.5s. Scroll spy marks the route. Auto state cycling
stops the moment the user touches the switcher. Reduced-motion disables all of it.

Font load: Google Fonts stylesheet in the preview files to identify families and
weights. Production loads Lexend, Source Sans 3, and IBM Plex Mono via next/font
or self-hosted @font-face.

Tokens in this file are normative. If a value is missing, add a named token first,
then reference it. No mid-build hex improvisation.
