"use strict";

import {
  buildJobViewModels,
  filterAndSortJobModels,
} from "/job-view-model.mjs";
import {
  hotelComparablePay,
  hotelRows,
  isHotelOnFrontier,
} from "/hotel-pareto.mjs";
import {
  albamonFrontierGroups,
  albamonParetoPoints,
} from "/albamon-pareto.mjs";

const state = {
  payload: null,
  filteredJobs: [],
  jobViewModels: [],
  jobsById: new Map(),
  jobDetails: new Map(),
  comparedIds: new Set(),
  shortlistOnly: false,
  selectedId: null,
  detailRequestId: 0,
  detailAbortController: null,
  lastFocused: null,
  map: null,
  mapLoadPromise: null,
  originMarker: null,
  markers: new Map(),
  albamonVisualization: "map",
  filterTimer: null,
  toastTimer: null,
  activeDesk: "albamon",
  hotelPayload: null,
  hotelPayMode: "actual",
  hotelLoadingPromise: null,
};

const FILTER_STORAGE_KEY = "jobscout.dashboard.filters.v1";
const LEAFLET_VERSION = "1.9.4";
const LEAFLET_BASE_URL = `https://unpkg.com/leaflet@${LEAFLET_VERSION}/dist`;
const CAPITAL_REGION_BOUNDS = { minLat: 36.8, maxLat: 38.3, minLon: 125.8, maxLon: 128.3 };

const elements = {
  albamonDeskTab: document.querySelector("#albamon-desk-tab"),
  hotelsDeskTab: document.querySelector("#hotels-desk-tab"),
  albamonView: document.querySelector("#albamon-view"),
  hotelsView: document.querySelector("#hotels-view"),
  albamonAllTab: document.querySelector("#albamon-all-tab"),
  albamonParetoTab: document.querySelector("#albamon-pareto-tab"),
  albamonParetoCount: document.querySelector("#albamon-pareto-count"),
  workspace: document.querySelector(".workspace"),
  refreshState: document.querySelector("#refresh-state"),
  reportLinks: document.querySelector("#report-links"),
  summaryNew: document.querySelector("#summary-new"),
  summaryActionable: document.querySelector("#summary-actionable"),
  summaryAttention: document.querySelector("#summary-attention"),
  pipelineNote: document.querySelector("#pipeline-note"),
  search: document.querySelector("#search-filter"),
  status: document.querySelector("#status-filter"),
  schedule: document.querySelector("#schedule-filter"),
  commute: document.querySelector("#commute-filter"),
  pay: document.querySelector("#pay-filter"),
  tradeoff: document.querySelector("#tradeoff-filter"),
  paretoExplainer: document.querySelector("#pareto-explainer"),
  district: document.querySelector("#district-filter"),
  zone: document.querySelector("#zone-filter"),
  newOnly: document.querySelector("#new-filter"),
  closingOnly: document.querySelector("#closing-filter"),
  requirements: document.querySelector("#requirements-filter"),
  easyApply: document.querySelector("#easy-apply-filter"),
  sort: document.querySelector("#sort-filter"),
  resultCount: document.querySelector("#result-count"),
  resultsEyebrow: document.querySelector("#results-eyebrow"),
  filterFeedback: document.querySelector("#filter-feedback"),
  shortlistFilter: document.querySelector("#shortlist-filter"),
  shortlistTotal: document.querySelector("#shortlist-total"),
  rows: document.querySelector("#job-rows"),
  empty: document.querySelector("#empty-state"),
  drawer: document.querySelector("#detail-drawer"),
  drawerScrim: document.querySelector("#drawer-scrim"),
  drawerTitle: document.querySelector("#detail-title"),
  drawerCompany: document.querySelector("#detail-company"),
  drawerKicker: document.querySelector("#detail-kicker"),
  drawerBody: document.querySelector("#drawer-body"),
  closeDrawer: document.querySelector("#close-drawer"),
  reload: document.querySelector("#reload-button"),
  clearFilters: document.querySelector("#clear-filters"),
  tableTab: document.querySelector("#table-tab"),
  mapTab: document.querySelector("#map-tab"),
  map: document.querySelector("#map"),
  mapHeading: document.querySelector("#map-heading"),
  visualizationEyebrow: document.querySelector("#visualization-eyebrow"),
  visualizationCaption: document.querySelector("#visualization-caption"),
  albamonMapVisual: document.querySelector("#albamon-map-visual"),
  albamonParetoVisual: document.querySelector("#albamon-pareto-visual"),
  albamonParetoChart: document.querySelector("#albamon-pareto-chart"),
  mapLegend: document.querySelector("#map-legend"),
  albamonChartLegend: document.querySelector("#albamon-chart-legend"),
  mapFallback: document.querySelector("#map-fallback"),
  mapStatusTitle: document.querySelector("#map-status-title"),
  mapStatusMessage: document.querySelector("#map-status-message"),
  compareTray: document.querySelector("#compare-tray"),
  compareCount: document.querySelector("#compare-count"),
  compareChips: document.querySelector("#compare-chips"),
  clearCompare: document.querySelector("#clear-compare"),
  openCompare: document.querySelector("#open-compare"),
  compareDialog: document.querySelector("#compare-dialog"),
  comparisonGrid: document.querySelector("#comparison-grid"),
  closeCompare: document.querySelector("#close-compare"),
  toast: document.querySelector("#toast"),
  hotelReportLinks: document.querySelector("#hotel-report-links"),
  hotelSummaryTotal: document.querySelector("#hotel-summary-total"),
  hotelSummaryFrontier: document.querySelector("#hotel-summary-frontier"),
  hotelSummaryPay: document.querySelector("#hotel-summary-pay"),
  hotelSummaryWeekly: document.querySelector("#hotel-summary-weekly"),
  hotelPayActual: document.querySelector("#hotel-pay-actual"),
  hotelPayWeekly: document.querySelector("#hotel-pay-weekly"),
  hotelShowAll: document.querySelector("#hotel-show-all"),
  hotelBasisNote: document.querySelector("#hotel-basis-note"),
  hotelSourceWarning: document.querySelector("#hotel-source-warning"),
  hotelChart: document.querySelector("#hotel-chart"),
  hotelTableHeading: document.querySelector("#hotel-table-heading"),
  hotelTableFeedback: document.querySelector("#hotel-table-feedback"),
  hotelRows: document.querySelector("#hotel-rows"),
  hotelEmpty: document.querySelector("#hotel-empty-state"),
};

