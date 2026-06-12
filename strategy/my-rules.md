# My rules (source of truth — edit this freely)

This file overrides the generic playbooks. The agent reads it before every setup. Change anything
here to match how you actually trade.

## Account & risk
- **Account size:** _not set_ (set yours in the app)
- **Risk per trade:** **1%** of account.
- **Max single position:** 10% of account (tightened from 20% on 2026-06-01 — keep risk small while
  still learning). **Max overnight exposure:** ~30%. *(Some earlier positions were taken at ~15–20%
  before this change — grandfathered, not re-flagged.)*
- **Stop:** never wider than **1× ADR**, always at a real structural level.

## What I trade
- **Market:** US stocks only.
- **Setups I take (in order of preference):** **1) Pullbacks** to the 10/20-day EMA, **2) AVWAP
  reclaims from the ATH or last earnings gap**, then 3) breakouts, 4) episodic pivots. Parabolic
  shorts — **I trade longs only, never short.** See [pullbacks-avwap.md](pullbacks-avwap.md).
- **Filters:** ADR > 4%, price above 10 & 20-day MA, recent relative-strength leader, prefer the
  leading theme/sector of the moment.

## Entry / exit defaults
- **Entry:** buy-stop above the consolidation high (breakout) or opening-range high (EP).
- **Raise stop:** once +1R, move the stop to breakeven so the trade can't turn red.
- **Trim — ONLY when parabolic.** I do **not** trim on ordinary strength or hit-R targets. I trim a
  position **only when it goes parabolic (or close to it): price stretched VERY far above the EMAs —
  roughly ≥4× ADR above the 9 EMA, miles above the 21/50** (the ARM / DELL blow-off look). Then I trim
  into the spike and trail the rest. Otherwise I let winners run on the 9-EMA trail.
- **Earnings:** not an automatic trim — a binary event I decide to hold through or reduce case by case.
- **Exit (momentum):** I ride winners and **exit when price closes under the 9 EMA.** Charts show
  the **9 / 21 / 50 EMA**; the 9 EMA is my trailing line.
- **Exit OUTLIER — the deep-pullback LONG HOLD (50-EMA trail):** the ONLY exception to the 9-EMA exit.
  When I catch a **strong long-term leader (consistent uptrend ~6+ months) in a DEEP PULLBACK down to the
  50 EMA** (grabbed at/near the 50, like CIEN / LITE), it's a long play — I **hold until it closes under the
  50 EMA**, NOT the 9. It sits below the 9 by design, so a close under the 9 is normal and not an exit.
  **This applies ONLY to a Deep Pullback bought AT the 50 — NOT to a Consolidation.** A consolidation I buy
  up near the 9/21 (like MXL) is a normal momentum trade and trails the **9 EMA**; letting it ride to the
  50 (which can be 25–30% below price) would hand the whole move back. **The trail = the EMA I entered
  against:** bought at the 50 → trail the 50; bought anywhere else → trail the 9. (2026-06-05.)
- **Minimum R to first target:** 2.
- **DEFEND MODE (added 2026-06-05) — flatten momentum into the close when the tape is extended AND weak
  right now.** When the market is **both** stretched/frothy (≥2 of SPX/QQQ/IWM above the 50-MA in ADR
  terms, or "frothy/late-stage", or Fear&Greed ≥ 72) **AND showing weakness on the current session**
  (≥2 indexes red — premarket/after-hours or intraday), I don't hold momentum trades overnight. The
  classic trigger: a good up day, then a red premarket — an extended, fearful tape round-trips the gains.
  So I **sell momentum positions into the close** and go to cash overnight; I re-enter fresh the next day
  if the setup is still there. **Exemption:** ONLY the Deep-Pullback 50-EMA long holds (a leader bought
  deep at the 50) are NOT flattened — a Consolidation bought near the 9/21 trails the 9 and IS flattened.
  **New entries are unaffected** — I still trade daily;
  the rule is purely "don't carry momentum overnight." The app **alerts** me to flatten (it never sells
  for me), firing in the last ~30 min (after 15:30 ET); a normal extended-but-GREEN tape does NOT arm it.
- **OVER-STRETCH SHIELD (added 2026-06-09) — an INDEPENDENT defend arm.** Separate from the "extended AND
  weak" path above: when **≥2 of SPX/QQQ/IWM are rubber-banded above their 50-MA** past a per-index line
  (`atr_mult_50` ≥ **SPX 7 / QQQ 7 / IWM 5**, engine ADR units, calibrated on 4yr of bars — the rarest ~6%
  of days, where real tops cluster), shield arms **on its own — green tape is not a pass** (a froth top
  reverts the same way a correction bleeds). Same action: flatten momentum into the close, Deep-Pullback
  50-EMA holds exempt. NB: these lines are in the engine's H/L-ADR units, which run ~20% ABOVE TradingView's
  true-ATR readout (so a TV "8" ≈ engine ~9.6). Distinct from the flat 4.5 `stretched_50` chase flag, which
  only nudges posture — this never touches a grade.

## Hard "don'ts" (seed list — `/review-trades` will add to this)
- Don't chase: no entries more than ~1× ADR above the trigger.
- Don't widen a stop. Don't average down. Don't enter before the breakout actually triggers.
- Don't size up past 1% risk because a setup "looks great."

## Position sizing — the math (1% risk)
```
risk_per_share = entry − stop                 (for longs)
shares         = (account_size × 0.01) / risk_per_share
dollar_risk    = shares × risk_per_share      (should ≈ 1% of account)
R              = (target − entry) / risk_per_share
```
- Use the account size above for real share counts. Sanity-check: if `risk_per_share > 1× ADR`, the
  stop is too wide — the setup is too extended, say so.
- Also cap the position at ≤10% of account (15% hard ceiling) regardless of what 1% risk allows.

## How to present a setup (output format — use every time)
Rank ideas best-first. "No clean setups" is a valid answer. For each idea give exactly:

> **TICKER** — *setup type* (breakout / episodic pivot / pullback / deep pullback / consolidation)
> - **Entry:** price + trigger. Two options when both make sense (don't force it):
>   **Breakout** = *buy-stop above $X* → phrase "break above $X" (X above current price);
>   **Pullback** = *buy-limit at support below* → "wait for the pullback to $Y" (Y below price — the
>   breakout level / reclaimed swing high / 9–20–50 EMA / AVWAP). Never say "pull back toward" a price
>   that's above current price. Mark **Episodic Pivot only for a true open GAP (~8%+) on fresh news**;
>   a multi-day run into a base high is a **Breakout**.
> - **Stop:** price + why ("$Y, low of breakout candle; risk $Z/sh = 0.8× ADR ✅")
> - **Size:** N shares (1% = $… risk) — or % of account if size unknown
> - **First target / trim:** ~$… (≈ R), then trail per the 9/50 rule above
> - **Why it fits:** one line (relative strength, tight base, catalyst…)
> - **Catalyst/hype:** from `/check-hype`, if any
> - **Data source:** e.g. "TradingView screener, read 2026-05-30 14:05 ET"

## Notes to the agent
- I'll confirm or correct these rules. Treat this file as authoritative when it conflicts with the
  generic Qullamaggie/Martin-Luk docs.
