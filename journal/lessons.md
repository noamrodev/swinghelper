# Lessons (my trading memory)

The agent reads this before proposing setups and checks every candidate against it. It grows from
my real trades via `/log-trade` and `/review-trades`. Keep it tight — only durable, actionable
lessons, newest insights merged in (not an endless list of duplicates).

## Active lessons
- **Nothing is confirmed when the market is going down — Tape Guard (2026-06-09, AXTI/INTC/TER + AMKR).**
  Every buy taken into a *rejecting* tape that session failed identically. The rule: when ≥2 of SPX/QQQ/IWM
  were UP / made an intraday high and have **rolled over** (red on the session AND faded well off their high —
  a rejection, not a quiet slow-red drift), STAND DOWN. New buys become watch-only (no chase), and EVERY open
  position moves its stop to **break-even** — don't give an open gain back into a falling tape. Intraday +
  alert-only (the app never moves a stop or sells); distinct from defend mode (which flattens momentum into the
  close). Implemented as the **Tape Guard** (`app.tape_guard_state`, thresholds in `rubric.TAPE_GUARD_*`).
- **The market spins back up just like our stocks — Tape Turn (2026-06-09, QQQ ~$282.8 reclaim).** The inverse
  of Tape Guard, the all-clear. When ≥2 of SPX/QQQ/IWM have **flushed** (made an intraday low off the high) and
  **spun back up** — bounced off that low, **reclaimed the 5-min 9/21 EMAs**, and are making a **higher low** —
  the rejection is over. Two phases: **forming** (spun but the reclaim hasn't held ~15 min → still watch-only,
  Guard wins) and **confirmed** (held ≥3 completed 5-min bars with a higher low → **lifts the Guard stand-down**
  so setups can confirm again, even if still red on the session). Standalone (arms without a prior Guard);
  intraday + alert-only; **break-even stops STAY** (Turn re-enables buys, never un-raises a stop). Implemented as
  the **Tape Turn** (`app.tape_turn_state` / `app._index_spin`, thresholds in `rubric.TAPE_TURN_*`). Stateless
  per poll off COMPLETED bars only, so a whippy up-down-up day re-arms Guard / resets Turn cleanly each poll.
- **AVWAP reclaim into a near-overhead EMA = clear the EMA, never reclaim under it (2026-06-09, IREN).**
  If an AVWAP-family setup's AVWAP support sits within ~0.5× ADR *under* the daily 9/21 EMA, buying the AVWAP
  reclaim means buying straight into the EMA wall a few % overhead (IREN: reclaim ~$59.1 with the 9-EMA at
  $60.64). The buy now waits for a **completed 5-min close above the EMA** (no AVWAP retest — the EMA close IS
  the trigger), stop still just under the AVWAP, risk ≤1× ADR. A far-overhead EMA (deep-pullback shape) keeps
  the normal reclaim+spin; the 50-reclaim deep-pullback path is unchanged. Needs a server restart + rescan to
  see it live (engine: `app.compute_now`, trigger tag `CLEAR_9EMA`).
- **AVWAP plain-reclaim: confirm NEAR the AVWAP and not under a wall (2026-06-10, INOD + ONTO).** Two ways the
  *fallback* reclaim path (Path B — fires when the overhead EMA sits too far above the support for the IREN gate)
  still chased: (1) **too far above the AVWAP** — ONTO reclaim fired $289.75 vs AVWAP $274.95 (~1× ADR above, top
  of the candle). The near-AVWAP entry cap is now **0.5× ADR** (was 1×); farther → stay ARMED for a closer retest.
  (2) **a wall right overhead** — INOD AVWAP $96.42 with the 9-EMA $103.28 only ~2.6% above the $100.67 reclaim =
  buying into the EMA. Now Path B clears overhead walls too (9/21 EMA / prior-day high / descending line within
  0.6× ADR above the *entry*): in-band → require a 5-min close above the HIGHEST wall, else ARM. And if clearing
  the wall would push the stop (under the AVWAP) past 1× ADR, there's no clean entry → stay armed (don't chase the
  ran-away bounce). Confirmation-only — grades unchanged. Restart + rescan to see it live (engine: `app.compute_now`).
