# Changelog

## 2026-06-02 (Chart UX — white price mark + declutter)
- **Current price now marked in WHITE** on the chart, not the green/red candle color (confusing next to the
  green buy zone / red stop). Candlestick `lastValueVisible:false`; a neutral white price line is drawn in the
  live (`obj`) view and tracks the live tick (`updateChartLive` updates `chartModal._priceLine`). (`web/app.js`)
- **Chart draws ONLY the selected setup's levels.** Removed the faint "other setup" overlay (zone/stop drawn
  faded) — it cluttered/overlapped, especially with the confirm + pullback levels close together. The
  Pullback/Confirm buttons still switch which setup is shown. Legend updated. (`web/app.js`, `web/index.html`)

## 2026-06-02 (Confirmation entry = nearest resistance + breakout-catch + day-low stop)
- **Confirmation is now a real per-setup ALT ENTRY** (`kind:"confirm"`), graded + forward-tested like any
  entry — replaces the earlier card-level "safer entry" box (which was per-TICKER, not per-setup). For
  `worth_waiting` setups (Deep Pullback / Consolidation), `scanner.analyze` adds it as the **nearest real
  resistance buyers must reclaim overhead**: the closest above price among the **9/21/50 EMAs** (computed to
  match the chart's 9/21/50 lines), the **prior-day high**, and recent **swing-high pivots** — 1× ADR stop.
  Don't wait for a far high; breaking the closest level is enough on a strong leader. (e.g. INTC → *reclaim the
  21 EMA ~109.7*, not the 113.3 prior-day high). When price is below the 50, the deep-pullback PRIMARY entry
  already IS the 50 reclaim. `confirm_kind ∈ {9ema,21ema,50ema,high,resistance}`. `ema10/20/50` exposed on the
  result. Card renders a **🔔 Confirm** badge per option. (`scanner.py`, `web/`)
- **LIVE breakout-catch for deep pullbacks** (`breakoutCatchFor`/`_applyCatch`, sibling to `rotationFor`): when
  the market's OPEN and a patient name has already broken out above its deep dip zone, a dip to the 50 EMA is
  unlikely — so the displayed pullback is swapped for a **shallow catch to the nearest rising EMA (10/20)** with
  a ≤1× ADR stop (🎯 *breakout catch* badge). EOD it reverts to the deep zone. (`web/app.js`, `web/index.html`)
- **Breakout-catch grade fixed:** it was inheriting the deep-pullback's A. Now graded off the **confirm/breakout
  option + a notch** (`_plusGrade` → e.g. **B+**) — better than chasing the confirmed breakout, but not the
  deep pullback's A. `gradeColor` reads the first letter so `B+`/`C+` color correctly. (`web/app.js`)
- **Confirm entry stop → the DAY'S LOW (live)** (`_confirmDayStop`): a confirmation is a breakout buy-stop, so
  once it triggers the real invalidation is today's low (like the rotation), not a fixed 1× ADR stop. Tightens to
  the day low (floored at 0.3× ADR; never wider than 1× ADR), recomputes risk/shares, labels **"Stop (day low)"**,
  and the chart stop line tracks it live. (e.g. INOD confirm risk 15.07→7.45/sh). (`web/app.js`, `web/index.html`)
- `entryHint`, the confirm/catch/rotation notes, and all `$` glyphs are **currency-aware** (`cur` → ₪ on IL).

## 2026-06-02 (Israeli / TASE market — local-only)
- **Added a second market (Israel / Tel Aviv Stock Exchange) with a top-right 🇺🇸/🇮🇱 toggle.** Each market is
  fully separated: own dashboard, account, positions, P&L, suggestions, and forward-test data. US is byte-for-byte
  unchanged; IL namespaces every data file under `data/il/`.
- **Backend (`app.py`):** mirrors the per-user workspace pattern — a thread-local `_ctx.market` set per request
  from an **`X-Market`** header (`set_workspace`); `_mns()` namespaces shared files and `_ud()` per-user files;
  the 16 shared-file constants became **per-market getter functions** (`suggest_f()`, `forward_f()`, `pnl_f()`,
  …); `write_json` makes parent dirs; lazy IL workspace bootstrap. **Gotcha fixed:** `_ctx` does NOT propagate to
  worker threads, so `_spawn()` re-applies market+udir into every scan/build/forward worker (else IL jobs would
  write into US files). Session/close gating (`_market_closed`/`_after_close_today`/`_session_date`/
  `_next_session_date`) is market-aware. Forward-test + P&L are per-market; `_run_forward_eod_all` loops US+IL
  (local-only).
- **Config (`scanner.py` `MARKETS`/`mcfg`):** benchmarks (US SPX/QQQ/IWM; IL `^TA125.TA` + `TA35.TA`), session
  hours, **trading days (IL = Sun–Thu)**, tz, currency, and universe thresholds (IL price/dollar-vol in **agorot**,
  marketCap in ₪). `scan`/`analyze_at`/`market_regime`/`_attach_rs` thread a `market` arg.
- **IL universe (`universe.py`, `data/il_symbols.json`):** TASE has no free symbol directory like NASDAQ-Trader,
  so the list is **auto-harvested from Yahoo's screener** (`exchange=TLV`, quoteType=EQUITY → 820, bonds/T-bills/
  series stripped → **499 candidate equities**); `build_universe(market='il', symbols=…)` re-quotes + liquidity-
  filters them (same pipeline as US) → **~222 kept**. TASE has ~500 listed equities total — no 800-name liquid set
  exists like the US; 222 ≈ the whole investable market.
- **Frontend (`web/`):** US/IL pill (mobile-safe), `currentMarket()`, `X-Market` on every request, `reloadAll()`
  swaps the whole app, and a `cur` getter renders **₪ vs $** (TASE quotes in agorot — strategy math is ratio-based
  so unaffected, display only).
- **Local-only:** the toggle is gated `x-show="!hosted"` and `init()` forces `market='us'` when hosted, so the
  hosted swinghelper site stays **US-only** (IL ships inert in the code; `data/il/` + `il_symbols.json` aren't
  copied by the build). Verified end-to-end: data isolation, IL scan (220 graded setups), regime, forward, mobile.

## 2026-06-02 (Forward-test: snapshot top 50, not 10)
- `FORWARD_TOP_N` **10 → 50** (`app.py`). The best setups are "worth waiting" and rarely trigger same-day, so the
  old top-10 was mostly un-entered names — a wider net captures the lower-ranked names that *do* have an entry
  today, so the forward test keeps collecting real data. Pulls from the full `suggestions.json` (not the
  dashboard's top-10), local-only, gated to post-close. Applies to both markets.

## 2026-06-02 (Grade is PER ENTRY OPTION, not per ticker)
- User: "grade should be PER setup — INOD pullback can be A but the breakout is DEF NOT." The grade was
  computed once per ticker from the primary's factors; now **each entry option is graded on its own merit**.
- `scanner.analyze` computes per-entry **`ext50_adr` + `entry_quality` + `chase_exempt`** on every `entries[]`
  plan — graded at the price you'd actually PAY (a buy-stop at its higher trigger; a BUYABLE-NOW pullback at
  the current price, not its lower limit — so APLD buyable at 48 grades as extended, not at its 46 limit).
- `_rating(it, unit)` (app.py) accepts per-entry inputs; the loop grades each option and sets the ticker
  headline grade = **max of the options** (best available setup); each option carries its own `grade`/`rating`.
- Card shows a **colored grade letter per option row**. Result: **INOD pullback A+ / breakout C**; DOCN
  pullback A+ / breakout D; ONDS pullback A / breakout C; DXYZ pullback A / breakout B (breakout only 1.7×
  ADR extended → still B, not blanket). **Restart python + re-scan.**