const STATUS_LABELS = {
  apply: "Actionable",
  maybe_later: "Maybe later",
  do_not_apply: "Skipped",
  excluded_by_distance: "Distance excluded",
  applied: "Applied",
};
function createElement(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

function appendText(parent, tag, className, text) {
  const node = createElement(tag, className, text);
  parent.append(node);
  return node;
}

function formatNumber(value) {
  return new Intl.NumberFormat("ko-KR").format(Number(value) || 0);
}

function formatDateTime(value) {
  if (!value) return "Not available";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("en-GB", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function cleanText(value) {
  return String(value ?? "").replace(/\s+/g, " ").trim();
}

function formatWon(value) {
  return hasNumericValue(value) ? `₩${formatNumber(value)}` : "—";
}

function svgElement(tag, attributes = {}, text = "") {
  const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [name, value] of Object.entries(attributes)) {
    node.setAttribute(name, String(value));
  }
  if (text) node.textContent = text;
  return node;
}

function hotelPayModeCopy() {
  if (state.hotelPayMode === "weekly_allowance") {
    return "Using the published conditional 주휴 rate where stated; hotels without one retain actual pay as a clearly marked fallback. Eligibility is not guaranteed.";
  }
  return "Using actual/base hourly pay. This is the reliable comparison and the ₩12,000 eligibility basis.";
}

function renderHotelChart(hotels) {
  const points = (hotels || []).map((hotel) => ({
    hotel,
    pay: hotelComparablePay(hotel, state.hotelPayMode),
    commute: Number(hotel.transit_minutes),
    frontier: isHotelOnFrontier(hotel, state.hotelPayMode),
  })).filter((point) => point.pay && Number.isFinite(point.commute));
  if (!points.length) {
    elements.hotelChart.replaceChildren(createElement("p", "chart-empty", "No verified pay and commute pairs."));
    return;
  }

  const width = 760;
  const height = 340;
  const margin = { top: 24, right: 24, bottom: 48, left: 72 };
  const plotWidth = width - margin.left - margin.right;
  const plotHeight = height - margin.top - margin.bottom;
  const commuteValues = points.map((point) => point.commute);
  const payValues = points.map((point) => point.pay);
  const xMin = Math.max(0, Math.floor((Math.min(...commuteValues) - 5) / 5) * 5);
  const xMax = Math.ceil((Math.max(...commuteValues) + 5) / 5) * 5;
  const yMin = Math.max(0, Math.floor((Math.min(...payValues) - 500) / 1000) * 1000);
  const yMax = Math.ceil((Math.max(...payValues) + 500) / 1000) * 1000;
  const x = (value) => margin.left + ((value - xMin) / Math.max(1, xMax - xMin)) * plotWidth;
  const y = (value) => margin.top + ((yMax - value) / Math.max(1, yMax - yMin)) * plotHeight;
  const svg = svgElement("svg", {
    viewBox: `0 0 ${width} ${height}`,
    "aria-hidden": "true",
    focusable: "false",
  });

  for (let index = 0; index <= 4; index += 1) {
    const commute = xMin + ((xMax - xMin) * index) / 4;
    const pay = yMin + ((yMax - yMin) * index) / 4;
    svg.append(
      svgElement("line", { x1: x(commute), y1: margin.top, x2: x(commute), y2: height - margin.bottom, class: "chart-grid" }),
      svgElement("text", { x: x(commute), y: height - 22, class: "chart-axis-label", "text-anchor": "middle" }, `${Math.round(commute)}m`),
      svgElement("line", { x1: margin.left, y1: y(pay), x2: width - margin.right, y2: y(pay), class: "chart-grid" }),
      svgElement("text", { x: margin.left - 11, y: y(pay) + 4, class: "chart-axis-label", "text-anchor": "end" }, `₩${Math.round(pay / 100) / 10}k`),
    );
  }
  svg.append(
    svgElement("text", { x: margin.left + plotWidth / 2, y: height - 4, class: "chart-axis-title", "text-anchor": "middle" }, "Verified commute (minutes) →"),
    svgElement("text", { x: 15, y: margin.top + plotHeight / 2, class: "chart-axis-title", transform: `rotate(-90 15 ${margin.top + plotHeight / 2})`, "text-anchor": "middle" }, "Comparable hourly pay →"),
  );

  const frontierPoints = points.filter((point) => point.frontier).sort((left, right) => left.commute - right.commute);
  if (frontierPoints.length > 1) {
    svg.append(svgElement("polyline", {
      points: frontierPoints.map((point) => `${x(point.commute)},${y(point.pay)}`).join(" "),
      class: "chart-frontier-line",
    }));
  }
  for (const point of [...points.filter((item) => !item.frontier), ...frontierPoints]) {
    const group = svgElement("g", { class: point.frontier ? "chart-point chart-point--frontier" : "chart-point" });
    group.append(
      svgElement("circle", { cx: x(point.commute), cy: y(point.pay), r: point.frontier ? 7 : 4.5 }),
      svgElement("title", {}, `${point.hotel.employer}: ${formatWon(point.pay)}, ${point.commute} min`),
    );
    if (point.frontier) {
      const label = cleanText(point.hotel.employer).replace(" 서울강남", "").replace(" 서울", "");
      group.append(svgElement("text", { x: x(point.commute) + 10, y: y(point.pay) - 10, class: "chart-point-label" }, label));
    }
    svg.append(group);
  }
  elements.hotelChart.replaceChildren(svg);
}

function truncateLabel(value, maximum = 18) {
  const text = cleanText(value);
  return text.length > maximum ? `${text.slice(0, maximum - 1)}…` : text;
}

function renderAlbamonParetoChart() {
  const points = albamonParetoPoints(state.payload?.jobs || []);
  if (!points.length) {
    elements.albamonParetoChart.replaceChildren(
      createElement("p", "chart-empty", "No actionable jobs have both verified commute and comparable pay."),
    );
    elements.albamonParetoChart.setAttribute("aria-label", "No comparable Albamon pay and commute pairs");
    return;
  }

  const width = 680;
  const height = 500;
  const margin = { top: 34, right: 34, bottom: 58, left: 78 };
  const plotWidth = width - margin.left - margin.right;
  const plotHeight = height - margin.top - margin.bottom;
  const commuteValues = points.map((point) => point.commute);
  const payValues = points.map((point) => point.pay);
  const xMin = 0;
  const xMax = Math.max(10, Math.ceil((Math.max(...commuteValues) + 5) / 5) * 5);
  const yMin = Math.max(0, Math.floor((Math.min(...payValues) - 500) / 1000) * 1000);
  const yMax = Math.max(yMin + 1000, Math.ceil((Math.max(...payValues) + 500) / 1000) * 1000);
  const x = (value) => margin.left + ((value - xMin) / (xMax - xMin)) * plotWidth;
  const y = (value) => margin.top + ((yMax - value) / (yMax - yMin)) * plotHeight;
  const svg = svgElement("svg", {
    viewBox: `0 0 ${width} ${height}`,
    "aria-hidden": "true",
    focusable: "false",
  });

  for (let index = 0; index <= 4; index += 1) {
    const commute = xMin + ((xMax - xMin) * index) / 4;
    const pay = yMin + ((yMax - yMin) * index) / 4;
    svg.append(
      svgElement("line", { x1: x(commute), y1: margin.top, x2: x(commute), y2: height - margin.bottom, class: "chart-grid" }),
      svgElement("text", { x: x(commute), y: height - 27, class: "chart-axis-label", "text-anchor": "middle" }, `${Math.round(commute)}m`),
      svgElement("line", { x1: margin.left, y1: y(pay), x2: width - margin.right, y2: y(pay), class: "chart-grid" }),
      svgElement("text", { x: margin.left - 12, y: y(pay) + 4, class: "chart-axis-label", "text-anchor": "end" }, `₩${Math.round(pay / 100) / 10}k`),
    );
  }
  svg.append(
    svgElement("text", { x: margin.left + plotWidth / 2, y: height - 5, class: "chart-axis-title", "text-anchor": "middle" }, "Verified one-way commute (minutes) →"),
    svgElement("text", { x: 16, y: margin.top + plotHeight / 2, class: "chart-axis-title", transform: `rotate(-90 16 ${margin.top + plotHeight / 2})`, "text-anchor": "middle" }, "Reliable comparable hourly pay →"),
  );

  const frontierGroups = albamonFrontierGroups(points);
  for (const [mode, frontier] of frontierGroups) {
    if (mode === "onsite" && frontier.length > 1) {
      svg.append(svgElement("polyline", {
        points: frontier.map((point) => `${x(point.commute)},${y(point.pay)}`).join(" "),
        class: "chart-frontier-line",
      }));
    }
  }
  const frontierPoints = points.filter((point) => point.frontier);
  const ordered = [...points.filter((point) => !point.frontier), ...frontierPoints];
  for (const [index, point] of ordered.entries()) {
    const classes = ["chart-point"];
    if (point.frontier) classes.push("chart-point--frontier");
    if (point.mode === "remote") classes.push("chart-point--remote");
    const group = svgElement("g", { class: classes.join(" ") });
    group.append(
      svgElement("circle", { cx: x(point.commute), cy: y(point.pay), r: point.frontier ? 7 : 4.5 }),
      svgElement("title", {}, `${point.job.company}: ${point.job.title} — ${formatWon(point.pay)}, ${point.mode === "remote" ? "remote" : `${point.commute} min`}`),
    );
    if (point.frontier) {
      const direction = index % 2 === 0 ? -1 : 1;
      group.append(svgElement("text", {
        x: x(point.commute) + 10,
        y: y(point.pay) + direction * 12,
        class: "chart-point-label",
      }, `${point.mode === "remote" ? "Remote · " : ""}${truncateLabel(point.job.company || point.job.title)}`));
    }
    svg.append(group);
  }
  const frontierCount = frontierPoints.length;
  elements.albamonParetoChart.replaceChildren(svg);
  elements.albamonParetoChart.setAttribute(
    "aria-label",
    `${points.length} actionable Albamon jobs plotted by hourly pay and commute; ${frontierCount} are on the Pareto frontier`,
  );
}

function renderHotelRows(hotels) {
  const rows = hotelRows(hotels, {
    mode: state.hotelPayMode,
    showAll: elements.hotelShowAll.checked,
  });
  const fragment = document.createDocumentFragment();
  for (const hotel of rows) {
    const row = createElement("tr");
    if (isHotelOnFrontier(hotel, state.hotelPayMode)) row.classList.add("is-frontier");
    const nameCell = createElement("td", "hotel-name-cell");
    const badges = createElement("div", "badge-row");
    if (isHotelOnFrontier(hotel, state.hotelPayMode)) badges.append(badge("Frontier", "pareto"));
    if (hotel.source_snapshot_stale) badges.append(badge("Cached source", "warning"));
    nameCell.append(badges, createElement("strong", "", hotel.employer));
    appendText(nameCell, "small", "", hotel.station || hotel.area || "Location unavailable");

    const comparableCell = createElement("td");
    appendText(comparableCell, "strong", "hotel-comparable-pay", formatWon(hotelComparablePay(hotel, state.hotelPayMode)));
    const comparableBasis = state.hotelPayMode === "weekly_allowance" && hotel.weekly_allowance_rate_stated
      ? "conditional 주휴"
      : state.hotelPayMode === "weekly_allowance" ? "actual fallback" : "actual/base";
    appendText(comparableCell, "small", "cell-secondary", comparableBasis);

    const payCell = createElement("td");
    appendText(payCell, "strong", "", formatWon(hotel.actual_hourly_max));
    appendText(payCell, "small", "cell-secondary", `주휴* ${formatWon(hotel.weekly_allowance_hourly_max)}`);

    const commuteCell = createElement("td");
    appendText(commuteCell, "strong", "commute-value", `${hotel.transit_minutes} min`);
    appendText(commuteCell, "small", "cell-secondary", "verified transit");

    const optionsCell = createElement("td");
    appendText(optionsCell, "strong", "", `${hotel.date_count} days · ${hotel.shift_count} times`);
    const providers = (hotel.providers || []).map((provider) => provider.label).join(" · ") || "Provider unavailable";
    appendText(optionsCell, "small", "cell-secondary", `${hotel.job_count} choices · ${providers}`);
    row.append(nameCell, comparableCell, payCell, commuteCell, optionsCell);
    fragment.append(row);
  }
  elements.hotelRows.replaceChildren(fragment);
  elements.hotelEmpty.hidden = rows.length > 0;
  elements.hotelTableHeading.textContent = elements.hotelShowAll.checked ? "All hotels" : "Frontier hotels";
  elements.hotelTableFeedback.textContent = `${rows.length} of ${hotels.length} aggregated hotels · updated ${formatDateTime(state.hotelPayload.generated_at)}`;
}

function renderHotelView() {
  const payload = state.hotelPayload;
  if (!payload) return;
  const hotels = payload.hotels || [];
  const frontier = hotels.filter((hotel) => isHotelOnFrontier(hotel, state.hotelPayMode));
  const highestPay = Math.max(0, ...frontier.map((hotel) => hotelComparablePay(hotel, state.hotelPayMode) || 0));
  elements.hotelSummaryTotal.textContent = formatNumber(payload.summary.hotel_count);
  elements.hotelSummaryFrontier.textContent = formatNumber(frontier.length);
  elements.hotelSummaryPay.textContent = formatWon(highestPay);
  elements.hotelSummaryWeekly.textContent = formatNumber(payload.summary.weekly_allowance_rate_count);
  elements.refreshState.textContent = `Hotel report refreshed ${formatDateTime(payload.generated_at)}`;
  elements.hotelBasisNote.textContent = hotelPayModeCopy();
  elements.hotelPayActual.classList.toggle("is-active", state.hotelPayMode === "actual");
  elements.hotelPayActual.setAttribute("aria-pressed", String(state.hotelPayMode === "actual"));
  elements.hotelPayWeekly.classList.toggle("is-active", state.hotelPayMode === "weekly_allowance");
  elements.hotelPayWeekly.setAttribute("aria-pressed", String(state.hotelPayMode === "weekly_allowance"));
  elements.hotelReportLinks.replaceChildren(...(payload.reports || []).map((report) => {
    const link = createElement("a", "", report.label);
    link.href = report.url;
    return link;
  }));
  elements.reportLinks.replaceChildren(...(payload.reports || []).map((report) => {
    const link = createElement("a", "", report.label);
    link.href = report.url;
    link.download = "";
    return link;
  }));

  const degraded = Object.entries(payload.sources || {}).filter(([, source]) => source.status !== "live");
  elements.hotelSourceWarning.hidden = degraded.length === 0 && payload.integrity_checks_passed;
  if (!elements.hotelSourceWarning.hidden) {
    const sourceText = degraded.map(([name, source]) => {
      const status = source.status === "stale_cache" ? "bounded cache in use" : source.status;
      return `${name}: ${status}`;
    }).join(" · ");
    elements.hotelSourceWarning.textContent = payload.integrity_checks_passed
      ? `Source freshness warning — ${sourceText}. Cached rows remain visibly marked.`
      : `Integrity checks did not pass. ${sourceText}`;
  }
  renderHotelChart(hotels);
  renderHotelRows(hotels);
}

async function loadHotelData({ force = false } = {}) {
  if (state.hotelPayload && !force) {
    renderHotelView();
    return state.hotelPayload;
  }
  if (state.hotelLoadingPromise && !force) return state.hotelLoadingPromise;
  elements.hotelTableFeedback.textContent = "Loading the separate report…";
  elements.reload.disabled = true;
  state.hotelLoadingPromise = (async () => {
    const response = await fetch("/api/weekend-agencies", { cache: "no-store" });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Hotel agency data is unavailable");
    if (payload.payload_version !== 1) throw new Error("Restart the dashboard server to load hotel agency data");
    state.hotelPayload = payload;
    renderHotelView();
    return payload;
  })();
  try {
    return await state.hotelLoadingPromise;
  } catch (error) {
    elements.hotelRows.replaceChildren();
    elements.hotelEmpty.hidden = false;
    elements.hotelChart.replaceChildren(createElement("p", "chart-empty", error.message || "Hotel report unavailable"));
    elements.hotelTableFeedback.textContent = error.message || "Hotel report unavailable";
    showToast(error.message || "Could not load the hotel report.", true);
    return null;
  } finally {
    state.hotelLoadingPromise = null;
    elements.reload.disabled = false;
  }
}

function setDesk(view) {
  state.activeDesk = view;
  const hotelsActive = view === "hotels";
  elements.albamonView.hidden = hotelsActive;
  elements.hotelsView.hidden = !hotelsActive;
  elements.albamonDeskTab.classList.toggle("is-active", !hotelsActive);
  elements.albamonDeskTab.setAttribute("aria-selected", String(!hotelsActive));
  elements.hotelsDeskTab.classList.toggle("is-active", hotelsActive);
  elements.hotelsDeskTab.setAttribute("aria-selected", String(hotelsActive));
  if (hotelsActive) {
    closeDrawer();
    elements.compareTray.hidden = true;
    document.querySelector(".skip-link").href = "#hotel-table-heading";
    loadHotelData();
  } else {
    document.querySelector(".skip-link").href = "#results";
    if (state.payload) renderHeader();
    renderComparisonTray();
  }
}

function syncAlbamonMode() {
  const showingPareto = elements.tradeoff.value === "pareto";
  elements.albamonAllTab.classList.toggle("is-active", !showingPareto);
  elements.albamonAllTab.setAttribute("aria-pressed", String(!showingPareto));
  elements.albamonParetoTab.classList.toggle("is-active", showingPareto);
  elements.albamonParetoTab.setAttribute("aria-pressed", String(showingPareto));
  elements.albamonParetoCount.textContent = formatNumber(
    state.payload?.summary?.pareto_frontier_count || 0,
  );
}

function setAlbamonMode(mode) {
  elements.tradeoff.value = mode === "pareto" ? "pareto" : "all";
  syncAlbamonMode();
  persistFilterPreferences();
  if (state.payload) filterJobs();
}

function syncWorksiteVisualization() {
  if (!state.payload) return;
  const showingPareto = state.albamonVisualization === "pareto";
  elements.albamonMapVisual.classList.toggle("is-active", !showingPareto);
  elements.albamonMapVisual.setAttribute("aria-pressed", String(!showingPareto));
  elements.albamonParetoVisual.classList.toggle("is-active", showingPareto);
  elements.albamonParetoVisual.setAttribute("aria-pressed", String(showingPareto));
  elements.mapLegend.hidden = showingPareto;
  elements.albamonChartLegend.hidden = !showingPareto;
  elements.albamonParetoChart.hidden = !showingPareto;

  if (showingPareto) {
    elements.map.hidden = true;
    elements.mapFallback.hidden = true;
    elements.visualizationEyebrow.textContent = "PAY ↑ · COMMUTE ↓";
    elements.mapHeading.textContent = `${formatNumber(state.payload.summary.pareto_frontier_count || 0)} unbeaten choices`;
    elements.visualizationCaption.textContent = "Every point is actionable with reliable comparable pay and verified commute. Gold choices are not beaten on both axes; remote roles are compared separately at 0 minutes.";
    renderAlbamonParetoChart();
    return;
  }

  elements.visualizationEyebrow.textContent = "WORKSITE MAP";
  elements.mapHeading.textContent = `From ${state.payload.origin.name}`;
  elements.visualizationCaption.textContent = "Markers use Albamon’s actual worksite coordinates. Commute eligibility uses verified public transport—not straight-line distance.";
  elements.map.hidden = !state.map;
  elements.mapFallback.hidden = Boolean(state.map);
  renderMapMarkers();
}

function setAlbamonVisualization(mode) {
  state.albamonVisualization = mode === "pareto" ? "pareto" : "map";
  syncWorksiteVisualization();
  if (state.albamonVisualization === "map") {
    requestMapLoad().then(() => {
      if (state.map) window.setTimeout(() => state.map.invalidateSize(), 20);
    });
  }
}

function formatRouteClock(value) {
  if (!value) return "time unavailable";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return cleanText(value);
  return new Intl.DateTimeFormat("en-GB", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date);
}

function lateNightJourneyText(journey) {
  const outbound = journey.outbound || {};
  const returnRoute = journey.return || {};
  const bicycle = journey.bicycle_fallback || {};
  const outboundText = outbound.mode === "public_transit_future_time"
    ? `Naver ${outbound.minutes} min (${formatRouteClock(outbound.departure_at)} → ${formatRouteClock(outbound.arrival_at)})`
    : `transit pattern ${outbound.minutes ?? "?"} min`;
  let returnText;
  if (returnRoute.mode === "public_transit_future_time") {
    const vehicles = (returnRoute.vehicles || []).join(", ") || "transit";
    returnText = `Naver ${vehicles} ${returnRoute.minutes} min (${formatRouteClock(returnRoute.departure_at)} → ${formatRouteClock(returnRoute.arrival_at)})`;
  } else if (returnRoute.mode === "night_bus_pattern") {
    returnText = `${(returnRoute.vehicles || ["night bus"]).join(", ")} pattern ${returnRoute.minutes ?? "?"} min`;
  } else if (returnRoute.mode === "public_transit_pattern") {
    returnText = `transit pattern ${returnRoute.minutes ?? "?"} min`;
  } else {
    returnText = `bicycle ${returnRoute.minutes ?? "?"} min`;
  }
  return `${journey.shift}: outbound ${outboundText}; return ${returnText}; bicycle fallback ${bicycle.minutes ?? "?"} min / ${bicycle.distance_km ?? "?"} km`;
}

function hasNumericValue(value) {
  return value !== null && value !== undefined && value !== "" && Number.isFinite(Number(value));
}

function hasPlausibleMapLocation(job) {
  const latitude = Number(job.latitude);
  const longitude = Number(job.longitude);
  return hasNumericValue(job.latitude) && hasNumericValue(job.longitude) &&
    latitude >= CAPITAL_REGION_BOUNDS.minLat && latitude <= CAPITAL_REGION_BOUNDS.maxLat &&
    longitude >= CAPITAL_REGION_BOUNDS.minLon && longitude <= CAPITAL_REGION_BOUNDS.maxLon;
}

function selectHasValue(select, value) {
  return [...select.options].some((option) => option.value === value);
}

function restoreFilterPreferences() {
  try {
    const saved = JSON.parse(window.localStorage.getItem(FILTER_STORAGE_KEY) || "null");
    if (!saved || typeof saved !== "object") return;
    for (const [element, key] of [
      [elements.status, "status"],
      [elements.schedule, "schedule"],
      [elements.commute, "commute"],
      [elements.pay, "pay"],
      [elements.tradeoff, "tradeoff"],
      [elements.district, "district"],
      [elements.zone, "zone"],
      [elements.sort, "sort"],
    ]) {
      if (typeof saved[key] === "string" && selectHasValue(element, saved[key])) {
        element.value = saved[key];
      }
    }
    elements.newOnly.checked = saved.newOnly === true;
    elements.closingOnly.checked = saved.closingOnly === true;
    elements.requirements.checked = saved.requirements === true;
    elements.easyApply.checked = saved.easyApply === true;
    syncAlbamonMode();
  } catch {
    window.localStorage.removeItem(FILTER_STORAGE_KEY);
  }
}

function persistFilterPreferences() {
  const preferences = {
    status: elements.status.value,
    schedule: elements.schedule.value,
    commute: elements.commute.value,
    pay: elements.pay.value,
    tradeoff: elements.tradeoff.value,
    district: elements.district.value,
    zone: elements.zone.value,
    newOnly: elements.newOnly.checked,
    closingOnly: elements.closingOnly.checked,
    requirements: elements.requirements.checked,
    easyApply: elements.easyApply.checked,
    sort: elements.sort.value,
  };
  try {
    window.localStorage.setItem(FILTER_STORAGE_KEY, JSON.stringify(preferences));
  } catch {
    // A private browsing policy may disable storage; filters still work in-session.
  }
}

function buildJobIndexes() {
  state.jobsById = new Map(
    state.payload.jobs.map((job) => [Number(job.recruit_no), job])
  );
  state.jobViewModels = buildJobViewModels(state.payload.jobs);
}

function scheduleFilter(delay = 0) {
  window.clearTimeout(state.filterTimer);
  state.filterTimer = window.setTimeout(filterJobs, delay);
}

function showToast(message, isError = false) {
  window.clearTimeout(state.toastTimer);
  elements.toast.textContent = message;
  elements.toast.classList.toggle("is-error", isError);
  elements.toast.hidden = false;
  state.toastTimer = window.setTimeout(() => {
    elements.toast.hidden = true;
  }, 3600);
}

function renderHeader() {
  const { payload } = state;
  elements.refreshState.textContent = payload.route_origin_current
    ? `Report refreshed ${formatDateTime(payload.generated_at)}`
    : "Routes need refresh from the new origin";
  elements.summaryNew.textContent = formatNumber(payload.summary.new_actionable_count);
  elements.summaryActionable.textContent = formatNumber(payload.summary.actionable_count);
  elements.summaryAttention.textContent = formatNumber(payload.summary.needs_review_count);
  elements.pipelineNote.textContent = payload.route_origin_current
    ? `${formatNumber(payload.summary.source_result_count)} listings collected → ` +
      `${formatNumber(payload.summary.eligible_count)} eligible → ` +
      `${formatNumber(payload.summary.actionable_count)} actionable. ` +
      `${formatNumber(payload.summary.pareto_frontier_count || 0)} Pareto trade-offs. ` +
      `${formatNumber(payload.summary.remote_actionable_count || 0)} remote and ` +
      `${formatNumber(payload.summary.evening_actionable_count || 0)} station-evening. ` +
      `${formatNumber(payload.summary.closing_soon_count)} close within 72 hours.` +
      (payload.availability_end_date
        ? ` Required work must finish by ${payload.availability_end_date}.`
        : "") +
      (payload.summary.expired_jobs_flushed_count
        ? ` ${formatNumber(payload.summary.expired_jobs_flushed_count)} expired rows were flushed from the latest report.`
        : "") +
      (payload.summary.pay_adjusted_commute_count
        ? ` ${formatNumber(payload.summary.pay_adjusted_commute_count)} qualify through premium commute limits.`
        : "")
    : `Commute origin changed to ${payload.origin.name}. Old route minutes are hidden until bus/subway routes are recalculated.`;

  elements.reportLinks.replaceChildren();
  for (const report of payload.reports) {
    const link = createElement("a", "", report.label);
    link.href = report.url;
    link.download = "";
    elements.reportLinks.append(link);
  }
  syncWorksiteVisualization();
}

function populateDistricts() {
  const previous = elements.district.value;
  const districts = [...new Set(
    state.payload.jobs.map((job) => cleanText(job.target_district)).filter(Boolean)
  )].sort((a, b) => a.localeCompare(b, "ko"));
  elements.district.replaceChildren();
  const all = createElement("option", "", "All districts");
  all.value = "all";
  elements.district.append(all);
  for (const district of districts) {
    const option = createElement("option", "", district);
    option.value = district;
    elements.district.append(option);
  }
  elements.district.value = districts.includes(previous) ? previous : "all";
}

function filterJobs() {
  const query = cleanText(elements.search.value).toLocaleLowerCase("ko");
  const commuteSetting = elements.commute.value;
  const maxCommute = ["all", "policy"].includes(commuteSetting) ? null : Number(commuteSetting);
  state.filteredJobs = filterAndSortJobModels(
    state.jobViewModels,
    {
      query,
      status: elements.status.value,
      schedule: elements.schedule.value,
      commutePolicyOnly: commuteSetting === "policy",
      maxCommute,
      minPay: Number(elements.pay.value),
      district: elements.district.value,
      zone: elements.zone.value,
      paretoOnly: elements.tradeoff.value === "pareto",
      newOnly: elements.newOnly.checked,
      closingOnly: elements.closingOnly.checked,
      requirementsOnly: elements.requirements.checked,
      easyApplyOnly: elements.easyApply.checked,
      sort: elements.sort.value,
    },
    state.comparedIds,
    state.shortlistOnly,
  );
  const showingPareto = elements.tradeoff.value === "pareto";
  syncAlbamonMode();
  elements.paretoExplainer.hidden = !showingPareto;
  elements.resultsEyebrow.textContent = showingPareto ? "PAY–COMMUTE FRONTIER" : "RANKED RESULTS";
  renderResults();
  syncWorksiteVisualization();
}

function badge(text, variant = "") {
  return createElement("span", `badge${variant ? ` badge--${variant}` : ""}`, text);
}

function setCompareButtonState(button, job, selected) {
  button.textContent = selected ? "✓" : "+";
  button.setAttribute("aria-pressed", String(selected));
  button.setAttribute(
    "aria-label",
    `${selected ? "Remove" : "Add"} ${job.title || job.company || "job"} ${selected ? "from" : "to"} comparison`,
  );
  button.title = selected ? "Remove from comparison" : "Shortlist for comparison";
}

function renderResults() {
  elements.resultCount.textContent = formatNumber(state.filteredJobs.length);
  const resultKind = elements.tradeoff.value === "pareto" ? "frontier jobs" : "matches";
  elements.filterFeedback.textContent =
    `${formatNumber(state.filteredJobs.length)} ${state.shortlistOnly ? "shortlisted" : resultKind} · ` +
    `${formatNumber(state.payload.jobs.length)} report jobs`;
  elements.empty.hidden = state.filteredJobs.length !== 0;
  const fragment = document.createDocumentFragment();
  for (const job of state.filteredJobs) {
    const row = createElement("tr");
    row.dataset.jobId = job.recruit_no;
    row.tabIndex = 0;
    row.setAttribute("aria-label", `${job.company}, ${job.title}`);
    row.classList.toggle("is-selected", Number(job.recruit_no) === state.selectedId);

    const compared = state.comparedIds.has(Number(job.recruit_no));
    const compareCell = createElement("td", "compare-cell");
    const compareButton = createElement("button", "compare-toggle");
    compareButton.type = "button";
    compareButton.dataset.compareId = job.recruit_no;
    setCompareButtonState(compareButton, job, compared);
    compareCell.append(compareButton);
    row.append(compareCell);

    const jobCell = createElement("td", "job-cell");
    const badges = createElement("div", "badge-row");
    if (job.is_new) badges.append(badge("New", "new"));
    if (job.pareto_frontier) badges.append(badge("Pareto", "pareto"));
    if (job.is_closing_soon) badges.append(badge("Closing soon", "warning"));
    if (job.application_disposition === "applied") badges.append(badge("History", "applied"));
    if (job.weekend_evidence_type === "exact") badges.append(badge("Exact weekend", "evidence"));
    if (job.weekend_evidence_type === "negotiable") badges.append(badge("Weekend flexible"));
    if ((job.eligibility_lanes || []).includes("remote_short_term")) badges.append(badge("Remote", "evidence"));
    if (job.availability_end_date) badges.append(badge(`Ends by ${job.availability_end_date.slice(5)}`, "evidence"));
    if ((job.eligibility_lanes || []).includes("aug_15_17_single_day")) badges.append(badge("Aug 15/17 one day", "evidence"));
    if ((job.eligibility_lanes || []).includes("songpa_late_night")) badges.append(badge("Station evening", "evidence"));
    if (job.pay_adjusted_commute) badges.append(badge("Premium commute", "warning"));
    if ((job.hard_requirements || []).length > 0) badges.append(badge("Requirements", "warning"));
    if (badges.childElementCount) jobCell.append(badges);
    appendText(jobCell, "strong", "", job.title || "Untitled job");
    appendText(jobCell, "small", "", `${job.company || "Unknown company"} · ${job.target_district || job.area || "Area unknown"}`);
    row.append(jobCell);

    const payCell = createElement("td");
    appendText(payCell, "span", "pay-value", `₩${formatNumber(job.comparable_hourly_pay)}/hr`);
    appendText(payCell, "span", "cell-secondary", `${job.salary_type || "Pay"} ${job.salary || "not stated"}`);
    row.append(payCell);

    const scheduleCell = createElement("td");
    appendText(scheduleCell, "span", "", job.work_days || "Schedule check");
    appendText(
      scheduleCell,
      "span",
      "cell-secondary",
      (job.late_night_shift_options || []).join(" · ") ||
        (job.target_date_options || []).join(" · ") ||
        (job.weekend_shift_options || []).join(" · ") ||
        [job.work_time, job.work_time_option].filter(Boolean).join(" · ") ||
        "Time not stated",
    );
    row.append(scheduleCell);

    const commuteCell = createElement("td");
    appendText(
      commuteCell,
      "span",
      "commute-value",
      job.remote_job
        ? "No commute"
        : hasNumericValue(job.transit_minutes)
          ? `${job.transit_minutes} min`
          : "Unverified",
    );
    appendText(
      commuteCell,
      "span",
      "cell-secondary",
      job.remote_job
        ? "Remote work"
        : job.evening_nearest_station
        ? `${job.evening_nearest_station} · ~${job.evening_station_walk_minutes} min walk`
        : job.pay_adjusted_commute
        ? `Premium limit · up to ${job.commute_limit_minutes} min`
        : hasNumericValue(job.transit_transfers)
          ? `${job.transit_transfers} transfer${job.transit_transfers === 1 ? "" : "s"}`
          : `From ${state.payload.origin.name}`,
    );
    row.append(commuteCell);

    const statusCell = createElement("td");
    statusCell.append(createElement("span", `status-pill status-pill--${job.application_disposition}`, STATUS_LABELS[job.application_disposition] || job.application_disposition));
    appendText(statusCell, "span", "cell-secondary", `Tier ${job.utility_tier || "—"} · #${job.utility_rank || "—"}`);
    row.append(statusCell);

    fragment.append(row);
  }
  elements.rows.replaceChildren(fragment);
}

function jobById(recruitNo) {
  return state.jobsById.get(recruitNo) || null;
}

function updateCompareButton(recruitNo) {
  const button = elements.rows.querySelector(`[data-compare-id="${recruitNo}"]`);
  if (!button) return;
  const selected = state.comparedIds.has(recruitNo);
  const job = jobById(recruitNo);
  if (job) setCompareButtonState(button, job, selected);
}

function renderShortlistFilter() {
  const count = state.comparedIds.size;
  if (count === 0) state.shortlistOnly = false;
  elements.shortlistTotal.textContent = formatNumber(count);
  elements.shortlistFilter.disabled = count === 0;
  elements.shortlistFilter.setAttribute("aria-pressed", String(state.shortlistOnly));
  elements.shortlistFilter.title = state.shortlistOnly
    ? "Show all matching jobs"
    : count
      ? `Show only ${count} shortlisted job${count === 1 ? "" : "s"}`
      : "Shortlist jobs with the plus buttons first";
}

function toggleComparison(recruitNo) {
  const wasShortlistOnly = state.shortlistOnly;
  if (state.comparedIds.has(recruitNo)) {
    state.comparedIds.delete(recruitNo);
  } else if (state.comparedIds.size >= 3) {
    showToast("Compare up to three jobs at a time.", true);
    return;
  } else {
    state.comparedIds.add(recruitNo);
  }
  if (state.comparedIds.size === 0) state.shortlistOnly = false;
  if (wasShortlistOnly) filterJobs();
  else updateCompareButton(recruitNo);
  renderComparisonTray();
}

function renderComparisonTray() {
  renderShortlistFilter();
  const jobs = [...state.comparedIds].map(jobById).filter(Boolean);
  elements.compareTray.hidden = jobs.length === 0;
  document.body.classList.toggle("has-comparison", jobs.length > 0);
  elements.compareCount.textContent = `${jobs.length} of 3 selected`;
  elements.openCompare.disabled = jobs.length < 2;
  elements.compareChips.replaceChildren();
  for (const job of jobs) {
    const chip = createElement("div", "compare-chip");
    appendText(chip, "span", "", job.company || job.title);
    const remove = createElement("button", "", "×");
    remove.type = "button";
    remove.setAttribute("aria-label", `Remove ${job.company || job.title} from comparison`);
    remove.addEventListener("click", () => toggleComparison(Number(job.recruit_no)));
    chip.append(remove);
    elements.compareChips.append(chip);
  }
}

function comparisonFact(label, value) {
  const item = createElement("div", "comparison-fact");
  appendText(item, "span", "", label);
  appendText(item, "strong", "", value || "Not stated");
  return item;
}

function openComparison() {
  const jobs = [...state.comparedIds].map(jobById).filter(Boolean);
  if (jobs.length < 2) return;
  elements.comparisonGrid.className = `comparison-grid comparison-grid--${jobs.length}`;
  elements.comparisonGrid.replaceChildren();
  for (const job of jobs) {
    const card = createElement("article", "comparison-card");
    appendText(card, "span", `status-pill status-pill--${job.application_disposition}`, STATUS_LABELS[job.application_disposition] || job.application_disposition);
    appendText(card, "h3", "", job.title);
    appendText(card, "p", "comparison-company", `${job.company} · ${job.target_district || job.area || "Area unknown"}`);
    card.append(
      comparisonFact("Comparable pay", `₩${formatNumber(job.comparable_hourly_pay)}/hr`),
      comparisonFact("Verified commute", job.remote_job ? "No commute · remote" : hasNumericValue(job.transit_minutes) ? `${job.transit_minutes} min · ${job.transit_transfers ?? "?"} transfer(s)` : "Unverified"),
      comparisonFact("Priority zone", job.search_zone_label || "Outer Seoul"),
      comparisonFact("Commute policy", job.commute_policy_reason || `Up to ${job.commute_limit_minutes || 50} min`),
      comparisonFact(
        "Schedule lane",
        (job.eligibility_lanes || []).includes("remote_short_term")
          ? "Remote · flexible/weekend short-term"
          : (job.eligibility_lanes || []).includes("songpa_late_night")
          ? "Station corridor · 19:00–02:00 · station walk ≈20 min"
          : (job.eligibility_lanes || []).includes("aug_15_17_single_day")
            ? `One day · ${(job.target_date_options || []).join(" or ")}`
          : job.weekend_evidence_type === "exact"
            ? "Exact Sat/Sun evidence"
            : "Negotiable weekend option",
      ),
      comparisonFact(
        "Schedule",
        (job.late_night_shift_options || []).join(" · ") ||
          (job.target_date_options || []).join(" · ") ||
          (job.weekend_shift_options || []).join(" · ") ||
          `${job.work_days || "Check days"} · ${job.work_time || "Check time"}`,
      ),
      comparisonFact("Duration", job.duration_requirement || "Numeric minimum not stated"),
      comparisonFact("Availability deadline", job.availability_end_date || "Not set"),
      comparisonFact("Hard requirements", (job.hard_requirements || []).join("; ") || "None stated"),
      comparisonFact("Pareto trade-off", job.pareto_frontier ? "Yes · not beaten on both pay and commute" : "No"),
      comparisonFact("Report position", `Tier ${job.utility_tier || "—"} · Rank #${job.utility_rank || "—"}`),
    );
    const view = createElement("button", "primary-button", "View full details");
    view.type = "button";
    view.addEventListener("click", () => {
      elements.compareDialog.close();
      selectJob(Number(job.recruit_no), elements.openCompare);
    });
    card.append(view);
    elements.comparisonGrid.append(card);
  }
  elements.compareDialog.scrollTop = 0;
  elements.comparisonGrid.scrollTop = 0;
  elements.comparisonGrid.scrollLeft = 0;
  elements.compareDialog.showModal();
}

function updateSelection(previousId, nextId) {
  for (const recruitNo of [previousId, nextId]) {
    if (!recruitNo) continue;
    const row = elements.rows.querySelector(`[data-job-id="${recruitNo}"]`);
    if (row) row.classList.toggle("is-selected", recruitNo === nextId);
    const marker = state.markers.get(recruitNo);
    const job = jobById(recruitNo);
    if (marker && job) {
      const selected = recruitNo === nextId;
      marker.setIcon(markerIcon(job, selected));
      marker.setZIndexOffset(selected ? 1000 : 0);
    }
  }
}

function markerIcon(job, selected) {
  const selectedClass = selected ? " map-marker--selected" : "";
  const mutedClass = job.application_disposition === "apply" ? "" : " map-marker--muted";
  return window.L.divIcon({
    className: "",
    html: `<div class="map-marker${selectedClass}${mutedClass}"><span>${selected ? "●" : ""}</span></div>`,
    iconSize: selected ? [30, 30] : [24, 24],
    iconAnchor: selected ? [8, 27] : [6, 21],
  });
}

function loadMapLibrary() {
  if (window.L) return Promise.resolve();
  if (state.mapLoadPromise) return state.mapLoadPromise;
  elements.mapStatusTitle.textContent = "Loading worksite map…";
  elements.mapStatusMessage.textContent = "The shortlist remains fully usable while map assets load.";

  const stylesheet = document.createElement("link");
  stylesheet.rel = "stylesheet";
  stylesheet.href = `${LEAFLET_BASE_URL}/leaflet.css`;
  stylesheet.crossOrigin = "anonymous";
  const script = document.createElement("script");
  script.src = `${LEAFLET_BASE_URL}/leaflet.js`;
  script.crossOrigin = "anonymous";

  state.mapLoadPromise = new Promise((resolve, reject) => {
    let stylesheetReady = false;
    let scriptReady = false;
    let settled = false;
    const fail = (error) => {
      if (settled) return;
      settled = true;
      window.clearTimeout(timeout);
      reject(error);
    };
    const timeout = window.setTimeout(() => fail(new Error("Map assets timed out")), 12000);
    const finish = () => {
      if (settled || !stylesheetReady || !scriptReady) return;
      settled = true;
      window.clearTimeout(timeout);
      resolve();
    };
    stylesheet.addEventListener("load", () => { stylesheetReady = true; finish(); }, { once: true });
    stylesheet.addEventListener("error", () => fail(new Error("Map styles unavailable")), { once: true });
    script.addEventListener("load", () => { scriptReady = true; finish(); }, { once: true });
    script.addEventListener("error", () => fail(new Error("Map library unavailable")), { once: true });
    document.head.append(stylesheet, script);
  });
  return state.mapLoadPromise;
}

async function requestMapLoad() {
  if (state.map) {
    syncWorksiteVisualization();
    return;
  }
  try {
    await loadMapLibrary();
    initializeMap();
    renderMapMarkers();
    syncWorksiteVisualization();
  } catch {
    if (state.albamonVisualization === "map") {
      elements.map.hidden = true;
      elements.mapFallback.hidden = false;
    }
    const loader = elements.mapFallback.querySelector(".map-loader");
    if (loader) loader.hidden = true;
    elements.mapStatusTitle.textContent = "Map unavailable";
    elements.mapStatusMessage.textContent = "The ranked list still has every job and exact commute result.";
  }
}

function initializeMap() {
  if (state.map || !window.L || !state.payload) return;
  const { origin } = state.payload;
  state.map = window.L.map(elements.map, { zoomControl: true }).setView([origin.latitude, origin.longitude], 12);
  window.L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: "&copy; OpenStreetMap contributors",
    maxZoom: 19,
  }).addTo(state.map);
  const originIcon = window.L.divIcon({
    className: "",
    html: '<div class="origin-marker">START</div>',
    iconSize: [28, 28],
    iconAnchor: [14, 14],
  });
  state.originMarker = window.L.marker([origin.latitude, origin.longitude], { icon: originIcon, zIndexOffset: 900 })
    .addTo(state.map)
    .bindTooltip(origin.name, { direction: "top" });
}