- **Under a DESCENDING trendline = WAIT for the break, never buy beneath it (2026-06-09, DOCN; relaxed v2).**
  The sloped sibling of the IREN overhead-EMA + AXTI horizontal-wall "clear the wall" gates. When a genuine
  descending resistance line (lower highs off a recent peak) sits just overhead the entry, a confirm fired
  under it buys straight into the line. Rule: if a respected, descending line sits within `WALL_NEAR_ADR`
  (0.6× ADR) above the entry, the buy ARMS (`CLEAR_TREND_ARMED`) and fires only on a completed 5-min CLOSE
  above the line (`CLEAR_TREND`). Drawn on the chart as a dashed-red "resistance — wait for the break" line.
  A far-overhead line does NOT gate; a clear path is unchanged. Engine: `app.compute_now` (50-reclaim +
  breakout/pullback confirms), `app._descending_trend_gate`; detector `scanner._descending_resistance` →
  `res_trendline`. Needs a server restart + rescan to see it live.
  **The gate is CONFIRMATION-ONLY — it never lowers the grade or hides the setup (firewalled; I still SEE the
  setup).** Because of that, the detector is deliberately tuned to ALSO catch YOUNGER lines: a recent PEAK +
  ≥2 lower highs over a short (≥3-bar) leg — like the literal DOCN line off the ~$184 June-4 peak (lower highs
  ~$180.5, $174.7 → today's line ~$172.75). v1 returned None for that (it demanded a strict ≥3-touch / ≥15-bar
  multi-week channel); v2 relaxes `TREND_MIN_TOUCHES`→2, `TREND_MIN_LEG`→3, `TREND_MIN_DROP_ADR`→0.5 and adds a
  lower-highs-staircase noise guard (`TREND_MIN_LOWER_HIGHS`=2). A young line firing a brief WAIT is **low-harm**
  (worst case a false wait — never a loss, never a grade hit), so the trade-off favors seeing it. **The real
  noise guard is PROXIMITY** (the gate only fires when the line is within 0.6× ADR above the entry), not the
  line's age — of 924 US names 58% carry a drawn line but only 6.6% actually gate, and only ever to ADD a wait.
  The strict multi-week channel (MRAM $51.5→$25.1) still qualifies too.
- **Anchor the descending line at the START of the CURRENT down-leg, not the stale global max (2026-06-10, BE,
  3rd refinement).** The detector defaulted its anchor to the GLOBAL MAX of the window and, when the current
  down-leg's line was too short (< `TREND_MIN_LEG`=3 bars), fell back to a stale line off that old top. On BE
  it drew off the May-22 $322.83 all-time high → today $293.30, when the trader's real resistance is the
  **June-2 $305.11 line → today $286.27** (4 touches, slope −4.71) — the start of the current unbroken
  lower-highs sequence. Three composing fixes: (1) add every fresh **current-down-leg dominant peak** (a
  right-pivot local high strictly below the running max) as a candidate anchor; (2) admit a **short fresh leg
  down to 2 bars** when it still carries the full staircase (touches + lower-highs + proximity — leg length
  was never the real noise guard; a 1-bar/bare-2-point line is still rejected); (3) **anchor-recency explicit**
  in selection (most-recent anchor wins within the proximate-ceiling eps band). Plus `TREND_ENDLOWER_CAP_PCT`
  (3% of price): the "end-anchor below the peak" margin is `min(0.5×ADR, 3%·price)` so an **explosive name**
  (high ADR) doesn't over-penalize a real lower-high step — **absolute cap over pure ADR-scaling** (the AXTI/
  TSEM/BE lesson). Confirmation-only, grades byte-for-byte (only `res_trendline` moved; TSLA in the golden set
  shifted to a nearer, more-recent down-leg line — same intended behavior). Needs a server restart + rescan to
  see it live. Steeper June2→June5/June8 lines are correctly rejected (the real June-4 high pokes above).
