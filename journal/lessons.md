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

<!--
Format for each lesson:
- **[short rule]** — what went wrong + how to avoid it. (from TICKER, YYYY-MM-DD)
Example:
- **Wait for the trigger** — bought before the breakout confirmed and got faked out. Only enter on
  a real break of the level. (from XYZ, 2026-05-12)
-->