function renderMapMarkers() {
  if (state.albamonVisualization !== "map") return;
  initializeMap();
  if (!state.map) return;
  const visibleJobs = new Map(
    state.filteredJobs
      .filter(hasPlausibleMapLocation)
      .map((job) => [Number(job.recruit_no), job])
  );
  let membershipChanged = false;
  for (const [recruitNo, marker] of state.markers) {
    if (visibleJobs.has(recruitNo)) continue;
    marker.remove();
    state.markers.delete(recruitNo);
    membershipChanged = true;
  }
  for (const [recruitNo, job] of visibleJobs) {
    if (state.markers.has(recruitNo)) continue;
    const latitude = Number(job.latitude);
    const longitude = Number(job.longitude);
    const selected = recruitNo === state.selectedId;
    const marker = window.L.marker([latitude, longitude], {
      icon: markerIcon(job, selected),
      zIndexOffset: selected ? 1000 : 0,
      title: `${job.company} — ${job.title}`,
    });
    marker.bindTooltip(`${cleanText(job.company)} · ${job.transit_minutes ?? "?"} min`, { direction: "top" });
    marker.on("click", () => selectJob(recruitNo, elements.map));
    marker.addTo(state.map);
    state.markers.set(recruitNo, marker);
    membershipChanged = true;
  }
  if (!membershipChanged) return;
  const bounds = [[state.payload.origin.latitude, state.payload.origin.longitude]];
  for (const job of visibleJobs.values()) {
    bounds.push([Number(job.latitude), Number(job.longitude)]);
  }
  if (bounds.length > 1) state.map.fitBounds(bounds, { padding: [28, 28], maxZoom: 13 });
  else state.map.setView(bounds[0], 12);
}