- **Stacked overhead walls = clear the HIGHEST, not the nearest (2026-06-10, DOCN AVWAP gap).** The AVWAP
  `CLEAR_9EMA` gate only knew about the 9/21 EMA — so DOCN's confirm fired at the 9-EMA ($168) while a
  descending line ($172.75) AND yesterday's high ($174.74) sat above it, still within the band. The buy landed
  UNDER resistance. Rule: when more than one overhead wall (the 9/21-EMA wall, a horizontal prior-day/recent
  high, and/or the descending trendline) sits within `WALL_NEAR_ADR` (0.6× ADR) of where the buy lands, require
  a completed 5-min CLOSE above the **HIGHEST** of them — you're not "above resistance" until all of it is
  cleared. A wall FARTHER than the band does NOT gate (no chasing a far line). Unified into `app._highest_overhead_wall`
  and wired into the AVWAP `CLEAR_9EMA` branch (was missing the trendline/wall) AND the 50-reclaim branch (was
  "nearest wall"). For AVWAP setups the support label now says "AVWAP", not "50 EMA". After-hours ARMED cards
  surface the line requirement too. **Honest note:** with DOCN's real 8.2% ADR the highest in-band wall is
  $174.74 (yesterday's high), ABOVE the $172.75 trendline — the gate correctly lands there. Confirmation-only,
  firewalled from grades (test_grader 7/7; all tests green). Needs a server restart + rescan to see it live.
- **A "Deep Pullback" must be a real LEADER, not just a big-gainer that fell apart.** Absolute gain
  (p6m/p3m ≥30) is NOT leadership — choppy laggards (NXT RS59, MRNA RS9, SATS RS41) also "ran 30%" then
  knifed below their EMAs, and the old gate handed them the deep-pullback +4 and the patient A-grade path.
  Rule (engine, 2026-06-09): a Deep Pullback ALSO needs **rs_pct ≥ 70 OR trend_template**. The edge is a
  *strong stock at SUPPORT*, never a weak stock that merely had a number. Also: setup `score` now carries an
  explicit RS+TT credit (was zero before) so a clean RS-97/TT leader (CIFR) outranks a deep-pullback
  non-leader — quality was literally inverted before (NXT 15.6 > CIFR 10.9; now CIFR B, NXT D). Needs a
  server restart + rescan to take effect live. (2026-06-09 leader/score calibration fix)
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
- **TP-ing a winner in a red/choppy tape is defensible DEFENSE — but make it a deliberate regime call,
  not a reflex (2026-06-10, DOCN/BE).** In a red light (posture 44, chop), banked DOCN at **+0.5R / +$35**
  (under its $175.71 target) and scratched **BE flat at break-even** rather than risk giving green back —
  consistent with protecting green in a hostile tape (cf. FLNC; Tape Guard → break-even stops). The
  watch-out: clipping winners below +1R is exactly the **win-rate-over-R leak** the backtest flagged when
  done by reflex in a NORMAL tape. Rule: in a confirmed hostile/red tape, a defensive TP/scratch is fine;
  in a healthy tape, let the 9-EMA trail decide the exit — don't cut the winner that pays for the book.
  (from DOCN/BE, 2026-06-10)
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
- **A discretionary macro de-risk can be the RIGHT call — but keep the leader, cut the tails, and never
  trust a "green light" that contradicts a same-morning risk-off alert.** 2026-06-08: after Friday's QQQ
  −5% + Iran/Lebanon war headlines + a posture-45 pullback tape, flattened the whole 7-name book (6 Deep
  Pullback + 1 Consolidation) intraday on a risk-off read. The app's defend was OFF and the gameplan flashed
  "VIX 19.7 calm" — but that was a **BUG**: `vix_trend` labeled an elevated post-spike VIX ('calm' despite
  +23% 5d, +14.8% vs MA20, after a +40% Friday spike to 21.51) because the level 19.74 sat under its hard 20
  cutoff. So the caution was MORE justified than the app showed — a sound discretionary read, **not a panic**.
  Result: net **+$19.76 / ~+0.7R**, carried entirely by **LITE (+1.49R)**; the other six were ~scratch on tight
  stops. REFINEMENTS: (1) when de-risking a correlated book, **keep the clear winner** (LITE was trending) and
  cut the redundant/correlated tails — don't flatten the leader with the laggards. (2) The book was one
  correlated bet ×7 at **37% > the 30% overnight cap** — that over-concentration is what made it a rollercoaster;
  size to the cap so a choppy tape doesn't whip the whole book. (3) When two app surfaces disagree (risk-off
  alert vs green-light gameplan), trust neither blindly — surface the bug. (from the 2026-06-08 flatten)