## 2026-06-02 (Grade rubric v5 — chases out of B, A reachable for the best)
- **Problem:** posture-58 tape capped every grade at B → 0 A's, 98 B's, ~half of them CHASES (e.g. SEDG
  +85%/1m, 4.1× ADR above the 50-EMA, graded B/72). "If chases get B, B is meaningless." Diagnosed via a
  3-agent deep-research pass over the rubric + the 228-trade backtest + the 9-trade journal (CRWV waited→+3R
  vs INOD chased→+0.9R; AVWAP +0.58R / Consolidation +0.41R / worth-waiting +0.36R work in any tape;
  breakouts/EPs fail in weak tape).
- **Fix (canonical in `strategy/scoring.md` v5, mirrored in `app.py _rating()` + `scanner.py analyze()`):**
  1. **Extension/chase penalty** on `ext50_adr` (distance above the 50-EMA in ADR) — graded demote
     `−(ext50_adr−2.5)×8`, **cap at B once ≥2.5×**, **HARD-CAP at C once ≥4×** (parabolic threshold 4.5→4.0,
     now caps the GRADE not just `buyable_now`). Gradient: <2.5× A-eligible / 2.5–4× B / ≥4× C. `worth_waiting`
     dip-buys exempt (deep pullback buys AT the 50; tight Consolidation has a tight base/stop). The 2.5× B-cap
     came from the **APLD** case (Pullback @ AVWAP, ext 2.7, +36%/1m — shallow pullback in an extended move,
     was A → now B).
  2. **`entry_quality` also penalizes distance from the 50-EMA/base** (`stretch50_pen`), not just the 10-EMA
     — closes the "10-EMA is itself parabolic" blind spot (SEDG entry_quality 42→7).
  3. **Timing rewards WAITING** (in-zone bonus only for non-extended names).
  4. **Regime gate is setup-aware** — breakouts/EPs stay capped at B below posture 65; patient
     worth_waiting/AVWAP setups with `ext50_adr<3` can reach **A even in mixed tape** (user choice: "let the
     best reach A now").
- **Result on the 796 (posture 58):** A=12, B=47 (was 98), C=167, D=570. SEDG→C; no ≥4×-ADR name above C;
  the A's are clean pullback/consolidation/AVWAP at support (incl. DOCN, the user's model trade). Magnitudes
  are a starting calibration — tune via the forward-test loop. **Restart python + re-scan.**

## 2026-06-02 (Dual entries, live rotation, forward-test fixes — session)

### Dual entry options + EP relabel (`scanner.py`, `app.py`, `web/`)
- Every suggestion now carries an **`entries` list** (1–2 plans): a **Breakout** (buy-stop above a pivot) and/or
  a **Pullback** (buy the dip to the best support below price). `entries[0]` mirrors the legacy primary so
  grade/sizing/forward/coach are unchanged. Helpers `_breakout_plan`/`_pullback_plan`; each plan typed +
  phrased (**"break above $X"** vs **"wait for the pullback to $Y"** — never "pull back" for a buy-stop).
  Patient setups also offer "break above the prior-day high"; shown only when distinct (≥0.4×ADR). Cards
  render both options with per-option sizing; chart has a **▲ Breakout / ⏳ Pullback** switch (other drawn faint).
- **Episodic Pivot now requires a TRUE open gap** (`gap_up`≥8, gated in scanner) **AND a fresh good-news
  catalyst** (confirmed in `grade_suggestions`); a multi-day run into a base high = **Breakout** (fixes the
  long-flagged ONDS/INOD relabel).
- Coach docs (`CLAUDE.md`, `find-setups`) updated with the EP rule + dual-option phrasing.

### Suggestions sort: grade dominates (`web/app.js`)
- `sugRank`/`gradeBand`: order is **grade band → buyable-now → rating**, so a buyable **C never outranks a B**
  (the "press show-all to find hidden B's" bug). Buyable-now still floats up, but only *within* a grade band.

### Live intraday ROTATION pullback (`scanner.py`, `app.py`, `web/`)
- During REGULAR hours, when a non-patient name has broken out and is **pulling back to its prior-day high**,
  the Pullback option becomes a live rotation: **buy the reclaim of the prior-day high, stop at TODAY's low**
  (both update each tick; ⚠ flag if risk > 1×ADR). `fetch_quotes` now returns `day_high/low/open`; `analyze`
  exposes `last_bar_date/last_high/prev_high`; `grade_suggestions` computes a date-correct `prior_high` via
  `_session_date()`; frontend `rotationFor`/`displayEntries`; chart tracks the rotation stop to the live low.

### EOD jobs are POST-CLOSE only — calendar/forward no longer corrupted mid-session (`app.py`)
- `record_daily_pnl` + the forward snapshot capture were firing on **every launch / 30-min heartbeat / scan**
  (not gated), so dev restarts wrote a stale mid-session value into today's P&L cell and re-froze the next
  snapshot from re-scanned data. New `_after_close_today()` (weekday, ET≥16:00) gates both — never pre-market/
  mid-session. Snapshots now carry `logged_at` + `frozen_at_close_of`. Cleaned the bogus 06-02 P&L cell + the
  premature 06-03 snapshot (backed up first).

### Forward-test chart + readout (`app.py`, `web/`)
- Forward-pick chart shows the **FROZEN snapshot levels** (not recomputed), a ▲ **signal marker at the freeze
  bar**, and a 🔬 **Snapshot badge**. **🔬 Frozen ⇄ 📈 Live** toggle: Live drops the setup and compares the
  **Entrance to current price** (the idea's progress). **No target line.** Metric is **% made, not R**
  (`fwdPct`/`fwdAvgPct`); day header shows "+X% avg". Levels drawn as **rays from the signal forward** so
  pre-freeze candles don't look like fills. Status labels: "filled · open" / "stopped out" / **"no fill yet"** /
  **"never triggered"**. Every pick carries **`progress_pct`** (entry → latest close) so a name that ran without
  giving its dip still shows the move (un-filled shown muted).
- **Snapshot date = the session you TRADE the picks; sim fills from that day inclusive.** The 06-02 snapshot
  held 06-01's traded picks (RGTI/CIFR filled on 06-01) — relabeled **06-02 → 06-01** so the fills show, and
  created today's **06-02** snapshot. (Open follow-up: track the breakout leg in the forward test too — on
  up-days the pullback leg misses moves a breakout would catch.)

## 2026-06-02 (Prediction now reads pre/after-hours SECTOR movement)
- User: the prediction's rotation (Into/Out of) is the EOD multi-day trend and ignored premarket — e.g.
  Photonics/Optics was popping pre-market two days running but still showed as "cooling."
- Added `_premarket_sector_moves()`: averages each sector's members' **`ext_change_pct`** (the true
  extended-hours move vs the regular close, ≥2 members printing) → up/down sector movers. `compute_prediction`
  adds a **🌙 {Pre-market|After-hours} sector moves** driver, nudges the score, mentions the leaders in the
  outlook, and returns `pm_sectors`. Prediction UI shows a dedicated **"🌙 Pre-market now:"** chip row
  (green up / red down) under Into/Out, with a "moves often fade at the open" caveat — kept SEPARATE from
  the multi-day trend so it's a heads-up, not a trend call.
- **Important:** used `ext_change_pct`, NOT `live_sector_heat`'s `perf_1d` — during PRE `perf_1d`'s
  `prev_close` is 2 days back, so it doubled in yesterday's session (showed AI-Networking +13%; the real
  premarket avg is ~+8%). Verified in PRE: Photonics/Optics now surfaces at +3.2% (n=19) as a leader.
  **Restart python + refresh.**

## 2026-06-02 ("Detect new groups" actually finds NEW ones)
- Bug: it was showing clusters whose common thread is an **existing theme** (e.g. INFQ/QBTS/RGTI → all
  Quantum), defeating the purpose. (The "Other" the user saw was the legacy *sector* column; the *theme*
  map has all three in Quantum.) `run_detect_groups` kept theme-dominated clusters with `novel=False`.