function detailSection(title) {
  const section = createElement("section", "detail-section");
  appendText(section, "h3", "", title);
  return section;
}

function detailList(values, emptyText = "None stated") {
  const list = createElement("ul", "detail-list");
  const items = Array.isArray(values) && values.length ? values : [emptyText];
  for (const value of items) {
    if (value && typeof value === "object") {
      const label = cleanText(
        value.title || value.question || value.description || value.content
      );
      appendText(
        list,
        "li",
        "",
        label ? `${label}${value.required ? " (required)" : ""}` : "Details not stated",
      );
    } else {
      appendText(list, "li", "", value);
    }
  }
  return list;
}

function fact(label, value) {
  const container = createElement("div", "fact");
  appendText(container, "span", "", label);
  appendText(container, "strong", "", value || "Not stated");
  return container;
}

function actionLink(text, href, primary = false) {
  const link = createElement("a", primary ? "primary-button" : "secondary-button", text);
  link.href = href;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  return link;
}

function renderDrawer(job) {
  elements.drawerKicker.textContent = `${STATUS_LABELS[job.application_disposition] || "Job"} · Tier ${job.utility_tier || "—"} · Rank #${job.utility_rank || "—"}`;
  elements.drawerTitle.textContent = job.title || "Untitled job";
  elements.drawerCompany.textContent = `${job.company || "Unknown company"} · ${job.target_district || job.area || "Area unknown"}`;
  elements.drawerBody.replaceChildren();

  const actions = createElement("div", "detail-actions");
  actions.append(actionLink("Open posting", job.url, true));
  if (job.transit_route_url) actions.append(actionLink("Verified route", job.transit_route_url));
  const copyButton = createElement("button", "secondary-button", "Copy message");
  copyButton.type = "button";
  copyButton.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(job.message || "");
      showToast("Application message copied.");
    } catch {
      showToast("Clipboard access was unavailable.", true);
    }
  });
  actions.append(copyButton);
  elements.drawerBody.append(actions);

  const overview = detailSection("Decision snapshot");
  const grid = createElement("div", "detail-grid");
  grid.append(
    fact("Comparable pay", `₩${formatNumber(job.comparable_hourly_pay)}/hr`),
    fact("Headline pay", `${job.salary_type || ""} ${job.salary || "Not stated"}`.trim()),
    fact(
      (job.eligibility_lanes || []).includes("remote_short_term")
        ? "Remote schedule"
        : (job.eligibility_lanes || []).includes("songpa_late_night")
        ? "Night schedule"
        : (job.eligibility_lanes || []).includes("aug_15_17_single_day")
          ? "Aug 15/17 one-day schedule"
          : "Weekend schedule",
      (job.late_night_shift_options || []).join(" · ") ||
        (job.target_date_options || []).join(" · ") ||
        (job.weekend_shift_options || []).join(" · ") ||
        `${job.work_days || "Check schedule"} · ${job.work_time || "Time not stated"}`,
    ),
    fact("Verified commute", job.remote_job ? "No commute · remote" : hasNumericValue(job.transit_minutes) ? `${job.transit_minutes} min · ${hasNumericValue(job.transit_transfers) ? job.transit_transfers : "?"} transfer(s)` : "Route not verified"),
    ...((job.eligibility_lanes || []).includes("songpa_late_night")
      ? [fact("Station-area reach", job.evening_nearest_station
          ? `${job.evening_nearest_station} · ~${job.evening_station_walk_minutes} min walk (${job.evening_station_distance_km} km direct)`
          : "Needs worksite-coordinate verification")]
      : []),
    fact("Priority zone", job.search_zone_label || "Outer Seoul"),
    fact("Commute policy", job.commute_policy_reason || `Up to ${job.commute_limit_minutes || 50} min`),
    fact("Duration", job.duration_requirement || "Numeric minimum not stated"),
    fact("Availability deadline", job.availability_end_date || "Not set"),
    fact("Application", (job.application_methods || []).join(", ") || "Check posting"),
  );
  overview.append(grid);
  elements.drawerBody.append(overview);

  if ((job.late_night_shift_journeys || []).length) {
    const commute = detailSection("Exact late-night commute");
    commute.append(detailList(job.late_night_shift_journeys.map(lateNightJourneyText)));
    const hasNaverFutureRoute = job.late_night_shift_journeys.some((journey) =>
      [journey.outbound, journey.return].some((leg) => leg?.mode === "public_transit_future_time"),
    );
    appendText(
      commute,
      "div",
      "warning-box",
      hasNaverFutureRoute
        ? "Naver future-time routes include timetable waiting for the representative date. Recheck the actual work date; the Kakao road-priority bicycle route remains the fallback."
        : "This is still a provisional Kakao transit pattern. Run the Naver future-time pass for the exact shift; the Kakao road-priority bicycle route remains the fallback.",
    );
    elements.drawerBody.append(commute);
  }

  if ((job.salary_notes || []).length) {
    const payDetail = detailSection("Pay details from the posting");
    payDetail.append(detailList(job.salary_notes));
    elements.drawerBody.append(payDetail);
  }

  if (job.fit_warning || job.disposition_reason || job.distance_exclusion_reason) {
    const warning = detailSection("Attention");
    appendText(warning, "div", "warning-box", job.fit_warning || job.disposition_reason || job.distance_exclusion_reason);
    elements.drawerBody.append(warning);
  }

  const ranking = detailSection("Why it ranks here");
  ranking.append(detailList(job.utility_reasons, "Passed all hard filters"));
  if ((job.utility_cautions || []).length) {
    appendText(ranking, "h3", "", "Cautions");
    ranking.append(detailList(job.utility_cautions));
  }
  elements.drawerBody.append(ranking);

  const conditions = detailSection("Requirements & evidence");
  appendText(conditions, "strong", "", "Hard requirements");
  conditions.append(detailList(job.hard_requirements, "No explicit hard requirements"));
  if ((job.pre_application_questions || []).length) {
    appendText(conditions, "strong", "", "Pre-application questions");
    conditions.append(detailList(job.pre_application_questions));
  }
  elements.drawerBody.append(conditions);

  const message = detailSection("Tailored Albamon message");
  appendText(message, "p", "message-box", job.message || "No message generated.");
  elements.drawerBody.append(message);

  const verify = detailSection("Check before sending");
  verify.append(detailList(job.verify_before_sending, "Review the posting and schedule"));
  if (job.optional_sentence_if_true) {
    appendText(verify, "div", "warning-box", `Only add if true: ${job.optional_sentence_if_true}`);
  }
  elements.drawerBody.append(verify);

  if (["apply", "maybe_later", "do_not_apply"].includes(job.application_disposition)) {
    const decision = detailSection("Your decision");
    const form = createElement("div", "decision-form");
    const label = createElement("label", "", "Reason for deferring or skipping");
    label.htmlFor = "decision-reason";
    const textarea = createElement("textarea");
    textarea.id = "decision-reason";
    textarea.maxLength = 500;
    textarea.placeholder = "Example: schedule overlaps, confirm certificate, low priority…";
    textarea.value = job.disposition_reason || "";
    const buttons = createElement("div", "decision-buttons");
    if (job.application_disposition !== "maybe_later") {
      const defer = createElement("button", "secondary-button", "Maybe later");
      defer.type = "button";
      defer.addEventListener("click", () => saveDisposition(job, "maybe_later", textarea.value));
      buttons.append(defer);
    }
    if (job.application_disposition !== "do_not_apply") {
      const skip = createElement("button", "danger-button", "Skip job");
      skip.type = "button";
      skip.addEventListener("click", () => saveDisposition(job, "do_not_apply", textarea.value));
      buttons.append(skip);
    }
    if (job.application_disposition !== "apply") {
      const restore = createElement("button", "primary-button", "Restore actionable");
      restore.type = "button";
      restore.addEventListener("click", () => saveDisposition(job, "apply", ""));
      buttons.append(restore);
    }
    form.append(label, textarea, buttons);
    decision.append(form);
    elements.drawerBody.append(decision);
  }

  const metadata = createElement("div", "detail-meta");
  metadata.append(
    createElement("span", "", `Albamon ID ${job.recruit_no}`),
    createElement("span", "", `First seen ${formatDateTime(job.first_seen_at)}`),
    createElement("span", "", cleanText(job.working_address || job.area)),
  );
  elements.drawerBody.append(metadata);
}

