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

function dataCenter() {
  return {
    nav: [
      { id: 'dashboard', label: 'Dashboard', icon: '🏠' },
      { id: 'suggestions', label: 'Suggestions', icon: '🎯' },
      { id: 'screeners', label: 'Screeners', icon: '🗂️' },
      { id: 'watchlist', label: 'Watchlist', icon: '👁️' },
      { id: 'journal', label: 'Journal', icon: '📓' },
      { id: 'news', label: 'News', icon: '📰' },
      { id: 'stats', label: 'Stats', icon: '📈' },
      { id: 'strategy', label: 'Strategy & Rules', icon: '📚' },
    ],
    view: 'dashboard',
    mobileNav: false,
    hosted: false,
    // pages hidden on the hosted free service (no persistent storage there, so they can't save)
    hostedHidden: ['journal', 'watchlist', 'strategy'],
    settings: { account_size: null, risk_pct: 1 },
    screeners: [],
    suggestions: { items: [] },
    trades: [],
    watchlist: [],
    stats: {},
    docs: { lessons: '' },
    scan: { running: false, done: 0, total: 0 },
    scanScreener: '',
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
    forward: null,
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
    showAllSug: false,
    calcModal: { open: false, ticker: '' },
    chartModal: { open: false, ticker: '', _chart: null, logScale: true, showChannel: true, showEmas: true, showAvwap: true, showVolume: true, _data: null, _obj: null },
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
      this.scalePct = parseInt(localStorage.getItem('dc_scale')) || 125;
      this.applyScale();
      window.addEventListener('resize', () => { clearTimeout(this._rt); this._rt = setTimeout(() => this.onResize(), 120); });
      try { this.hosted = !!(await this.api('/env')).hosted; } catch (e) {}
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
    },

    // ---------- live updates (free Yahoo quotes, polled while the market is open) ----------
    get marketOpen() { return ['PRE', 'PREPRE', 'REGULAR', 'POST', 'POSTPOST'].includes(this.live.market_state); },
    get liveLabel() {
      if (!this.liveOn) return 'LIVE off';
      if (!this.live.market_state) return 'LIVE…';
      const m = { REGULAR: 'OPEN', PRE: 'PRE', PREPRE: 'PRE', POST: 'AFTER', POSTPOST: 'AFTER', CLOSED: 'CLOSED' }[this.live.market_state] || this.live.market_state;
      return 'LIVE · ' + m + (this.live.updated_at ? ' · ' + this.liveAgeSec + 's' : '');
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
    },
    async tickLive() {
      if (!this.liveOn) return;
      const syms = this.liveSymbols();
      try {
        const r = await this.api('/live?symbols=' + encodeURIComponent(syms.join(',')));
        this.live = { prices: r.prices || {}, market_state: r.market_state, updated_at: r.updated_at, posture: r.posture };
        this._liveAt = Date.now(); this.liveAgeSec = 0;
        this.mergeLive();
        // live Sector Heat — only while viewing that tab (it fetches all member quotes), throttled ~60s
        if (this.view === 'screeners' && this.screenTab === 'heat' && this.marketOpen
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
      const trail = patient ? e50 : e9, trailLabel = patient ? '50 EMA' : '9 EMA';
      const adr = (c.ext9_adr ? Math.abs(c.ext9 / c.ext9_adr) : null);
      const ext9 = e9 ? (price / e9 - 1) * 100 : null;
      const ext9_adr = (ext9 != null && adr) ? ext9 / adr : null;
      const edays = c.earnings_days, earnSoon = edays != null && edays >= 0 && edays <= 7;
      const rtxt = r != null ? ((r >= 0 ? '+' : '') + r.toFixed(1) + 'R') : '';
      const rsuffix = rtxt ? ' (' + rtxt + ')' : '';
      let action, tone, reason;
      if (price <= stop) {
        if (bePlus) { action = 'EXIT'; tone = 'warn'; reason = `stop $${stop} is your locked-in (breakeven+) exit${rtxt ? ' — ' + rtxt : ''}`; }
        else { action = 'EXIT'; tone = 'danger'; reason = `price $${price} is at/below your stop $${stop} — should be out`; }
      }
      else if (trail != null && price < trail && !armed) { action = 'HOLD'; tone = 'good'; reason = `below the ${trailLabel} ($${trail}) but it hasn't reclaimed the line yet — not an exit; only your stop $${stop} exits until it closes back above`; }
      else if (trail != null && price < trail) { action = 'WATCH'; tone = 'warn'; reason = `back under the ${trailLabel} ($${trail}) intraday — exit only if it CLOSES under it`; }
      else if (patient && e9 != null && price < e9) { action = 'HOLD'; tone = 'good'; reason = `under the 9 EMA but holding the 50 EMA ($${e50}) — that's the deep-pullback/base plan`; }
      else if (earnSoon && r != null && r >= 0.5) { action = 'TRIM'; tone = 'warn'; reason = `earnings in ${edays}d — trim to lock ${rtxt} before the print`; }
      else if (r != null && r >= 3 && ext9_adr != null && ext9_adr > 2.2) { action = 'TRIM'; tone = 'warn'; reason = `${rtxt} & ${ext9_adr.toFixed(1)}× ADR over the 9-EMA — trim into strength`; }
      else if (r != null && r >= 1 && stop < e) { action = 'RAISE STOP'; tone = 'good'; reason = `${rtxt} — raise the stop to breakeven ($${e}) so it can't turn red`; }
      else { action = 'HOLD'; tone = 'good'; reason = `trend intact above the ${trailLabel}${rsuffix} — hold; exit on a daily close under it`; }
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
      });
      (this.suggestions.items || []).forEach(s => {
        const q = px[s.ticker]; if (!q || q.price == null) return;
        s.close = q.price; s._livechg = q.change_pct;
        if (s.zone_bottom != null && s.zone_top != null) s.buyable_now = (q.price >= s.zone_bottom && q.price <= s.zone_top);
        s._liveStopped = s.stop != null && q.price <= s.stop;
      });
      if (this.live.posture) {
        this.marketRegime.posture = this.live.posture.posture;
        this.marketRegime.label = this.live.posture.label;
        if (this.live.posture.indexes) this.marketRegime.indexes = this.live.posture.indexes;
      }
      // live re-rank: names that have pulled INTO their buy zone float to the top
      if (this.suggestions.items) {
        this.suggestions.items.sort((a, b) => (b.buyable_now ? 1 : 0) - (a.buyable_now ? 1 : 0) || (b.rating || 0) - (a.rating || 0));
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
      const px = this.live.prices || {}, today = this.todayStr(); let s = 0, have = false;
      this.trades.forEach(t => {
        if (t.status !== 'open' || !t.shares) return;
        const q = px[t.ticker]; if (!q || q.price == null) return;
        const base = (t.taken_at === today) ? (t.entry || t.planned_entry) : q.prev_close;
        if (base == null) return;
        s += (q.price - base) * t.shares; have = true;
      });
      return have ? s : null;
    },
    async api(path, method = 'GET', body) {
      const opt = { method, headers: { 'Content-Type': 'application/json', 'X-Workspace': workspaceId() } };
      if (body) opt.body = JSON.stringify(body);
      const r = await fetch('/api' + path, opt);
      return r.json();
    },

    // ---------- loaders ----------
    async loadSettings() { this.settings = await this.api('/settings'); },
    async loadScreeners() { this.screeners = await this.api('/screeners'); },
    async loadSuggestions() { this.suggestions = await this.api('/suggestions'); this.mergeLive(); },
    async loadTrades() { this.trades = await this.api('/trades'); this.mergeLive(); },
    async loadWatchlist() { this.watchlist = await this.api('/watchlist'); },
    async loadStats() { this.stats = await this.api('/stats'); },
    async loadMarket() { this.marketRegime = await this.api('/market'); },
    async loadGameplan() { try { this.gameplan = await this.api('/gameplan'); } catch (e) {} },
    async loadForward() { try { this.forward = await this.api('/forward'); } catch (e) {} },
    async loadPrediction() {
      this.predictionLoading = true;
      try { this.prediction = await this.api('/prediction'); } catch (e) {}
      this.predictionLoading = false;
    },
    async loadUniverse() { this.universe = await this.api('/universe'); },
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
    gradeColor(g) { return (g === 'A+' || g === 'A') ? '#22e0a1' : g === 'B' ? '#6a8dff' : g === 'C' ? '#ffb53d' : '#93a1b8'; },
    gradeLetter(r) { return r >= 82 ? 'A+' : r >= 73 ? 'A' : r >= 63 ? 'B' : r >= 52 ? 'C' : 'D'; },
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
    get riskDollar() { return this.settings.account_size ? Math.round(this.settings.account_size * (this.settings.risk_pct || 1) / 100).toLocaleString() : ''; },
    setupClass(t) {
      t = t || '';
      if (t.includes('AVWAP')) return 'badge-avwap';
      if (t.includes('Pullback')) return 'badge-pullback';
      if (t.includes('Episodic')) return 'badge-ep';
      return 'badge-breakout';
    },
    wl(r) { return this.wlData[r.ticker] || {}; },
    get topSuggestions() { return (this.suggestions.items || []).filter(s => s.status !== 'rejected').slice(0, 8); },
    get filteredSuggestions() {
      let items = (this.suggestions.items || []);
      if (this.filter === 'pending') items = items.filter(s => s.status === 'pending');
      else if (this.filter === 'approved') items = items.filter(s => s.status === 'approved' || s.status === 'taken');
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
      if (this.waitFilter) items = items.filter(s => s.worth_waiting);
      if (this.leaderFilter) items = items.filter(s => (s.rs_pct || 0) >= 85);
      if (this.risingFilter) items = items.filter(s => s.theme_trend === 'Rising');
      return items;
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
    get newsTickerRows() {
      return Object.entries(this.news.ticker_news || {})
        .sort((a, b) => this._pubTime(b[1].published) - this._pubTime(a[1].published));
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
      if (v >= 1e9) return '$' + (v / 1e9).toFixed(1) + 'B/d';
      if (v >= 1e6) return '$' + Math.round(v / 1e6) + 'M/d';
      return '$' + Math.round(v / 1e3) + 'K/d';
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
      if (action === 'reject') body.reason = prompt('Why reject ' + s.ticker + '? (optional)') || '';
      await this.api('/suggestions/' + s.ticker + '/' + action, 'POST', body);
      await this.loadSuggestions();
    },
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
      await fetch('/api/screeners/' + id, { method: 'DELETE', headers: { 'X-Workspace': workspaceId() } });
      await this.loadScreeners();
    },

    // ---------- view switching ----------
    selectView(id) {
      this.view = id;
      this.mobileNav = false;          // close the mobile drawer after picking a view
      if (id === 'watchlist') this.loadWatchlistAnalysis();
      if (id === 'news') this.loadNews();
      if (id === 'dashboard' && !this.gameplan) this.loadGameplan();
      if (id === 'stats') this.loadForward();
    },

    // ---------- sector heat ----------
    async loadSectorHeat() { this.sectorHeat = await this.api('/sector-heat'); },
    async loadSectorHeatLive() { try { this.sectorHeat = await this.api('/sector-heat/live'); this._lastHeatLive = Date.now(); } catch (e) {} },
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
      if (lean === 'Bullish') return '#22e0a1';
      if (lean === 'Constructive') return '#84cc16';
      if (lean.startsWith('Neutral')) return '#eab308';
      if (lean === 'Cautious') return '#f97316';
      return '#ef4444';
    },
    driverColor(d) { return d === 'pos' ? '#22e0a1' : d === 'neg' ? '#ff5d73' : '#93a1b8'; },
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
    stateEmoji(s) {
      return ({ 'Healthy uptrend': '🟢', 'Recovery': '🟢', 'Extended': '🟠',
        'Pullback': '🟡', 'Mid-correction': '🔻', 'Deep correction': '🔻' })[s] || '➖';
    },
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
      return s.entry_type === 'limit' ? s.close <= s.entry : s.close >= s.entry;
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
      await this.api('/settings', 'PUT', { account_size: this.settings.account_size, risk_pct: this.settings.risk_pct, max_position_pct: this.settings.max_position_pct });
      await Promise.all([this.loadSuggestions(), this.loadTrades()]);
      this.flash('Saved');
    },
    async saveDoc() {
      await this.api('/docs/' + this.docTab, 'PUT', { content: this.docEdit });
      if (this.docTab === 'lessons') this.docs.lessons = this.docEdit;
      this.flash('Saved');
    },
    flash(m) { this.savedMsg = m; setTimeout(() => this.savedMsg = '', 1500); },

    // ---------- charts ----------
    async showChart(ticker, obj) {
      this.chartModal.open = true; this.chartModal.ticker = ticker;
      this.chartModal._obj = obj || null;
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
      // regression trend channel (the "tunnel") — toggleable
      if (this.chartModal.showChannel && data.channel) {
        const chLine = (arr, w, style) => chart.addLineSeries({ color: '#3b82f6', lineWidth: w, lineStyle: style, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false }).setData(arr);
        chLine(data.channel.upper, 2, 0);
        chLine(data.channel.lower, 2, 0);
        chLine(data.channel.mid, 1, 2);   // dashed midline
      }
      // buy-zone band (green) + stop (red)
      const pl = (p, c, w, style, title) => { if (p) series.createPriceLine({ price: p, color: c, lineWidth: w, lineStyle: style, axisLabelVisible: true, title }); };
      if (obj) {
        if (obj.zone_top && obj.zone_bottom) {
          pl(obj.zone_top, '#22e0a1', 1, 2, 'Buy ≤');
          pl(obj.zone_bottom, '#22e0a1', 2, 0, 'Buy ≥');
        } else if (obj.entry || obj.planned_entry) {
          pl(obj.entry || obj.planned_entry, '#22e0a1', 2, 0, 'Entry');
        }
        pl(obj.stop, '#ff5d73', 1, 2, 'Stop');
      }
      chart.timeScale().fitContent();
      this.chartModal._chart = chart;
    },
    closeChart() {
      if (this.chartModal._ro) { try { this.chartModal._ro.disconnect(); } catch (e) {} this.chartModal._ro = null; }
      if (this.chartModal._chart) { this.chartModal._chart.remove(); this.chartModal._chart = null; }
      this.chartModal._series = null; this.chartModal._bars = null;
      this.chartModal.open = false;
    },
  };
}
window.dataCenter = dataCenter;
