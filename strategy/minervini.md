# Mark Minervini — SEPA / Trend Template / VCP (playbook)

> Added 2026-06-01 from verified deep research. Minervini's edge for *us* is the **pre-trigger
> watchlist**: which stocks are even eligible ("worth watching"), and which are coiling toward a buy
> point. It's the **eligibility gate** that sits *in front of* the Qullamaggie/Luk trigger (breakout /
> EP / pullback you actually buy). Use it to build the "worth watching" universe, not to replace the
> entry tactics. *(Confidence tags below: ✅ = verified 3-0 across ≥3 sources; ⚠️ = community-standard
> but exact primary-source numbers unconfirmed — don't treat as gospel.)*

## SEPA — the 5 elements ✅
**S**pecific **E**ntry **P**oint **A**nalysis ranks a stock on five things, in order:
1. **Trend** — must already be in a confirmed **Stage-2 uptrend** (the Trend Template is the first gate).
2. **Fundamentals** — earnings/sales acceleration, margin expansion (⚠️ exact thresholds unconfirmed).
3. **Catalyst** — a reason institutions are buying (earnings, new product, contract, theme).
4. **Entry point** — a low-risk pivot off a tight base (VCP), not an extended chase.
5. **Exit point** — predefined stop + sell-into-strength rules.
> "You don't buy a stock hoping it goes up — you buy one that's *already* going up, at a low-risk point."

## The Trend Template ✅ (the most valuable, fully-encodable piece — and what we built)
A stock must pass ALL of these to be a Stage-2 candidate (this is `trend_template` in the scanner):
1. Price **>** the 50-, 150-, **and** 200-day SMAs
2. **50-day > 150-day > 200-day** (proper MA stacking)
3. **200-day SMA is rising** (≥ ~1 month / 22 trading days; ideally 4–5 months)
4. Price **≥ 30%** above its 52-week low
5. Price **within 25%** of its 52-week high
6. **RS Rating ≥ 70** (relative strength; ideally 80–90+) — we proxy this as `rs_pct ≥ 70`
   (percentile of blended 1m/3m return in the scanned universe).

**⛔ Do NOT use these (the research refuted them):** the 52-week-low distance is **30%, not 25%**;
there is **no** verified "40–50% above-average volume" breakout rule (don't hardcode a volume
multiple); only the **200-day** slope is a required criterion (not "both 150 and 200 rising").

## Stan Weinstein Stage Analysis ✅ (the backbone — Minervini buys ONLY Stage 2)
- **Stage 1 — Base** (neutral, after a downtrend): sideways, flat MAs. *Don't buy.*
- **Stage 2 — Advance** (the only buy zone): price above a **rising 30-week (≈150-day) MA**, higher
  highs & higher lows. **Stage 2A** (just after the Stage-1B breakout) is the ideal aggressive entry.
- **Stage 3 — Top**: choppy, MA flattens, distribution. *Take profits / don't initiate.*
- **Stage 4 — Decline**: below a falling 30-week MA. *Never long.*
> Note Weinstein anchors on the **150-day (30-week)** MA — distinct from our 50-EMA Consolidation flag.

## Volatility Contraction Pattern (VCP) ⚠️ (we built an APPROXIMATE detector)
A base where each successive pullback is **shallower than the last** ("2T", "3T"… contractions),
**volume dries up**, and price coils near the highs — the "line of least resistance." The breakout
through the final pivot is the buy.
- **⚠️ Exact thresholds are NOT confirmed in primary sources** (commonly cited: 2–6 contractions,
  often 3–4; depth roughly halving, e.g. 25% → 12% → 6%; volume drying into the pivot). Our `_vcp()`
  uses defensible, tunable approximations: **≥2 contractions, each ≤ 0.8× the prior, last ≤ 12% deep,
  price within ~8% of the base high, recent volume < the prior base's volume.** Treat the `vcp` flag
  as "VCP-like / worth a closer look," not a precise Minervini call. Tune in `scanner._vcp`.

## Entry mechanics
- Buy the **pivot** (the breakout level of the final tight contraction) on the move through it.
- **Don't chase**: extended past the pivot = worse R (⚠️ the often-cited "~5% above pivot max" is
  unconfirmed, but the principle matches our anti-chase `entry_quality` + buy-zone logic).

## Risk management ✅ (largely matches our existing rules)
- **Stop ~5% reference** (headline max ~7–8%), and demand a **minimum 2:1–3:1 reward:risk** — if a 5%
  stop isn't offset by bigger gains, change something. *(Ours: stop-at-structure ≤1× ADR — complementary.)*
- **Sell half at 2R and move the stop to break-even** ("sell into strength") — *we already do this*
  (the trim/raise-stop lesson + the position coach).
- **Progressive exposure**: start with small pilot positions; scale size/count up **only after** they
  work; pull back when stopped out. "Trade largest when trading best, smallest when worst."

## How we use it in the app
- **`trend_template` (boolean) + `tt_count` (n/8)** on every suggestion → a **"✓ Trend Template" filter
  + badge**. This is the "worth watching" eligibility gate.
- **`vcp` (boolean, approx) + `vcp_contractions`** → a **VCP filter + badge** (a coiling base).
- It composes with the existing engine: Trend Template = *is this a Stage-2 leader?*; the
  Qulla/Luk setup type = *how/when to enter*. A Trend-Template-passing **Consolidation/Deep-Pullback**
  is the premium "worth waiting" candidate.

## Open questions (source from his books before hardening)
- VCP exact contraction count / depth-tightening % / volume dry-up magnitude.
- Fundamentals thresholds (earnings & sales acceleration rates, "code 33"/triple-confirmation, margin
  expansion) — the SEPA Fundamentals & Catalyst elements are confirmed to exist but not quantified.
- His specific general-market timing filters (distribution-day counts, follow-through days) to pair
  with our SPX/QQQ/IWM regime module.

## Sources
ChartMill, Deepvue, TradingView (Minervini Trend Template scripts), ProRealCode, TraderLion
(risk-management), stageanalysis.net (Weinstein), FinerMarketPoints — all reproducing Minervini's
*Trade Like a Stock Market Wizard* (2013) & *Think and Trade Like a Champion* (2017) and Weinstein's
*Secrets for Profiting in Bull and Bear Markets* (1988). No primary page numbers verified; refuted
claims above were excluded.