function renderDrawerLoading(job) {
  elements.drawerKicker.textContent = `${STATUS_LABELS[job.application_disposition] || "Job"} · Tier ${job.utility_tier || "—"} · Rank #${job.utility_rank || "—"}`;
  elements.drawerTitle.textContent = job.title || "Untitled job";
  elements.drawerCompany.textContent = `${job.company || "Unknown company"} · ${job.target_district || job.area || "Area unknown"}`;
  const loading = createElement("div", "drawer-loading");
  loading.append(createElement("span", "map-loader"));
  appendText(loading, "strong", "", "Loading full decision evidence…");
  appendText(loading, "span", "", "The table stays compact by loading messages and ranking details only when needed.");
  elements.drawerBody.replaceChildren(loading);
}

function renderDrawerError(job, message) {
  const error = createElement("div", "drawer-loading");
  appendText(error, "strong", "", "Could not load job details");
  appendText(error, "span", "", message);
  const retry = createElement("button", "primary-button", "Try again");
  retry.type = "button";
  retry.addEventListener("click", () => {
    state.jobDetails.delete(Number(job.recruit_no));
    selectJob(Number(job.recruit_no), retry);
  });
  error.append(retry);
  elements.drawerBody.replaceChildren(error);
}

async function fetchJobDetail(recruitNo, signal) {
  if (state.jobDetails.has(recruitNo)) return state.jobDetails.get(recruitNo);
  const response = await fetch(`/api/jobs/${recruitNo}`, { signal });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || "Job detail is unavailable");
  state.jobDetails.set(recruitNo, payload.job);
  return payload.job;
}

