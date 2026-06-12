// Trading Data Center — front-end logic (Alpine component)

// Each browser gets a stable, private workspace id. Sent as X-Workspace on every
// request so the server routes to this user's own data (hosted mode). Harmless locally.
function workspaceId() {
  let id = localStorage.getItem('dc_workspace');
  if (!id) {
    id = (window.crypto && crypto.randomUUID)
      ? crypto.randomUUID()
      : 'ws-' + Math.random().toString(36).slice(2) + Date.now().toString(36);
    localStorage.setItem('dc_workspace', id);
  }
  return id;
}

// Which market the user is viewing (US default, or IL = Tel Aviv). Sent as X-Market on
// every request so the server routes to that market's separate data (suggestions, account,
// positions, forward test). Persisted per browser.
function currentMarket() {
  return localStorage.getItem('dc_market') || 'us';
}

function dataCenter() {
  return {
    nav: [
      { id: 'hq', label: 'HQ', icon: '🧠' },
      { id: 'competition', label: 'Competition', icon: '🏆' },
      { id: 'dashboard', label: 'Dashboard', icon: '🏠' },
      { id: 'autopilot', label: 'Auto Pilot', icon: '🛩️' },
      { id: 'suggestions', label: 'Suggestions', icon: '🎯' },
      { id: 'screeners', label: 'Screeners', icon: '🗂️' },
      { id: 'watchlist', label: 'Watchlist', icon: '👁️' },
      { id: 'journal', label: 'Journal', icon: '📓' },
      { id: 'news', label: 'News', icon: '📰' },
      { id: 'learninghub', label: 'Learning Hub', icon: '🧠' },
      { id: 'strategy', label: 'Strategy & Rules', icon: '📚' },
    ],
    view: 'dashboard',           // the website is for browsing; the tray "live coach" makes the calls
    chartOnly: false,            // ?chart= deep-link (autopilot/coach "View chart"): show ONLY the chart, no dashboard
    watchOpen: false,            // 📋 live-market watchlist drawer (right edge) — all names by sector
    watchCollapsed: {},          // per-sector collapse state in the watchlist drawer
    watchSort: 'sector',         // sector | gainers | losers | grade — how the watchlist is organized
    market: currentMarket(),     // 'us' (default) or 'il' (Tel Aviv) — switches all data
    backupMsg: '',
    mobileNav: false,
    hosted: false,
    // pages hidden on the hosted free service for friends: journal/watchlist can't save (no storage), and
    // stats/strategy are the owner's tools — friends only need Auto Pilot + browsing. (Auto Pilot is the headline.)
    hostedHidden: ['hq', 'competition', 'journal', 'watchlist', 'learninghub', 'strategy'],   // HQ + Competition = owner's local command center
    hq: { agents: [], squads: [], updated: '', loaded: false },
    competition: { bots: [], loaded: false },   // 🏆 the 10 strategy bots paper-trading the live universe
    compSelected: null,          // expanded bot id
    compDetail: null,            // selected bot's full detail
    compTab: 'overview',         // overview | trades | calendar | memory
    compDay: null,               // the open daily report
    compDayDate: null,
    armedHistory: {},            // 📡 per-day log of every setup the engine armed/confirmed live (local learning data)
    learning: null,              // 🧠 Learning Hub — /api/learning response
    lhTab: 'brief',              // 'brief' | 'edge'
    health: { ok: false, healthy: false, subs: [] },   // ❤️ vital signs — scan/Yahoo/Telegram heartbeat
    settings: { account_size: null, risk_pct: 1 },
    screeners: [],
    suggestions: { items: [] },
    trades: [],
    watchlist: [],
    stats: {},
    docs: { lessons: '' },
    scan: { running: false, done: 0, total: 0 },
    scanScreener: '',
    // Hosted-only stale-data gate. States: null (local/not-yet-loaded) | 'waiting' (scan in progress)
    // | 'live' (fresh data rendered). Never falls back to stale items.
    hostedScan: { state: null, done: 0, total: 0, current: '', scannedAt: null, screener_id: null, _timer: null, _stalled: false },
    filters: ['all', 'pending', 'approved'],
    filter: 'all',
    marketRegime: { posture: null, label: '', indexes: [] },
    universe: { kept: null, passed_filter: null, universe_total: null, built_at: null, status: { running: false, stage: '', done: 0, total: 0 } },
    news: { computed_at: null, sections: [], ticker_news: {} },
    newsStatus: { running: false, done: 0, total: 0, current: '' },
    newsTab: 'news',
    suspicious: { buying: [], selling: [], scanned: null, scanned_at: null, status: { running: false, done: 0, total: 0, current: '' } },
    premarket: { movers: [], scanned: null, scanned_at: null, status: { running: false, done: 0, total: 0, current: '' } },
    spinning: { spins: [], scanned: null, candidates: null, scanned_at: null, status: { running: false, done: 0, total: 0, current: '' } },
    spinLeadersOnly: false,
    spinRisingOnly: false,
    refreshState: { running: false, stage: '', done: 0, total: 4 },
    gameplan: null,
    gameplanOpen: true,
    now: null,                // /api/now — confirmation engine output (confirmed buys + armed setups)
    // Auto Pilot is a STANDALONE page (web/autopilot.html @ /autopilot.html) — the nav item + the hosted
    // landing redirect there, so its 5-min auto-refresh stays put (it is NOT an SPA view).
    tgMsg: '',                // telegram test-send status
    expandedPlan: {},         // per-ticker toggle for the 📋 setup plan in Live entries
    nowByTicker() {           // ticker -> {state:'confirmed'|'early'|'armed', rec} for badging suggestion cards
      const m = {};
      (this.now && this.now.buys || []).forEach(b => m[b.ticker] = { state: b.early ? 'early' : 'confirmed', rec: b });
      (this.now && this.now.armed || []).forEach(a => { if (!m[a.ticker]) m[a.ticker] = { state: 'armed', rec: a }; });
      return m;
    },
    expandedPos: {},          // per-position "show more" toggle in the gameplan (keyed by trade id)
    forward: null,
    openForwardDay: null,
    fwdMonth: null,             // "YYYY-MM" shown in the forward calendar
    fwdDayReport: null,         // the loaded per-day report (from /forward/day)
    pnlCal: {},
    // coach thresholds — fetched from /coach-config (single-sourced in rubric.py). Defaults match
    // the backend so the live coach still works if the fetch fails.
    coachCfg: { parabolic_adr: 4.0, raise_r: 1.0, earn_soon_days: 7, guard_min_lock: 40, guard_buffer_adr: 1.5, guard_step_dollars: 25 },
    pnlMonthOffset: 0,
    live: { prices: {}, market_state: null, updated_at: null, posture: null },
    liveOn: true,
    liveAgeSec: 0,
    _liveAt: 0,
    autoScan: true,
    _lastAutoScan: 0,
    prediction: null,
    predictionLoading: false,
    newScreener: { name: '', tickers: '' },
    newTrade: { ticker: '', setup_type: 'Breakout', entry: null, stop: null, shares: null, notes: '' },
    docTabs: [
      { id: 'my-rules', label: 'My Rules' },
      { id: 'pullback-avwap', label: 'Pullbacks & AVWAP' },
      { id: 'qullamaggie', label: 'Qullamaggie' },
      { id: 'martin-luk', label: 'Martin Luk' },
      { id: 'minervini', label: 'Minervini' },
      { id: 'lessons', label: 'Lessons' },
    ],
    docTab: 'my-rules',
    docEdit: '',
    savedMsg: '',
    scalePct: 125,
    calc: { entry: null, stop: null },
    newWatch: '',
    wlData: {},
    wlLoading: false,
    _wlCharts: {},
    _wlObs: [],
    screenTab: 'lists',
    sectorHeat: { computed_at: null, sectors: [] },
    sectorStatus: { running: false, done: 0, total: 0, current: '' },
    groups: { computed_at: null, groups: [], status: { running: false, done: 0, total: 0, current: '' } },
    sectorSort: 'score',
    secOpen: {},
    sectorSearch: '',
    themesMap: {},
    _stockHits: [],
    suspSort: 'vol_mult',
    setupFilter: 'All',
    momFilter: 'All',
    sectorFilter: 'All',
    waitFilter: false,
    leaderFilter: false,
    risingFilter: false,
    ttFilter: false,
    vcpFilter: false,
    newsFilter: false,
    buyableFilter: false,
    showFilters: false,
    showAllSug: false,
    showPassed: false,        // reveal passed (✗-rejected) suggestions so a pass can be undone
    calcModal: { open: false, ticker: '' },
    chartModal: { open: false, ticker: '', _chart: null, logScale: true, showChannel: true, showEmas: true, showAvwap: true, showVolume: true, _data: null, _obj: null, entryIdx: 0, _patternLabel: null },
    tradeModal: { open: false, mode: 'take', ticker: '' },
    _pollTimer: null,

    // ---------- display scale ----------
    applyScale() {
      // cap the zoom on phones so the 125% desktop default doesn't crush small screens
      const eff = window.innerWidth < 760 ? Math.min(this.scalePct, 100) : this.scalePct;
      document.documentElement.style.fontSize = (eff / 100 * 16) + 'px';
    },
    incScale(d) {
      this.scalePct = Math.min(180, Math.max(85, this.scalePct + d));
      localStorage.setItem('dc_scale', this.scalePct);
      this.applyScale();
    },

    // ---------- position calculator (1% risk, capped at buying power) ----------
    get calcBudget() { return (this.settings.account_size || 0) * (this.settings.risk_pct || 1) / 100; },
    get _calcRiskShares() {
      const e = this.calc.entry, s = this.calc.stop;
      if (!e || !s || s >= e) return null;
      return Math.floor(this.calcBudget / (e - s));
    },
    get calcShares() {
      const rs = this._calcRiskShares; if (rs == null) return null;
      const e = this.calc.entry, acct = this.settings.account_size || 0;
      const maxpos = this.settings.max_position_pct || 15;
      const maxposShares = e > 0 ? Math.floor(acct * maxpos / 100 / e) : rs;
      const afford = e > 0 ? Math.floor(acct / e) : rs;
      return Math.max(0, Math.min(rs, maxposShares, afford));
    },
    get calcCapped() { const rs = this._calcRiskShares; return rs != null && this.calcShares != null && this.calcShares < rs; },
    get calcCapReason() {
      const rs = this._calcRiskShares; if (rs == null || this.calcShares >= rs) return null;
      const e = this.calc.entry, acct = this.settings.account_size || 0, maxpos = this.settings.max_position_pct || 15;
      const maxposShares = Math.floor(acct * maxpos / 100 / e);
      return this.calcShares === maxposShares ? ('max position size (' + maxpos + '%)') : 'buying power';
    },
    get calcCost() { return this.calcShares ? Math.round(this.calcShares * this.calc.entry).toLocaleString() : ''; },
    get calcRisk() { return this.calcShares ? Math.round(this.calcShares * (this.calc.entry - this.calc.stop)).toLocaleString() : ''; },
    get calcRiskPct() { const a = this.settings.account_size; return (this.calcShares && a) ? (this.calcShares * (this.calc.entry - this.calc.stop) / a * 100).toFixed(2) : ''; },
    get calcPct() { const a = this.settings.account_size; return (this.calcShares && a) ? (this.calcShares * this.calc.entry / a * 100).toFixed(1) : ''; },

    // ---------- lifecycle ----------
    onResize() {
      this.applyScale();               // re-apply the phone zoom cap on rotate/resize
      const box = document.getElementById('chartBox');
      if (this.chartModal._chart && box) { try { this.chartModal._chart.resize(box.clientWidth, box.clientHeight); } catch (e) {} }
      for (const k in this._wlCharts) {
        const b = document.getElementById('wlc-' + k);
        if (b && this._wlCharts[k]) { try { this._wlCharts[k].resize(b.clientWidth, b.clientHeight); } catch (e) {} }
      }
    },
    async init() {
      window.__dc = this;   // global handle so x-html-rendered controls (plan-view chart button) can call methods
      this.scalePct = parseInt(localStorage.getItem('dc_scale')) || 125;
      this.applyScale();
      window.addEventListener('resize', () => { clearTimeout(this._rt); this._rt = setTimeout(() => this.onResize(), 120); });
      try { this.hosted = !!(await this.api('/env')).hosted; } catch (e) {}
      // Auto Pilot is a STANDALONE page (/autopilot.html) so its 5-min auto-refresh stays put (a full
      // SPA reload would drop back to the dashboard). On hosted, friends LAND there; the page's
      // "Browse setups →" link comes back to the SPA with ?site=1 (loop-safe).
      if (this.hosted && !location.search.includes('site')) { location.replace('/autopilot.html'); return; }
      try { this.coachCfg = await this.api('/coach-config'); } catch (e) {}
      if (this.hosted) { this.market = 'us'; localStorage.setItem('dc_market', 'us'); }   // IL is local-only; site stays US
      if (this.hosted && this.hostedHidden.includes(this.view)) this.view = 'dashboard';
      await Promise.all([this.loadSettings(), this.loadScreeners(), this.loadSuggestions(),
        this.loadTrades(), this.loadWatchlist(), this.loadStats(), this.loadDoc('lessons'),
        this.loadSectorHeat(), this.loadNews(), this.loadMarket(), this.loadUniverse(), this.loadThemes()]);
      this.loadGameplan();
      const def = this.screeners.find(s => s.is_default) || this.screeners[0];
      this.scanScreener = this.suggestions.screener_id || (def && def.id) || '';
      this.docEdit && (this.docEdit = this.docEdit);
      this.loadDoc(this.docTab);
      this.startLive();
      setInterval(() => { if (this.live.updated_at) this.liveAgeSec = Math.round((Date.now() - this._liveAt) / 1000); }, 1000);
      this.loadHealth();                                     // ❤️ vital signs now + every 20s
      setInterval(() => this.loadHealth(), 20000);
      // deep-link: Auto Pilot's plan "View chart" button arrives as /?site=1&chart=TICKER
      const _ct = new URLSearchParams(location.search).get('chart');
      if (_ct) { this.chartOnly = true; this.view = 'suggestions'; try { this.showChart(_ct.toUpperCase(), {}); } catch (e) {} }
    },

    // ---------- live updates (free Yahoo quotes, polled while the market is open) ----------
    get marketOpen() { return ['PRE', 'PREPRE', 'REGULAR', 'POST', 'POSTPOST'].includes(this.live.market_state); },
    get liveLabel() {
      if (!this.liveOn) return 'LIVE off';
      if (!this.live.market_state) return 'LIVE…';
      const m = { REGULAR: 'OPEN', PRE: 'PRE', PREPRE: 'PRE', POST: 'AFTER', POSTPOST: 'AFTER', CLOSED: 'CLOSED' }[this.live.market_state] || this.live.market_state;
      return 'LIVE · ' + m + (this.live.updated_at ? ' · ' + this.liveAgeSec + 's' : '');
    },
    // Hosted freshness stamp: "LIVE · HH:MM" converted from UTC scanned_at to viewer local time.
    // Returns null when data is not yet fresh (waiting state or never scanned).
    get hostedLiveStamp() {
      const sa = this.hostedScan.scannedAt || this.suggestions.scanned_at;
      if (!sa) return null;
      try {
        // scanned_at arrives as "YYYY-MM-DD HH:MM:SS UTC" — parse to local
        const utcStr = sa.replace(' UTC', '').replace(' ', 'T') + 'Z';
        const d = new Date(utcStr);
        if (isNaN(d.getTime())) return sa;   // fallback: show raw string
        return 'LIVE · ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
      } catch (e) { return sa; }
    },
    liveSymbols() {
      const a = new Set();
      this.trades.forEach(t => { if (t.status === 'open') a.add(t.ticker); });
      (this.watchlist || []).forEach(r => a.add(r.ticker));
      (this.suggestions.items || []).slice(0, 40).forEach(s => a.add(s.ticker));
      if (this.chartModal.open && this.chartModal.ticker) a.add(this.chartModal.ticker);
      if (this.view === 'screeners' && this.screenTab === 'premarket') (this.premarket.movers || []).forEach(m => a.add(m.ticker));
      return [...a];
    },
    // move today's candle on the open chart with the live price
    updateChartLive() {
      const cm = this.chartModal;
      if (!cm.open || !cm._series || !cm._bars || !cm._bars.length) return;
      const q = (this.live.prices || {})[cm.ticker];
      if (!q || q.price == null) return;
      const lb = cm._bars[cm._bars.length - 1];
      try { cm._series.update({ time: lb.time, open: lb.open, high: Math.max(lb.high, q.price), low: Math.min(lb.low, q.price), close: q.price }); } catch (e) {}
      if (cm._priceLine) { try { cm._priceLine.applyOptions({ price: +q.price.toFixed(2) }); } catch (e) {} }   // keep the white price mark live
      // live rotation: keep the stop pinned to today's low as the session builds
      if (cm._rotStop && cm._rotEntry && q.day_low != null) {
        let st = q.day_low;
        if (cm._rotEntry - st < 0.3 * cm._rotAdrPx) st = cm._rotEntry - 0.3 * cm._rotAdrPx;
        try { cm._rotStop.applyOptions({ price: +st.toFixed(2) }); } catch (e) {}
      }
    },
    async tickLive() {
      if (!this.liveOn) return;
      const syms = this.liveSymbols();
      try {
        const r = await this.api('/live?symbols=' + encodeURIComponent(syms.join(',')));
        this.live = { prices: r.prices || {}, market_state: r.market_state, updated_at: r.updated_at, posture: r.posture };
        this._liveAt = Date.now(); this.liveAgeSec = 0;
        this.mergeLive();
        // CROSS-SURFACE SYNC: the live tick only merges PRICES into the cached trades/suggestions. Re-fetch
        // them so a change made in the Live Coach app (✓/✗) or via the Telegram bot (stop move, close, take,
        // pass) shows up here without a manual reload. Trades are light (re-fetch ~30s); the suggestions list
        // re-grades, so refresh it gentler (~90s) and only where it's shown.
        if (!this._lastTradeSync || Date.now() - this._lastTradeSync > 30000) {
          this._lastTradeSync = Date.now(); this.loadTrades();
        }
        if ((this.view === 'dashboard' || this.view === 'suggestions')
            && (!this._lastSugSync || Date.now() - this._lastSugSync > 90000)) {
          this._lastSugSync = Date.now(); this.loadSuggestions();
        }
        // pre/after-hours: keep the gameplan (and prediction, if open) in sync with the LIVE regime
        // — they recompute server-side off the extended-hours index prices (~every 3 min).
        if (this.live.posture && this.live.posture.extended
            && (!this._lastGpLive || Date.now() - this._lastGpLive > 3 * 60 * 1000)) {
          this._lastGpLive = Date.now();
          this.loadGameplan();
          if (this.view === 'news' && this.newsTab === 'prediction') this.loadPrediction();
        }
        // live entries (armed/confirmed) — refresh on the dashboard during market hours so a setup that
        // confirms its trigger surfaces within ~45s. Light: the engine live-quotes only the ~12 shortlist.
        if ((this.view === 'dashboard' || this.view === 'suggestions') && this.marketOpen
            && (!this._lastNow || Date.now() - this._lastNow > 40000)) {
          this._lastNow = Date.now(); this.loadNow();
        }
        // live Sector Heat — while viewing that tab OR the 📋 watchlist drawer is open (both use member quotes), throttled ~60s
        if (((this.view === 'screeners' && this.screenTab === 'heat') || this.watchOpen) && this.marketOpen
            && (!this._lastHeatLive || Date.now() - this._lastHeatLive > 60000)) {
          this.loadSectorHeatLive();
        }
        // auto re-scan pre-market movers every ~8 min during the pre-market session (finds NEW gappers)
        if (this.view === 'screeners' && this.screenTab === 'premarket' && ['PRE', 'PREPRE'].includes(this.live.market_state)
            && !(this.premarket.status && this.premarket.status.running)
            && (!this._lastPmScan || Date.now() - this._lastPmScan > 8 * 60 * 1000)) {
          this._lastPmScan = Date.now(); this.scanPremarket();
        }
        // auto re-scan spins every ~3 min while viewing the tab during market hours (catches new spins)
        if (this.view === 'screeners' && this.screenTab === 'spinning' && this.marketOpen
            && !(this.spinning.status && this.spinning.status.running)
            && (!this._lastSpinScan || Date.now() - this._lastSpinScan > 3 * 60 * 1000)) {
          this._lastSpinScan = Date.now(); this.scanSpinning();
        }
      } catch (e) {}
    },
    _scheduleLive(delay) {
      clearTimeout(this._liveTimer);
      this._liveTimer = setTimeout(async () => {
        await this.tickLive();
        this.maybeAutoScan();
        this._scheduleLive(this.marketOpen ? 45000 : 300000);   // 45s when open, 5min when closed
      }, delay);
    },
    startLive() { this.liveOn = true; if (!this._lastAutoScan) this._lastAutoScan = Date.now(); this._scheduleLive(0); },
    stopLive() { this.liveOn = false; clearTimeout(this._liveTimer); },
    toggleLive() { this.liveOn ? this.stopLive() : this.startLive(); },
    // Recompute the position coach from the LIVE price so the action never contradicts the live
    // P&L. Mirrors the backend ladder, with one intraday nuance: dipping under the 9-EMA mid-session
    // is a "watch" (the exit rule is a daily CLOSE under it), not a hard EXIT.
    _guardReady(g, price, entry, stop, shares, adr) {
      // The server found a structural guard stop (swing low / reclaimed level / EMA). Re-validate it
      // against the LIVE price: it must still bank >= guard_min_lock, sit >= guard_buffer_adr ADR below
      // the live price (room), and bank >= guard_step_dollars MORE than the current stop already locks.
      // Otherwise hide it — never choke the position. Structure level is held from the server (no JS pivots).
      if (!g || !entry || !shares || price == null) return false;
      const adrPx = (adr ? price * adr / 100 : 0) || 0.01;
      const lock = (g.guard_stop - entry) * shares;
      const room = price - g.guard_stop;
      const curLock = Math.max(0, (stop && stop > entry ? (stop - entry) * shares : 0));
      return lock >= this.coachCfg.guard_min_lock
          && room >= this.coachCfg.guard_buffer_adr * adrPx
          && (lock - curLock) >= this.coachCfg.guard_step_dollars;
    },
    liveCoach(t, price) {
      const e = t.entry || t.planned_entry, stop = t.stop;
      if (!e || !stop || price == null) return null;
      // original 1R: entry−stop, or recovered from the 2R target once the stop is at/above entry
      const risk0 = (e > stop) ? (e - stop) : null;
      const tgt = t.target;
      const risk = risk0 || (tgt && tgt > e ? (tgt - e) / 2 : null);
      const r = risk ? (price - e) / risk : null;
      const bePlus = stop >= e;                              // breakeven+ stop = house money
      const c = t.coach || {};
      const e9 = c.e9 != null ? c.e9 : null, e50 = c.e50 != null ? c.e50 : null;
      const patient = !!c.patient, armed = c.armed !== false;  // armed = has CLOSED above its line since entry
      const trail = c.trail != null ? c.trail : (patient ? e50 : e9);
      const trailLabel = c.trail_n != null ? c.trail_n + ' EMA' : (patient ? '50 EMA' : '9 EMA');
      const adr = (c.ext9_adr ? Math.abs(c.ext9 / c.ext9_adr) : null);
      const ext9 = e9 ? (price / e9 - 1) * 100 : null;
      const ext9_adr = (ext9 != null && adr) ? ext9 / adr : null;
      const edays = c.earnings_days, earnSoon = edays != null && edays >= 0 && edays <= this.coachCfg.earn_soon_days;
      const rtxt = r != null ? ((r >= 0 ? '+' : '') + r.toFixed(1) + 'R') : '';
      const rsuffix = rtxt ? ' (' + rtxt + ')' : '';
      // A stop/exit can only ACT during the REGULAR session — a broker stop won't fill pre/after-hours and
      // exits are decided on the daily CLOSE. So an extended-hours dip below the stop/trail is NOT an exit
      // (the MXL after-hours auto-close bug). Upside (raise/guard) still works live off the extended price.
      const reg = this.live.market_state === 'REGULAR';
      const extLabel = ['PRE', 'PREPRE'].includes(this.live.market_state) ? 'pre-market'
        : ['POST', 'POSTPOST'].includes(this.live.market_state) ? 'after-hours' : 'extended hours';
      let action, tone, reason;
      if (!reg && (price <= stop || (trail != null && price < trail))) {
        return { action: 'WATCH', tone: 'warn',
          reason: `under your stop/line in ${extLabel} only — your stop is regular-hours, so you're NOT out; exits confirm at the close.`,
          r_mult: r != null ? +r.toFixed(2) : null, ext9_adr: ext9_adr != null ? +ext9_adr.toFixed(1) : null };
      }
      if (price <= stop) {
        if (bePlus) { action = 'EXIT'; tone = 'warn'; reason = `stop $${stop} is your locked-in (breakeven+) exit${rtxt ? ' — ' + rtxt : ''}`; }
        else { action = 'EXIT'; tone = 'danger'; reason = `price $${price} is at/below your stop $${stop} — should be out`; }
      }
      else if (trail != null && price < trail && !armed) { action = 'HOLD'; tone = 'good'; reason = `below the ${trailLabel} ($${trail}) but it hasn't reclaimed the line yet — not an exit; only your stop $${stop} exits until it closes back above`; }
      else if (trail != null && price < trail) { action = 'WATCH'; tone = 'warn'; reason = `back under the ${trailLabel} ($${trail}) intraday — exit only if it CLOSES under it`; }
      else if (patient && e9 != null && price < e9) { action = 'HOLD'; tone = 'good'; reason = `under the 9 EMA but holding the 50 EMA ($${e50}) — that's the deep-pullback/base plan`; }
      // TRIM only on a genuine PARABOLIC blow-off (price VERY far above the EMAs — the ARM/DELL case),
      // not on ordinary strength. ≥4× ADR above the 9 EMA. (ext9_adr is live → premarket-aware.)
      else if (r != null && r >= this.coachCfg.raise_r && ext9_adr != null && ext9_adr >= this.coachCfg.parabolic_adr) { action = 'TRIM'; tone = 'warn'; reason = `parabolic — ${ext9_adr.toFixed(1)}× ADR over the 9-EMA (far above 9/21/50)${rsuffix} — trim into the spike, trail the rest`; }
      else if (earnSoon) { action = 'WATCH'; tone = 'warn'; reason = `earnings in ${edays}d — binary event; hold through or reduce, your call${rsuffix}`; }
      else if (this._guardReady(c.guard, price, e, stop, t.shares, c.adr || adr)) {
        // Bank real money at a structural level with room below price — re-checked against the LIVE
        // price (premarket-aware): only shows while the guard still keeps its breathing room.
        const g = c.guard, adrPx = ((c.adr || adr) ? price * (c.adr || adr) / 100 : 0) || 0.01;
        const lock = Math.round((g.guard_stop - e) * t.shares);
        const roomAdr = (price - g.guard_stop) / adrPx, roomPct = (price - g.guard_stop) / price * 100;
        const verb = stop >= e ? 'raise' : 'lock it in: raise';
        action = 'GUARD STOP'; tone = 'good';
        reason = `${rtxt} — ${verb} your stop to $${g.guard_stop} (just under the ${g.structure_label} $${g.structure}). Banks $${lock} if it pulls back, ${roomAdr.toFixed(1)}× ADR / ${roomPct.toFixed(1)}% under $${price} so noise won't hit it — exit on a daily close below it.`;
      }
      else if (r != null && r >= this.coachCfg.raise_r && stop < e) {
        // Trail the stop just UNDER the EMA (the real exit line), not a fixed breakeven — the RGTI
        // lesson: snapping to breakeven right where the 9 EMA sits gets wicked on noise. Give it room
        // to the line and exit on a CLOSE under it. Falls back to breakeven if the EMA is below the stop.
        action = 'RAISE STOP'; tone = 'good';
        const adrPx = (adr ? price * adr / 100 : 0) || 0.01;
        const emaStop = trail != null ? +(trail - 0.10 * adrPx).toFixed(2) : null;
        if (emaStop != null && emaStop > stop) {
          const qual = emaStop >= e ? 'locks in above breakeven' : 'risk a little to the line, not a tight breakeven that gets wicked';
          reason = `${rtxt} — trail the stop to just under the ${trailLabel} ($${emaStop}) — ${qual}; exit on a daily close under the line`;
        } else {
          reason = `${rtxt} — raise the stop to breakeven ($${e}) so it can't turn red`;
        }
      }
      else { action = 'HOLD'; tone = 'good'; reason = `trend intact above the ${trailLabel}${rsuffix} — hold; exit on a daily close under it`; }
      // DEFEND MODE — in the closing window (server sets flatten_now only in RTH after 15:30 ET), flip every
      // momentum position to FLATTEN. Patient 50-EMA holds are exempt; an EXIT already means 'get out'.
      const dfd = this.now && this.now.defend;
      if (dfd && dfd.flatten_now && !patient && reg && action !== 'EXIT') {
        action = 'FLATTEN'; tone = 'warn';
        reason = `🛡️ defend mode — sell into the close; don't hold this overnight (extended + weak tape tends to give the gains back). Your call.`;
      }
      // ⚠️ TAPE GUARD — intraday: market rejected & rolling over → move ALL open positions to break-even
      // (stop → entry). ALERT-ONLY. Yields to a stronger sell (EXIT / defend FLATTEN); skips a position
      // already at/above break-even (stop >= entry). Mirrors the server's RAISE_BE override in compute_now.
      const tg = this.now && this.now.tape_guard;
      if (tg && tg.on && action !== 'EXIT' && action !== 'FLATTEN' && e != null && (stop == null || stop < e)) {
        action = 'RAISE_BE'; tone = 'warn';
        reason = `⚠️ tape guard — market rejected & rolling over${tg.indices&&tg.indices.length?` (${tg.indices.join(', ')})`:''}. Move the stop → break-even ($${e}); don't give an open gain back into a falling tape. Your call.`;
      }
      return { action, tone, reason, r_mult: r != null ? +r.toFixed(2) : null, ext9_adr: ext9_adr != null ? +ext9_adr.toFixed(1) : null };
    },
    mergeLive() {
      const px = this.live.prices || {};
      this.trades.forEach(t => {
        if (t.status !== 'open') return;
        const q = px[t.ticker]; if (!q || q.price == null) return;
        t.last = q.price;
        const e = t.entry || t.planned_entry;
        if (e && t.shares) { t.pnl = +((q.price - e) * t.shares).toFixed(2); t.pnl_pct = +(((q.price / e) - 1) * 100).toFixed(2); }
        t._live = true;
        t._liveCoach = this.liveCoach(t, q.price);
        t._hitTarget = t.target != null ? q.price >= t.target : false;
        // pre/after-hours move on THIS position: % vs the regular-session close + its $ impact
        if (q.ext_price != null && q.reg_price != null) {
          t._extPct = q.ext_change_pct != null ? q.ext_change_pct : +(((q.ext_price / q.reg_price) - 1) * 100).toFixed(2);
          t._extImpact = t.shares ? +((q.ext_price - q.reg_price) * t.shares).toFixed(2) : null;
        } else { t._extPct = null; t._extImpact = null; }
      });
      (this.suggestions.items || []).forEach(s => {
        const q = px[s.ticker]; if (!q || q.price == null) return;
        s.close = q.price; s._livechg = q.change_pct;
        // mirror the scanner's chase guard — a parabolic-extended (non-patient) name, a
        // distribution bar, or a stretched setup is NOT "buyable now" even if price sits in
        // the zone (the NBIS case). Without this, the live tick would wipe the scan's guard.
        const chase = (s.parabolic && !s.worth_waiting) || s.distribution_today || s.extended;
        if (s.zone_bottom != null && s.zone_top != null) {
          s.buyable_now = (q.price >= s.zone_bottom && q.price <= s.zone_top) && !chase;
        }
        // each entry option has its own zone — recompute its live buyability too
        (s.entries || []).forEach(e => {
          if (e.zone_bottom != null && e.zone_top != null)
            e.buyable_now = (q.price >= e.zone_bottom && q.price <= e.zone_top) && !chase;
        });
        s._liveStopped = s.stop != null && q.price <= s.stop;
      });
      if (this.live.posture) {
        this.marketRegime.posture = this.live.posture.posture;
        this.marketRegime.label = this.live.posture.label;
        if (this.live.posture.indexes) this.marketRegime.indexes = this.live.posture.indexes;
        if (this.live.posture.fear_greed) this.marketRegime.fear_greed = this.live.posture.fear_greed;
      }
      // live re-rank: within each GRADE band, names that have pulled INTO their buy zone float up
      // (live actionability). Grade still dominates — a buyable C never outranks a B (sugRank).
      // NOTE: this is intentionally a LIVE view and will differ intraday from the frozen forward
      // snapshot — that's by design (see the roadmap item to reconcile/label the two). Don't remove it.
      if (this.suggestions.items) {
        this.suggestions.items.sort((a, b) => this.sugRank(a, b));
      }
      this.updateChartLive();
      // live pre-market movers (during the pre-market session, on that tab)
      if (this.view === 'screeners' && this.screenTab === 'premarket'
          && ['PRE', 'PREPRE'].includes(this.live.market_state) && this.premarket.movers) {
        this.premarket.movers.forEach(m => { const q = px[m.ticker]; if (q && q.price != null) { m.price = q.price; if (q.change_pct != null) m.gap = q.change_pct; } });
        this.premarket.movers.sort((a, b) => b.gap - a.gap);
      }
    },
    // auto-rescan: every 30 min while the market's open, pull FRESH bars (today's candle) so new
    // setups/grades appear intraday. Guarded so it never overlaps a running scan.
    maybeAutoScan() {
      // On hosted, scans are driven exclusively by the stale-gate and the manual Re-scan button —
      // the 30-min auto-rescan must not interfere with that flow.
      if (this.hosted) return;
      if (!this.liveOn || !this.autoScan || !this.marketOpen || this.scan.running) return;
      const now = Date.now();
      if (this._lastAutoScan && now - this._lastAutoScan < 30 * 60 * 1000) return;
      const def = this.screeners.find(s => s.is_default) || this.screeners[0];
      if (!def) return;
      this._lastAutoScan = now;
      this.api('/scan/' + def.id + '?fresh=1', 'POST').then(r => { if (r && r.ok) { this.scan.running = true; this.poll(); } });
    },
    todayStr() { const d = new Date(); return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0'); },
    // today's mark-to-market on open positions. Baseline = your ENTRY for positions opened today
    // (you only owned it from the fill), else yesterday's close for overnight holds.
    get dailyPnl() {
      // REGULAR-session move only — use reg_price (regularMarketPrice), never the pre/after
      // print, so extended-hours moves don't leak into "today's P&L" (they get their own tile).
      // During PRE (before today's open) regularMarketPrice is still YESTERDAY's close, so
      // reg_price−prev_close would be yesterday's move — today hasn't traded yet, so it's null.
      if (['PRE', 'PREPRE'].includes(this.live.market_state)) return null;
      const px = this.live.prices || {}, today = this.todayStr(); let s = 0, have = false;
      this.trades.forEach(t => {
        if (t.status !== 'open' || !t.shares) return;
        const q = px[t.ticker]; if (!q) return;
        const cur = q.reg_price != null ? q.reg_price : q.price; if (cur == null) return;
        const base = (t.taken_at === today) ? (t.entry || t.planned_entry) : q.prev_close;
        if (base == null) return;
        s += (cur - base) * t.shares; have = true;
      });
      return have ? s : null;
    },
    // pre-market / after-hours move on open positions, separate from the regular-session P&L:
    // (extended price − the regular-session close) × shares. Null outside extended hours.
    get extPnl() {
      const px = this.live.prices || {}; let s = 0, have = false;
      this.trades.forEach(t => {
        if (t.status !== 'open' || !t.shares) return;
        const q = px[t.ticker]; if (!q || q.ext_price == null) return;
        const ref = q.reg_price != null ? q.reg_price : q.prev_close; if (ref == null) return;
        s += (q.ext_price - ref) * t.shares; have = true;
      });
      return have ? s : null;
    },
    get extLabel() {
      return ['PRE', 'PREPRE'].includes(this.live.market_state) ? 'Pre-market P&L'
        : ['POST', 'POSTPOST'].includes(this.live.market_state) ? 'After-hours P&L' : 'Extended P&L';
    },
    async api(path, method = 'GET', body) {
      const opt = { method, headers: { 'Content-Type': 'application/json', 'X-Workspace': workspaceId(), 'X-Market': currentMarket() } };
      if (body) opt.body = JSON.stringify(body);
      const r = await fetch('/api' + path, opt);
      return r.json();
    },

    // ---------- market switch (US <-> Israel/TASE) ----------
    toggleMarket() {
      this.market = this.market === 'il' ? 'us' : 'il';
      localStorage.setItem('dc_market', this.market);
      this.reloadAll();
    },
    get marketLabel() { return this.market === 'il' ? '🇮🇱 IL' : '🇺🇸 US'; },
    get cur() { return this.market === 'il' ? '₪' : '$'; },   // currency glyph for price labels (TASE quotes in agorot)
    async reloadAll() {
      // re-fetch everything under the new X-Market so the whole dashboard, account,
      // positions, suggestions and forward test swap to the selected market.
      this.gameplan = null; this.now = null; this.forward = null; this.openForwardDay = null; this.fwdMonth = null; this.fwdDayReport = null; this.pnlCal = {};
      this.live = { prices: {}, market_state: null, updated_at: null, posture: null };
      await Promise.all([this.loadSettings(), this.loadScreeners(), this.loadSuggestions(),
        this.loadTrades(), this.loadWatchlist(), this.loadStats(),
        this.loadSectorHeat(), this.loadNews(), this.loadMarket(), this.loadUniverse(), this.loadThemes()]);
      this.loadGameplan(); this.loadNow();
      this.loadForward(); this.loadPnlCal();
      const def = this.screeners.find(s => s.is_default) || this.screeners[0];
      this.scanScreener = this.suggestions.screener_id || (def && def.id) || '';
    },

    // ---------- loaders ----------
    async loadSettings() { this.settings = await this.api('/settings'); if (this.settings.briefing_enabled === undefined) this.settings.briefing_enabled = true; },
    async loadScreeners() { this.screeners = await this.api('/screeners'); },
    async loadSuggestions() {
      const d = await this.api('/suggestions');
      // Keep the screener id current so the Re-scan button works even on the fresh path.
      if (this.hosted && d.screener_id) this.hostedScan.screener_id = d.screener_id;
      // Hosted stale gate — trust the server's `stale` flag exclusively; never recompute.
      if (this.hosted && d.stale) {
        // If we already have live data showing, keep it displayed — do NOT wipe the live rows.
        // Only the first-load (state===null) or a manual Re-scan (state==='waiting') triggers
        // the wait panel. Periodic background refreshes that see stale simply keep the last
        // live payload and ignore the stale flag.
        if (this.hostedScan.state === 'live') {
          // Keep the last good data; just don't update this.suggestions with stale payload
          return;
        }
        // First load or rescan: show the wait panel
        this.suggestions = d;
        this.mergeLive();
        this.hostedScan.screener_id = d.screener_id || this.scanScreener || '';
        if (this.hostedScan.state !== 'waiting') {
          this.hostedScan.state = 'waiting';
          this.hostedScan._stalled = false;
        }
        if (d.scanning) {
          this._hostedPollScan();   // scan already running server-side — just poll
        } else {
          await this._hostedStartScan();
        }
      } else if (this.hosted && !d.stale) {
        // A manual Re-scan in progress owns the transition back to live — a background refresh that
        // sees the (still-fresh) data must NOT clear the poll or kill the wait panel mid-scan.
        if (this.hostedScan.state === 'waiting') return;
        // Data is fresh — capture freshness metadata, mark live, and render
        this.suggestions = d;
        this.mergeLive();
        this.hostedScan.scannedAt = d.scanned_at || null;
        this.hostedScan.state = 'live';
        this.hostedScan._stalled = false;
        clearTimeout(this.hostedScan._stalled_timer);
        clearInterval(this.hostedScan._timer);
        this.hostedScan._timer = null;
      } else {
        // LOCAL — unchanged path
        this.suggestions = d;
        this.mergeLive();
      }
    },
    async _hostedStartScan() {
      const sid = this.hostedScan.screener_id;
      if (!sid) return;
      try {
        const r = await this.api('/scan/' + sid + '?fresh=1', 'POST');
        // 409 = scan already running — that's fine, just start polling
        if (r && r.ok) { /* started */ }
      } catch (e) { /* 409 arrives as a rejected fetch on some setups — safe to ignore */ }
      this._hostedPollScan();
    },
    _hostedPollScan() {
      if (this.hostedScan._timer) return;   // already polling
      const STALL_MS = 18 * 60 * 1000;     // generous — full scan ~5 min local, slower on the throttled free dyno
      this.hostedScan._stalled = false;
      // Stall watchdog
      this.hostedScan._stalled_timer = setTimeout(() => {
        if (this.hostedScan.state === 'waiting') {
          this.hostedScan._stalled = true;
          clearInterval(this.hostedScan._timer);
          this.hostedScan._timer = null;
        }
      }, STALL_MS);
      this.hostedScan._timer = setInterval(async () => {
        try {
          const st = await this.api('/scan/status');
          this.hostedScan.done = st.done || 0;
          this.hostedScan.total = st.total || 0;
          this.hostedScan.current = st.current || '';
          if (!st.running) {
            // Scan finished — re-fetch payload and check freshness
            clearInterval(this.hostedScan._timer);
            this.hostedScan._timer = null;
            clearTimeout(this.hostedScan._stalled_timer);
            const d = await this.api('/suggestions');
            this.suggestions = d;
            this.mergeLive();
            if (!d.stale) {
              this.hostedScan.scannedAt = d.scanned_at || null;
              this.hostedScan.state = 'live';
            } else {
              // still stale after scan (e.g. another workspace's scan finished first) — restart
              await this._hostedStartScan();
            }
          }
        } catch (e) { /* transient network error — keep polling */ }
      }, 2500);
    },
    // Manual Re-scan (hosted only) — user taps the button after data is live to get a refresh
    async hostedRescan() {
      if (this.hostedScan.state === 'waiting') return;  // already scanning
      this.hostedScan.state = 'waiting';
      this.hostedScan._stalled = false;
      this.hostedScan.screener_id = this.suggestions.screener_id || this.scanScreener || '';
      await this._hostedStartScan();
    },
    // Retry after a stall (the "Scan failed — tap to retry" button)
    async hostedRetry() {
      this.hostedScan._stalled = false;
      this.hostedScan.state = 'waiting';
      this.hostedScan._timer = null;   // ensure poll can restart
      await this._hostedStartScan();
    },
    async loadTrades() { this.trades = await this.api('/trades'); this.mergeLive(); },
    async loadWatchlist() { this.watchlist = await this.api('/watchlist'); },
    onWatch(t) { return (this.watchlist || []).some(r => r.ticker === t); },
    async addSugToWatch(s) {
      await this.loadWatchlist();                       // refresh first so we never overwrite the list
      if (this.onWatch(s.ticker)) return;
      this.watchlist.push({ ticker: s.ticker, why: s.why || '', level: (s.zone_bottom + '–' + s.zone_top),
        setup: s.setup_type || '', catalyst: s.news_headline || '' });
      await this.saveWatchlist();
    },
    async loadStats() { this.stats = await this.api('/stats'); },
    async loadMarket() { this.marketRegime = await this.api('/market'); },
    async loadGameplan() { try { this.gameplan = await this.api('/gameplan'); } catch (e) {} },
    async loadNow() { try { this.now = await this.api('/now'); } catch (e) {} },
    // which entry is winning live (higher avg R) — confirmation vs touch
    bestEntry() {
      const ec = this.forward && this.forward.entry_compare; if (!ec) return null;
      const c = ec.confirmation && ec.confirmation.avg_r, t = ec.touch && ec.touch.avg_r;
      if (c == null && t == null) return null;
      if (c == null) return 'touch'; if (t == null) return 'confirmation';
      return c >= t ? 'confirmation' : 'touch';
    },
    // how far an armed setup is from its trigger (breakout: % price must rise to break the prior-day high)
    fwdArmedAway(a) {
      if (!a || !a.trigger || !a.live_price) return null;
      return Math.round((a.trigger / a.live_price - 1) * 1000) / 10;   // % to go (≤0 ⇒ already through)
    },
    // short, value-only TARGET / INVALID strings per setup type (the levels strip wants values, not sentences)
    planShortTarget(setup) {
      const st = (setup || '').toLowerCase();
      if (st.includes('deep pullback')) return 'trail 50';
      if (st.includes('consolidation')) return 'trail 9';
      if (st.includes('avwap') || st.includes('pullback')) return 'trail 20';
      return 'trim 2R';
    },
    planShortInvalid(setup) {
      const st = (setup || '').toLowerCase();
      if (st.includes('deep pullback')) return 'close<50';
      if (st.includes('consolidation')) return 'lose cluster';
      if (st.includes('avwap')) return 'lose AVWAP';
      return 'lose day low';
    },
    // the per-setup PLAN (the 📋) — v5 layout: BUY hero · levels strip · WHY metric grid · chart button.
    // Spec: mockups/plan.html. Built from the plan dict (p.plan) + structured fields on the item p.
    planHtml(p) {
      const pl = (p && p.plan) || {};
      const esc = s => (s == null ? '' : String(s)).replace(/[<>&]/g, c => ({ '<': '&lt;', '>': '&gt;', '&': '&amp;' }[c]));
      const setup = p && p.setup_type;

      // --- BUY hero: the plan's buy/trigger line, kept concise ---
      const buyTxt = esc(pl.trigger || 'Buy the confirmed trigger off the level — tight stop just under it.');

      // --- levels strip (short values only) ---
      const stopV = p && p.stop != null ? '$' + p.stop : (esc(pl.stop) || '—');
      const sizeV = (p && p.shares != null) ? p.shares + ' sh'
        : (pl.size ? esc(String(pl.size).split('·')[0].trim()) : '—');   // fall back to the plan's size text
      const tgtV = this.planShortTarget(setup);
      const invalV = this.planShortInvalid(setup);
      const levels = `
        <div class="levels">
          <div class="lvl5 stop"><div class="lvl-l">Stop</div><div class="lvl-v mono">${stopV}</div></div>
          <div class="lvl5 size"><div class="lvl-l">Size</div><div class="lvl-v mono">${sizeV}</div></div>
          <div class="lvl5 tgt"><div class="lvl-l">Target</div><div class="lvl-v">${tgtV}</div></div>
          <div class="lvl5 inval"><div class="lvl-l">Invalid</div><div class="lvl-v mono">${invalV}</div></div>
        </div>`;

      // --- WHY grid: real structured metrics (not prose) ---
      const pct = v => (v == null ? null : (v > 0 ? '+' : '') + Math.round(v) + '%');
      const cell = (label, val, cls, sub) => {
        if (val == null) return `<div class="m"><div class="m-l">${label}</div><div class="m-v">—</div></div>`;
        return `<div class="m"><div class="m-l">${label}</div><div class="m-v ${cls || ''} ${cls ? 'mono' : ''}">${val}${sub ? `<span class="m-s"> ${sub}</span>` : ''}</div></div>`;
      };
      const p1 = pct(p && p.p1m), p6 = pct(p && p.p6m);
      const pull = (p && p.pull_from_high != null) ? Math.round(p.pull_from_high) + '%' : null;
      const vol = (p && p.volc != null) ? p.volc.toFixed(2) : null;
      const volDry = (p && p.volc != null && p.volc < 1) ? '↓dry' : (p && p.volc != null ? '↑up' : '');
      const whyGrid = `
        <div class="why-grid">
          ${cell('1-mo', p1, p1 && p1[0] === '+' ? 'up' : (p1 ? 'amb' : ''))}
          ${cell('6-mo', p6, p6 && p6[0] === '+' ? 'up' : (p6 ? 'amb' : ''))}
          <div class="m"><div class="m-l">Pullback</div><div class="m-v">${pull || '—'}${pull ? '<span class="m-s"> to 50</span>' : ''}</div></div>
          ${cell('Volume', vol, 'amb', volDry)}
        </div>`;
      // clean one-line thesis — the metrics already live in the grid above, so keep this purely qualitative
      const _lead = (p && p.p6m != null && p.p6m >= 60) ? 'A strong leader' : 'A leader';
      const _dry = (p && p.volc != null && p.volc < 0.85) ? ' on drying volume — sellers exhausting' : '';
      const _st = (setup || '').toLowerCase();
      const thesis = _st.includes('pullback') ? `${_lead} pulling back into the 50 EMA${_dry}.`
        : _st.includes('avwap') ? `${_lead} reclaiming its anchored VWAP${_dry}.`
        : _st.includes('episodic') ? `${_lead} gapping on a fresh catalyst${_dry}.`
        : (_st.includes('breakout') || _st.includes('consol')) ? `${_lead} breaking out of its base${_dry}.`
        : `${_lead} setting up${_dry}.`;

      return `<div class="plan5">
        <div class="buy${p && p.early ? ' early' : ''}"><div class="buy-l">🎯 Buy${p && p.early ? ' <span class="early-badge">Early</span>' : ''}</div><div class="buy-t">${buyTxt}</div></div>
        ${levels}
        <div class="why"><div class="why-h">Why this setup</div>${whyGrid}${thesis ? `<div class="thesis">${thesis}</div>` : ''}</div>
        <button class="chart-btn" onclick="window.__dc && window.__dc.showChart('${esc(p && p.ticker)}', {})">📈 View chart</button>
      </div>`;
    },
    async loadForward() {
      try {
        this.forward = await this.api('/forward');
        const days = this.forward && this.forward.by_day || [];
        if (days.length) {
          if (!this.fwdMonth) this.fwdMonth = days[0].date.slice(0, 7);   // latest month with data
          if (!this.openForwardDay) this.loadForwardDay(days[0].date);     // open the latest day
        }
      } catch (e) {}
    },
    async loadForwardDay(date) {
      if (!date) return;
      this.openForwardDay = date;
      this.fwdDayReport = null;
      try { this.fwdDayReport = await this.api('/forward/day?date=' + date); } catch (e) {}
    },
    // forward-test calendar helpers ------------------------------------------------
    fwdByDate() { const m = {}; (this.forward && this.forward.by_day || []).forEach(d => m[d.date] = d); return m; },
    fwdMonths() { const s = new Set((this.forward && this.forward.by_day || []).map(d => d.date.slice(0, 7))); return [...s].sort(); },
    fwdMonthLabel() {
      if (!this.fwdMonth) return '';
      const [y, mo] = this.fwdMonth.split('-').map(Number);
      return new Date(Date.UTC(y, mo - 1, 1)).toLocaleString('en-US', { month: 'long', year: 'numeric' });
    },
    fwdStepMonth(dir) {
      const ms = this.fwdMonths(); if (!ms.length) return;
      let i = ms.indexOf(this.fwdMonth);
      if (i < 0) i = ms.length - 1;
      i = Math.max(0, Math.min(ms.length - 1, i + dir));
      this.fwdMonth = ms[i];
    },
    fwdHasPrevMonth() { const ms = this.fwdMonths(); return ms.indexOf(this.fwdMonth) > 0; },
    fwdHasNextMonth() { const ms = this.fwdMonths(); const i = ms.indexOf(this.fwdMonth); return i >= 0 && i < ms.length - 1; },
    fwdWeeks() {
      if (!this.fwdMonth) return [];
      const [y, mo] = this.fwdMonth.split('-').map(Number);
      const startDow = new Date(Date.UTC(y, mo - 1, 1)).getUTCDay();
      const days = new Date(Date.UTC(y, mo, 0)).getUTCDate();
      const cells = [];
      for (let i = 0; i < startDow; i++) cells.push(null);
      for (let d = 1; d <= days; d++) cells.push(`${y}-${String(mo).padStart(2, '0')}-${String(d).padStart(2, '0')}`);
      while (cells.length % 7) cells.push(null);
      const weeks = []; for (let i = 0; i < cells.length; i += 7) weeks.push(cells.slice(i, i + 7));
      return weeks;
    },
    fwdCellStyle(date) {
      const d = this.fwdByDate()[date];
      if (!d || !d.summary) return '';
      const a = d.summary.avg_r;
      if (a == null) return 'background:rgba(148,163,184,.10);border-color:rgba(148,163,184,.25)';
      if (a > 0.3) return 'background:rgba(34,224,161,.18);border-color:rgba(34,224,161,.45)';
      if (a < -0.3) return 'background:rgba(255,107,125,.16);border-color:rgba(255,107,125,.45)';
      return 'background:rgba(148,163,184,.14);border-color:rgba(148,163,184,.3)';
    },
    fwdDayNum(date) { return date ? Number(date.slice(8, 10)) : ''; },
    async loadPnlCal() { try { this.pnlCal = await this.api('/pnl-calendar'); } catch (e) {} },
    get pnlMonthDate() { const d = new Date(); d.setDate(1); d.setMonth(d.getMonth() + this.pnlMonthOffset); return d; },
    get pnlMonthLabel() { return this.pnlMonthDate.toLocaleString('en-US', { month: 'long', year: 'numeric' }); },
    get pnlCalGrid() {
      const base = this.pnlMonthDate, y = base.getFullYear(), m = base.getMonth();
      const start = new Date(y, m, 1); start.setDate(1 - start.getDay());   // back to the Sunday
      const cells = [];
      for (let i = 0; i < 42; i++) {
        const dt = new Date(start); dt.setDate(start.getDate() + i);
        const key = dt.getFullYear() + '-' + String(dt.getMonth() + 1).padStart(2, '0') + '-' + String(dt.getDate()).padStart(2, '0');
        const rec = (this.pnlCal || {})[key];
        cells.push({ key, day: dt.getDate(), inMonth: dt.getMonth() === m, weekend: dt.getDay() === 0 || dt.getDay() === 6,
          pnl: rec && rec.day_pnl != null ? rec.day_pnl : null });
      }
      return cells;
    },
    get pnlMonthTotal() {
      const y = this.pnlMonthDate.getFullYear(), m = this.pnlMonthDate.getMonth() + 1; let t = 0, any = false;
      Object.entries(this.pnlCal || {}).forEach(([k, v]) => { const [yy, mm] = k.split('-').map(Number); if (yy === y && mm === m && v && v.day_pnl != null) { t += v.day_pnl; any = true; } });
      return any ? Math.round(t) : null;
    },
    async loadPrediction() {
      this.predictionLoading = true;
      try { this.prediction = await this.api('/prediction'); } catch (e) {}
      this.predictionLoading = false;
    },
    async loadUniverse() { this.universe = await this.api('/universe'); },
    async backupData() {
      this.backupMsg = '…';
      try { const r = await this.api('/backup', 'POST'); this.backupMsg = r.ok ? ('✓ ' + r.files + ' files') : ('✗ ' + (r.error || 'failed')); }
      catch (e) { this.backupMsg = '✗ failed'; }
      setTimeout(() => { this.backupMsg = ''; }, 4000);
    },
    async buildUniverse() {
      const r = await this.api('/universe/build', 'POST');
      if (r.ok) { this.universe.status = { running: true, stage: 'Starting…', done: 0, total: 0 }; this.pollUniverse(); }
    },
    pollUniverse() {
      clearInterval(this._uvi);
      this._uvi = setInterval(async () => {
        await this.loadUniverse();
        if (!this.universe.status?.running) { clearInterval(this._uvi); await this.loadScreeners(); }
      }, 1500);
    },
    async loadDoc(id) {
      const d = await this.api('/docs/' + id);
      if (id === this.docTab) this.docEdit = d.content || '';
      if (id === 'lessons') this.docs.lessons = d.content || '';
    },

    // ---------- computed-ish ----------
    get navItems() { return this.hosted ? this.nav.filter(n => !this.hostedHidden.includes(n.id)) : this.nav; },
    get openTrades() { return this.trades.filter(t => t.status === 'open'); },
    get activeTrades() { return this.trades.filter(t => t.status === 'open'); },
    get closedTrades() { return this.trades.filter(t => t.status === 'closed'); },
    get hotSectors() { return (this.sectorHeat.sectors || []).filter(s => s.tier === 'Hot' || s.trend === 'Rising').slice(0, 6).map(s => s.sector); },
    trendClass(t) { return { Rising: 'badge-pullback', Slowing: 'badge-ep', Falling: 'badge', Steady: 'badge' }[t] || 'badge'; },
    trendIcon(t) { return { Rising: '🚀', Slowing: '🐢', Falling: '🔻', Steady: '➖' }[t] || ''; },
    tierClass(t) { return t === 'Hot' ? 'badge-hot' : (t === 'Warm' ? 'badge-ep' : ''); },
    get alerts() { return this.news.alerts || []; },
    alertStyle(d) {
      return d === 'buy' ? 'background:rgba(34,224,161,.12);border-color:rgba(34,224,161,.55)'
        : d === 'avoid' ? 'background:rgba(255,93,115,.12);border-color:rgba(255,93,115,.55)'
        : 'background:rgba(255,181,61,.12);border-color:rgba(255,181,61,.5)';
    },
    gradeColor(g) { const b = (g || '')[0]; return b === 'A' ? '#22e0a1' : b === 'B' ? '#6a8dff' : b === 'C' ? '#ffb53d' : '#93a1b8'; },
    gradeLetter(r) { return r >= 82 ? 'A+' : r >= 73 ? 'A' : r >= 63 ? 'B' : r >= 52 ? 'C' : 'D'; },
    _plusGrade(g) { return !g ? g : (g.endsWith('+') ? g : g + '+'); },   // B -> B+, A -> A+ (A+ stays A+)
    entryGradeStats(list) {
      const g = (list || []).filter(t => t.entry_rating != null);
      if (!g.length) return null;
      const avg = Math.round(g.reduce((a, t) => a + t.entry_rating, 0) / g.length);
      return { n: g.length, avg, letter: this.gradeLetter(avg), low: g.filter(t => t.low_grade).length };
    },
    alertDate(a) {
      if (!a.published) return '';
      const d = new Date(a.published); if (isNaN(d)) return '';
      const days = Math.floor((Date.now() - d) / 86400000);
      const ds = d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
      return ds + (days <= 0 ? ' · today' : days === 1 ? ' · 1d ago' : ' · ' + days + 'd ago');
    },
    get openPnl() { return this.trades.filter(t => t.status === 'open').reduce((a, t) => a + (t.pnl || 0), 0); },
    get realizedPnl() { return this.trades.filter(t => t.status === 'closed').reduce((a, t) => a + (t.pnl || 0), 0); },
    get sortedSectors() { const k = this.sectorSort; return [...(this.sectorHeat.sectors || [])].sort((a, b) => b[k] - a[k]); },
    get riskDollar() { const a = (this.settings.equity_info ? this.settings.equity_info.equity : this.settings.account_size); return a ? Math.round(a * (this.settings.risk_pct || 1) / 100).toLocaleString() : ''; },
    setupClass(t) {
      t = t || '';
      if (t.includes('AVWAP')) return 'badge-avwap';
      if (t.includes('Pullback')) return 'badge-pullback';
      if (t.includes('Episodic')) return 'badge-ep';
      return 'badge-breakout';
    },
    // ---- Lexicon context badges (display-only; see strategy/lexicon.md) ----
    // Tags already shown as a dedicated badge (Trend Template / VCP / the setup-type badge) are
    // suppressed here to avoid a double badge.
    _LEX_DEDUP: { STAGE2: 1, VCP: 1, EP: 1 },
    _LEX_LABEL: { '52WH_BO': '52WH BO', RS_LEADER: 'RS Leader', BGU: 'Buyable Gap-Up',
      YH_RECLAIM: 'YH ↑', TIGHTNESS: 'Tight', VDU: 'Volume Dry-Up', PARABOLIC: '⚠ Parabolic' },
    lexLabel(tag) { return this._LEX_LABEL[tag] || tag; },
    lexStyle(role) {
      return ({
        S: 'background:rgba(106,141,255,.18);color:#b8c8ff;border-color:rgba(106,141,255,.45)',  // setup → indigo
        T: 'background:rgba(34,211,238,.16);color:#a5ecf7;border-color:rgba(34,211,238,.40)',    // trigger → cyan
        C: 'background:rgba(168,85,247,.18);color:#d9b3ff;border-color:rgba(168,85,247,.45)',    // context → violet
        X: 'background:rgba(255,181,61,.16);color:#ffd591;border-color:rgba(255,181,61,.45)',    // trap → amber
      })[role] || 'background:rgba(148,163,184,.12);color:#94a3b8;border-color:rgba(148,163,184,.30)';
    },
    // Returns { shown:[≤3 tags], moreCount, moreTitle } — backend already sorts by conviction.
    lexVisible(s) {
      const all = (s && s.lexicon_tags || []).filter(t => !this._LEX_DEDUP[t.tag]);
      const shown = all.slice(0, 3);
      const more = all.slice(3);
      return { shown, moreCount: more.length,
        moreTitle: more.map(t => this.lexLabel(t.tag) + ' — ' + t.define).join('\n') };
    },
    // Lexicon Phase 2 — the confirmation menu (what will confirm an armed setup), in tag vocabulary.
    _CONFIRM_LABEL: { ORH_BREAK: "opening-range-high break", HOD_BREAK: "today's-high break",
      YH_RECLAIM: "reclaim of yesterday's high", RECLAIM_50: "reclaim of the 50 EMA",
      EMA_RECLAIM: "reclaim above the EMA cluster" },
    confirmMenuText(a) {
      const m = (a && a.confirm_menu) || [];
      return m.map(t => this._CONFIRM_LABEL[t] || t).join(' or ');
    },
    // When a descending-resistance / overhead-wall gate is active (break_level set), return a single
    // human label so the "confirms on" row shows ONE clear requirement instead of the redundant menu.
    // Returns null when there's no overhead wall — callers fall back to the generic confirm_menu loop.
    wallConfirmText(rec) {
      if (!rec || rec.break_level == null) return null;
      const m = rec.confirm_menu || [];
      const lvl = '$' + rec.break_level;
      // A descending resistance line is THE drawn/watched resistance → name it the downtrend line (matches the
      // chart's "resistance — wait for the break"). Takes priority over the generic menu. (user 2026-06-10)
      if (rec.res_trendline) return 'close above ' + lvl + ' — clear the downtrend line';
      // YH_RECLAIM in the menu → the wall IS the prior-day high (most common: AVWAP pullback setups)
      if (m.includes('YH_RECLAIM')) return 'close above ' + lvl + ' — clear the prior-day high';
      // Only ORH/HOD in menu → wall is overhead resistance (EMA cluster or consolidation ceiling)
      if (m.includes('ORH_BREAK') || m.includes('HOD_BREAK')) return 'close above ' + lvl + ' — clear the overhead resistance';
      // RECLAIM_50 with a break_level (unusual but possible)
      if (m.includes('RECLAIM_50')) return 'close above ' + lvl + ' — reclaim the 50 EMA';
      // Fallback — wall exists but no known menu tag
      return 'close above ' + lvl;
    },
    // Per-ENTRY confirm source: a breakout entry carries its OWN break_level/res_trendline/confirm_menu
    // (set in scanner.analyze) so its "confirms on" names the BREAK — "clear the downtrend line" — instead
    // of the pullback's "reclaim of the 50 EMA". A non-breakout entry borrows this name's LIVE rec ONLY when
    // it's the same kind (entry_type) — so a breakout-primary's pullback 2nd-entry never shows the breakout's
    // confirm (and vice-versa); on a mismatch we show no confirm row rather than the wrong one.
    entryConfirmRec(s, e) {
      if (e && e.break_level != null) return e;
      const r = this.confRec(s);
      if (r && e && r.entry_type && e.entry_type && r.entry_type !== e.entry_type) return null;
      return r;
    },
    wl(r) { return this.wlData[r.ticker] || {}; },
    get topSuggestions() { return (this.suggestions.items || []).filter(s => s.status !== 'rejected' && s.status !== 'taken').slice(0, 8); },
    // grade band from the rating (A+ 5 … D 1) — used so the sort respects GRADE first:
    // a buyable C must never rank above a B. Buyable-now only floats WITHIN a grade band.
    gradeBand(s) { const r = s.rating || 0; return r >= 82 ? 5 : r >= 73 ? 4 : r >= 63 ? 3 : r >= 52 ? 2 : 1; },
    sugRank(a, b) {
      return this.gradeBand(b) - this.gradeBand(a)
        || (b.buyable_now ? 1 : 0) - (a.buyable_now ? 1 : 0)   // buyable-now floats up (live actionability)
        || (b.rating || 0) - (a.rating || 0)
        || (b.score || 0) - (a.score || 0);                    // raw-score tiebreak so the order is STABLE (no rating-72 reshuffle)
    },
    get filteredSuggestions() {
      // SHOW ALL setups — never hide a decided name (user 2026-06-08: "i want to see all setups").
      // Taken (✓) and passed (✗) keep their status badge so they're still distinguishable at a glance,
      // but nothing is removed from the list. status.json still syncs decisions across surfaces.
      // ('approved'/'pending' explicit filters kept for completeness; default 'all' filters nothing.)
      let items = (this.suggestions.items || []);
      if (this.filter === 'approved') items = items.filter(s => s.status === 'approved' || s.status === 'taken');
      else if (this.filter === 'pending') items = items.filter(s => s.status === 'pending' || !s.status);
      // else ('all'): no status filtering — every setup stays visible
      const sf = this.setupFilter;
      if (sf === 'Pullback') items = items.filter(s => (s.setup_type || '').includes('Pullback'));
      else if (sf === 'AVWAP') items = items.filter(s => (s.setup_type || '').includes('AVWAP'));
      else if (sf === 'Breakout') items = items.filter(s => s.setup_type === 'Breakout');
      else if (sf === 'EP') items = items.filter(s => (s.setup_type || '').includes('Episodic'));
      // Momentum cohorts are EXCLUSIVE by recency, so 1M = genuinely NEW movers,
      // not established leaders that also happen to be up this month.
      const mf = this.momFilter;
      if (mf === '1M') items = items.filter(s => s.screen_1m && !s.screen_3m && !s.screen_6m);
      else if (mf === '3M') items = items.filter(s => s.screen_3m && !s.screen_6m);
      else if (mf === '6M') items = items.filter(s => s.screen_6m);
      if (this.sectorFilter !== 'All') items = items.filter(s => s.theme === this.sectorFilter);
      if (this.waitFilter) items = items.filter(s => this.isWorthWaiting(s));
      if (this.leaderFilter) items = items.filter(s => (s.rs_pct || 0) >= 85);
      if (this.risingFilter) items = items.filter(s => s.theme_trend === 'Rising');
      if (this.ttFilter) items = items.filter(s => s.trend_template);
      if (this.vcpFilter) items = items.filter(s => s.vcp);
      if (this.newsFilter) items = items.filter(s => s.news_flag);
      if (this.buyableFilter) items = items.filter(s => this.inZone(s));
      return [...items].sort((a, b) => this.sugRank(a, b));
    },
    get activeFilterCount() {
      return [this.setupFilter !== 'All', this.momFilter !== 'All',
        this.sectorFilter !== 'All', this.waitFilter, this.leaderFilter, this.risingFilter,
        this.ttFilter, this.vcpFilter, this.newsFilter, this.buyableFilter].filter(Boolean).length;
    },
    clearFilters() {
      this.setupFilter = 'All'; this.momFilter = 'All'; this.sectorFilter = 'All';
      this.waitFilter = this.leaderFilter = this.risingFilter = false;
      this.ttFilter = this.vcpFilter = this.newsFilter = this.buyableFilter = false;
    },
    // cap how many cards actually render (top by rating) — keeps the DOM light & the live merge fast
    get displayedSuggestions() { const f = this.filteredSuggestions; return this.showAllSug ? f : f.slice(0, 120); },
    // sector filter options: every category from the (updated) sector heater, plus any
    // theme present in the current suggestions — ordered hottest-first by sector heat.
    get sectorOptions() {
      const heat = {};
      (this.sectorHeat.sectors || []).forEach(s => { heat[s.sector] = s.score; });
      const names = new Set([
        ...Object.keys(heat),
        ...(this.suggestions.items || []).map(s => s.theme).filter(Boolean),
      ]);
      return [...names].sort((a, b) => (heat[b] ?? -999) - (heat[a] ?? -999) || a.localeCompare(b));
    },

    // ---------- news: newest first (used on the News tab AND the dashboard) ----------
    _pubTime(p) { const t = p ? Date.parse(p) : NaN; return isNaN(t) ? 0 : t; },
    newsByDate(items) { return [...(items || [])].sort((a, b) => this._pubTime(b.published) - this._pubTime(a.published)); },
    ago(published) {
      const t = this._pubTime(published); if (!t) return '';
      const mins = Math.max(0, Math.round((Date.now() - t) / 60000));
      if (mins < 60) return mins + 'm ago';
      const h = Math.round(mins / 60); if (h < 24) return h + 'h ago';
      const d = Math.round(h / 24); return d + 'd ago';
    },
    get newsTickerRows() {
      return Object.entries(this.news.ticker_news || {})
        .sort((a, b) => this._pubTime(b[1].published) - this._pubTime(a[1].published));
    },
    // ONE table: each stock with a news catalyst, joined to its current setup (grade/why/zone/action)
    get catalystTable() {
      const sug = {}; (this.suggestions.items || []).forEach(s => { sug[s.ticker] = s; });
      return Object.entries(this.news.ticker_news || {}).map(([tk, n]) => {
        const s = sug[tk] || null;
        return {
          ticker: tk, headline: n.title, link: n.link, sentiment: n.sentiment, trump: n.trump, published: n.published,
          grade: s && s.grade, setup: s && s.setup_type, why: s && s.why,
          zone_bottom: s && s.zone_bottom, zone_top: s && s.zone_top, entry: s && s.entry,
          entry_type: s && s.entry_type,
          trigger_note: s && s.entries && s.entries[0] ? s.entries[0].trigger_note : null,
          buyable: s ? this.inZone(s) : false, _sug: s,
        };
      }).sort((a, b) => (a.grade ? 0 : 1) - (b.grade ? 0 : 1) || this._pubTime(b.published) - this._pubTime(a.published));
    },

    // ---------- suspicious activity: sort the loaded list (default = volume spike) ----------
    suspSorted(list) {
      const k = this.suspSort;
      return [...(list || [])].sort((a, b) =>
        (k === 'move' ? Math.abs(b.move) - Math.abs(a.move) : (b[k] || 0) - (a[k] || 0)));
    },

    // ---------- sector heat: find a stock's group ----------
    get sortedSectorsView() {                       // sortedSectors, narrowed when searching
      const all = this.sortedSectors;
      if (!(this.sectorSearch || '').trim()) return all;
      const hits = new Set(this._stockHits || []);
      return all.filter(s => hits.has(s.sector));
    },
    get stockGroups() {                             // authoritative: which group(s) hold this ticker
      const q = (this.sectorSearch || '').trim().toUpperCase();
      if (!q) return [];
      const out = [];
      for (const [grp, ts] of Object.entries(this.themesMap || {})) {
        if ((ts || []).some(t => (t || '').toUpperCase() === q)) out.push(grp);
      }
      return out;
    },
    memberHit(m) {
      const q = (this.sectorSearch || '').trim().toUpperCase();
      return !!q && (m.ticker || '').toUpperCase().includes(q);
    },
    runStockFinder() {
      const q = (this.sectorSearch || '').trim().toUpperCase();
      this._stockHits = [];
      if (!q) return;
      // exact membership from themes.json (authoritative — works even for names with no chart yet)
      for (const [grp, ts] of Object.entries(this.themesMap || {})) {
        if ((ts || []).some(t => (t || '').toUpperCase() === q)) this._stockHits.push(grp);
      }
      // also catch partial ticker matches among computed members
      (this.sectorHeat.sectors || []).forEach(s => {
        if ((s.members || []).some(m => (m.ticker || '').toUpperCase().includes(q)) && !this._stockHits.includes(s.sector)) {
          this._stockHits.push(s.sector);
        }
      });
      this._stockHits.forEach(g => { this.secOpen[g] = true; });   // auto-expand matches
    },
    fmtDollarVol(v) {
      if (!v) return '—';
      if (v >= 1e9) return this.cur + (v / 1e9).toFixed(1) + 'B/d';
      if (v >= 1e6) return this.cur + Math.round(v / 1e6) + 'M/d';
      return this.cur + Math.round(v / 1e3) + 'K/d';
    },
    liqColor(score) {
      if (score == null) return '#93a1b8';
      if (score >= 70) return '#22e0a1';
      if (score >= 45) return '#93a1b8';
      if (score >= 25) return '#ffb53d';
      return '#ff5d73';
    },
    statusClass(s) {
      return { approved: 'bg-emerald-700/40 text-emerald-300', taken: 'bg-accent/40 text-blue-200',
        rejected: 'bg-rose-800/40 text-rose-300', pending: '' }[s] || '';
    },

    // ---------- scanning ----------
    async runScan() {
      if (!this.scanScreener) return;
      const r = await this.api('/scan/' + this.scanScreener, 'POST');
      if (r.ok) { this.scan.running = true; this.poll(); }
      else alert(r.error || 'could not start scan');
    },
    poll() {
      clearInterval(this._pollTimer);
      this._pollTimer = setInterval(async () => {
        this.scan = await this.api('/scan/status');
        if (!this.scan.running) {
          clearInterval(this._pollTimer);
          await this.loadSuggestions();
        }
      }, 1000);
    },

    // ---------- suggestion actions ----------
    async act(s, action) {
      let body = {};
      if (action === 'reject') {
        const r = prompt('Pass on ' + s.ticker + '? Reason (optional) — Cancel to abort:');
        if (r === null) return;            // Cancel aborts, so a stray click doesn't pass it
        body.reason = r;
      }
      await this.api('/suggestions/' + s.ticker + '/' + action, 'POST', body);
      await this.loadSuggestions();
    },
    get passedCount() { return (this.suggestions.items || []).filter(s => s.status === 'rejected').length; },
    async saveCatalyst(s, val) {
      await this.api('/suggestions/' + s.ticker + '/catalyst', 'POST', { catalyst: val });
    },
    openTake(s) {
      this.tradeModal = { open: true, mode: 'take', ticker: s.ticker, _sug: s,
        entry: s.entry, stop: s.stop, shares: s.shares, notes: '' };
    },
    openClose(t) {
      this.tradeModal = { open: true, mode: 'close', ticker: t.ticker, _trade: t,
        exit: null, result_r: null, rules_followed: 'yes', lesson: '', notes: '' };
    },
    closeRPreview() {
      const m = this.tradeModal, t = m && m._trade;
      if (!t || m.exit == null) return null;
      const istop = t.initial_stop != null ? t.initial_stop : t.stop;
      const risk = t.entry - istop;
      if (!risk || risk <= 0) return null;
      return Math.round((m.exit - t.entry) / risk * 100) / 100;
    },
    openEdit(t) {
      this.tradeModal = { open: true, mode: 'edit', ticker: t.ticker, _trade: t,
        setup_type: t.setup_type, entry: t.entry, stop: t.stop, target: t.target,
        shares: t.shares, notes: t.notes || '' };
    },
    async submitTrade() {
      const m = this.tradeModal;
      if (m.mode === 'take') {
        await this.api('/suggestions/' + m.ticker + '/take', 'POST',
          { entry: m.entry, stop: m.stop, shares: m.shares, target: m._sug && m._sug.target, notes: m.notes });
      } else if (m.mode === 'edit') {
        await this.api('/trades/' + m._trade.id, 'PUT',
          { setup_type: m.setup_type, entry: m.entry, stop: m.stop, target: m.target, shares: m.shares, notes: m.notes });
      } else {
        await this.api('/trades/' + m._trade.id + '/close', 'POST',
          { exit: m.exit, result_r: m.result_r, rules_followed: m.rules_followed, lesson: m.lesson, notes: m.notes });
      }
      this.tradeModal.open = false;
      await Promise.all([this.loadSuggestions(), this.loadTrades(), this.loadStats(), this.loadDoc('lessons'), this.loadSettings()]);
    },

    // ---------- screeners ----------
    async addScreener() {
      if (!this.newScreener.name || !this.newScreener.tickers) return;
      await this.api('/screeners', 'POST', this.newScreener);
      this.newScreener = { name: '', tickers: '' };
      await this.loadScreeners();
    },
    async deleteScreener(id) {
      if (!confirm('Delete this screener?')) return;
      await fetch('/api/screeners/' + id, { method: 'DELETE', headers: { 'X-Workspace': workspaceId(), 'X-Market': currentMarket() } });
      await this.loadScreeners();
    },

    // ---------- view switching ----------
    selectView(id) {
      if (id === 'autopilot') { window.location.href = '/autopilot.html'; return; }   // standalone page (reload-stable)
      this.view = id;
      this.mobileNav = false;          // close the mobile drawer after picking a view
      if (id === 'watchlist') this.loadWatchlistAnalysis();
      if (id === 'news') this.loadNews();
      if (id === 'dashboard' && !this.gameplan) this.loadGameplan();
      if (id === 'dashboard' || id === 'suggestions') this.loadNow();   // suggestions show live confirmation status
      if (id === 'stats') { this.loadForward(); this.loadPnlCal(); }
      if (id === 'armedlog') this.loadArmedHistory();
      if (id === 'learninghub') { this.loadLearning(); this.loadForward(); this.loadPnlCal(); }
      if (id === 'hq') this.loadHQ();
      if (id === 'competition') this.loadCompetition();
    },
    async loadArmedHistory() { try { this.armedHistory = await this.api('/armed-history'); } catch (e) {} },
    async loadLearning() { try { this.learning = await this.api('/learning'); } catch (e) {} },
    // lesson tag → display config for Learning Hub
    lhLessonTag(tag) {
      return {
        chase:      { icon: '🏃', color: '#ff5d73', label: 'Chasing' },
        revenge:    { icon: '😤', color: '#ff5d73', label: 'Revenge trading' },
        'tight-stop': { icon: '✂️', color: '#ffb53d', label: 'Tight stop' },
        streak:     { icon: '🎲', color: '#ffb53d', label: 'Streak bias' },
      }[tag] || { icon: '💡', color: '#6a8dff', label: 'Lesson' };
    },
    async loadHQ() { try { this.hq = { ...(await this.api('/hq')), loaded: true }; } catch (e) { this.hq.loaded = true; } },
    // ---------- 🏆 Competition ----------
    async loadCompetition() { try { this.competition = { ...(await this.api('/competition')), loaded: true }; } catch (e) { this.competition = { bots: [], loaded: true }; } },
    async openBot(id) {
      if (this.compSelected === id) { this.compSelected = null; this.compDetail = null; return; }
      this.compSelected = id; this.compDetail = null; this.compDay = null; this.compTab = 'overview';
      try { this.compDetail = await this.api('/competition/bot/' + id); } catch (e) {}
      await this.$nextTick(); this.renderEquityCurve();
    },
    async openBotDay(date) {
      if (!date) return;
      this.compDayDate = date; this.compDay = null;
      try { this.compDay = await this.api('/competition/bot/' + this.compSelected + '/day?date=' + date); } catch (e) {}
      await this.$nextTick(); this.renderCompDayCharts();
    },
    setCompTab(t) { this.compTab = t; if (t === 'overview') this.$nextTick(() => this.renderEquityCurve()); },
    // sparkline path for a bot's equity array (viewBox 0..80 x 0..32)
    sparkPath(arr) {
      if (!arr || arr.length < 2) return '';
      const lo = Math.min(...arr), hi = Math.max(...arr), rng = (hi - lo) || 1;
      return arr.map((v, i) => `${(i / (arr.length - 1) * 80).toFixed(1)},${(30 - (v - lo) / rng * 28).toFixed(1)}`).join(' ');
    },
    compRegimeColor(r) { return r === 'bull' ? '#22e0a1' : (r === 'soft' ? '#ffb53d' : '#ff5d73'); },
    botIsWarmup(t) { return this.compDetail && this.compDetail.live_start && (t.exit_date || t.fill_date || '') < this.compDetail.live_start; },
    renderEquityCurve() {
      const box = document.getElementById('compEquityBox');
      if (!box || !this.compDetail || !window.LightweightCharts) return;
      if (this._compChart) { try { this._compChart.remove(); } catch (e) {} this._compChart = null; }
      box.innerHTML = '';
      const curve = this.compDetail.equity_curve || [];
      if (curve.length < 2) { box.innerHTML = '<div class="text-xs text-slate-500 p-4">Equity curve appears once the bot has a few days of history.</div>'; return; }
      const chart = LightweightCharts.createChart(box, {
        width: box.clientWidth, height: 190,
        layout: { background: { color: 'transparent' }, textColor: '#6b7890' },
        grid: { vertLines: { visible: false }, horzLines: { color: '#1c2230' } },
        rightPriceScale: { borderColor: '#2a3344' }, timeScale: { borderColor: '#2a3344' },
        handleScroll: false, handleScale: false,
      });
      const ls = chart.addLineSeries({ color: this.compDetail.color || '#22d3ee', lineWidth: 2, priceLineVisible: false, lastValueVisible: true });
      ls.setData(curve.map(p => ({ time: p.date, value: p.equity })));
      const base = (this.competition && this.competition.start_equity) || 10000;
      ls.createPriceLine({ price: base, color: '#475569', lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: 'start' });
      chart.timeScale().fitContent();
      this._compChart = chart;
    },
    async renderCompDayCharts() {
      if (!this.compDay || !this.compDay.trades) return;
      this._compDayCharts = (this._compDayCharts || []);
      this._compDayCharts.forEach(c => { try { c.remove(); } catch (e) {} }); this._compDayCharts = [];
      for (const [i, t] of this.compDay.trades.entries()) {
        const box = document.getElementById('compDayChart' + i);
        if (!box || !window.LightweightCharts) continue;
        let data = null;
        try { data = await this.api('/chart/' + t.ticker); } catch (e) { continue; }
        box.innerHTML = '';
        const chart = LightweightCharts.createChart(box, {
          width: box.clientWidth, height: box.clientWidth < 400 ? 150 : 180,
          layout: { background: { color: 'transparent' }, textColor: '#6b7890' },
          grid: { vertLines: { visible: false }, horzLines: { visible: false } },
          rightPriceScale: { visible: false }, timeScale: { visible: false },
          handleScroll: false, handleScale: false,
        });
        const s = chart.addCandlestickSeries({ upColor: '#22e0a1', downColor: '#ff5d73', borderVisible: false, wickUpColor: '#22e0a1', wickDownColor: '#ff5d73' });
        s.setData((data.bars || []).slice(-50).map(b => ({ time: b.time, open: b.open, high: b.high, low: b.low, close: b.close })));
        if (t.entry) s.createPriceLine({ price: t.entry, color: '#22e0a1', lineWidth: 1, lineStyle: 0, axisLabelVisible: false, title: 'entry' });
        if (t.stop) s.createPriceLine({ price: t.stop, color: '#ff5d73', lineWidth: 1, lineStyle: 2, axisLabelVisible: false, title: 'stop' });
        if (t.tp) s.createPriceLine({ price: t.tp, color: '#fbbf24', lineWidth: 1, lineStyle: 2, axisLabelVisible: false, title: 'tp' });
        chart.timeScale().fitContent();
        this._compDayCharts.push(chart);
      }
    },
    hqOfficeStyle(a, i) {   // wandering ghost in the animated office (pure CSS, varied per character)
      const lane = [60, 94, 128][i % 3], left = 5 + (i * 11) % 66, wx = 80 + (i % 4) * 45,
            dur = (13 + (i * 1.6) % 9).toFixed(1), delay = (-(i * 2.1)).toFixed(1);
      return `--c:${a.color || '#6a8dff'};left:${left}%;bottom:${lane}px;--wx:${wx}px;animation:hqxWander ${dur}s ease-in-out ${delay}s infinite alternate`;
    },
    hqSquad(name) { return (this.hq.agents || []).filter(a => a.squad === name && !a.leader); },
    get hqChief() { return (this.hq.agents || []).find(a => a.leader); },
    hqDot(status) { return status === 'active' || status === 'working' ? 'bg-emerald-400' : (status === 'blocked' ? 'bg-red-400' : 'bg-slate-600'); },
    // ---- Agent Office (game-style HQ) ----
    hqOpen: null,                                   // the room/agent whose modal is open
    hqLive(status) { return status === 'active' || status === 'working'; },
    hqStatusColor(status) { return this.hqLive(status) ? '#22e0a1' : (status === 'blocked' ? '#ff5d73' : '#7c8aa6'); },
    // per-agent room theme: label, accent neon, a prop glyph for the scene
    hqRoomMeta(a) {
      const M = {
        'chief':        { label: 'COMMAND HQ',  color: '#6a8dff', prop: '🛰️' },
        'quant':        { label: 'RESEARCH LAB', color: '#22d3ee', prop: '🧪' },
        'data-steward': { label: 'OBSERVATORY',  color: '#a855f7', prop: '🔭' },
        'risk-auditor': { label: 'THE VAULT',    color: '#ffb53d', prop: '🔐' },
        'ux':           { label: 'STUDIO',       color: '#ff5fa2', prop: '🎨' },
        'optimizer':    { label: 'ENGINE ROOM',  color: '#22e0a1', prop: '⚙️' },
        'qa':           { label: 'TEST BAY',     color: '#ff5d73', prop: '🧫' },
        'planner':      { label: 'WAR ROOM',     color: '#f5a623', prop: '🗺️' },
        'analyst':      { label: 'LIBRARY',      color: '#7dd3fc', prop: '📚' },
        'shipper':      { label: 'LAUNCH BAY',   color: '#c084fc', prop: '🚀' },
        'token-master': { label: 'SIGNAL TOWER', color: '#ec4899', prop: '🗜️' },
      };
      return M[a.name] || { label: (a.squad || 'OFFICE').toUpperCase() + ' ROOM', color: '#6a8dff', prop: '🖥️' };
    },
    hqRoomClass(a) {
      let c = a.leader ? 'is-hq ' : '';
      c += this.hqLive(a.status) ? 'is-busy' : 'is-asleep';
      return c;
    },
    // chief first (largest tile), then a stable order grouped by squad
    get hqRooms() {
      const agents = this.hq.agents || [];
      const order = ['Brain', 'Build', 'Protect', 'Steer', 'Ship', 'Critic'];
      return [...agents].sort((x, y) => {
        if (x.leader !== y.leader) return x.leader ? -1 : 1;
        const sx = order.indexOf(x.squad), sy = order.indexOf(y.squad);
        if (sx !== sy) return sx - sy;
        return 0;
      });
    },
    async loadHealth() {
      try {
        this.health = await this.api('/health');
        // AUTO-RELOAD on server restart: when the server's boot_id changes (a code restart/rebuild), reload
        // the page so an already-open window picks up the new code WITHOUT a manual hard refresh — kills the
        // "I restarted but don't see the change" annoyance. Fires once per restart (~within the 20s poll). (2026-06-08)
        const bid = this.health && this.health.boot_id;
        if (bid) { if (this._bootId && this._bootId !== bid) location.reload(); else this._bootId = bid; }
      } catch (e) { this.health = { ok: false, healthy: false, subs: [] }; }
    },
    get armedHistoryDays() {       // [{date, rows[], confirmed}] newest day first, rows by arm time
      const h = this.armedHistory || {};
      return Object.keys(h).sort().reverse().map(date => {
        const rows = Object.values(h[date] || {}).sort((a, b) => (a.first_armed || '').localeCompare(b.first_armed || ''));
        return { date, rows, confirmed: rows.filter(r => r.ever_confirmed).length };
      });
    },

    // ---------- sector heat ----------
    async loadSectorHeat() { this.sectorHeat = await this.api('/sector-heat'); },
    async loadSectorHeatLive() { try { this.sectorHeat = await this.api('/sector-heat/live'); this._lastHeatLive = Date.now(); } catch (e) {} },
    // ----- 📋 live-market watchlist drawer (all names split by Sector Heat theme) -----
    toggleWatch() {
      this.watchOpen = !this.watchOpen;
      if (this.watchOpen) {
        if (!this.sectorHeat.sectors || !this.sectorHeat.sectors.length) this.loadSectorHeat();
        if (!this.suggestions.items || !this.suggestions.items.length) this.loadSuggestions();   // grade badges
        if (this.marketOpen) this.loadSectorHeatLive();
      }
    },
    gradeRank(g) { return { 'A+': 5, 'A': 4, 'B': 3, 'C': 2, 'D': 1 }[g] || 0; },
    get watchSugMap() { const m = {}; (this.suggestions.items || []).forEach(s => { m[(s.ticker || '').toUpperCase()] = s; }); return m; },
    get watchSectors() {               // sectors by rank (hottest first), each member enriched with its setup grade
      const sm = this.watchSugMap;
      return [...(this.sectorHeat.sectors || [])]
        .sort((a, b) => (a.rank || 999) - (b.rank || 999))
        .map(s => ({ sector: s.sector, tier: s.tier, perf_1d: s.perf_1d,
          members: (s.members || []).map(m => ({ ...m, _sug: sm[(m.ticker || '').toUpperCase()] || {} })) }));
    },
    get watchRows() {                  // FLAT list (all names) enriched + sorted for gainers/losers/grade views
      const sm = this.watchSugMap, rows = [];
      (this.sectorHeat.sectors || []).forEach(s => (s.members || []).forEach(m =>
        rows.push({ ...m, sector: s.sector, _sug: sm[(m.ticker || '').toUpperCase()] || {} })));
      const d = this.watchSort, P = (m, def) => (m.perf_1d != null ? m.perf_1d : def);
      if (d === 'gainers') rows.sort((a, b) => P(b, -999) - P(a, -999));
      else if (d === 'losers') rows.sort((a, b) => P(a, 999) - P(b, 999));
      else if (d === 'grade') rows.sort((a, b) => this.gradeRank(b._sug.grade) - this.gradeRank(a._sug.grade) || (P(b, -999) - P(a, -999)));
      return rows;
    },
    get watchIndexes() {               // SPX/QQQ/IWM (or TA125/TA35) live strip at the top of the drawer
      const map = { us: [['SPX', '^GSPC'], ['QQQ', 'QQQ'], ['IWM', 'IWM']], il: [['TA125', '^TA125.TA'], ['TA35', 'TA35.TA']] };
      const px = this.live.prices || {};
      const stored = {}; (this.market.indexes || []).forEach(i => { stored[i.name] = i; });
      return (map[this.market] || map.us).map(([name, sym]) => {
        const q = px[sym.toUpperCase()] || {};
        const last = q.price != null ? q.price : (stored[name] ? stored[name].close : null);
        const prev = q.prev_close;
        const chg = (last != null && prev != null) ? +(last - prev).toFixed(2) : null;
        let chg_pct = q.change_pct;
        if (chg_pct == null && last != null && prev != null) chg_pct = +((last / prev - 1) * 100).toFixed(2);
        return { name, last, chg, chg_pct, ext_pct: q.ext_change_pct };
      });
    },
    get watchCount() { return (this.sectorHeat.sectors || []).reduce((n, s) => n + (s.members || []).length, 0); },
    memChg(m) {                        // $ change today — live `chg` if present, else derived from close & perf_1d
      if (m.chg != null) return m.chg;
      if (m.close != null && m.perf_1d != null) { const prev = m.close / (1 + m.perf_1d / 100); return +(m.close - prev).toFixed(2); }
      return null;
    },
    async loadGroups() { this.groups = await this.api('/groups'); },
    async detectGroups() {
      const r = await this.api('/groups/detect', 'POST');
      if (r.ok) { this.groups.status = { running: true, done: 0, total: 0, current: 'starting…' }; this.pollGroups(); }
    },
    pollGroups() {
      clearInterval(this._gt);
      this._gt = setInterval(async () => {
        const st = await this.api('/groups/status');
        this.groups.status = st;
        if (!st.running) { clearInterval(this._gt); await this.loadGroups(); }
      }, 1500);
    },
    async refreshSectorHeat() {
      const r = await this.api('/sector-heat/refresh', 'POST');
      if (r.ok) { this.sectorStatus.running = true; this.pollSector(); }
    },
    pollSector() {
      clearInterval(this._st);
      this._st = setInterval(async () => {
        this.sectorStatus = await this.api('/sector-heat/status');
        if (!this.sectorStatus.running) { clearInterval(this._st); await this.loadSectorHeat(); }
      }, 1500);
    },

    // ---------- news ----------
    async loadNews() { this.news = await this.api('/news'); },
    async loadThemes() { this.themesMap = await this.api('/themes'); },
    async refreshNews() {
      const r = await this.api('/news/refresh', 'POST');
      if (r.ok) { this.newsStatus.running = true; this.pollNews(); }
    },
    pollNews() {
      clearInterval(this._nt);
      this._nt = setInterval(async () => {
        this.newsStatus = await this.api('/news/status');
        if (!this.newsStatus.running) { clearInterval(this._nt); await Promise.all([this.loadNews(), this.loadSuggestions()]); }
      }, 1500);
    },
    // ---------- suspicious end-of-day activity ----------
    async loadSuspicious() { this.suspicious = await this.api('/suspicious'); },
    async scanSuspicious() {
      const r = await this.api('/suspicious/scan', 'POST');
      if (r.ok) { this.suspicious.status = { running: true, done: 0, total: 0, current: 'starting…' }; this.pollSuspicious(); }
    },
    pollSuspicious() {
      clearInterval(this._sus);
      this._sus = setInterval(async () => {
        await this.loadSuspicious();
        if (!this.suspicious.status?.running) clearInterval(this._sus);
      }, 1500);
    },
    async loadPremarket() { this.premarket = await this.api('/premarket'); },
    async scanPremarket() {
      const r = await this.api('/premarket/scan', 'POST');
      if (r.ok) { this.premarket.status = { running: true, done: 0, total: 0, current: 'starting…' }; this.pollPremarket(); }
    },
    pollPremarket() {
      clearInterval(this._pm);
      this._pm = setInterval(async () => {
        await this.loadPremarket();
        if (!this.premarket.status?.running) clearInterval(this._pm);
      }, 1500);
    },
    async loadSpinning() { this.spinning = await this.api('/spinning'); },
    async scanSpinning() {
      const r = await this.api('/spinning/scan', 'POST');
      if (r.ok) { this.spinning.status = { running: true, done: 0, total: 0, current: 'starting…' }; this.pollSpinning(); }
    },
    pollSpinning() {
      clearInterval(this._sp);
      this._sp = setInterval(async () => {
        await this.loadSpinning();
        if (!this.spinning.status?.running) clearInterval(this._sp);
      }, 1500);
    },
    spinFiltered() {
      return (this.spinning.spins || []).filter(s =>
        (!this.spinLeadersOnly || s.leader) && (!this.spinRisingOnly || s.rising_sector));
    },
    fmtVol(n) { return n >= 1e6 ? (n / 1e6).toFixed(1) + 'M' : n >= 1e3 ? Math.round(n / 1e3) + 'K' : ('' + (n || 0)); },
    // ---------- "new day" full refresh ----------
    async refreshAll() {
      const r = await this.api('/refresh-all', 'POST');
      if (r.ok) { this.refreshState = { running: true, stage: 'Starting…', done: 0, total: 4 }; this.pollRefresh(); }
    },
    pollRefresh() {
      clearInterval(this._rfa);
      this._rfa = setInterval(async () => {
        this.refreshState = await this.api('/refresh-all');
        if (!this.refreshState.running) { clearInterval(this._rfa); await this.reloadAll(); }
      }, 1500);
    },
    async reloadAll() {
      await Promise.all([this.loadSettings(), this.loadScreeners(), this.loadSuggestions(),
        this.loadTrades(), this.loadWatchlist(), this.loadStats(), this.loadDoc('lessons'),
        this.loadSectorHeat(), this.loadNews(), this.loadMarket(), this.loadUniverse(), this.loadThemes()]);
      this.loadGameplan();
    },
    // ---------- gameplan / prediction helpers ----------
    actionStyle(tone) {
      return ({
        danger: 'background:rgba(255,93,115,.18);color:#ffb3bf;border-color:rgba(255,93,115,.5)',
        warn: 'background:rgba(255,181,61,.18);color:#ffd591;border-color:rgba(255,181,61,.5)',
        good: 'background:rgba(34,224,161,.16);color:#9bf0cf;border-color:rgba(34,224,161,.45)',
        neutral: 'background:rgba(120,140,180,.14);color:#c3cee0;border-color:rgba(120,140,180,.3)',
      })[tone] || 'background:rgba(120,140,180,.14);color:#c3cee0;border-color:rgba(120,140,180,.3)';
    },
    leanColor(lean) {
      if (!lean) return '#93a1b8';
      const s = lean.toLowerCase();
      if (s.includes('bullish') || s === 'likely up') return '#22e0a1';
      if (s.includes('constructive') || s.includes('lean up')) return '#84cc16';
      if (s.includes('neutral') || s.includes('mixed') || s.includes('chop')) return '#eab308';
      if (s.includes('cautious') || s.includes('lean down')) return '#f97316';
      return '#ef4444';   // risk-off / likely down
    },
    driverColor(d) { return d === 'pos' ? '#22e0a1' : d === 'neg' ? '#ff5d73' : '#93a1b8'; },
    lightColor(l) { return l === 'green' ? '#22e0a1' : l === 'yellow' ? '#ffb53d' : '#ff5d73'; },
    // earnings proximity badge text/colour for a suggestion (or null when far out)
    earnBadge(s) {
      const d = s.earnings_days;
      if (d == null || d < 0 || d > 14) return null;
      const est = s.earnings_estimate ? '~' : '';
      return { text: '🗓 ' + est + (d === 0 ? 'today' : d + 'd'),
        soon: d <= 7,
        style: d <= 7 ? 'background:rgba(255,93,115,.18);color:#ffb3bf;border-color:rgba(255,93,115,.5)'
          : 'background:rgba(255,181,61,.16);color:#ffd591;border-color:rgba(255,181,61,.45)' };
    },
    // ---------- market regime helpers ----------
    postureColor(p) {
      if (p == null) return '#64748b';
      if (p >= 75) return '#22c55e';
      if (p >= 55) return '#84cc16';
      if (p >= 40) return '#eab308';
      if (p >= 25) return '#f97316';
      return '#ef4444';
    },
    // Fear & Greed: greed reads as caution (amber/red, frothy), fear as cool (blue/green) —
    // matches the gauge bar's blue→red gradient.
    fgColor(s) {
      if (s == null) return '#64748b';
      if (s >= 75) return '#ff7a8c';
      if (s >= 55) return '#ffb53d';
      if (s > 45) return '#84cc16';
      if (s > 20) return '#22e0a1';
      return '#3b82f6';
    },
    // VIX velocity state → color/icon/label. Mirrors backend scanner.vix_trend(): 6 states keyed off
    // vs_ma20 / 1d/5d % moves (spiking/rising/elevated = caution; elevated-falling = fading; falling/calm = OK).
    // Reused tokens — no new design language.
    vixStateColor(s) {
      return ({ spiking: '#ff5d73', rising: '#ffb53d', elevated: '#ffb53d', 'elevated-falling': '#ffb53d',
                falling: '#22e0a1', calm: '#22e0a1' })[s] || '#64748b';
    },
    vixStateIcon(s) {
      return ({ spiking: '🚨', rising: '📈', elevated: '📈', 'elevated-falling': '📉',
                falling: '📉', calm: '😌' })[s] || '📊';
    },
    vixStateLabel(s) {
      return ({ spiking: 'Panic spike', rising: 'VIX rising', elevated: 'VIX elevated',
                'elevated-falling': 'Elevated, cooling',
                falling: 'VIX falling', calm: 'VIX calm' })[s] || s;
    },
    stateEmoji(s) {
      return ({ 'Healthy uptrend': '🟢', 'Recovery': '🟢', 'Bottoming / turning up': '🟢',
        'Extended': '🟠', 'Pullback': '🟡', 'Mid-correction': '🔻', 'Deep correction': '🔻' })[s] || '➖';
    },
    // color a regime STATE by its meaning (matches stateEmoji) — NOT by raw posture, so an
    // "Extended" index reads amber even though its posture number (55) sits in the green band.
    stateColor(s) {
      return ({ 'Healthy uptrend': '#22c55e', 'Recovery': '#22c55e', 'Bottoming / turning up': '#22c55e',
        'Extended': '#f59e0b', 'Pullback': '#eab308', 'Mid-correction': '#f97316', 'Deep correction': '#ef4444' })[s] || '#64748b';
    },
    get majorNews() { return this.news.macro || []; },
    // leader-in-group medal: 🥇 for the strongest name in a theme (by RS), 🥈🥉 for a sizeable group
    groupMedal(s) {
      if (!s.group_rank || !(s.group_size >= 2)) return '';
      if (s.group_rank === 1) return '🥇';
      if (s.group_size >= 4 && s.group_rank <= 3) return s.group_rank === 2 ? '🥈' : '🥉';
      return '';
    },

    // is current price already inside the buy zone (fillable now)?
    inZone(s) {
      if (s.buyable_now != null) return s.buyable_now;
      if ((s.parabolic && !s.worth_waiting) || s.distribution_today || s.extended) return false;
      return s.entry_type === 'limit' ? s.close <= s.entry : s.close >= s.entry;
    },
    // ---- LIVE confirmation status (ties a suggestion to the confirmation engine /api/now) ----
    // 'confirmed' = the engine saw the trigger taken out (it's in now.buys); 'armed' = lined up &
    // watched (now.armed); 'waiting' = not confirmed (not watched, faded, or market closed). A setup is
    // only a real "BUYABLE NOW" when it's CONFIRMED *and* price is in the zone — being in the zone alone
    // is NOT a buy (it just means price reached support; wait for the trigger). This kills the old
    // "buyable now just because price is in the zone" that fed chasing.
    confStatus(s) {
      const n = this.now; if (!n || !s) return 'waiting';
      if ((n.buys || []).some(b => b.ticker === s.ticker)) return 'confirmed';
      if ((n.armed || []).some(a => a.ticker === s.ticker)) return 'armed';
      return 'waiting';
    },
    confRec(s) {
      const n = this.now; if (!n || !s) return null;
      return (n.buys || []).find(b => b.ticker === s.ticker)
        || (n.armed || []).find(a => a.ticker === s.ticker) || null;
    },
    // the green "BUYABLE NOW" only when the engine CONFIRMED the trigger AND price is in this leg's zone
    isConfirmedBuyable(s, e) { return !!(e && e.buyable_now && this.confStatus(s) === 'confirmed'); },
    // typed status line for an entry plan: a buy-STOP is an upside trigger ("break above"),
    // a LIMIT is a dip to support ("wait for the pullback") — never "pull back" for a stop.
    // Prefer the plan's own trigger_note (set by the engine / the live rotation) when present.
    entryHint(e, close) {
      const cur = this.cur;
      const now = close != null ? ` (now ${cur}${close})` : '';
      // STALE: price has run far above this dip entry — say so plainly instead of "wait for the pullback".
      if (e.stale) {
        const pct = (close && e.entry) ? Math.round((close / e.entry - 1) * 100) : null;
        return `⛔ broke out & ran${pct != null ? ` ~${pct}% above` : ' past'} this dip (${cur}${e.entry}) — unlikely to fill; graded as a chase`;
      }
      if (e.trigger_note) return ((e.entry_type === 'stop' ? '▲ ' : '⏳ ') + e.trigger_note + now).replaceAll('$', cur);
      if (e.entry_type === 'stop') return `▲ break above ${cur}${e.entry} to trigger${now}`;
      return `⏳ wait for the pullback to ${cur}${e.entry}${now}`;
    },
    // The single condition line for a setup's status panel — same content as entryHint but with the
    // leading ⏳/▲/⛔ icon stripped (the panel already shows ONE state label + pulsing dot, so the icon
    // would be a redundant double-hourglass). Kills the "⏳ Waiting · ⏳ wait for the pullback" repeat.
    condText(e, close) { return this.entryHint(e, close).replace(/^[⏳▲⛔]\s*/, ''); },
    // The status panel's state for a setup leg → { label, cls } where cls ∈ ''(amber waiting) | 'grn' | 'red'.
    // 'BUYABLE NOW' (green) only when the engine confirmed; 'In the zone' amber when fillable but unconfirmed;
    // 'Ran away' red for a stale dip; otherwise 'Waiting'. One label, never doubled.
    setupState(s, e) {
      if (e.stale) return { label: 'Ran away', cls: 'red' };
      const nr = (this.nowByTicker()[s.ticker] || {});
      if (nr.state === 'early' && e.buyable_now) return { label: 'Early entry', cls: 'early' };
      if (this.isConfirmedBuyable(s, e)) return { label: 'Buyable now', cls: 'grn' };
      if (e.buyable_now) return { label: 'In the zone', cls: '' };
      return { label: 'Waiting', cls: '' };
    },
    // LIVE intraday rotation: once the market's open and a (non-patient) name has broken out and
    // sits ABOVE its daily pullback zone, the realistic pullback entry is the rotation — buy the
    // reclaim of the PRIOR-DAY high, stop at TODAY's low (both update through the session). Returns
    // the rotation levels for the name's pullback option, or null (keep the daily dip zone).
    rotationFor(s) {
      if (!s || s.worth_waiting || !s.prior_high) return null;        // patient deep-dip setups keep their zone
      const q = (this.live.prices || {})[s.ticker];
      if (!q || q.market_state !== 'REGULAR' || q.day_low == null || q.price == null) return null;
      const pb = (s.entries || []).find(e => e.kind === 'pullback');
      const zoneTop = pb ? (pb.zone_top != null ? pb.zone_top : pb.entry) : s.zone_top;
      if (zoneTop != null && q.price <= zoneTop) return null;          // not above the dip zone → keep daily zone
      const entry = s.prior_high;
      const adrPx = entry * (s.adr || 0) / 100 || 0.01;
      // it must actually be PULLING BACK to the prior-day high — i.e. price is at/below it (so the
      // reclaim buy-stop sits above price). A name making new highs far above the prior-day high
      // isn't pulling back, so no rotation (it keeps its breakout option / daily dip zone).
      if (q.price > entry + 0.3 * adrPx) return null;
      let stop = q.day_low;
      if (entry - stop < 0.3 * adrPx) stop = +(entry - 0.3 * adrPx).toFixed(2);   // floor so a gap-up isn't ~0 risk
      stop = +stop.toFixed(2);
      const riskPs = +(entry - stop).toFixed(2);
      if (riskPs <= 0) return null;
      const status = q.price < entry ? 'below' : (q.price <= entry + 0.3 * adrPx ? 'reclaim' : 'extended');
      return {
        entry: +entry.toFixed(2), stop, risk_ps: riskPs, wide: riskPs > 1.0 * adrPx,
        zone_bottom: +entry.toFixed(2), zone_top: +(entry + 0.3 * adrPx).toFixed(2),
        target: +(entry + 2 * riskPs).toFixed(2),
        buyable_now: status === 'reclaim', status,
      };
    },
    // merge a live rotation onto the pullback entry for display (entry/stop/zone/sizing + phrasing)
    _applyRot(s, e, rot) {
      const acct = this.settings.account_size, rp = this.settings.risk_pct || 1, mp = this.settings.max_position_pct || 15;
      let shares = null;
      if (acct && rot.risk_ps > 0) shares = Math.max(0, Math.min(
        Math.floor(acct * rp / 100 / rot.risk_ps), Math.floor(acct * mp / 100 / rot.entry), Math.floor(acct / rot.entry)));
      const note = rot.status === 'reclaim'
        ? `rotating above the prior-day high $${rot.entry} — reclaim · stop = day low $${rot.stop}`
        : rot.status === 'below'
          ? `buy the reclaim of the prior-day high $${rot.entry} · stop = day low $${rot.stop}`
          : `above the prior-day-high trigger $${rot.entry} — wait for a pullback to it · stop = day low $${rot.stop}`;
      return { ...e, ...rot, entry_type: 'stop', rotation: true, shares, stale: false,
        trigger_note: note, entry_note: 'intraday rotation: reclaim the prior-day high, stop at the day low' };
    },
    // LIVE "pullback catch of the breakout" for patient deep-dip setups (Deep Pullback / Consolidation):
    // once the market's open and a strong leader has ALREADY BROKEN OUT above its deep dip zone, a
    // pullback all the way to the 50 EMA is unlikely. The realistic dip is a SHALLOW pullback to the
    // nearest rising EMA (10/20) — so swap the deep zone for a catch there (buy-limit, ≤1× ADR stop).
    // Live/market-hours only; EOD the deep zone stands. Returns the catch levels or null.
    breakoutCatchFor(s) {
      if (!s || !s.worth_waiting) return null;
      const q = (this.live.prices || {})[s.ticker];
      if (!q || q.market_state !== 'REGULAR' || q.price == null) return null;
      const price = q.price;
      const pb = (s.entries || []).find(e => e.kind === 'pullback');
      const zoneTop = pb ? (pb.zone_top != null ? pb.zone_top : pb.entry) : s.zone_top;
      if (zoneTop == null || price <= zoneTop) return null;          // not broken out → keep the deep zone
      const adrPx = price * (s.adr || 0) / 100 || 0.01;
      const below = [['10 EMA', s.ema10], ['20 EMA', s.ema20]].filter(([l, v]) => v && v <= price - 0.2 * adrPx);
      if (!below.length) return null;            // price hugging the EMAs → no meaningful dip to catch
      const [lbl, lvl] = below.reduce((a, b) => (b[1] > a[1] ? b : a));   // highest (nearest) EMA below price
      const entry = +(+lvl).toFixed(2);
      const stop = +(entry - adrPx).toFixed(2);  // ≤1× ADR below the catch EMA
      const riskPs = +(entry - stop).toFixed(2);
      if (riskPs <= 0) return null;
      return { entry, stop, risk_ps: riskPs, lbl, status: 'catch', buyable_now: false,
        zone_bottom: entry, zone_top: +(entry + 0.4 * adrPx).toFixed(2), target: +(entry + 2 * riskPs).toFixed(2) };
    },
    // merge a live breakout-catch onto the pullback entry for display (entry/stop/zone/sizing + phrasing)
    _applyCatch(s, e, cat) {
      const acct = this.settings.account_size, rp = this.settings.risk_pct || 1, mp = this.settings.max_position_pct || 15;
      let shares = null;
      if (acct && cat.risk_ps > 0) shares = Math.max(0, Math.min(
        Math.floor(acct * rp / 100 / cat.risk_ps), Math.floor(acct * mp / 100 / cat.entry), Math.floor(acct / cat.entry)));
      // GRADE: the catch is NOT the original deep pullback (its A grade) — it's a pullback INTO a
      // breakout, so grade it off the confirmation/breakout option + one notch ("a +"): better than
      // chasing the confirmed breakout (you get a dip entry), but not the deep-pullback's A.
      const base = (s.entries || []).find(x => x.kind === 'confirm') || (s.entries || []).find(x => x.kind === 'breakout');
      let grade = e.grade, rating = e.rating;
      if (base && base.grade) {
        grade = this._plusGrade(base.grade);
        rating = Math.min((base.rating || 63) + 5, 81);     // a notch up, never crossing into A+ territory
      } else if (e.rating != null) {                         // no momentum option to anchor on -> demote a notch
        rating = Math.max(52, e.rating - 10); grade = this.gradeLetter(rating);
      }
      return { ...e, ...cat, entry_type: 'limit', breakoutCatch: true, shares, grade, rating, stale: false,
        trigger_note: `broke out — catch a dip to the ${cat.lbl} $${cat.entry} (the deep dip is now unlikely) · stop $${cat.stop}`,
        entry_note: `pullback catch of today's breakout — buy the dip to the ${cat.lbl}` };
    },
    // the entries to render for a card/chart — the pullback option swapped for the live rotation
    // when one applies. Falls back to a single synthesized entry for legacy items without `entries`.
    displayEntries(s) {
      const entries = (s.entries && s.entries.length) ? s.entries : [{
        kind: (s.entry_type === 'stop' ? 'breakout' : 'pullback'), entry_type: s.entry_type,
        entry: s.entry, stop: s.stop, target: s.target, zone_bottom: s.zone_bottom,
        zone_top: s.zone_top, buyable_now: s.buyable_now, shares: s.shares, risk_ps: s.risk_ps,
        trigger_note: s.trigger_note,
      }];
      const rot = this.rotationFor(s);                 // non-patient: reclaim the prior-day high
      const cat = rot ? null : this.breakoutCatchFor(s); // patient + broke out: shallow catch to nearest EMA
      let list = [];
      entries.forEach(e => {
        if (e.kind === 'pullback') {
          if (cat) {                                   // broke out intraday: the catch is the live entry,
            list.push(this._applyCatch(s, e, cat));    // and it becomes the primary (carries the grade);
            list.push({ ...e, stale: true });          // the original deep dip has RUN AWAY → keep it as a
          } else if (rot) {                            // dimmed secondary so you can still see it.
            list.push(this._applyRot(s, e, rot));
          } else {
            list.push(e);
          }
        } else {                                       // breakout secondary (break above the prior-day high)
          list.push(e);
        }
      });
      // Ran-away (stale) legs sink below the actionable ones, so the breakout / real entry is the primary
      // and the ran-away dip shows dimmed beneath it.
      if (list.some(e => e.stale)) list = [...list.filter(e => !e.stale), ...list.filter(e => e.stale)];
      return list;
    },
    // Has the patient dip RUN AWAY? (the pullback leg is stale → price left it behind). Used to keep
    // setup-state tags/filters honest: a name whose deep dip ran away is no longer "worth waiting".
    dipRanAway(s) { return (s.entries || []).some(e => e.kind === 'pullback' && e.stale); },
    // Is this still a live "worth waiting" patient setup? Only if it's flagged AND its dip hasn't run away.
    isWorthWaiting(s) { return !!s.worth_waiting && !this.dipRanAway(s); },
    // The card's headline grade = the AVERAGE of the actionable entries on offer, mirroring the backend
    // (so display and sort agree). Reads the legs ACTUALLY shown (displayEntries applies the catch and
    // flags ran-away dips) and drops the ran-away ones — so a great-but-unlikely entry can't carry the
    // card and a run-away dip can't either. Uses per-leg grades the backend already set (no rubric dup).
    cardGrade(s) {
      const pool = this.displayEntries(s).filter(e => e.rating != null && !e.stale);
      if (!pool.length) return { grade: s.grade, rating: s.rating };
      const avg = Math.round(pool.reduce((sum, e) => sum + e.rating, 0) / pool.length);
      return { grade: this.gradeLetter(avg), rating: avg };
    },
    // LIVE: a confirmation entry is a breakout buy-STOP — once it triggers the real invalidation is the
    // DAY'S LOW (same as the rotation), not a fixed 1× ADR stop. While the market's open, tighten the
    // confirm stop to today's low (floored so a gap isn't ~0 risk; never WIDER than the 1× ADR stop).
    _confirmDayStop(s, e) {
      const q = (this.live.prices || {})[s.ticker];
      if (!q || q.market_state !== 'REGULAR' || q.day_low == null || e.entry == null) return e;
      const entry = e.entry, adrPx = entry * (s.adr || 0) / 100 || 0.01;
      let stop = Math.max(q.day_low, e.stop);                       // tighten to the day low, but never wider than 1× ADR
      if (entry - stop < 0.3 * adrPx) stop = entry - 0.3 * adrPx;   // floor so a gap-up isn't ~0 risk
      stop = +stop.toFixed(2);
      const risk = +(entry - stop).toFixed(2);
      if (risk <= 0) return e;
      const acct = this.settings.account_size, rp = this.settings.risk_pct || 1, mp = this.settings.max_position_pct || 15;
      let shares = null;
      if (acct && risk > 0) shares = Math.max(0, Math.min(
        Math.floor(acct * rp / 100 / risk), Math.floor(acct * mp / 100 / entry), Math.floor(acct / entry)));
      return { ...e, stop, risk_ps: risk, shares, dayLowStop: true,
        trigger_note: (e.trigger_note || '') + ` · stop = day low $${stop}` };
    },

    // ---------- watchlist ----------
    async saveWatchlist() {
      await this.api('/watchlist', 'PUT', { rows: this.watchlist });
    },
    async addWatch() {
      const t = (this.newWatch || '').trim().toUpperCase(); if (!t) return;
      if (!this.watchlist.find(r => r.ticker === t)) this.watchlist.push({ ticker: t, why: '', level: '', setup: '', catalyst: '' });
      this.newWatch = '';
      await this.saveWatchlist();
      delete this.wlData[t];
      await this.loadWatchlistAnalysis();
    },
    removeWatch(i) {
      const t = this.watchlist[i].ticker; this.watchlist.splice(i, 1);
      if (this._wlCharts[t]) { try { this._wlCharts[t].remove(); } catch (e) {} delete this._wlCharts[t]; }
      this.saveWatchlist();
    },
    openCalc(o) { this.calc.entry = (o && o.entry) || null; this.calc.stop = (o && o.stop) || null; this.calcModal = { open: true, ticker: (o && o.ticker) || '' }; },
    toggleSector(name) { this.secOpen[name] = !this.secOpen[name]; },
    async loadWatchlistAnalysis() {
      this.wlLoading = true;
      for (const r of this.watchlist) {
        if (!this.wlData[r.ticker]) {
          try { const d = await this.api('/analyze/' + r.ticker); if (d.analysis) this.wlData[r.ticker] = { ...d.analysis, bars: d.bars }; } catch (e) {}
        }
      }
      this.wlLoading = false;
      await this.$nextTick();
      this.renderWatchlistCharts();
    },
    renderWatchlistCharts() {
      this._wlObs.forEach(o => { try { o.disconnect(); } catch (e) {} });
      this._wlObs = [];
      for (const r of this.watchlist) {
        const data = this.wlData[r.ticker]; const box = document.getElementById('wlc-' + r.ticker);
        if (!box) continue;
        if (this._wlCharts[r.ticker]) { try { this._wlCharts[r.ticker].remove(); } catch (e) {} }
        box.innerHTML = '';
        if (!data || !data.bars) continue;
        const chart = LightweightCharts.createChart(box, {
          width: box.clientWidth, height: box.clientHeight,
          layout: { background: { color: 'transparent' }, textColor: '#6b7890' },
          grid: { vertLines: { visible: false }, horzLines: { visible: false } },
          rightPriceScale: { visible: false }, timeScale: { visible: false },
          handleScroll: false, handleScale: false,
        });
        const ro = new ResizeObserver(() => { try { chart.resize(box.clientWidth, box.clientHeight); } catch (e) {} });
        ro.observe(box); this._wlObs.push(ro);
        const s = chart.addCandlestickSeries({ upColor: '#22e0a1', downColor: '#ff5d73', borderVisible: false, wickUpColor: '#22e0a1', wickDownColor: '#ff5d73' });
        const bars = data.bars.slice(-60);
        s.setData(bars.map(b => ({ time: b.time, open: b.open, high: b.high, low: b.low, close: b.close })));
        const k = 2 / 10; let p = bars[0].close; const arr = [];
        for (const b of bars) { p = b.close * k + p * (1 - k); arr.push({ time: b.time, value: +p.toFixed(2) }); }
        chart.addLineSeries({ color: '#eab308', lineWidth: 2, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false }).setData(arr);
        if (data.entry) s.createPriceLine({ price: data.entry, color: '#22e0a1', lineWidth: 1, lineStyle: 2, axisLabelVisible: false });
        if (data.stop) s.createPriceLine({ price: data.stop, color: '#ff5d73', lineWidth: 1, lineStyle: 2, axisLabelVisible: false });
        chart.timeScale().fitContent();
        this._wlCharts[r.ticker] = chart;
      }
    },

    // ---------- trades / journal ----------
    async addTrade() {
      if (!this.newTrade.ticker) return;
      await this.api('/trades', 'POST', { ...this.newTrade, ticker: this.newTrade.ticker.toUpperCase() });
      this.newTrade = { ticker: '', setup_type: 'Breakout', entry: null, stop: null, shares: null, notes: '' };
      await Promise.all([this.loadTrades(), this.loadStats()]);
    },
    async uploadShot(t, ev) {
      const file = ev.target.files[0]; if (!file) return;
      const data = await new Promise(res => { const fr = new FileReader(); fr.onload = () => res(fr.result); fr.readAsDataURL(file); });
      await this.api('/upload', 'POST', { trade_id: t.id, filename: file.name, data });
      await this.loadTrades();
    },

    // ---------- settings & docs ----------
    async saveSettings() {
      await this.api('/settings', 'PUT', { account_size: this.settings.account_size, risk_pct: this.settings.risk_pct, max_position_pct: this.settings.max_position_pct, size_factor: this.settings.size_factor, telegram_token: this.settings.telegram_token, telegram_chat_id: this.settings.telegram_chat_id, briefing_enabled: this.settings.briefing_enabled });
      await Promise.all([this.loadSuggestions(), this.loadTrades()]);
      this.flash('Saved');
    },
    async saveDoc() {
      await this.api('/docs/' + this.docTab, 'PUT', { content: this.docEdit });
      if (this.docTab === 'lessons') this.docs.lessons = this.docEdit;
      this.flash('Saved');
    },
    flash(m) { this.savedMsg = m; setTimeout(() => this.savedMsg = '', 1500); },
    async testTelegram() {
      this.tgMsg = 'sending…';
      try { await this.saveSettings(); const r = await this.api('/telegram/test', 'POST', {});
        this.tgMsg = r.ok ? '✅ sent — check your phone' : ('✗ ' + (r.error || 'failed'));
      } catch (e) { this.tgMsg = '✗ failed'; }
      setTimeout(() => this.tgMsg = '', 6000);
    },

    // ---------- charts ----------
    // % actually made on a forward pick (the trader thinks in % gained, not R). The sim gives R =
    // (exit − entry)/risk, so % = R × (entry − stop)/entry × 100. null until it matures.
    fwdPct(R, entry, stop) {
      if (R == null || !entry || !stop || entry <= stop) return null;
      return +(R * (entry - stop) / entry * 100).toFixed(1);
    },
    // average % across a day's picks that actually filled (null until at least one fills)
    fwdAvgPct(picks) {
      const ps = (picks || []).map(p => this.fwdPct(p.R, p.entry, p.stop)).filter(v => v != null);
      if (!ps.length) return null;
      return +(ps.reduce((a, b) => a + b, 0) / ps.length).toFixed(1);
    },
    // `meta` (optional) flags a FORWARD-TEST snapshot view: {frozen:true, date, frozenAt, status, R, ...}.
    // It makes the chart show the ORIGINAL frozen levels with a signal marker + outcome, so it reads as
    // "here's the setup I gave you and how it did", not a fresh live setup.
    async showChart(ticker, obj, meta) {
      this.chartModal.open = true; this.chartModal.ticker = ticker;
      this.chartModal._obj = obj || null;
      this.chartModal._meta = meta || null;
      this.chartModal._showLive = false;            // forward snapshots default to the FROZEN view
      this.chartModal.entryIdx = 0;                 // default to the primary setup
      await this.$nextTick();
      this.chartModal._data = await this.api('/chart/' + ticker);
      this.renderChart();
      if (this.liveOn && this.marketOpen) this.tickLive();   // pull this symbol's live price now
    },
    renderChart() {
      const box = document.getElementById('chartBox');
      if (!box || !this.chartModal._data) return;
      if (this.chartModal._ro) { try { this.chartModal._ro.disconnect(); } catch (e) {} this.chartModal._ro = null; }
      if (this.chartModal._chart) { try { this.chartModal._chart.remove(); } catch (e) {} this.chartModal._chart = null; }
      box.innerHTML = '';
      const data = this.chartModal._data;
      // Forward-snapshot view: FROZEN draws the entry/stop I gave; LIVE drops the setup and instead
      // compares the ENTRANCE (frozen entry) to where price is NOW — the idea's progress.
      const meta = this.chartModal._meta;
      const isSnapshot = !!(meta && meta.frozen);
      const showLive = isSnapshot && this.chartModal._showLive;
      const obj = this.chartModal._obj;
      const chart = LightweightCharts.createChart(box, {
        width: box.clientWidth, height: box.clientHeight,
        layout: { background: { color: '#131722' }, textColor: '#94a3b8' },
        grid: { vertLines: { color: '#1c2230' }, horzLines: { color: '#1c2230' } },
        timeScale: { borderColor: '#2a3344' },
        rightPriceScale: { borderColor: '#2a3344', mode: this.chartModal.logScale ? 1 : 0 },
      });
      const ro = new ResizeObserver(() => { try { chart.resize(box.clientWidth, box.clientHeight); } catch (e) {} });
      ro.observe(box);
      this.chartModal._ro = ro;
      const series = chart.addCandlestickSeries({
        upColor: '#22c55e', downColor: '#ef4444', borderVisible: false,
        wickUpColor: '#22c55e', wickDownColor: '#ef4444',
        lastValueVisible: false,        // hide the green/red price tag — we mark price in WHITE below
      });
      const bars = data.bars || [];
      series.setData(bars.map(b => ({ time: b.time, open: b.open, high: b.high, low: b.low, close: b.close })));
      this.chartModal._series = series;          // kept so the live tick can move today's candle
      this.chartModal._bars = bars;
      // volume pane (histogram, pinned to the bottom ~20% on its own hidden scale)
      if (this.chartModal.showVolume) {
        series.priceScale().applyOptions({ scaleMargins: { top: 0.06, bottom: 0.24 } });
        const vol = chart.addHistogramSeries({ priceScaleId: 'vol', priceFormat: { type: 'volume' }, lastValueVisible: false, priceLineVisible: false });
        chart.priceScale('vol').applyOptions({ scaleMargins: { top: 0.80, bottom: 0 } });
        vol.setData(bars.map(b => ({ time: b.time, value: b.volume,
          color: b.close >= b.open ? 'rgba(34,194,87,.45)' : 'rgba(239,68,68,.45)' })));
      }
      // 9 / 21 / 50 EMA overlays (toggleable)
      if (this.chartModal.showEmas) {
        const emaCfg = [[9, '#eab308'], [21, '#f59e0b'], [50, '#5b8cff']];
        for (const [p, color] of emaCfg) {
          if (bars.length < p) continue;
          const k = 2 / (p + 1); let prev = bars[0].close; const arr = [];
          for (const b of bars) { prev = b.close * k + prev * (1 - k); arr.push({ time: b.time, value: +prev.toFixed(2) }); }
          const ls = chart.addLineSeries({ color, lineWidth: p === 9 ? 2 : 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
          ls.setData(arr);
        }
      }
      // AVWAP curves — anchored at all-time high (violet) and last earnings gap (cyan) (toggleable)
      if (this.chartModal.showAvwap) {
        const avwap = (anchor) => {
          let num = 0, den = 0; const a = [];
          for (let i = anchor; i < bars.length; i++) {
            const b = bars[i]; const tp = (b.high + b.low + b.close) / 3;
            num += tp * b.volume; den += b.volume;
            a.push({ time: b.time, value: den ? +(num / den).toFixed(2) : b.close });
          }
          return a;
        };
        if (bars.length) {
          let athI = 0; for (let i = 1; i < bars.length; i++) if (bars[i].high > bars[athI].high) athI = i;
          chart.addLineSeries({ color: '#a855f7', lineWidth: 2, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false }).setData(avwap(athI));
          let eI = -1, eg = 0; const st0 = Math.max(1, bars.length - 75);
          for (let i = st0; i < bars.length; i++) { const g = (bars[i].open / bars[i - 1].close - 1) * 100; if (g > eg) { eg = g; eI = i; } }
          if (eg >= 6 && eI > 0) chart.addLineSeries({ color: '#22d3ee', lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false }).setData(avwap(eI));
        }
      }
      // (Bull-flag / pennant / wedge + regression-channel pattern overlay REMOVED 2026-06-09 — the trader
      // found it noisy and unactionable for this strategy. The descending-resistance "wall" line below stays.)
      // DESCENDING-trendline resistance (the DOCN "clear the wall" line) — a dashed RED line from the peak to
      // today so the trader SEES the line the buy is waiting to clear. lastValueVisible shows the today level
      // as a price label on the axis. (Mobile: thin 2px line, no crosshair marker.)
      if (data.res_trendline && data.res_trendline.p0_time) {
        const rt = data.res_trendline;
        chart.addLineSeries({ color: '#ef4444', lineWidth: 2, lineStyle: 2, priceLineVisible: false, lastValueVisible: true, crosshairMarkerVisible: false, title: 'resistance — wait for the break' })
          .setData([{ time: rt.p0_time, value: rt.p0_val }, { time: rt.p1_time, value: rt.p1_val }]);
      }
      // buy-zone band (green) + stop (red) for the SELECTED setup only (toggle switches setups).
      // The pullback option may be a LIVE rotation (reclaim the prior-day high, stop = day low) —
      // its stop line is tracked so the live tick can move it with today's low.
      const pl = (p, c, w, style, title) => p ? series.createPriceLine({ price: p, color: c, lineWidth: w, lineStyle: style, axisLabelVisible: true, title }) : null;
      this.chartModal._rotStop = null;
      // signal bar = the prior close the snapshot was frozen at (last bar BEFORE the act-session date)
      let sigTime = null;
      if (isSnapshot && meta.date) {
        const prior = bars.filter(b => b.time < meta.date);
        sigTime = prior.length ? prior[prior.length - 1].time : meta.date;
      }
      const lastT = bars.length ? bars[bars.length - 1].time : null;
      // draw a level as a RAY from the signal forward (the actionable window only) — so candles BEFORE
      // the freeze don't look like fills (the entry/stop only apply after the setup was frozen). This is
      // the confusion: a pullback's dip to the entry often happened on/before the signal day, not after.
      const ray = (val, color, title) => {
        if (val == null || !sigTime || !lastT) return;
        chart.addLineSeries({ color, lineWidth: 2, lastValueVisible: true, priceLineVisible: false, crosshairMarkerVisible: false, title })
          .setData([{ time: sigTime, value: val }, { time: lastT, value: val }]);
      };
      if (isSnapshot) {
        // FROZEN = the entry/stop I gave; LIVE = entrance vs current price (the idea's PROGRESS, no setup)
        const entry = meta.entry, stop = meta.stop;
        const cur = ((this.live.prices || {})[this.chartModal.ticker] || {}).price
          || (bars.length ? bars[bars.length - 1].close : null);
        if (!showLive) {
          ray(entry, '#22e0a1', 'Snapshot entry');
          ray(stop, '#ff5d73', 'Snapshot stop');
        } else {
          ray(entry, '#22e0a1', 'Entrance');
          if (cur && entry) {
            const prog = +(((cur - entry) / entry) * 100).toFixed(1);
            pl(+cur.toFixed(2), '#22d3ee', 2, 0, 'Now (' + (prog >= 0 ? '+' : '') + prog + '%)');
          }
        }
      } else if (obj) {
        const dEntries = this.displayEntries(obj);
        const idx = dEntries.length ? Math.min(this.chartModal.entryIdx || 0, dEntries.length - 1) : 0;
        const e = dEntries.length ? dEntries[idx] : obj;
        // draw ONLY the selected setup's levels (the toggle above switches setups) — drawing the other
        // setup faintly cluttered the view, especially with the confirm/pullback levels close together.
        if (e.zone_top && e.zone_bottom) {
          pl(e.zone_top, '#22e0a1', 1, 2, e.rotation ? 'Reclaim ≤' : 'Buy ≤');
          pl(e.zone_bottom, '#22e0a1', 2, 0, e.rotation ? 'Reclaim (prior-day high)' : 'Buy ≥');
        } else if (e.entry || obj.entry || obj.planned_entry) {
          pl(e.entry || obj.entry || obj.planned_entry, '#22e0a1', 2, 0, 'Entry');
        }
        const stopLine = pl(e.stop || obj.stop, '#ff5d73', 1, 2, (e.rotation || e.dayLowStop) ? 'Stop (day low)' : 'Stop');
        if ((e.rotation || e.dayLowStop) && stopLine) {     // keep the day-low stop tracking today's low live
          this.chartModal._rotStop = stopLine;
          this.chartModal._rotEntry = e.entry;
          this.chartModal._rotAdrPx = (e.entry || 0) * (obj.adr || 0) / 100 || 0.01;
        }
        // current price marked in WHITE (neutral) so it never reads as a green buy / red stop level
        const _px = ((this.live.prices || {})[this.chartModal.ticker] || {}).price
          || (bars.length ? bars[bars.length - 1].close : null);
        if (_px) this.chartModal._priceLine = series.createPriceLine({ price: +(+_px).toFixed(2), color: '#ffffff', lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: '' });
      }
      // forward-test snapshot: drop a marker at the SIGNAL bar so you can watch the price action AFTER it
      // (the entry/stop rays start here — fills only count from this bar forward).
      if (isSnapshot && sigTime) {
        try { series.setMarkers([{ time: sigTime, position: 'belowBar', color: '#22d3ee', shape: 'arrowUp', text: 'signal' }]); } catch (e) {}
      }
      chart.timeScale().fitContent();
      this.chartModal._chart = chart;
    },
    closeChart() {
      if (this.chartModal._ro) { try { this.chartModal._ro.disconnect(); } catch (e) {} this.chartModal._ro = null; }
      if (this.chartModal._chart) { this.chartModal._chart.remove(); this.chartModal._chart = null; }
      this.chartModal._series = null; this.chartModal._bars = null; this.chartModal._rotStop = null; this.chartModal._priceLine = null;
      this.chartModal._meta = null; this.chartModal._showLive = false;
      this.chartModal.open = false;
      if (this.chartOnly) this.chartOnly = false;   // chart-only deep-link: closing reveals the app on suggestions, never the dashboard
    },
  };
}
window.dataCenter = dataCenter;