- **A Deep Pullback only counts if the 50 EMA is RISING and the pull was ORDERLY — a leader that
  COLLAPSED into a rolling-over 50 is a falling knife, not a pullback.** LUNR (2026-06-09): a real
  +165%/6mo leader (passed the strong-leader + RS gate) then crashed **~57% from the $46 peak in a
  near-vertical waterfall**, slicing through a now-declining 9/21/50, lower highs, descending trendline.
  The engine still flagged `buyable_now=True` at the 50 EMA ($29.89) because `near_50` + `strong_leader`
  were true and the **pull-% cap was deliberately removed** for the AXTI case (see the AXTI lesson above) —
  so a 57% crash *to* the 50 looks identical to a 50% controlled pull *that holds* the 50. The confirmation
  fired the buy and it stopped instantly (−1R, −$37). Two faults: (1) ENGINE — proximity-to-the-50 is not
  enough. The 50 EMA **still slopes UP here** (it lags — +1.7 over 8 days while price crashed through it),
  so a "rising-50" gate would miss this. The real disqualifier is **confirmed downtrend structure: ≥3
  successive lower highs (43.4→41.1→38.8→34.7→32.7→30.8) with price under DECLINING 9/21 EMAs** — a knife
  slicing the 50 on lower highs, not a bounce holding it. Deep Pullback should require a **respected bounce
  AT the 50** (green reclaim bar), which the deep-pullback path skips today because it uses a day-high
  trigger, not a limit (so the `_respected_bounce` knife-catch gate never runs). (2) ME — stop $29.74 was
  only **2.4% vs the 14.4% ADR** = noise-tight, a guaranteed shakeout even if the setup were valid; on a
  14-ADR name give it real room or cut size. (from LUNR, 2026-06-09)
- **Never confirm a breakout UNDER a wall — clear the prior-day high / horizontal resistance, don't buy
  the reclaim beneath it.** AXTI (2026-06-09): the live confirmation (50-reclaim + spin) fired while the
  prior-day high **$96.56** sat just overhead; price poked $96.67, failed the level, and reversed to $91.82
  — a textbook false breakout, bought into resistance. This is the SAME family as IREN (fired under an
  overhead 9/21 EMA) and CRDO/AAOI (fired far above the zone), but the IREN `CLEAR_9EMA` gate is **AVWAP-only**
  and never extended to a horizontal prior-day high / swing-high resistance. FIX (engine, quant): when a known
  resistance (prior-day high, recent swing high, round level) sits within ~½–1× ADR above the live trigger,
  the confirmation must require a completed close **above** that level — not a reclaim/spin underneath it.
  (from AXTI, 2026-06-09)