async function selectJob(recruitNo, trigger) {
  const job = jobById(recruitNo);
  if (!job) return;
  const previousId = state.selectedId;
  state.selectedId = recruitNo;
  state.lastFocused = trigger || document.activeElement;
  updateSelection(previousId, recruitNo);
  renderDrawerLoading(job);
  elements.drawerScrim.hidden = false;
  elements.drawer.inert = false;
  elements.drawer.classList.add("is-open");
  elements.drawer.setAttribute("aria-hidden", "false");
  window.setTimeout(() => elements.closeDrawer.focus(), 50);
  const requestId = ++state.detailRequestId;
  if (state.detailAbortController) state.detailAbortController.abort();
  const controller = new AbortController();
  state.detailAbortController = controller;
  try {
    const detail = await fetchJobDetail(recruitNo, controller.signal);
    if (requestId === state.detailRequestId && state.selectedId === recruitNo) {
      renderDrawer(detail);
    }
  } catch (error) {
    if (error.name === "AbortError") return;
    if (requestId === state.detailRequestId && state.selectedId === recruitNo) {
      renderDrawerError(job, error.message || "Try reloading the report.");
    }
  } finally {
    if (state.detailAbortController === controller) {
      state.detailAbortController = null;
    }
  }
}

function closeDrawer() {
  state.detailRequestId += 1;
  if (state.detailAbortController) state.detailAbortController.abort();
  state.detailAbortController = null;
  elements.drawer.classList.remove("is-open");
  elements.drawer.setAttribute("aria-hidden", "true");
  elements.drawer.inert = true;
  elements.drawerScrim.hidden = true;
  if (state.lastFocused && typeof state.lastFocused.focus === "function") state.lastFocused.focus();
}