- Fix: a cluster that is **entirely an existing theme is now dropped**. An existing-theme cluster is kept
  ONLY when **new names are joining it** (members not in that theme) — surfaced as `joining` (candidate
  additions), e.g. "🆕 INFQ → Quantum". Genuinely new clusters (thread not a known theme) stay as
  `novel` 🆕 groups. Frontend: joining badge + amber ring + a "candidate addition" note + a 🆕 tag on the
  new member; help text rewritten. Verified: the Quantum cluster now drops (0 groups → honest empty
  state); injected joining/novel samples render correctly. **Restart python + refresh.**

## 2026-06-02 (System is now pre/after-hours aware — regime, gameplan, prediction)
- **% color:** position P&L % was grey — now inherits the row's green/red (up/down). (index.html)
- **Market regime shows pre/after:** `live_posture` now returns per-index `ext_pct` + `market_state`/
  `extended`; the regime card shows a 🌙 chip per index (QQQ/IWM; the cash index ^GSPC has no premarket
  print) and a "🌙 PRE/AFTER" header badge. The posture/states already re-blended from live prices.
- **Gameplan & Prediction take pre/after into account:** new `_effective_regime()` — during pre/after
  hours it re-blends SPX/QQQ/IWM from extended-hours prices (`live_posture`); otherwise the stored daily
  regime. Both `compute_gameplan` and `compute_prediction` use it, so posture/stance/lean reflect what's
  moving NOW. Gameplan stance appends a "🌙 Pre-market: … this read uses live prices" note + a header
  badge (`regime_live`/`market_state`); prediction adds a 🌙 extended-hours index-move driver that biases
  the lean. Frontend re-fetches gameplan (+prediction if open) every ~3 min during extended hours.
  Verified in PRE: gameplan `regime_live:true` posture 58 (live), prediction uses it, regime card 🌙 chips.

