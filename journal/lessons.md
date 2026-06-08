# Lessons (my trading memory)

The agent reads this before proposing setups and checks every candidate against it. It grows from
my real trades via `/log-trade` and `/review-trades`. Keep it tight — only durable, actionable
lessons, newest insights merged in (not an endless list of duplicates).

## Active lessons
- **Record the REAL initial stop the moment you log a trade — and never let a breakeven raise erase
  it.** The day-1 logs showed stop = entry (placeholders), which hid the actual risk: MSFT's true stop
  was the breakout-candle low $432 (0.44% risk), DOCN's was $146.1 (0.64%) — both fine, just not
  written down. Without the initial stop, R can't be measured and the learning loop can't see the
  trade. Habit: log the actual structural stop at entry; system fix: keep `initial_stop` separate from
  the live stop (roadmap). (from the day-1 batch, 2026-05-30)
- **Trim into strength and trail the 9 EMA once a winner clears ~+2R — don't give it all back.**
  INOD is ~+0.9R and DOCN ~+1.2R while still open; the plan is to bank a partial into the spike
  and raise the stop (breakeven after +1R, then trail the 9 EMA on a daily-close basis) so a winner
  can't round-trip. (open observation, INOD/DOCN, 2026-06-01 — confirm when closed)
- **Late to a news catalyst → only enter on a spin/reclaim near support, never the extended top, and
  don't size up on the thesis.** ONDS was a Trump/US "drones" news chase a day late; entering on the
  intraday 9-EMA reclaim (a "spin") was the right way to get controlled risk on a chase — but it was
  still a C-grade, #4-preference (EP) setup and risk came in at ~1.24% (over the 1% cap). A hot story
  ("gov't-backed names always run") is not a reason to break sizing. Keep risk ≤1% and put the stop
  under the spin low; if it doesn't reclaim, there's no trade. (from ONDS, 2026-05-30)
- **A beaten-down low-ADR mega-cap "catch-up" bet is the opposite of this strategy — don't run it on
  the momentum book.** MSFT was bought as mean-reversion (lagging, ADR<4%, not an RS leader) on a real
  macro read (software rotation / IGV inflows + ARM-PC news). The thesis can be right, but a slow
  low-ADR name won't give the explosive R this system needs, and the 1×ADR stop math doesn't fit. If
  you take a conviction off-strategy trade: define a REAL stop (never stop = entry — MSFT had zero
  risk defined), size it ≤1%, keep it small, and treat it as separate from the momentum-leader book so
  it doesn't pollute the learning data. **Express a sector-rotation thesis through the LEADER, not the
  laggard:** same IGV/software read gave MSFT (beaten-down, ADR<4%, grade D) *and* DOCN (breakout of a
  multi-year base, grade A, +1R). The thesis was right both times — the vehicle decided the outcome.
  (from MSFT vs DOCN, 2026-05-30)
- **When it runs before you get in, WAIT for the retest of the breakout/support level — don't chase
  the extended move.** This is the single biggest R lever in the journal, shown by two trades in the
  same situation:
  - ✅ **CRWV** ran hard in pre-market; instead of chasing, waited for the pullback to the $114
    support (a prior high → support) and bought the retest. Tight structural stop → **0.24% risk and
    ~+3R**.
  - ❌ **INOD** was late to the breakout, entered at ~$101.6 but left the stop back at the missed
    AVWAP ($87.2) → a ~14% stop = **1.45% risk (over cap), only ~+0.9R** despite a big % move.
  Same setup, opposite execution: the retest gives a tight stop and big R; chasing with a far-back
  stop oversizes risk and caps R. Real-time **phone alerts** would let me catch the retest live.
  (from CRWV & INOD, 2026)
- **ENGINE/coach issue, not a chase: the confirmation must fire NEAR the planned zone, not far above it.**
  On 2026-06-05 the app gave CRDO (*Pullback @ AVWAP*, planned $205.44) and AAOI (*Consolidation*,
  planned $157.19), and the confirmation engine froze the BUY well above those levels — CRDO at
  $219.67 (~+7%) and AAOI at $197.08. Following the app is correct execution (I did NOT chase these),
  but a pullback/consolidation confirmation that fills 7%+ above its own zone produces a stop near 1×
  ADR and leaves the trade exposed to exactly the overnight gap-down that stopped both out (−1R each).
  FIX (system, on me as the coach): a Pullback/Consolidation confirmation should only fire inside (or
  just reclaiming) the planned zone; if live price has run far above the zone before the trigger, the
  engine should re-base the setup or stand down — not freeze a buy at the extended price. (from CRDO &
  AAOI, 2026-06-05)
- **Check the room to target before entering — overhead resistance caps the grade.** RGTI was a clean
  rising-sector (quantum) pullback-to-the-9-EMA entry with tight risk, but it's a *recovering* name
  (not a new-high leader) and a clear ceiling (~$27.67, the 400 EMA / AVWAP) sat ~11% up — so a clean
  2R landed right at that resistance. That's what makes it a B, not an A: an A wants a clear RS leader
  AND open air to target. With overhead supply, plan to trim into it / at 1R and only press if it
  reclaims the level on volume. (from RGTI, 2026-06-01)