async function saveDisposition(job, status, reason) {
  if (status !== "apply" && !cleanText(reason)) {
    showToast("Add a short reason first.", true);
    return;
  }
  try {
    const response = await fetch("/api/dispositions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ recruit_no: job.recruit_no, status, reason }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Could not save the decision");
    showToast(status === "apply" ? "Job restored to actionable." : "Decision saved and reports rebuilt.");
    await loadData({ keepSelection: true });
  } catch (error) {
    showToast(error.message || "Could not save the decision.", true);
  }
}

function setMobileView(view) {
  const showMap = view === "map";
  elements.workspace.classList.toggle("show-map", showMap);
  elements.tableTab.classList.toggle("is-active", !showMap);
  elements.mapTab.classList.toggle("is-active", showMap);
  elements.tableTab.setAttribute("aria-selected", String(!showMap));
  elements.mapTab.setAttribute("aria-selected", String(showMap));
  if (showMap) {
    if (state.albamonVisualization === "map") {
      requestMapLoad().then(() => {
        if (state.map) window.setTimeout(() => state.map.invalidateSize(), 20);
      });
    } else {
      syncWorksiteVisualization();
    }
  }
}

function resetFilters() {
  elements.search.value = "";
  elements.status.value = "apply";
  elements.schedule.value = "all";
  elements.commute.value = "policy";
  elements.pay.value = "0";
  elements.tradeoff.value = "all";
  elements.district.value = "all";
  elements.zone.value = "all";
  elements.newOnly.checked = false;
  elements.closingOnly.checked = false;
  elements.requirements.checked = false;
  elements.easyApply.checked = false;
  elements.sort.value = "rank";
  state.shortlistOnly = false;
  try {
    window.localStorage.removeItem(FILTER_STORAGE_KEY);
  } catch {
    // Reset still applies to the current view when storage is unavailable.
  }
  renderShortlistFilter();
  syncAlbamonMode();
  filterJobs();
}