## 2026-06-02 (Trim strategy: parabolic-only — no more quick trims)
- User: "I do not trim my positions so quickly. I only trim if a stock went parabolic or close to it,
  the EMAs are VERY far from it — like ARM or DELL." The coach trimmed too eagerly (TRIM at `r≥3 &&
  ext9_adr>2.2`, and pre-earnings at `r≥0.5`).
- **Change (both `position_coach` in app.py and `liveCoach` in app.js, kept in sync):** TRIM now fires
  **only on a genuine parabolic blow-off — `ext9_adr ≥ 4.0` (price ≥4× ADR above the 9 EMA, miles above
  the 21/50) with `r ≥ 1`**. Ordinary strength now stays HOLD/RAISE-STOP and rides the 9-EMA trail.
  **Earnings → WATCH** ("binary event; hold through or reduce, your call"), no longer an auto-TRIM.
  `liveCoach`'s ext9_adr is live, so the parabolic trim is premarket-aware.
- Verified: parabolic (4.5× ADR) → TRIM; strong-but-not-parabolic (1× ADR) → RAISE STOP; the old eager
  case (2.5× ADR, +R) → now holds. Playbook updated in `strategy/my-rules.md`. **Restart python + refresh.**

## 2026-06-02 (Premarket P&L semantics fixed + per-position pre/after change)
- **Bug:** during PRE, "Today's P&L" showed a non-zero number = **yesterday's** full-day move. Cause:
  before today's open, Yahoo's `regularMarketPrice` is still yesterday's close and `prev_close` is the
  day before, so `reg_price − prev_close` = yesterday's move. Today's regular session hasn't happened.
  **Fix:** `dailyPnl` returns null during PRE/PREPRE → tile shows "—"; the only live number premarket is
  the **🌙 Pre-market P&L** sub-line (extended price − yesterday's close). Verified: PRE → Today "—",
  Pre-market +$87.
- **Per-position pre/after-hours change** (user ask): each position row in the Gameplan now shows a
  **🌙 chip** with its extended-hours % (and $ impact in the tooltip + expanded line). `tickLive`
  attaches `t._extPct`/`t._extImpact` from the quote's `ext_price`/`reg_price`/`ext_change_pct`.
  Verified live: ONDS −1.71% (−$46), CRWV +2.71% (+$61), etc. Frontend only — **refresh**.

## 2026-06-02 (Dashboard: positions merged into Gameplan, regime color fix, major-news banner, premarket coach)
Four user-requested dashboard improvements (verified live in PRE, desktop + 375px, no console errors):
- **Manage positions ⨉ Open positions merged.** The Gameplan now has one full-width **"Your positions"**
  section (driven by `openTrades`): each row shows the live coach action + ticker + P&L/R + reason, with a
  **"more ▾"** toggle that expands to the full open-position detail (setup/grade badges, entry/stop/risk-
  basis/shares, hit-target, all coach reasons, Chart/Edit/Close). The standalone "Open positions" card was
  removed (redundant). Per-row expand state = `expandedPos` (keyed by trade id).
- **Regime color bug fixed.** An "Extended" index showed **green** because it was colored by raw posture
  (55 → lime band) while the emoji said 🟠. New `stateColor(state)` colors each index by its STATE
  (Extended → amber), matching the emoji. IWM/SPX/QQQ now read amber when extended.
- **Major market news banner.** A deliberately HIGH-bar macro detector (`MACRO_PATTERNS` + `_detect_macro`
  in app.py, fed by a dedicated macro RSS query) surfaces ONLY regime-changers — war/military, Fed-chair
  change, emergency Fed move, election/president shock, market crash/halt, debt/fiscal shock, national
  crisis. Routine Fed speeches, "price war", single-stock moves, and everyday tariff headlines are
  rejected (verified). Shows as a prominent red/amber banner at the top of the dashboard; empty on a
  normal day (`news.macro`). Populates on the next **New day** / news refresh.
- **Premarket-aware position coach.** The merged section uses the **live** coach (`t._liveCoach||t.coach`),
  which recomputes off the live (pre/after-hours) price. Verified: in premarket, INOD flipped from the
  static **HOLD** (last close) to **RAISE STOP** because the premarket spike pushed it past +1R.
  **Restart python** (app.py changed) **+ refresh** (app.js/index.html changed).

## 2026-06-02 (Professional dashboard redesign — two-column, decluttered)
- User feedback: the dashboard was a long single stack of heavy cards — "too cluttered, not easy on the
  eyes." Reorganized into a clean, scannable layout (verified desktop + 375px mobile, no console errors,
  no horizontal overflow):
  - **Toolbar** slimmed (data-as-of + Rebuild universe + New day); the verbose universe-coverage line is
    now the Rebuild button's tooltip instead of a standalone gray paragraph.
  - **KPI strip** at the top — `.stat` tiles: **Equity** (new), Today's P&L (with the 🌙 pre/after-hours
    move folded in as a sub-line so the strip is always a clean 6), Open P&L, Realized, Win rate, Avg R.
  - **Two-column work area** (`lg:grid-cols-12`): main (8 cols) = Gameplan → Open positions → Top
    suggestions; right rail (4 cols) = compact Market regime → vertical Catalysts list → compact Position
    calculator. Collapses to one column on mobile (main above rail).
  - **Declutter:** explanatory footnotes (regime "equal-blend…", gameplan "synthesized…") moved to
    tooltips; market-regime condensed from 3 big multi-chip cards to one compact row per index
    (state · 50-MA ext · off-high · 1m); catalysts changed from a horizontal scroll strip to a tidy
    vertical list (ticker + headline + date).
  - No data/logic changes — pure layout; all existing Alpine bindings/getters reused. **Refresh the
    browser** (frontend only).

## 2026-06-02 (Chase guard held on live ticks + pre/after-hours P&L split out)
- **Bug 1 — extended name showing "🟢 BUYABLE NOW":** NBIS (5.6× ADR above the 50 EMA, AVWAP-reclaim,
  not a patient setup) showed BUYABLE NOW *and* the ⚠️ "extended — chasing" warning at once. The scanner
  correctly set `buyable_now=False` (chase guard), but the frontend live tick (`tickLive`, app.js:298)
  recomputed `buyable_now` from **live price vs zone only**, wiping the guard on every poll. Root trigger:
  the "in-zone" price ($271.25) was a **pre-market** print. **Fix:** the live recompute now mirrors the
  scanner guard — `parabolic && !worth_waiting`, `distribution_today`, or `extended` ⇒ not buyable, even
  if price sits in the zone. Hardened `inZone()`'s fallback the same way.
- **Bug 2 — pre/after-hours leaking into "Today's P&L":** during PRE/POST, `fetch_quotes` collapsed
  `price` to the extended-hours print, and `dailyPnl` used it — so a pre-market gap (e.g. MRVL reg 219 →
  pre 276) counted as today's regular-session P&L. **Fix:** `scanner.fetch_quotes` now returns
  `reg_price` / `ext_price` / `ext_change_pct` separately (keeps `price` as the live value for position
  P&L/charts); `/api/live` forwards them. Frontend: **Today's P&L = regular-session move only**
  (`reg_price` − base), plus a new **Pre-market / After-hours P&L** tile (extended price − regular close)
  that shows only during extended hours. Verified live in PRE: NBIS reg 264.51 / pre 272.91 split cleanly.
  **Restart python** (scanner.py + app.py changed) **then refresh** (app.js + index.html changed).

## 2026-06-02 (News catalysts surface ANY universe mover, not just the top-16 suggestions)
- **Bug the user hit:** MRVL got a big catalyst ("Marvell stock soars — Nvidia CEO calls it the next
  trillion-dollar company") but it never showed in **catalysts**. Root cause: the per-ticker news pool
  (`ticker_news` → the catalyst table + 🚀BUY/🛑AVOID alerts) was built ONLY from the top-16 graded
  suggestions, so a fresh mover that wasn't already a setup was invisible (it sat in the raw feed only).
- **Fix:** after building the feed, `run_news_refresh` now **promotes big catalysts on ANY universe
  name** by resolving each material headline back to a ticker — by company name (`Marvell Technology`→
  MRVL) or an explicit ticker token (`HPE stock soars`). New: `universe.fetch_symbol_names()` +
  `clean_company_name()` (keyless NASDAQ directory → `data/symbol_names.json`, cached ~monthly),
  `app._symbol_names()` + `_build_news_resolver()`.
- **Precision guards** (so it surfaces the SUBJECT, not every mentioned name): a name only counts when
  it's immediately followed by stock/shares/possessive or a price-action verb (kills "Truist cuts…",
  "…Morgan Stanley sees", "Price **Target**", "(NASDAQ:…"); restricted to the tradeable universe;
  generic first-words/tokens blocklisted; **mixed-sentiment roundups skipped** ("…Rally; Credo Plunges").
- Alerts now **buy-first then recency-sorted** (cap 8→10) so a fresh mover outranks week-old news.
  Verified: MRVL is the #1 🚀BUY catalyst + shows in the catalyst table ("not in current setups",
  clickable to chart); promoted set was clean (MRVL/HPE/S/SMCI). Frontend needed no changes.
  **Restart the python process** (app.py changed), then **Refresh news / New day**.

## 2026-06-02 (Live re-rank RESTORED — reconciliation roadmapped)
- Reverted the previous change: the dashboard's **live re-rank is back** (buyable-now floats to the top,
  updates live) — removing it made the best setups lag, which the user (rightly) rejected. The
  dashboard "Top suggestions" is intentionally a LIVE view and will differ intraday from the frozen
  forward snapshot. **Reconciling the two properly is now an OPEN BUG / roadmap item** (PROJECT.md →
  "Next highest-value" item 0): root cause is that top picks all tie at **rating 72** (regime-gate cap),
  so any re-rank reshuffles; fix by breaking the tie with raw score / widening the grade and labeling
  the snapshot (static) vs the dashboard (live) — without removing the live re-rank.

## (superseded) 2026-06-02 (Dashboard top picks == forward snapshot)
- [reverted — see above] Had removed the intraday reorder to force a match; that caused lag.

## 2026-06-02 (Forward log keyed by the TRADE day, not the signal day)
- Snapshots now key by **`_next_session_date()`** (the upcoming session you'd ACT on the picks), not the
  signal session. So picks captured at tonight's close show up labeled **tomorrow** ("your watchlist
  FOR 06-02"), matching how a trader thinks. `_sim_forward` measures from that trade day onward (`>=`),
  which also avoids the signal-day instant-loss bug. `run_forward_eod` captures the next-session
  watchlist from the current top suggestions (no auto-scan). Existing 06-01 snapshot re-keyed → 06-02.
  Verified: forward shows **2026-06-02 · 10 awaiting** (DOCN/INOD/APP/ONDS/AEHR…); scores after 06-02 trades.

## 2026-06-02 (Forward sim fix #2 — measure AFTER the signal day only)
- **Root bug:** the suggested entry is a buy-stop set just above the signal day's range — which equals
  that day's HIGH (ONDS entry 13.91 = 06-01 high; INOD 117.19 = 06-01 high). The sim was measuring the
  signal day ITSELF, so it "triggered at the high and closed lower" = a fake −R (ONDS −0.33R) and a
  misleading −1R aggregate. **Fix:** `_sim_forward` now looks ONLY at bars *after* the signal day — you
  act on the trigger the next session and the result is measured from there. With no post-signal bar
  yet → status **"awaiting"** (no fake R). Each pick's tooltip says exactly what it's waiting for
  ("would enter on the break above $X next session"). Day summary shows "N awaiting next session".

## 2026-06-02 (Forward sim fix — honor the entry TRIGGER)
- **Bug:** the sim entered every pick at the day's OPEN, ignoring the setup's entry trigger — so a
  breakout buy-stop that never broke out (INOD, entry $117.19, closed $114.40) was wrongly "entered"
  and an intraday wick to the stop was logged as a **false −1R** (which was dragging the matured
  aggregate to −1R). **Fix:** `_sim_forward` now waits for the trigger — buy-stop fills only when a
  bar's HIGH reaches entry; limit/pullback fills when a bar's LOW reaches it. Never triggered → **"no
  fill"** (not a loss). On the fill day a stop counts only if the bar CLOSES below it (an intraday wick
  that closes strong isn't a false stop-out). Result: INOD now reads **open −0.13R** (triggered, slightly
  red), pullbacks that didn't fill show "no fill", and the bogus −1R is gone.

## 2026-06-02 (Forward picks → chart + personal P&L calendar)
- **Each forward pick is now clickable to its chart** with the SNAPSHOT's entry/stop drawn — so you can
  see exactly which stop the forward-sim used (it uses the *suggested* stop, not your personal one;
  e.g. INOD "stop" exit was the suggested level, not where your stop sat).
- **Personal P&L calendar (Stats).** `record_daily_pnl()` saves the account's daily equity + **day P&L**
  (= today's equity − the last recorded day's; realized + open) into `pnl_calendar.json`, updated every
  EOD cycle (local only). New month-grid calendar colors each weekday green/red by its P&L with a
  month total. `GET /api/pnl-calendar`.

## 2026-06-02 (Forward = same-day open→close + news connected to setups)
- **Forward picks now score from their SIGNAL DAY** (enter at that day's OPEN, track open→close then
  forward), instead of waiting for the next session. So the day's top picks show their result at that
  day's own close — verified: 06-01's picks now read DOCN +0.4R, ONDS +0.3R, INOD −1.0R, avg +0.19R,
  70% win, with a real lesson. `run_forward_eod()` no longer gates on market-closed — it **scores
  continuously and captures each new US session's picks** (so tomorrow's set is added at the next open).
- **News tab = ONE connected table.** Removed the spammy "Actionable now" chip cards. The primary view
  is now **Catalysts → setups**: each stock with a news catalyst joined to its grade/setup/why and
  **what to do** (🟢 buy zone / ⏳ wait), sorted actionable-first then newest. Broad macro/sector
  headlines moved to a secondary **Market headlines** feed below. `catalystTable` computed (news ⋈ suggestions).

## 2026-06-02 (News feed cleanup + prediction-news + gameplan clarity)
- **News tab rebuilt as ONE clean feed.** Replaced the scattered category cards with a single
  **deduped, newest-first "Latest catalysts" feed** (material-only) with **relative timestamps**
  ("2h ago"), sentiment icons, source, and a Trump tag. Backend builds `news.feed` (deduped from the
  already-sorted `pool_imp`, ≤24); frontend `ago()` helper. The "Tickers worth watching" sidebar stays.
- **Prediction now names the actual catalysts** (not just a +/- count) and weights news a bit more —
  a "Catalysts: 🚀 …" driver lists the top material headlines feeding the lean.
- **Daily Gameplan clarity:** the stance is now a prominent posture-tinted **banner** (headline +
  bottom line), and Manage / New-entries / Avoid / Remember are separated into distinct panels.

## 2026-06-02 (Forward log: keyed by US session + autonomous updating)
- **Bug fix — key by the US trading-session date, not the local clock.** `now_date()` is the local
  date, which rolls over before the US close (e.g. Israel midnight = 17:00 ET, prior session). The
  forward log now keys off `_session_date()` (SPY's latest daily bar), so days aren't mislabeled and
  no phantom "next day" is created before the US session trades.
- **Autonomous EOD updating.** `run_forward_eod()` (on launch + every 30 min, local only, while the
  market is CLOSED): `_refresh_forward_bars()` pulls the latest session's bars (SPY-probed, only for
  picks that are behind) so **prior days' picks score forward automatically as each session closes**;
  and it **captures the latest completed session's top picks** if not yet logged (auto re-rating the
  universe first if the current scan isn't from that session). Results now update themselves at each
  close with no clicks.

## 2026-06-02 (Forward test: per-day results + daily lesson)
- The **Stats → 🔬 Forward test** card now shows a **day-by-day breakdown** (collapsible per date) of
  the top picks logged that day, each with its forward R + status (matured / open / no-entry) and
  Trend-Template/VCP badges — plus an **auto-generated lesson per day** (`_day_lesson()`): avg R,
  best/worst pick, and which trait carried the edge (Trend Template / VCP / buyable-now / setup type).
  `score_forward()` now returns `by_day`. Results accrue as forward sessions arrive (enters the session
  after the signal; "matures" ~7 sessions).

## 2026-06-01 (Dashboard reorder + auto daily forward-data)
- **Dashboard reordered by priority** (flex `order`, no risky block moves): refresh → coverage →
  **Market regime → Daily Gameplan → Today's P&L → Open positions → catalysts → Top suggestions →
  Position calculator (now last)**. Fixes the calculator/tool sitting above your positions & ideas.
- **Auto daily forward-data (local only).** `log_forward_picks()` now snapshots the **top 10 by rating
  (ANY grade** — some days have no A/A+) instead of only A/A+, with `trend_template`/`vcp` flags.
  `run_forward_eod()` + a background heartbeat (`_forward_eod_loop`, started in local `main()`)
  **auto-snapshot once per day when the market is closed** (`_market_closed()` ET check), gated to
  today's scan + not-already-captured. Scored over the following days by the existing `score_forward()`
  / Stats "forward" tab — a growing dataset to learn which setups actually work. Never runs hosted.

## 2026-06-01 (Suggestions UX + auto-equity + chase guard)
- **One watchlist button.** Removed Approve/Reject (and the pending/approved status filter) from
  Suggestions — replaced with a single **+ Watchlist** button (`addSugToWatch`/`onWatch`); the status
  badge now shows only "✓ taken". "Took it" (log a trade) stays.
- **Auto account equity.** The typed-in account size is now the **base**; the app derives live
  **equity = base + realized + open P&L** (`compute_equity()`), uses it for sizing (`_equity_settings()`),
  and shows it in the sidebar ("$21,284.96 (+$1,067 P&L)"). The close handler no longer mutates the
  base — realized is computed from closed trades, so the account "updates itself."
- **Chase guard (NBIS fix).** A momentum/breakout name **parabolic-extended ≥4.5× ADR above the 50 EMA**
  is no longer flagged "buyable now" even if the close lands in the zone — that's chasing a vertical
  move. Card shows "⚠️ Extended ~X× ADR above the 50 EMA — chasing; wait for a pullback." Patient
  dip-buy setups (deep pullback / consolidation) are exempt. Verified: NBIS (5.6× ADR) → not buyable.

## 2026-06-01 (Minervini Trend Template + VCP + redesigned filter bar)
- **New strategy: Mark Minervini** (`strategy/minervini.md`, from verified deep research) as the
  "worth watching" **eligibility gate** — complements (doesn't replace) Qulla/Luk.
- **Trend Template** (`scanner`): `trend_template` boolean + `tt_count` (n/8) — price > 50/150/200
  SMAs, 50>150>200 stacked, 200-SMA rising, ≥30% above the 52w low, within 25% of the 52w high, RS
  rating ≥70 (the RS criterion finalized in `_attach_rs`). 137/796 pass — a clean Stage-2 leader
  universe (DOCN/ONDS/AEHR/INTC… 8/8). Refuted variants deliberately NOT encoded.
- **VCP detector** (`scanner._vcp`, approximate): `vcp` + `vcp_contractions` — successive shallower
  contractions on drying volume near the base high; hardened against flatline/illiquid false
  positives (plateau-collapse + 2–6 contraction cap + a real ≥8% leg required).
- **Suggestions filter bar redesigned**: 4 stacked rows → one grouped, collapsible bar (`.fchip`),
  with the top signals always visible + a "⚙ Filters (N)" toggle + match count + clear. **New filters:
  Trend Template, VCP, News catalyst, Buyable-now**; ✓ Trend Template / 🌀 VCP badges on cards. Mobile-safe.
- Wired `minervini.md` into the in-app Strategy tab (`DOCS`, `docTabs`) and the build (`make-build.ps1`).

## 2026-06-01 (Initial-stop / risk basis — R survives a breakeven raise)
- Trades now carry **`initial_stop`** (the stop taken at entry) separate from the editable live
  **`stop`**. Raising the stop to breakeven no longer destroys the trade's R: **all R is measured off
  `initial_stop`** — `position_coach` r_mult, the new unrealized **`r_open`** in `enrich_trades`, and
  **`result_r` on close (now computed server-side**, not trusted from the client). Set at creation,
  the original is captured the first time the stop is edited, and older trades default it to the
  current stop on read. UI shows "(init $X)" beside the live stop, the live R on journal cards, and a
  live R preview in the close modal — all off the initial stop. Fixes MSFT/DOCN (live stop = breakeven)
  which were showing broken/zero R; they now read +1.3R / +1.1R. Unblocks the ±8 win/loss-by-setup
  learning loop for trades managed with a trailed stop.

## 2026-06-01 (Entry grade on trades — grade your own setups)
- Every trade now carries the **system grade of its setup as of the entry date**, derived
  automatically from `taken_at` — no manual entry. `scanner.analyze_at(sym, date)` slices the cached
  daily bars to the entry date, runs `analyze()`, and attaches an RS-outperformance proxy from the
  indexes sliced to the same date; `app.entry_grade_for()` rates it on the **reconstructable price
  factors** (setup quality, entry location, relative strength, liquidity) with market
  regime/sector/news held **neutral** (they can't be time-traveled) — same weights + letter
  thresholds as the live grade (`_grade_letter`, shared now).
- `enrich_trades` adds `entry_grade` / `entry_rating` / `graded_setup` / `low_grade` (<B) to every
  trade. Shown as a colored **grade badge** on dashboard Open Positions and both Journal lists, with a
  dashboard header summary ("your entries avg C (62) · 5 below B"). The engine's own setup read is in
  the tooltip when it differs from what was logged (e.g. DOCN logged AVWAP → engine saw Consolidation/A).
- Purpose: a mirror on the trader's **own** entries (taking C/D setups is a fair, self-sourced lesson),
  separate from the system's picks. *Limitation: price-based only — historical regime/sector/news
  aren't reconstructed, so the entry grade isn't identical to a live grade on the same name.*

## 2026-06-01 (Spinning: 9 EMA + 2-green confirmation)
- **Switched the spin line from the 10 EMA to the 9 EMA** (5-min) to match the chart.
- **Confirmation:** a name now needs **2 closed green candles above the 9 EMA** (recent window) before
  it counts as a spin — a single candle tagging the line no longer qualifies. The higher-lows
  tolerance still applies *after* confirmation, so a confirmed name isn't dropped on one dip under the
  line. Verified live: every returned spin has `green_above ≥ 2` (e.g. NXPI/HOOD confirmed then sitting
  right on the line). UI relabeled 10EMA→9EMA throughout.

## 2026-06-01 (Spinning stocks screener)

### 🔄 Spinning stocks (Screeners → 🔄 Spinning)
- New intraday-reversal screener: **beaten-down stocks starting to rotate back up.** On the 5-min
  chart, a green candle reclaims a turning-up 10 EMA off a real flush — ranked by how much snap-back
  potential is *left* (early reclaim, near the line, buyers stepping in), not how far it's already
  bounced. The ASTS example (flushed ~$113→$101, reclaiming the 10 EMA at ~$103) is the gold standard.
- `scanner.spin_signal()` (5-min detector) + `scanner.scan_spinning()` with a **smart live-quote
  pre-filter**: pulls quotes for the ~800 universe, keeps only names *down on the day* (the beaten-down
  set), then fetches 5-min bars for just those (~50s, ~140 candidates instead of 800).
- **Gates** (tuned on live data): real flush (`drop ≥ max(4%, 0.8×ADR)`), reclaimed the 10 EMA
  (fresh cross or ≤6 bars above), MA turning up + off the low, and not-already-run-away (≤8 bars
  above / ≤2.2% over the line). **Potential score** blends drop depth, freshness, up/down volume on
  the turn, reclaim-candle strength, proximity to the line, and a sweet-spot off-low.
- **Higher-lows fix (less aggressive):** a single 5-min candle dipping a little under the 10 EMA no
  longer drops a name — if it reclaimed recently and is still making **higher lows** (rising 5-min
  structure, `scanner._higher_lows()`), it stays on the list within a small tolerance under the line
  (`min(1.2%, 0.2×ADR)`). Higher-lows structure is now a scored term and shown as a 📈 badge; the
  "vs 10EMA" chip handles the small negative. (e.g. ENPH was kept at −0.11% under the line.)
- **Leader / rising-sector** toggle filters + a ranking boost (+8 RS-leader, +6 rising sector); each
  card shows RS, setup, theme, drop%, off-low, Δ-to-10EMA, volume, news, and a stop idea (intraday
  low). `GET /api/spinning` (+ enrichment/boost) and `POST /api/spinning/scan`; **auto-refreshes
  every ~3 min** while the tab is open during market hours. Verified live (UMAC/LUNR/RKLB/ASTS led a
  space/drone rotation day), clean console, mobile-safe at 375px.

## 2026-06-01 (perf + groups polish)

### Performance (no behavior change)
- **Scans ~10× faster.** The scan slept `0.05s` per ticker unconditionally (~40s of pure sleep over
  800 names) even when every bar was cached. `get_bars` now flags whether it hit the network
  (`_DID_FETCH`); the scan only throttles on actual fetches. **Cached full scan: ~44s → ~4s** — makes
  "New day", the 30-min auto-rescan, and group detection near-instant.
- **Lighter DOM.** The Suggestions grid rendered all ~700 cards (a ~7MB DOM that slowed rendering and
  the live merge). Now caps at the **top 120** by rating (accurate "showing top 120 of N · show all"),
  so the page and live updates are snappier.
- Backed up the full project to `backups/<timestamp>/` before the pass.

### Detect-new-groups polish
- Each group now **names its common thread** (preferred: the specific theme; fallback: a broad sector),
  and groups with **no shared thread** (no tag shared by a majority of members) are **hidden** — no more
  "Emerging / cross-sector" blobs. 🆕 marks a thread that isn't one of the 38 fixed themes. *(Note:
  `sectors.json` is sparse, so some real clusters can't be named and are conservatively hidden.)*

## 2026-06-01 (latest) — Detect new groups, live pre-market, P&L baseline

### #6 Detect new groups (Screeners → 🧭 New groups)
- `scanner.detect_groups()` finds **emerging groups**: takes the recent strong movers, z-scores their
  last 15 daily returns, links any pair with correlation ≥ 0.86, and returns the connected components
  (size 3–20). `GET /api/groups` + `POST /api/groups/detect` (background job); app tags each group's
  dominant sector and flags 🆕 when it spans multiple/none of the 38 fixed themes (a genuinely new
  group). New Screeners tab renders each cluster's members + avg 1-week move. (Found e.g. TEAM/NOW/
  WDAY/IOT and QBTS/RGTI/INFQ moving together.)

### #5 phase 2 — live pre-market movers
- On the Pre-market tab during the PRE session: listed movers' price/gap update live from quotes
  (pre-market overlay), and a full re-scan auto-fires every ~8 min to catch new gappers. Green
  "live · auto-updating" badge.

### Today's P&L baseline fix
- Positions **entered today** now use your **entry** as the day baseline (you only owned it from the
  fill), not yesterday's close — so a name you bought today shows only the gain since your fill.
  (MP bought today: +$20, not the phantom +$127.)

## 2026-06-01 (late) — Live updates + backtest validation

### Live updates during market hours (roadmap #5, v1)
- **Batched live quotes** — `scanner.fetch_quotes()` pulls Yahoo `v7/quote` (cookie+crumb), 50 symbols
  per call, 30s shared cache, pre/post-market price overlay. Degrades gracefully.
- **`GET /api/live?symbols=`** — returns live prices + `market_state` + a **live blended posture**
  (regime recomputed with live index prices).
- **Frontend live poller** — every ~45s while the market's open (5min when closed), gated by market
  state. Merges live price into: open positions (live P&L/R + **hit-target / at-stop / now-under-9-EMA**
  flags), suggestions (live price → live "buyable now" / stopped), and the market-regime card. A
  **"● LIVE · OPEN · 8s"** header badge shows state + age with a pause toggle.
- Scope: positions + suggestions + regime. Phase 2 (queued): live Sector Heat + pre-market.

### Live refinements (same day)
- **Live coach** — the position action now recomputes from the LIVE price (was stale EOD → e.g. CRWV
  showed +5% *and* "EXIT below stop"). Intraday under the 9-EMA is now **WATCH** ("exit only if it
  CLOSES under it"), not a hard EXIT.
- **Setup-aware exits** — Deep Pullback / Consolidation are bought at the 50-EMA / in a base, so they
  sit under the 9-EMA by design. Those setups now trail the **50-EMA** (not the 9), and a fresh entry
  (≤2 sessions) holds to its **stop** instead of getting shaken out. Coach carries `e50`/`patient`/`young`;
  patient match is case-insensitive.
- **Live re-rank** — suggestions that pull into their buy zone float to the top as prices move.
- **Auto-rescan** — optional (default on), every 30 min while the market's open, with **fresh bars**
  (`scan ?fresh=1` → `max_age=0`) so new setups/grades appear intraday. Toggle on the Suggestions tab.
- **Today's P&L** tile on the dashboard (live mark-to-market vs prior close).
- Server now sends `Cache-Control: no-cache` on the app shell/scripts so a rebuild never looks
  "stuck" behind a stale cached `app.js`.

### Live coach correctness (round 2)
- **"Armed" trailing exit** — the 9/50-EMA close-exit only applies once the position has *closed
  above* its line since entry. Buying a dip BELOW the line is no longer treated as an exit; until it
  reclaims the line, only the hard stop exits. (Generalizes the deep-pullback case to every setup.)
- **Breakeven/raised stops fixed** — a stop at/above entry used to make risk ≈ 0, which broke the
  coach (it fell back to a stale "EXIT below stop"). R is now recovered from the 2R target, and a
  breakeven+ stop reads as a *locked-in* exit, not a panic. (Your CRWV: now HOLD, not EXIT.)
- **Live no longer reverts on save** — `loadTrades()`/`loadSuggestions()` re-apply the live merge,
  so saving a journal entry / editing a stop doesn't flip the page back to stale EOD data for ~45s.
- **Live charts** — the chart modal now fetches fresh bars (today's forming candle shows, `/chart`
  uses a 15-min cache) and the last candle **moves with the live price** each tick while open.
- **Live Sector Heat** — `GET /api/sector-heat/live` recomputes each sector's & member's TODAY %
  and the heat score/rank from live quotes (multi-day trend/streak kept from the EOD compute).
  Polls ~60s while you're on the Sector Heat tab; green "live" badge shows the time. Read-only —
  never overwrites the stored EOD heat.

### Backtest validation of the grader
- Built `backtest.py` (replay grade as-of past dates → simulate forward with real exits). Found the
  A/A+ grade is positive but context-dependent; shipped six tuning changes (regime gate, Rising>Hot,
  PREF, anti-chase, wider stop, trim dry-vol) → `scoring.md` v4.2.
- **Out-of-sample check** (fresh dates): edge holds but in-sample was ~3× optimistic (≈+0.26R/36%
  generalizable). **Forward/paper test** wired (`/api/forward`, Stats tab) — logs live A/A+ daily,
  scores as they mature; true OOS, no survivorship bias.
- Hosted: hid Journal / Watchlist / Strategy (no persistence on the free host) via `/api/env`.

## 2026-06-01 — Earnings, volume, position coach, daily gameplan, prediction

Built roadmap items 4 → 3 → 2 → 1 → 7 in one session.

### Earnings dates (new data source)
- `scanner.get_earnings()` pulls the next earnings date from Yahoo `quoteSummary/calendarEvents`
  via a cached cookie+crumb session. Disk-cached daily; degrades to `None` on any failure so a Yahoo
  change can never break the scan. Returns `{date, ts, estimate}` (`estimate` = Yahoo's est. flag).

### #4 — Upcoming-earnings warning in Suggestions
- The scan fetches earnings for the top ~70 names; `grade_suggestions()` computes
  `earnings_days`/`earnings_soon`/`earnings_near` and **demotes the grade −18 (≤7d) / −6 (8–14d)**.
- Suggestion cards show a 🗓 earnings badge (red ≤7d, amber ≤14d) + a ⚠️ "probably skip" banner.

### #3 — Earnings + volume on charts, volume into the setup score
- Chart modal: a **volume pane** (green/red histogram, Vol toggle) + a next-earnings chip in the header.
- `scanner.analyze` adds a **volume-character signal** (`±2` on the raw score): rising/heavy down-day
  volume on a pullback = distribution (flag, penalize); drying volume = healthy; rising up-day volume
  on an advance = accumulation (favor); thin advance = penalize. Shown in the "why" line. Rubric v4.

### #2 — Suggested position actions (Dashboard)
- `position_coach()` rates every open position: **EXIT** (closed under the 9-EMA / below stop),
  **TRIM** (extended +3R or earnings imminent with profit), **RAISE STOP** (+1R → breakeven),
  **WATCH** (bad news / earnings with no cushion), **HOLD**, plus an optional **ADD** note. Each open
  position now shows an action pill + the reasons (profit R, extension vs 9-EMA in ADR units, earnings days).

### #1 — Daily Gameplan (Dashboard, top card)
- `GET /api/gameplan` synthesizes regime, positions + their actions, exposure (invested % / free cash /
  open risk %), buyable A/A+ setups, earnings/news avoids, and top lessons into one prioritized plan
  with an honest bottom line — "do nothing today" is a valid plan.

### #7 — Prediction (News → 🔮 Prediction tab)
- `GET /api/prediction` blends market regime, sector rising/slowing/falling, breadth, news tone,
  end-of-day buy/sell footprint and pre-market skew into a **lean** (Bullish → Risk-off) + confidence
  + a driver list. Framed as probabilistic, not advice.

### Distribution-day fix + two new Suggestions filters (same day)
- **Distribution / climax-reversal day** (`scanner.analyze`): a heavy-volume rejection off a recent
  high (or the "distribution" volume signal) now sets `distribution_today`, **forces
  `buyable_now=false`**, and **caps the composite grade at C** in `grade_suggestions` — overriding RS
  and a hot sector. Fixes the case where ASTS graded A "buyable now" the same scan it flagged "pullback
  may not be over." A "let it settle, don't buy the drop" banner shows on the card. (Extended >2.2×
  ADR names are no longer flagged buyable-now either.) Verified: ASTS A→**C**, top 8 now clean.
- **Two Suggestions filters** next to ⏳ Worth waiting: **🏆 Market leaders** (RS percentile ≥ 85, ~top
  15%) and **🚀 Rising sector** (`theme_trend == Rising`). Verified counts: 124 / 129 of 796.
- Re-scanned the full 800-name universe so earnings dates + the new flags are live (70 earnings dates,
  3 earnings-soon demoted, 175 distribution-day flags). Rubric → `scoring.md` v4.1.

### Internals
- Extracted the suggestion grading out of the HTTP handler into a reusable `grade_suggestions()`
  module function (used by both `/suggestions` and `/gameplan`). Added `days_until()` + `datetime` import.
- Verified live: gameplan, coach, prediction, volume pane, earnings chip; clean console; no 375px overflow.

## 2026-05-31 (evening) — Hosted launch, pre-market movers, mobile

### Hosting (share with friends)
- **Multi-user "hosted" mode** (`HOSTED=1`): each browser gets its own private workspace
  (`data/users/<id>/`, keyed by an `X-Workspace` id in localStorage — no login). Market-wide data
  (suggestions, screeners, universe, sector heat, news, regime, price cache) is **shared**.
- Local mode (`HOSTED` unset) is unchanged — single-user, all data in `data/`.
- `DATA_DIR` env points data at a mounted disk so journals survive redeploys; shared seed files are
  copied in on first boot. Binds `0.0.0.0`, reads `$PORT`, runs a daily shared-refresh thread.
- Deployable repo `swinghelper/` (Dockerfile, `render.yaml`, `fly.toml`); deployed on Render.
  One-command publish via `make-build.ps1` ("make a new build").

### Pre-market movers (new)
- New **🌅 Pre-market** sub-tab under Screeners. Scans the universe for the biggest pre-market gaps
  vs prior close (Yahoo pre/post bars). Each card shows gap %, price, prior close, sector (🔥 if hot)
  + trend, setup type + RS percentile (from the daily scan), a linked news catalyst (sentiment-colored),
  and pre-market volume. Tap a card for the chart.

### Sector Heat
- **Find-a-stock search** — type a ticker (e.g. CRCL) to see its group, auto-expanded + highlighted
  (authoritative lookup via `themes.json`, exposed at `/api/themes`).
- **Click column headers to sort** (Today/Week/Month/6mo/Streak/Heat); removed the top-right buttons.

### News / Suspicious / Suggestions
- News sorted **newest → oldest** (News tab + dashboard).
- Suspicious activity **sort control** (Volume / Move / Strength), default Volume.
- Suggestions sector filter lists **all sector-heat categories** (not just those in the current scan).

### Charts
- **EMA** and **AVWAP** toggle buttons (on by default) alongside Log / Channel.

### Mobile / responsive
- Full mobile pass: off-canvas **drawer nav + hamburger**, display-zoom auto-capped on phones,
  responsive inputs/modals/grids, and **horizontal scroll on wide data tables** (sector heat,
  suspicious) so columns never smush or get cut off. Standing rule: every change must be
  mobile-friendly (see `PROJECT.md`).

---

## 2026-05-31 — Big upgrade day

A full day of work turning the Data Center from "scans a hand-pasted list of 193 names with a
simple grade" into "scans the whole liquid US market with a context-aware grade, market regime,
sector heat across 38 themes, and proper momentum filters." Here's everything, in plain English.

---

### 🧠 Smarter suggestion grades

- **Wrote down how grading actually works** → [strategy/scoring.md](strategy/scoring.md). It used to
  live only in code; now there's a plain-English rubric we edit first and keep the code in sync with.
- **Relative strength** is now a real factor (15% → 14%): each name's 1M/3M return vs the market, as
  both a percentile-in-universe and outperformance-vs-index. Leaders float to the top.
- **Entry location** is now graded, not pass/fail: buying stretched far above the 10-EMA, or with a
  stop forced near a full 1× ADR, drags the grade down. This is the "don't chase" dimension.
- **Liquidity** added (8%): average daily dollar volume on a log scale. No liquidity = no
  institutions. Shown as a 💧$X/d chip on every card.
- Final weights: setup 28 · RS 14 · regime 14 · entry 14 · liquidity 8 · sector 10 · timing 6 ·
  news 6, plus the ±8 nudge from your own realized trades once you've logged ~5+.

### 📊 Market context (new)

- **Market regime panel** on the dashboard — SPX / QQQ / IWM each classified (Healthy / Recovery /
  Extended / Pullback / Mid- or Deep-correction) with a blended 0–100 "posture" that feeds 15% of
  every grade. Breakouts get demoted harder than pullbacks when the tape is weak.
- **Distance-from-the-50-MA gauge** (your "ATR multiple from 50-MA" idea) on each index — a
  correction-risk meter that flags ⚠ when an index is stretched far above its 50, with a graded
  posture haircut. Also added the **20-EMA** distance alongside it.

### 🎯 Better stops & leaders

- **Reclaimed-swing-high stops** (your NVDA example): the engine now picks the tighter *valid*
  structural stop between the 5-day low and a reclaimed prior swing high (reported "close below").
- **Leader-in-group medal** 🥇 — within each sector, the strongest name gets crowned, ranked by
  **relative strength + liquidity** so a thin stock spiking on low volume doesn't get called the
  leader.

### 🌐 The whole US market, not a pasted list

- **New universe builder** (`universe.py`) — pulls every US-listed stock (~5,100, NASDAQ/NYSE/AMEX),
  filters to your Market Leaders criteria (price > $10, mkt cap > $300M, $vol > $10M), and keeps the
  **top 800 by liquidity**. Fully keyless (NASDAQ symbol directory + Yahoo crumb-authed quotes).
- The old hand-pasted 193-ticker list is gone; "Market Leaders" now auto-populates the real universe.
- **🌐 Rebuild universe** button on the dashboard does this on demand. It does **not** run on "New
  day" — that only refreshes the existing names.

### 📈 Momentum screens (your 1M / 3M / 6M)

- Recreated your three TradingView momentum screens as **filter buttons** on Suggestions.
- To make them real, the price-history fetch was extended from 8 months to **1 year** (so the
  200-MA and 52-week-high are computable; also sharpens the AVWAP-from-ATH).
- **Made them exclusive cohorts** so they actually find *new* movers: **🔥 New (1M)** shows only
  fresh breakouts up >20% this month that aren't already an established 3M/6M trend — it no longer
  shows the old leaders that happen to be up this month.

### 🗂️ Sector Heat — 38 themes now

- Read your carefully-built sector taxonomy and **added 104 momentum-relevant names** from the new
  universe, completing your existing groups (Memory, Photonics, Semi Testing, Precious Metals, …).
- **11 new themes**: Nuclear, Power Producers, Cybersecurity, AI Software/Apps, AI
  Networking/Hardware, Analog/Power Semi, Compute (CPU/GPU), Foundry, Steel, Copper, LNG/Nat Gas.
- Left all the broad-market names (banks, REITs, staples, healthcare, transports) **out on purpose**
  — that's how you built it.
- Ran the heat: 38 sectors now rank by momentum (AI Networking/Hardware came out hottest).

### 🔎 Filters on Suggestions

- **Sector dropdown** (all 38 themes, ordered hottest-first) — e.g. "show me only Nuclear setups."
- **Live match count** + a **✕ clear filters** button.
- All filters — setup type, momentum cohort, sector, status — **stack together** (AND), so you can do
  "new movers in Quantum that are pullbacks" in a few clicks.

### 🛠️ Fixes & docs

- **"New day" no longer rebuilds the universe** — it just refreshes regime, setups/ratings, sector
  heat, and news for the names you already track (4 stages). Rebuilding the 800 is manual only.
- **Front-end style guide** written → [web/STYLE.md](web/STYLE.md), so the dark/glassy look stays
  consistent across sessions.
- Kept [PROJECT.md](PROJECT.md) and [strategy/scoring.md](strategy/scoring.md) up to date with all of
  the above.

### 🎣 Catching strong leaders on the dip (AXTI/VRT fix)

- **New "Deep Pullback" setup** — a strong leader (big run, still above the 200-MA) that's pulled
  back *below* its short EMAs toward the 50 EMA used to get mis-classified as a near-ATH breakout
  (dumb). Now it's caught at the **50 EMA / recent support with a tight stop below the swing** —
  e.g. AXTI now reads entry $100 / stop $93.70 instead of "buy near the $150 ATH," VRT at its 50 EMA.
- On parabolic names the 50 EMA lags far below price, so the entry is **floored at the recent
  pullback low** (where it's actually finding support), not 14% lower.
- **"⏳ Worth waiting" tag + filter** — the patient at-support setups: a strong leader correcting
  *deep* into its 50 EMA (VRT/AXTI/LITE), **or** a strong stock **consolidating** sideways in a tight
  base on the 50 EMA (the SNDK base — buy the dip to the 50 while it waits). NOT shallow pullbacks
  near the highs that are still moving up. Filter button + card badge.
- **Buyable-now buffer** — a name sitting just above its buy zone (IREN at $63.54 vs a $63.02 top)
  now reads "buyable now" instead of "wait" (~0.3× ADR tolerance).
- The stock's **own strength carries the grade** even when its sector is cooling.

### 📐 Charts: log scale + trend channel

- **Logarithmic price scale, ON by default** (with a Log toggle) — essential for $3→$155 names.
- **Trend channel ("tunnel")** — an auto-fit linear-regression channel (fit on log price so it's a
  straight band on the log chart), drawn with a 📐 Channel toggle.

### 🕵️ Suspicious activity (insider-style EOD footprints)

- New **Suspicious Activity** tab under News (📰 News / 🕵️ Suspicious), split **🟢 buying / 🔴 selling**.
- Scans the universe's **intraday 5-min bars including pre/post-market** for an end-of-day or
  **after-hours volume spike (≥4× the day's average 5-min volume) paired with a directional move** —
  the footprint of aggressive buying/selling into the close or after hours.
- Validated on the examples: **CIEN +4.2% on 9.4× vol**, **USAR +3.2% on 7.9×** (buying), **NVDA −1.9%
  on 10.2×**, **INTC −2.3% on 8.7×** (selling) — while boring names (AAPL/KO/PG) correctly flag nothing.
- On-demand **"🔍 Scan now"** button (full 800-name universe, ~8–10 min — your choice); doesn't slow
  down New day. Each row links to the chart.

### ⚙️ Under the hood

- New data files: `data/market.json` (regime), `data/universe.json` (last build).
- New API routes: `/api/market`, `/api/universe` (+ `/api/universe/build`).
- Yahoo data is still keyless; the one new trick is a cookie+crumb session for batch quotes
  (`universe._yahoo_session()`), needed because Yahoo now 401s the plain quote endpoint.