- **Explosive volume on a beaten-down name can override a weak-sector view — but enter on the reclaim,
  not after the candle runs.** LITE was in a slowing/rotating-out sector (normally a pass), but a
  pre-market flush + huge opening volume reclaiming the 50 EMA / $799 support was a textbook reversal
  **spin** — volume is the tell, and that's exactly what the Spinning screener flags. Good: confirmed
  on the break of the prior-day candle, stop at structure, 0.73% risk. Miss: entered $873 *after* the
  candle ran ~8% off the low, so the stop is ~1.2× ADR and entry location is stretched. Take the spin,
  but nearer the reclaim for a tighter stop. (from LITE, 2026-06-01)
- **Maximize R, not win rate — let winners RUN.** Every "fix" that raised win rate cut total R (enters higher / clips big winners). The max-R recipe: enter **EARLY** (prior-day-high break), stop at the **DAY'S LOW**, trail the 9 EMA. The 50-EMA trail is ONLY for a leader bought DEEP at the 50; everything else trails the 9. A 48–52% win rate with monster winners beats a 60%+ win rate that clips them. **Full ruleset: [strategy/swing-system.md](../strategy/swing-system.md).** (from the intraday backtest, 2026-06-03; trail revised 2026-06-05)
- **Don't trade a deep correction — stand aside.** The system correctly grades NOTHING A-worthy in a real correction and even the strongest names get whipsawed in a crash. The big winners ran in the RECOVERY, and the system catches those when the tape turns back up. Cash is a position; never knife-catch a falling market. (from the March backtest, 2026-06-03; full stats → strategy/swing-system.md)
- **Never decide — or TUNE — with hindsight.** A backtest is only honest if every call uses bars up to THAT
  day only. I once peeked at which March stocks had risen and tuned the grader to reward them — pure
  curve-fitting; it fell apart (−13%) when traded blind. Holding a trade forward to measure it is fair; using
  the future to make or tune the decision is cheating. Stay honest about survivorship too (today's universe
  applied to the past) — the live forward test is the only true judge. (methodology, 2026-06-03)

- **A pullback entry is a RESPECTED-SUPPORT BOUNCE, not a passive limit fill in mid-air.** TSEM showed a
  "buy zone" (232–248) sitting in air — the stock rocketed $200→$300 then knifed straight down through the
  band, yet the engine flagged it *buyable now* on a big red day. Wrong. The rule: price must pull back TO
  real support, **respect it** (not close decisively below), and **jump off it** — either a same-day spin
  reclaim (intraday 9-EMA / OR break with buyers) or a next-day green candle off the held support. Being
  *inside* the zone = ARMED (watch), never an auto-buy. **Backtest-validated** (blind, winsorized,
  `tools/research_bounce.py`): vs the passive-limit fill the bounce rule **~halved the trades (233→107),
  lifted win rate (28→34%), and ~doubled avg R (+0.62→+1.27R)** — it discards exactly the falling-knife
  fills. Shipped in `scanner.analyze` (`_respected_bounce` gates `buyable_now` for limit entries) +
  reinforced live by the 2026-06-05 buyers-confirm gate. (from TSEM, 2026-06-05)

- **Deep Pullback vs Consolidation = EMA fan, not range.** AXTI (a +650%/6mo leader pulled back to the 50
  EMA, just like TSEM) was mislabeled "Consolidation." The tell that separates them: a real **consolidation
  has the 9/21 EMAs bunched near the 50** (sideways long enough to converge); a **deep pullback has the 9/21
  fanned well above the 50** (it dropped fast). The old detector keyed on range scaled by ADR — useless on a
  15%-ADR name (a 62% "tight base"!) and fooled by an up-then-down round trip reading as "sideways." Fixed:
  consolidation now requires the EMAs converged; deep pullback keys on PROXIMITY to the 50 EMA (not a pull-%
  cap, since explosive leaders pull >50% off the high and still just be at the 50). Matters because the trail
  rule differs — a deep pullback bought at the 50 is the 50-EMA hold; a consolidation trails the 9. (AXTI, 2026-06-05)

<!--
Format for each lesson:
- **[short rule]** — what went wrong + how to avoid it. (from TICKER, YYYY-MM-DD)
Example:
- **Wait for the trigger** — bought before the breakout confirmed and got faked out. Only enter on
  a real break of the level. (from XYZ, 2026-05-12)
-->
- **Place the stop at the STRUCTURE you found at entry — never a hair under the entry price.** RKLB
  (Consolidation) was entered at $116.58 with the real structural stop at $110.30 (the base low), but the
  LIVE stop was set at $116.44 — 0.12% away — and it stopped out instantly at −0.02R on normal noise. A stop
  that tight isn't risk control, it's a coin-flip exit that throws the setup away before it can work. The
  initial_stop you identify at entry IS the stop; if 1× ADR of wiggle is too much to risk, the trade is too
  big — cut size, don't choke the stop. (from RKLB, 2026-06-05)
- **The chase leak is quantified: 12 of 21 closed trades filled >5% above plan and averaged ≈ −0.7R; that
  pattern is the bulk of the −12R book.** AAOI Consolidation was bought +25.4% and +16.5% above its planned
  zone (both −1R); MXL +20.7%/+16.1%; INTC +8.2%. The fix is split across both actors: the ENGINE now stays
  *armed* (won't freeze a buy) once price runs >½× ADR past the zone (2026-06-06), and ME — wait for the dip
  back to the zone or skip it. Re-entering the SAME name repeatedly after a failure (AAOI appears 3×) is the
  revenge/over-concentration tell — one position per name, and if a setup already failed today, demand a
  genuinely fresh base before going again. (from the 30-trade review, 2026-06-06)