async function loadData({ keepSelection = false } = {}) {
  const drawerWasOpen = elements.drawer.classList.contains("is-open");
  elements.reload.disabled = true;
  elements.refreshState.textContent = "Loading latest report…";
  try {
    const response = await fetch("/api/dashboard", { cache: "no-store" });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Dashboard data is unavailable");
    if (payload.payload_version !== 3) throw new Error("Restart the dashboard server to load the optimized API");
    if (state.detailAbortController) state.detailAbortController.abort();
    state.detailAbortController = null;
    for (const marker of state.markers.values()) marker.remove();
    state.markers.clear();
    state.payload = payload;
    state.jobDetails.clear();
    state.comparedIds = new Set(
      [...state.comparedIds].filter((recruitNo) => payload.jobs.some((job) => Number(job.recruit_no) === recruitNo))
    );
    if (state.comparedIds.size === 0) state.shortlistOnly = false;
    if (!keepSelection) state.selectedId = null;
    renderHeader();
    populateDistricts();
    restoreFilterPreferences();
    if (!payload.route_origin_current && elements.status.value === "apply") {
      elements.status.value = "all";
    }
    if (!payload.route_origin_current) elements.commute.value = "all";
    buildJobIndexes();
    filterJobs();
    renderComparisonTray();
    if (keepSelection && state.selectedId && drawerWasOpen) {
      const selected = jobById(state.selectedId);
      if (selected) selectJob(state.selectedId, state.lastFocused);
      else closeDrawer();
    }
    if (!window.matchMedia("(max-width: 900px)").matches) {
      if ("requestIdleCallback" in window) {
        window.requestIdleCallback(requestMapLoad, { timeout: 800 });
      } else {
        window.setTimeout(requestMapLoad, 0);
      }
    }
  } catch (error) {
    elements.refreshState.textContent = "Report unavailable";
    elements.rows.replaceChildren();
    elements.empty.hidden = false;
    elements.empty.querySelector("h3").textContent = "Could not load the latest report";
    elements.empty.querySelector("p").textContent = error.message || "Run the reporting pipeline first.";
    showToast(error.message || "Could not load the report.", true);
  } finally {
    elements.reload.disabled = false;
  }
}

elements.search.addEventListener("input", () => {
  if (state.payload) scheduleFilter(80);
});

// Rows are replaced after every filter change; one delegated handler avoids
// allocating and discarding multiple listeners for every rendered job.
elements.rows.addEventListener("click", (event) => {
  const target = event.target instanceof Element ? event.target : null;
  if (!target) return;
  const compareButton = target.closest("[data-compare-id]");
  if (compareButton) {
    toggleComparison(Number(compareButton.dataset.compareId));
    return;
  }
  const row = target.closest("tr[data-job-id]");
  if (row) selectJob(Number(row.dataset.jobId), row);
});
elements.rows.addEventListener("keydown", (event) => {
  const row = event.target instanceof Element
    ? event.target.closest("tr[data-job-id]")
    : null;
  if (event.target !== row || !["Enter", " "].includes(event.key)) return;
  event.preventDefault();
  selectJob(Number(row.dataset.jobId), row);
});

for (const input of [
  elements.status,
  elements.schedule,
  elements.commute,
  elements.pay,
  elements.tradeoff,
  elements.district,
  elements.zone,
  elements.newOnly,
  elements.closingOnly,
  elements.requirements,
  elements.easyApply,
  elements.sort,
]) {
  input.addEventListener("change", () => {
    persistFilterPreferences();
    if (state.payload) scheduleFilter();
  });
}
elements.reload.addEventListener("click", () => {
  if (state.activeDesk === "hotels") loadHotelData({ force: true });
  else loadData({ keepSelection: true });
});
elements.clearFilters.addEventListener("click", resetFilters);
elements.shortlistFilter.addEventListener("click", () => {
  if (state.comparedIds.size === 0) return;
  state.shortlistOnly = !state.shortlistOnly;
  renderShortlistFilter();
  filterJobs();
});
elements.closeDrawer.addEventListener("click", closeDrawer);
elements.drawerScrim.addEventListener("click", closeDrawer);
elements.tableTab.addEventListener("click", () => setMobileView("table"));
elements.mapTab.addEventListener("click", () => setMobileView("map"));
elements.albamonDeskTab.addEventListener("click", () => setDesk("albamon"));
elements.hotelsDeskTab.addEventListener("click", () => setDesk("hotels"));
elements.albamonAllTab.addEventListener("click", () => setAlbamonMode("all"));
elements.albamonParetoTab.addEventListener("click", () => setAlbamonMode("pareto"));
elements.albamonMapVisual.addEventListener("click", () => setAlbamonVisualization("map"));
elements.albamonParetoVisual.addEventListener("click", () => setAlbamonVisualization("pareto"));
for (const tab of [elements.albamonDeskTab, elements.hotelsDeskTab]) {
  tab.addEventListener("keydown", (event) => {
    if (!["ArrowLeft", "ArrowRight"].includes(event.key)) return;
    event.preventDefault();
    const next = tab === elements.albamonDeskTab ? elements.hotelsDeskTab : elements.albamonDeskTab;
    next.focus();
    next.click();
  });
}
elements.hotelPayActual.addEventListener("click", () => {
  state.hotelPayMode = "actual";
  renderHotelView();
});
elements.hotelPayWeekly.addEventListener("click", () => {
  state.hotelPayMode = "weekly_allowance";
  renderHotelView();
});
elements.hotelShowAll.addEventListener("change", renderHotelView);
elements.clearCompare.addEventListener("click", () => {
  const previousIds = [...state.comparedIds];
  state.comparedIds.clear();
  const wasShortlistOnly = state.shortlistOnly;
  state.shortlistOnly = false;
  if (wasShortlistOnly) filterJobs();
  else for (const recruitNo of previousIds) updateCompareButton(recruitNo);
  renderComparisonTray();
});
elements.openCompare.addEventListener("click", openComparison);
elements.closeCompare.addEventListener("click", () => elements.compareDialog.close());
window.matchMedia("(max-width: 900px)").addEventListener("change", (event) => {
  if (!event.matches && state.payload) requestMapLoad();
});
document.addEventListener("keydown", (event) => {
  const activeElement = document.activeElement;
  const isTextEntry = activeElement?.matches("input, textarea, select, [contenteditable='true']");
  if (
    event.key === "/" &&
    !event.defaultPrevented &&
    !event.metaKey &&
    !event.ctrlKey &&
    !event.altKey &&
    !isTextEntry &&
    !elements.compareDialog.open &&
    !elements.drawer.classList.contains("is-open")
  ) {
    event.preventDefault();
    elements.search.focus();
    return;
  }
  if (
    event.key === "Escape" &&
    !elements.compareDialog.open &&
    elements.drawer.classList.contains("is-open")
  ) closeDrawer();
});

loadData();
