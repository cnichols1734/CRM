/**
 * Market Insights dashboard widget controller (plain JS).
 *
 * Mirrors frontend/controllers/market_insights_controller.js so that the next
 * Vite build can swap to the Stimulus version, but works today without
 * requiring a frontend rebuild. Initializes against the
 * data-controller="market-insights" element on the dashboard.
 */
(function () {
    "use strict";

    const ACCENT = "#f97316";
    const SLATE_400 = "#94a3b8";
    const SLATE_500 = "#64748b";

    // localStorage key for the agent's preferred collapsed/expanded state.
    // Default on first visit (or invalid stored value) is COLLAPSED so the
    // dashboard stays focused on contacts/tasks and we don't burn RentCast
    // quota for users who never look at the panel.
    const LS_COLLAPSED_KEY = "crm.market-insights.collapsed";

    const USD = new Intl.NumberFormat("en-US", {
        style: "currency", currency: "USD", maximumFractionDigits: 0
    });
    const COMPACT_USD = new Intl.NumberFormat("en-US", {
        style: "currency", currency: "USD", notation: "compact", maximumFractionDigits: 1
    });
    const INT = new Intl.NumberFormat("en-US");

    const KPI_FORMATTERS = {
        price: (v) => v == null ? "—" : USD.format(v),
        ppsf: (v) => v == null ? "—" : `$${Math.round(v)}`,
        inventory: (v) => v == null ? "—" : INT.format(Math.round(v)),
        dom: (v) => v == null ? "—" : `${Math.round(v)}d`
    };

    // For DOM, "down" is good; for everything else, "up" is good.
    const KPI_POSITIVE = { price: "up", ppsf: "up", inventory: "up", dom: "down" };

    class MarketInsights {
        constructor(root) {
            this.root = root;
            this.cache = new Map();
            this.charts = { sparks: {} };
            this.previousValues = {};
            this.currentSlug = root.dataset.marketInsightsDefaultSlugValue || "mont-belvieu";
            this.currentWindow = root.dataset.marketInsightsDefaultWindowValue || "12m";
            this.lastResponse = null;
            this.started = false;

            this.collapsed = this._readCollapsedPref();
            this._applyCollapsedState({ animate: false });

            this._bindEvents();
        }

        // Lazy entry point: only run the (async) data fetch once the agent
        // has actually opened the panel. Charts also need a non-zero width
        // container, so we MUST defer ApexCharts initialization until visible.
        async start() {
            if (this.started) return;
            this.started = true;
            await this._waitForLibraries();
            await this._loadAreas();
            await this._loadInsights();
        }

        async toggle() {
            this.collapsed = !this.collapsed;
            this._writeCollapsedPref(this.collapsed);
            this._applyCollapsedState({ animate: true });
            if (!this.collapsed) {
                if (!this.started) {
                    await this.start();
                } else {
                    // Re-render charts so ApexCharts measures the (now
                    // visible) container correctly. Cheap no-op if data
                    // is already loaded.
                    this._reflowCharts();
                }
            }
        }

        _readCollapsedPref() {
            try {
                const v = window.localStorage.getItem(LS_COLLAPSED_KEY);
                if (v === "0") return false;
                if (v === "1") return true;
            } catch (_e) { /* localStorage unavailable */ }
            return true;
        }

        _writeCollapsedPref(collapsed) {
            try { window.localStorage.setItem(LS_COLLAPSED_KEY, collapsed ? "1" : "0"); }
            catch (_e) { /* ignore quota / privacy mode */ }
        }

        _applyCollapsedState({ animate }) {
            const open = !this.collapsed;
            this.root.classList.toggle("is-open", open);

            const body = this.$target("body");
            if (body) {
                if (open) body.removeAttribute("hidden");
                else body.setAttribute("hidden", "");
            }

            const controls = this.$target("controls");
            if (controls) controls.classList.toggle("hidden", !open);
            const asOfWrap = this.$target("asOfWrap");
            if (asOfWrap) asOfWrap.classList.toggle("hidden", !open);

            const toggleBtn = this.$target("toggleButton");
            if (toggleBtn) {
                toggleBtn.setAttribute("aria-expanded", String(open));
                toggleBtn.setAttribute(
                    "aria-label",
                    open ? "Hide market insights" : "Show market insights"
                );
            }
            // animate flag is consumed via CSS animation on .crm-mi.is-open
            void animate;
        }

        _reflowCharts() {
            try {
                if (this.charts.main && typeof this.charts.main.render === "function") {
                    this.charts.main.render();
                }
                Object.values(this.charts.sparks || {}).forEach((c) => {
                    if (c && typeof c.render === "function") c.render();
                });
            } catch (_e) { /* swallow re-render races */ }
        }

        // --- DOM helpers ----------------------------------------------------
        $(selector) { return this.root.querySelector(selector); }
        $$(selector) { return Array.from(this.root.querySelectorAll(selector)); }
        $target(name) { return this.root.querySelector(`[data-market-insights-target="${name}"]`); }
        $targets(name) { return Array.from(this.root.querySelectorAll(`[data-market-insights-target="${name}"]`)); }

        // --- Event wiring ---------------------------------------------------
        _bindEvents() {
            const select = this.$target("areaSelect");
            if (select) select.addEventListener("change", (e) => this._changeArea(e));

            this.$targets("windowButton").forEach((btn) => {
                btn.addEventListener("click", (e) => this._setWindow(e));
            });
            const errorRetry = this.$target("errorState");
            if (errorRetry) {
                const btn = errorRetry.querySelector("button");
                if (btn) btn.addEventListener("click", () => this._refresh());
            }

            const toggleBtn = this.$target("toggleButton");
            if (toggleBtn) toggleBtn.addEventListener("click", () => this.toggle());
        }

        async _changeArea(event) {
            const slug = event.currentTarget.value;
            if (!slug || slug === this.currentSlug) return;
            this.currentSlug = slug;
            await this._loadInsights();
        }

        async _setWindow(event) {
            const win = event.currentTarget.dataset.window;
            if (!win || win === this.currentWindow) return;
            this.currentWindow = win;
            this.$targets("windowButton").forEach((btn) => {
                btn.classList.toggle("is-active", btn.dataset.window === win);
            });
            await this._loadInsights();
        }

        async _refresh() {
            this.cache.delete(this._cacheKey());
            await this._loadInsights();
        }

        // --- Data loading ---------------------------------------------------
        async _loadAreas() {
            try {
                const r = await fetch("/api/market-insights/areas", { credentials: "same-origin" });
                if (!r.ok) throw new Error(`Areas request failed: ${r.status}`);
                const { areas } = await r.json();
                this._populateAreaSelect(areas || []);
            } catch (err) {
                console.error("Failed to load market insight areas", err);
                this._showError("Could not load service areas.");
            }
        }

        _populateAreaSelect(areas) {
            const select = this.$target("areaSelect");
            if (!select) return;
            select.innerHTML = "";
            if (!areas.length) {
                const opt = document.createElement("option");
                opt.textContent = "No areas configured";
                opt.disabled = true;
                select.appendChild(opt);
                return;
            }
            let chosen = null;
            areas.forEach((a) => {
                const opt = document.createElement("option");
                opt.value = a.slug;
                opt.textContent = a.display_name;
                select.appendChild(opt);
                if (a.slug === this.currentSlug) chosen = a.slug;
            });
            if (!chosen) {
                this.currentSlug = areas[0].slug;
                select.value = this.currentSlug;
            } else {
                select.value = chosen;
            }
        }

        async _loadInsights() {
            if (!this.currentSlug) return;
            const key = this._cacheKey();
            const cached = this.cache.get(key);
            if (cached) { this._render(cached); return; }
            this._setLoading(true);
            this._hideError();
            try {
                const url = `/api/market-insights/${encodeURIComponent(this.currentSlug)}?window=${encodeURIComponent(this.currentWindow)}`;
                const r = await fetch(url, { credentials: "same-origin" });
                if (!r.ok) {
                    const body = await r.json().catch(() => ({}));
                    throw new Error(body.error || `Request failed (${r.status})`);
                }
                const data = await r.json();
                this.cache.set(key, data);
                this._render(data);
            } catch (err) {
                console.error("Failed to load market insights", err);
                this._showError(err.message || "Couldn't load this area right now.");
            } finally {
                this._setLoading(false);
            }
        }

        _cacheKey() { return `${this.currentSlug}|${this.currentWindow}`; }

        // --- Rendering ------------------------------------------------------
        _render(data) {
            this.lastResponse = data;

            const asOf = this.$target("asOf");
            if (asOf) {
                asOf.textContent = data.as_of
                    ? `Data as of ${this._formatAsOf(data.as_of)}`
                    : "Data freshly loaded";
            }
            this._toggleStale(Boolean(data.is_stale));

            this._renderKpi("price", data.median_home_price, data, (h) => h.median_price);
            this._renderKpi("ppsf", data.median_price_per_sqft, data, (h) => h.median_ppsf);
            this._renderKpi("inventory", data.active_inventory, data, (h) => h.total_listings);
            this._renderKpi("dom", data.days_on_market, data, (h) => h.median_dom);

            this._renderSecondary(data);
            this._renderMainChart(data);
        }

        _renderKpi(kpi, metric, data, historyAccessor) {
            const value = metric ? metric.value : null;
            const valueEl = this._byKpi("kpiValue", kpi);
            if (valueEl) {
                const previous = this.previousValues[kpi];
                this._animateNumber(valueEl, previous, value, KPI_FORMATTERS[kpi]);
                this.previousValues[kpi] = value;
            }

            const deltaEl = this._byKpi("kpiDelta", kpi);
            if (deltaEl) {
                const isDays = metric && metric.change_days != null;
                const change = !metric ? null
                    : metric.change_pct != null ? metric.change_pct
                    : metric.change_days != null ? metric.change_days
                    : null;
                this._renderDelta(deltaEl, kpi, change, metric ? metric.change_vs : null, isDays);
            }

            this._renderKpiSpark(kpi, data, historyAccessor);
        }

        _renderDelta(el, kpi, change, vsLabel, isDays) {
            const textEl = el.querySelector('[data-market-insights-target="kpiDeltaText"]');
            el.classList.remove("crm-mi__delta--up", "crm-mi__delta--down", "crm-mi__delta--flat", "hidden");
            el.classList.add("crm-mi__delta");

            const icon = el.querySelector("i");
            if (change == null || isNaN(change)) {
                el.classList.add("crm-mi__delta--flat");
                if (icon) icon.className = "fas fa-minus";
                if (textEl) textEl.textContent = "—";
                return;
            }
            const direction = change > 0 ? "up" : change < 0 ? "down" : "flat";
            const positive = KPI_POSITIVE[kpi];
            let tone;
            if (direction === "flat") tone = "flat";
            else if (direction === positive) tone = "up";
            else tone = "down";
            el.classList.add(`crm-mi__delta--${tone}`);

            if (icon) {
                icon.className = direction === "up"
                    ? "fas fa-arrow-trend-up"
                    : direction === "down"
                        ? "fas fa-arrow-trend-down"
                        : "fas fa-minus";
            }
            if (textEl) {
                const sign = change > 0 ? "+" : "";
                textEl.textContent = isDays
                    ? `${sign}${Math.round(change)}d vs ${vsLabel || ""}`.trim()
                    : `${sign}${change.toFixed(1)}% vs ${vsLabel || ""}`.trim();
            }
        }

        _renderKpiSpark(kpi, data, accessor) {
            const el = this._byKpi("kpiSpark", kpi);
            if (!el || typeof window.ApexCharts === "undefined") return;

            const series = (data.history || []).map((h) => {
                const v = accessor(h);
                return v == null ? null : Number(v);
            });
            if (!series.some((v) => v != null)) {
                el.innerHTML = "";
                return;
            }

            const opts = {
                chart: {
                    type: "area",
                    height: 44,
                    sparkline: { enabled: true },
                    animations: { enabled: true, easing: "easeinout", speed: 600 }
                },
                stroke: { curve: "smooth", width: 2 },
                colors: [ACCENT],
                fill: {
                    type: "gradient",
                    gradient: { shadeIntensity: 0.6, opacityFrom: 0.45, opacityTo: 0.05, stops: [0, 100] }
                },
                tooltip: {
                    enabled: true, theme: "dark",
                    x: { show: false },
                    y: {
                        formatter: (v) => v == null ? "—" : KPI_FORMATTERS[kpi](v),
                        title: { formatter: () => "" }
                    },
                    marker: { show: false }
                },
                series: [{ name: kpi, data: series }]
            };

            if (this.charts.sparks[kpi]) {
                this.charts.sparks[kpi].updateOptions(opts, true, true);
            } else {
                this.charts.sparks[kpi] = new window.ApexCharts(el, opts);
                this.charts.sparks[kpi].render();
            }
        }

        _renderSecondary(data) {
            const newL = this.$target("newListings");
            if (newL) {
                const v = data.new_listings ? data.new_listings.value : null;
                newL.textContent = v == null ? "—" : INT.format(Math.round(v));
            }
        }

        _renderMainChart(data) {
            const el = this.$target("mainChart");
            if (!el || typeof window.ApexCharts === "undefined") return;

            const history = data.history || [];
            const priceSeries = history
                .filter((h) => h.median_price != null)
                .map((h) => ({ x: this._monthToTimestamp(h.month), y: Number(h.median_price) }));
            const inventorySeries = history
                .filter((h) => h.total_listings != null)
                .map((h) => ({ x: this._monthToTimestamp(h.month), y: Number(h.total_listings) }));

            const opts = {
                chart: {
                    type: "line",
                    height: 320,
                    toolbar: { show: false },
                    zoom: { enabled: false },
                    fontFamily: "-apple-system, BlinkMacSystemFont, 'SF Pro Display', 'Helvetica Neue', Arial, sans-serif",
                    animations: { enabled: true, easing: "easeinout", speed: 700 }
                },
                colors: [ACCENT, SLATE_400],
                stroke: { curve: "smooth", width: [3, 2], dashArray: [0, 4] },
                fill: {
                    type: ["gradient", "solid"],
                    gradient: { shadeIntensity: 0.5, opacityFrom: 0.35, opacityTo: 0.0, stops: [0, 90] },
                    opacity: [0.35, 0]
                },
                dataLabels: { enabled: false },
                markers: { size: 0, strokeWidth: 0, hover: { size: 5 } },
                grid: { borderColor: "#e2e8f0", strokeDashArray: 4,
                        padding: { top: 0, right: 8, bottom: 0, left: 8 } },
                xaxis: {
                    type: "datetime",
                    labels: { style: { colors: SLATE_500, fontSize: "11px" },
                              datetimeFormatter: { year: "yyyy", month: "MMM", day: "MMM dd" } },
                    axisBorder: { show: false },
                    axisTicks: { show: false },
                    tooltip: { enabled: false }
                },
                yaxis: [
                    {
                        seriesName: "Median asking price",
                        labels: { style: { colors: SLATE_500, fontSize: "11px" },
                                  formatter: (v) => v == null ? "" : COMPACT_USD.format(v) }
                    },
                    {
                        seriesName: "Active listings",
                        opposite: true,
                        labels: { style: { colors: SLATE_500, fontSize: "11px" },
                                  formatter: (v) => v == null ? "" : INT.format(Math.round(v)) }
                    }
                ],
                tooltip: {
                    theme: "dark", shared: true, intersect: false,
                    x: { format: "MMM yyyy" },
                    y: [
                        { formatter: (v) => v == null ? "—" : USD.format(v) },
                        { formatter: (v) => v == null ? "—" : `${INT.format(Math.round(v))} listings` }
                    ],
                    marker: { show: true }
                },
                legend: { show: false },
                series: [
                    { name: "Median asking price", type: "area", data: priceSeries },
                    { name: "Active listings", type: "line", data: inventorySeries }
                ]
            };

            if (this.charts.main) {
                this.charts.main.updateOptions(opts, true, true);
            } else {
                this.charts.main = new window.ApexCharts(el, opts);
                this.charts.main.render();
            }
        }

        // --- UI helpers ----------------------------------------------------
        _byKpi(targetName, kpi) {
            return this.$targets(targetName).find((el) => el.dataset.kpi === kpi) || null;
        }

        _setLoading(isLoading) {
            this.$targets("kpiCard").forEach((card) => card.classList.toggle("opacity-60", isLoading));
        }

        _showError(message) {
            const el = this.$target("errorState");
            if (!el) return;
            el.classList.remove("hidden");
            const msg = this.$target("errorMessage");
            if (msg) msg.textContent = message;
        }
        _hideError() {
            const el = this.$target("errorState");
            if (el) el.classList.add("hidden");
        }
        _toggleStale(isStale) {
            const badge = this.$target("staleBadge");
            const sep = this.$target("staleSeparator");
            if (badge) badge.classList.toggle("hidden", !isStale);
            if (sep) sep.classList.toggle("hidden", !isStale);
        }

        _animateNumber(el, from, to, formatter) {
            if (to == null) { el.textContent = "—"; return; }
            const start = (typeof from === "number" && !isNaN(from)) ? from : 0;
            const CountUp = window.countUp && window.countUp.CountUp;
            if (CountUp) {
                const cu = new CountUp(el, to, {
                    startVal: start, duration: 0.9, useGrouping: true, formattingFn: formatter
                });
                if (!cu.error) { cu.start(); return; }
            }
            const duration = 700;
            const t0 = performance.now();
            const tick = (now) => {
                const p = Math.min((now - t0) / duration, 1);
                const eased = 1 - Math.pow(1 - p, 3);
                el.textContent = formatter(start + (to - start) * eased);
                if (p < 1) requestAnimationFrame(tick);
            };
            requestAnimationFrame(tick);
        }

        _monthToTimestamp(month) {
            if (!month) return null;
            const [y, m] = month.split("-").map(Number);
            return Date.UTC(y, (m || 1) - 1, 1);
        }

        _formatAsOf(iso) {
            try {
                const d = new Date(iso);
                return d.toLocaleDateString("en-US", { year: "numeric", month: "short", day: "numeric" });
            } catch (_e) { return iso; }
        }

        _escape(s) {
            return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({
                "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
            })[c]);
        }

        async _waitForLibraries() {
            if (typeof window.ApexCharts !== "undefined") return;
            await new Promise((resolve) => {
                const start = Date.now();
                const tick = () => {
                    if (typeof window.ApexCharts !== "undefined") return resolve();
                    if (Date.now() - start > 5000) return resolve();
                    setTimeout(tick, 50);
                };
                tick();
            });
        }
    }

    function init() {
        const root = document.querySelector('[data-controller~="market-insights"]');
        if (!root) return;
        const widget = new MarketInsights(root);
        // If the user previously left the panel expanded, kick off the data
        // load now. Otherwise we wait until they click the chevron toggle.
        if (!widget.collapsed) widget.start();
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
