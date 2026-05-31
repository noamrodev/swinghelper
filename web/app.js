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
    refreshState: { running: false, stage: '', done: 0, total: 4 },
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
    sectorSort: 'score',
    secOpen: {},
    setupFilter: 'All',
    momFilter: 'All',
    sectorFilter: 'All',
    waitFilter: false,
    calcModal: { open: false, ticker: '' },
    chartModal: { open: false, ticker: '', _chart: null, logScale: true, showChannel: true, _data: null, _obj: null },
    tradeModal: { open: false, mode: 'take', ticker: '' },
    _pollTimer: null,

    // ---------- display scale ----------
    applyScale() { document.documentElement.style.fontSize = (this.scalePct / 100 * 16) + 'px'; },
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
      await Promise.all([this.loadSettings(), this.loadScreeners(), this.loadSuggestions(),
        this.loadTrades(), this.loadWatchlist(), this.loadStats(), this.loadDoc('lessons'),
        this.loadSectorHeat(), this.loadNews(), this.loadMarket(), this.loadUniverse()]);
      const def = this.screeners.find(s => s.is_default) || this.screeners[0];
      this.scanScreener = this.suggestions.screener_id || (def && def.id) || '';
      this.docEdit && (this.docEdit = this.docEdit);
      this.loadDoc(this.docTab);
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
    async loadSuggestions() { this.suggestions = await this.api('/suggestions'); },
    async loadTrades() { this.trades = await this.api('/trades'); },
    async loadWatchlist() { this.watchlist = await this.api('/watchlist'); },
    async loadStats() { this.stats = await this.api('/stats'); },
    async loadMarket() { this.marketRegime = await this.api('/market'); },
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
      return items;
    },
    // sectors present in the current suggestions, ordered hottest-first (by sector heat)
    get sectorOptions() {
      const present = new Set((this.suggestions.items || []).map(s => s.theme).filter(Boolean));
      const heat = {};
      (this.sectorHeat.sectors || []).forEach(s => { heat[s.sector] = s.score; });
      return [...present].sort((a, b) => (heat[b] ?? -999) - (heat[a] ?? -999) || a.localeCompare(b));
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
      if (id === 'watchlist') this.loadWatchlistAnalysis();
      if (id === 'news') this.loadNews();
    },

    // ---------- sector heat ----------
    async loadSectorHeat() { this.sectorHeat = await this.api('/sector-heat'); },
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
        this.loadSectorHeat(), this.loadNews(), this.loadMarket(), this.loadUniverse()]);
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
      // 9 / 21 / 50 EMA overlays
      const emaCfg = [[9, '#eab308'], [21, '#f59e0b'], [50, '#5b8cff']];
      for (const [p, color] of emaCfg) {
        if (bars.length < p) continue;
        const k = 2 / (p + 1); let prev = bars[0].close; const arr = [];
        for (const b of bars) { prev = b.close * k + prev * (1 - k); arr.push({ time: b.time, value: +prev.toFixed(2) }); }
        const ls = chart.addLineSeries({ color, lineWidth: p === 9 ? 2 : 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
        ls.setData(arr);
      }
      // AVWAP curves — anchored at all-time high (violet) and last earnings gap (cyan)
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
      this.chartModal.open = false;
    },
  };
}
window.dataCenter = dataCenter;
