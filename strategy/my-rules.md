# My rules (source of truth â€” edit this freely)

This file overrides the generic playbooks. The agent reads it before every setup. Change anything
here to match how you actually trade.

## Account & risk
- **Account size:** _not set_ (set yours in the app)
- **Risk per trade:** **1%** of account.
- **Max single position:** 10% of account (tightened from 20% on 2026-06-01 â€” keep risk small while
  still learning). **Max overnight exposure:** ~30%. *(Some earlier positions were taken at ~15â€“20%
  before this change â€” grandfathered, not re-flagged.)*
- **Stop:** never wider than **1Ã— ADR**, always at a real structural level.

## What I trade
- **Market:** US stocks only.
- **Setups I take (in order of preference):** **1) Pullbacks** to the 10/20-day EMA, **2) AVWAP
  reclaims from the ATH or last earnings gap**, then 3) breakouts, 4) episodic pivots. Parabolic
  shorts â€” **I trade longs only, never short.** See [pullbacks-avwap.md](pullbacks-avwap.md).
- **Filters:** ADR > 4%, price above 10 & 20-day MA, recent relative-strength leader, prefer the
  leading theme/sector of the moment.

## Entry / exit defaults
- **Entry:** buy-stop above the consolidation high (breakout) or opening-range high (EP).
- **First action:** trim 1/3â€“1/2 at ~2â€“3R or after 3â€“5 strong days; move stop to breakeven.
- **Exit (momentum):** I ride winners and **exit when price closes under the 9 EMA.** Charts show
  the **9 / 21 / 50 EMA**; the 9 EMA is my trailing line.
- **Minimum R to first target:** 2.

## Hard "don'ts" (seed list â€” `/review-trades` will add to this)
- Don't chase: no entries more than ~1Ã— ADR above the trigger.
- Don't widen a stop. Don't average down. Don't enter before the breakout actually triggers.
- Don't size up past 1% risk because a setup "looks great."

## Notes to the agent
- I'll confirm or correct these rules. Treat this file as authoritative when it conflicts with the
  generic Qullamaggie/Martin-Luk docs.