- **Don't take ANY buy confirmation while the market is being REJECTED / rolling over intraday — nothing
  is confirmed in a falling tape.** 2026-06-09 (the tilt session): the indices rallied ~+1.5%, hit the
  9-EMA, got rejected and rolled over — and every individual buy taken into that tape failed identically:
  **AXTI −1R** (a re-entry of a knife we diagnosed that same morning — revenge), **INTC −1R** (low-ADR
  mega-cap, off-strategy), **TER −1R** (entered while QQQ/IWM/VOO were all red), and **AMKR** got a BUY
  confirm (a 58–79 two-month CHOP, −4% on the month, fired into the down tape and ~3.5% above its own
  planned $71.96 entry after a +7% spike). A single-stock breakout/AVWAP-reclaim has **no edge when the
  index it lives in is reversing down** — the market drags it back. RULES: (1) Before any entry, glance at
  SPX/QQQ/IWM — if ≥2 are red and rolling over off a rejection, **stand down**; cash is the position. (2)
  **Never re-enter a name that just stopped you** (AXTI twice). (3) Chop and low-ADR mega-caps (INTC/TER/
  AMKR) are not this strategy — trade clean RS leaders only. The ONE good call all day: **raising the FLNC
  stop to bank +$71.40** — protecting green in a hostile tape is correct, never apologize for it. Sizing
  was disciplined (each loss ≤0.54% → net −$108 ≈ −0.5% of the account); the leak was ENTERING, not size.
  ENGINE FIX (proposed): the live confirmation must gate on the **intraday index tape** — suppress / drop
  to watch-only any new BUY when ≥2 indices are red and rolling over off a session rejection (distinct from
  the EOD defend mode). (from the 2026-06-09 tilt session: AXTI/INTC/TER/AMKR/FLNC)
- **FIXED (engine, 2026-06-09 — three pullback/confirmation fixes, restart+rescan to go live, Burry to verify).**
  (1) **NBIS — "Pullback to 21-EMA" classification added.** The SMA-based `uptrend` gate (`above≥2` off the
  10/20/50 *SMA*) dropped a clean dip to the rising **21-EMA** (NBIS@218 read above=1 → fell through to a
  prior-high Breakout, entry $264 = a chase). New middle-ground setup between shallow Pullback and Deep
  Pullback: a rising EMA fan (9>21>50, all rising), price ≥1× ADR above a rising 50, holding within ~1× ADR of
  the 9/21, higher lows intact. Entry anchors to the 9/21 line (NBIS$218→entry$217.74, eq 90), trails the 9
  (NOT a 50-EMA long-hold). Branch placed AFTER deep/consol/pullback/avwap in the elif chain → only catches
  what was Breakout/EP; 9 of 1148 cache names reclassified, ALL Breakout→Pullback-to-21 (ADI/COHR/MX/SIMO/…),
  ZERO existing pullback names touched. (2) **LUNR knife-catch.** The deep-pullback 50-reclaim (entry_type
  "stop") skipped the `_respected_bounce` gate, so a leader waterfalling INTO the 50 fired buyable. NEW: a
  deep pullback in a CONFIRMED DOWNTREND (≥3 successive lower daily highs AND price below DECLINING 9 *and* 21
  EMAs) stays `worth_waiting` but is NOT `buyable_now` until it RESPECTS the 50 (the same bounce proxy). No
  pull-% cap (keeps AXTI-style legit deep pulls), no 50-slope gate (it lags). LUNR/AXTI/CNQ/OXY/PLUG/WFRD
  flipped to not-buyable (knives); RIO stayed buyable (it bounced the 50), LUMN/NXT/RNG stayed (21 not
  declining = holding). (3) **AXTI wall gate.** Generalized the AVWAP-only `CLEAR_9EMA` to HORIZONTAL
  resistance: when a prior-day high / recent high sits within `WALL_NEAR_ADR` (0.6× ADR) above the 50-reclaim
  entry, the confirmation arms (`CLEAR_WALL_ARMED`) and fires only on a completed 5-min CLOSE above the wall
  (tag `CLEAR_WALL`), never the reclaim beneath it. No overhead wall → normal RECLAIM_50 unchanged. New rubric
  constants: `PB21_MIN_EXT50_ADR/PB21_NEAR_ADR/PB21_PREF`, `KNIFE_LOWER_HIGHS`, `WALL_NEAR_ADR`. Grader 7/7
  byte-for-byte (goldens intact — none are deep/pb21).
