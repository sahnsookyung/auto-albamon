"use strict";

import {
  buildJobViewModels,
  filterAndSortJobModels,
} from "/job-view-model.mjs";
import {
  hotelComparablePay,
  hotelLocationLabel,
  hotelParetoPoints,
  hotelPointForHotel,
  hotelPointId,
  hotelPointProviderBreakdown,
  hotelPointSummary,
  hotelProviderComparisons,
  hotelProviderParetoPoints,
  hotelRows,
  hotelVerifiedCommuteMinutes,
  isHotelOnFrontier,
  mergedHotelProviderComparisons,
  providerConicGradient,
  providerBreakdownConicGradient,
} from "/hotel-pareto.mjs";
import {
  albamonFrontierGroups,
  albamonParetoPoints,
  albamonPointForJob,
  albamonPointSummary,
} from "/albamon-pareto.mjs";
import {
  filterHotels,
  hotelDecisionSummary,
  serializeHotelFilters,
} from "/hotel-view-model.mjs";
import {
  clusterProjectedPoints,
  interactivePointControls,
  PARETO_SHARED_POSITION_DISTANCE,
} from "/point-layout.mjs";
import {
  readStoredJson,
  removeStoredValue,
  writeStoredJson,
} from "/safe-storage.mjs";

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
  mapResizeObserver: null,
  originMarker: null,
  markers: new Map(),
  paretoPointsById: new Map(),
  albamonVisualization: "map",
  filterTimer: null,
  toastTimer: null,
  activeDesk: "albamon",
  hotelPayload: null,
  filteredHotels: [],
  hotelPayMode: "actual",
  payMetric: "hourly",
  hotelGranularity: "hotel",
  hotelMobileView: "list",
  groupOverlaps: true,
  albamonMarkerVisibility: { actionable: true, other: true },
  hotelMarkerVisibility: { frontier: true, comparable: true },
  hotelVisualization: "pareto",
  hotelLoadingPromise: null,
  hotelMap: null,
  hotelMapResizeObserver: null,
  hotelOriginMarker: null,
  hotelMarkers: new Map(),
  hotelMapLabels: [],
  hotelLabelFrame: null,
  hotelPointsById: new Map(),
  hotelPointClusters: new Map(),
  albamonPointClusters: new Map(),
  selectedHotelId: null,
  hotelSearchCustom: false,
  hotelOperationId: null,
  hotelOperationPoll: null,
  hotelFilterTimer: null,
  originCandidate: null,
  originPickMode: false,
  originCandidateMarker: null,
  hotelOriginCandidateMarker: null,
};

const FILTER_STORAGE_KEY = "jobscout.dashboard.filters.v1";
const HOTEL_SEARCH_STORAGE_KEY = "jobscout.hotel-date-search.v1";
const HOTEL_FILTER_STORAGE_KEY = "jobscout.hotel-result-filters.v1";
const HOTEL_SAVED_SEARCH_STORAGE_KEY = "jobscout.hotel-saved-searches.v1";
const VISUALIZATION_STORAGE_KEY = "jobscout.visualization.v1";
const LEAFLET_VERSION = "1.9.4";
const LEAFLET_BASE_URL = `https://unpkg.com/leaflet@${LEAFLET_VERSION}/dist`;
const CAPITAL_REGION_BOUNDS = { minLat: 36.8, maxLat: 38.3, minLon: 125.8, maxLon: 128.3 };

const elements = {
  albamonDeskTab: document.querySelector("#albamon-desk-tab"),
  hotelsDeskTab: document.querySelector("#hotels-desk-tab"),
  albamonView: document.querySelector("#albamon-view"),
  hotelsView: document.querySelector("#hotels-view"),
  workspace: document.querySelector(".workspace"),
  refreshState: document.querySelector("#refresh-state"),
  reportLinks: document.querySelector("#report-links"),
  summaryNew: document.querySelector("#summary-new"),
  summaryNewCard: document.querySelector("#summary-new-card"),
  summaryActionable: document.querySelector("#summary-actionable"),
  summaryActionableCard: document.querySelector("#summary-actionable-card"),
  summaryActionableDetail: document.querySelector("#summary-actionable-detail"),
  summaryAttention: document.querySelector("#summary-attention"),
  summaryAttentionCard: document.querySelector("#summary-attention-card"),
  summaryAttentionDetail: document.querySelector("#summary-attention-detail"),
  pipelineNote: document.querySelector("#pipeline-note"),
  search: document.querySelector("#search-filter"),
  status: document.querySelector("#status-filter"),
  schedule: document.querySelector("#schedule-filter"),
  commute: document.querySelector("#commute-filter"),
  pay: document.querySelector("#pay-filter"),
  district: document.querySelector("#district-filter"),
  zone: document.querySelector("#zone-filter"),
  newOnly: document.querySelector("#new-filter"),
  closingOnly: document.querySelector("#closing-filter"),
  requirements: document.querySelector("#requirements-filter"),
  easyApply: document.querySelector("#easy-apply-filter"),
  sort: document.querySelector("#sort-filter"),
  resultsPanel: document.querySelector(".results-panel"),
  resultCount: document.querySelector("#result-count"),
  resultsEyebrow: document.querySelector("#results-eyebrow"),
  filterFeedback: document.querySelector("#filter-feedback"),
  albamonFilterSummary: document.querySelector("#albamon-filter-summary"),
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
  albamonGroupOverlaps: document.querySelector("#albamon-group-overlaps"),
  albamonPayHourly: document.querySelector("#albamon-pay-hourly"),
  albamonPayTotal: document.querySelector("#albamon-pay-total"),
  albamonMarkerActionable: document.querySelector("#albamon-marker-actionable"),
  albamonMarkerOther: document.querySelector("#albamon-marker-other"),
  albamonParetoChart: document.querySelector("#albamon-pareto-chart"),
  albamonParetoTooltip: document.querySelector("#albamon-pareto-tooltip"),
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
  hotelSummaryFrontier: document.querySelector("#hotel-summary-frontier"),
  hotelSummaryPay: document.querySelector("#hotel-summary-pay"),
  hotelSummaryPayLabel: document.querySelector("#hotel-summary-pay-label"),
  hotelSummaryCommute: document.querySelector("#hotel-summary-commute"),
  hotelPayActual: document.querySelector("#hotel-pay-actual"),
  hotelPayWeekly: document.querySelector("#hotel-pay-weekly"),
  hotelPayHourly: document.querySelector("#hotel-pay-hourly"),
  hotelPayTotal: document.querySelector("#hotel-pay-total"),
  hotelGranularityHotel: document.querySelector("#hotel-granularity-hotel"),
  hotelGranularityProvider: document.querySelector("#hotel-granularity-provider"),
  hotelGroupOverlaps: document.querySelector("#hotel-group-overlaps"),
  hotelBasisNote: document.querySelector("#hotel-basis-note"),
  hotelSourceWarning: document.querySelector("#hotel-source-warning"),
  hotelMapVisual: document.querySelector("#hotel-map-visual"),
  hotelParetoVisual: document.querySelector("#hotel-pareto-visual"),
  hotelVisualTitle: document.querySelector("#hotel-visual-title"),
  hotelVisualDescription: document.querySelector("#hotel-visual-description"),
  hotelChartLegend: document.querySelector("#hotel-chart-legend"),
  hotelMapLegend: document.querySelector("#hotel-map-legend"),
  hotelChart: document.querySelector("#hotel-chart"),
  hotelParetoTooltip: document.querySelector("#hotel-pareto-tooltip"),
  hotelMap: document.querySelector("#hotel-map"),
  hotelMapFallback: document.querySelector("#hotel-map-fallback"),
  hotelFrontierLayout: document.querySelector(".hotel-frontier-layout"),
  hotelListTab: document.querySelector("#hotel-list-tab"),
  hotelVisualTab: document.querySelector("#hotel-visual-tab"),
  hotelTableHeading: document.querySelector("#hotel-table-heading"),
  hotelTableFeedback: document.querySelector("#hotel-table-feedback"),
  hotelComparableHeading: document.querySelector("#hotel-comparable-heading"),
  hotelRows: document.querySelector("#hotel-rows"),
  hotelEmpty: document.querySelector("#hotel-empty-state"),
  agencyCatalogRows: document.querySelector("#agency-catalog-rows"),
  hotelDateForm: document.querySelector("#hotel-date-form"),
  hotelStartDate: document.querySelector("#hotel-start-date"),
  hotelEndDate: document.querySelector("#hotel-end-date"),
  hotelDateSubmit: document.querySelector("#hotel-date-submit"),
  hotelDateRefresh: document.querySelector("#hotel-date-refresh"),
  hotelDateReset: document.querySelector("#hotel-date-reset"),
  hotelDateStatus: document.querySelector("#hotel-date-status"),
  hotelOperationProgress: document.querySelector("#hotel-operation-progress"),
  hotelOperationMessage: document.querySelector("#hotel-operation-message"),
  hotelOperationDetail: document.querySelector("#hotel-operation-detail"),
  hotelOperationMeter: document.querySelector("#hotel-operation-meter"),
  hotelOperationCancel: document.querySelector("#hotel-operation-cancel"),
  hotelSearchFilter: document.querySelector("#hotel-search-filter"),
  hotelFilterStartDate: document.querySelector("#hotel-filter-start-date"),
  hotelFilterEndDate: document.querySelector("#hotel-filter-end-date"),
  hotelShiftStart: document.querySelector("#hotel-shift-start"),
  hotelShiftEnd: document.querySelector("#hotel-shift-end"),
  hotelCommuteFilter: document.querySelector("#hotel-commute-filter"),
  hotelMinPayFilter: document.querySelector("#hotel-min-pay-filter"),
  hotelRoleFilter: document.querySelector("#hotel-role-filter"),
  hotelProviderFilter: document.querySelector("#hotel-provider-filter"),
  hotelFilterFeedback: document.querySelector("#hotel-filter-feedback"),
  hotelFilterSummary: document.querySelector("#hotel-filter-summary"),
  hotelClearFilters: document.querySelector("#hotel-clear-filters"),
  hotelSavedSearch: document.querySelector("#hotel-saved-search"),
  hotelSaveSearch: document.querySelector("#hotel-save-search"),
  hotelDeleteSearch: document.querySelector("#hotel-delete-search"),
  hotelShareSearch: document.querySelector("#hotel-share-search"),
  hotelSourceHealth: document.querySelector("#hotel-source-health"),
  originName: document.querySelector("#origin-name"),
  originAddress: document.querySelector("#origin-address"),
  originCoordinates: document.querySelector("#origin-coordinates"),
  originKakaoLink: document.querySelector("#origin-kakao-link"),
  originNaverLink: document.querySelector("#origin-naver-link"),
  originEditButton: document.querySelector("#origin-edit-button"),
  originEditor: document.querySelector("#origin-editor"),
  originAddressInput: document.querySelector("#origin-address-input"),
  originSearchButton: document.querySelector("#origin-search-button"),
  originPinButton: document.querySelector("#origin-pin-button"),
  originCancelButton: document.querySelector("#origin-cancel-button"),
  originCandidate: document.querySelector("#origin-candidate"),
  originCandidateName: document.querySelector("#origin-candidate-name"),
  originCandidateAddress: document.querySelector("#origin-candidate-address"),
  originCandidateCoordinates: document.querySelector("#origin-candidate-coordinates"),
  originUseButton: document.querySelector("#origin-use-button"),
  originHelp: document.querySelector("#origin-help"),
  originConfirmDialog: document.querySelector("#origin-confirm-dialog"),
  originConfirmAddress: document.querySelector("#origin-confirm-address"),
  originConfirmCoordinates: document.querySelector("#origin-confirm-coordinates"),
  originConfirmButton: document.querySelector("#origin-confirm-button"),
  pointChoiceDialog: document.querySelector("#point-choice-dialog"),
  pointChoiceTitle: document.querySelector("#point-choice-title"),
  pointChoiceLegend: document.querySelector("#point-choice-legend"),
  pointChoiceList: document.querySelector("#point-choice-list"),
  closePointChoice: document.querySelector("#close-point-choice"),
  queueFilterButtons: [...document.querySelectorAll("[data-queue-filter]")],
};

const STATUS_LABELS = {
  apply: "Actionable",
  maybe_later: "Maybe later",
  do_not_apply: "Skipped",
  excluded_by_distance: "Distance excluded",
  applied: "Applied",
};
const HOTEL_ROLE_LABELS = {
  guest_service: "Guest service / banquet",
  dishwashing_back_of_house: "Dishwashing / back of house",
  kitchen_assistant: "Kitchen assistant",
  housekeeping: "Housekeeping",
  retail_flower: "Retail / flower",
  other: "Other",
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

function localIsoDate(value = new Date()) {
  const year = value.getFullYear();
  const month = String(value.getMonth() + 1).padStart(2, "0");
  const day = String(value.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function currentOrigin() {
  return state.payload?.origin || state.hotelPayload?.origin || null;
}

function externalMapLinks(origin) {
  if (!origin) return { kakao: "#", naver: "#" };
  const label = encodeURIComponent(origin.name || origin.address || "Commute origin");
  const address = encodeURIComponent(origin.address || origin.name || "");
  return {
    kakao: origin.map_links?.kakao || `https://map.kakao.com/link/map/${label},${origin.latitude},${origin.longitude}`,
    naver: origin.map_links?.naver || `https://map.naver.com/p/search/${address}`,
  };
}

function directionLinks(destination) {
  const origin = currentOrigin();
  if (!origin || !hasPlausibleMapLocation(destination)) return null;
  const originName = encodeURIComponent(origin.name || "Origin");
  const destinationName = encodeURIComponent(destination.employer || destination.company || destination.title || "Worksite");
  return {
    kakao: `https://map.kakao.com/?map_type=TYPE_MAP&target=transit&rt=${origin.longitude},${origin.latitude},${destination.longitude},${destination.latitude}&rt1=${originName}&rt2=${destinationName}`,
    naver: `https://map.naver.com/p/directions/${origin.longitude},${origin.latitude},${originName}/${destination.longitude},${destination.latitude},${destinationName}/-/transit`,
  };
}

function renderOriginControl(origin = currentOrigin()) {
  if (!origin) return;
  elements.originName.textContent = origin.name || "Commute origin";
  elements.originAddress.textContent = origin.address || "Address not supplied";
  elements.originCoordinates.textContent = `${Number(origin.latitude).toFixed(5)}, ${Number(origin.longitude).toFixed(5)}`;
  const links = externalMapLinks(origin);
  elements.originKakaoLink.href = links.kakao;
  elements.originNaverLink.href = links.naver;
  if (elements.originEditor.hidden || !elements.originAddressInput.value) {
    elements.originAddressInput.value = origin.address || "";
  }
  if (state.originMarker) state.originMarker.setLatLng([origin.latitude, origin.longitude]);
  if (state.hotelOriginMarker) state.hotelOriginMarker.setLatLng([origin.latitude, origin.longitude]);
}

function originCandidateIcon() {
  return window.L.divIcon({
    className: "",
    html: '<div class="origin-candidate-marker"><span>+</span></div>',
    iconSize: [28, 28],
    iconAnchor: [7, 25],
  });
}

function clearOriginCandidateMarkers() {
  for (const key of ["originCandidateMarker", "hotelOriginCandidateMarker"]) {
    if (state[key]) state[key].remove();
    state[key] = null;
  }
}

function syncOriginCandidateMarkers() {
  clearOriginCandidateMarkers();
  const candidate = state.originCandidate;
  if (!candidate || !window.L) return;
  if (state.map) {
    state.originCandidateMarker = window.L.marker(
      [candidate.latitude, candidate.longitude],
      { icon: originCandidateIcon(), zIndexOffset: 1200 },
    ).addTo(state.map).bindTooltip("Proposed commute origin", { direction: "top" });
  }
  if (state.hotelMap) {
    state.hotelOriginCandidateMarker = window.L.marker(
      [candidate.latitude, candidate.longitude],
      { icon: originCandidateIcon(), zIndexOffset: 1200 },
    ).addTo(state.hotelMap).bindTooltip("Proposed commute origin", { direction: "top" });
  }
}

function setOriginCandidate(candidate) {
  state.originCandidate = candidate;
  elements.originCandidate.hidden = false;
  elements.originCandidateName.textContent = candidate.name || "Dropped map pin";
  elements.originCandidateAddress.textContent = candidate.address || "Exact coordinates selected on map";
  elements.originCandidateCoordinates.textContent = `${Number(candidate.latitude).toFixed(6)}, ${Number(candidate.longitude).toFixed(6)}`;
  syncOriginCandidateMarkers();
  const activeMap = state.activeDesk === "hotels" ? state.hotelMap : state.map;
  if (activeMap) activeMap.panTo([candidate.latitude, candidate.longitude]);
}

function stopOriginPickMode() {
  state.originPickMode = false;
  elements.originPinButton.classList.remove("is-active");
  elements.originPinButton.setAttribute("aria-pressed", "false");
  elements.originPinButton.textContent = "Drop pin on map";
}

function closeOriginEditor() {
  stopOriginPickMode();
  state.originCandidate = null;
  clearOriginCandidateMarkers();
  elements.originCandidate.hidden = true;
  elements.originEditor.hidden = true;
  elements.originAddressInput.value = currentOrigin()?.address || "";
}

function handleOriginMapClick(event) {
  if (!state.originPickMode) return;
  const latitude = Number(event.latlng.lat.toFixed(7));
  const longitude = Number(event.latlng.lng.toFixed(7));
  const typed = cleanText(elements.originAddressInput.value);
  const existingAddress = cleanText(currentOrigin()?.address);
  const customLabel = typed && typed !== existingAddress ? typed : "";
  setOriginCandidate({
    name: customLabel || "Dropped map pin",
    address: customLabel
      ? `Map pin near ${customLabel}`
      : `Map-selected coordinates ${latitude}, ${longitude}`,
    latitude,
    longitude,
  });
  stopOriginPickMode();
  elements.originHelp.textContent = "Pin selected. Check the point and confirm below; no address API call was made.";
}

function restoreHotelSearchPreferences() {
  const today = localIsoDate();
  elements.hotelStartDate.min = today;
  elements.hotelEndDate.min = today;
  const saved = readStoredJson(HOTEL_SEARCH_STORAGE_KEY);
  if (saved?.custom && saved.startDate >= today && saved.endDate >= saved.startDate) {
    state.hotelSearchCustom = true;
    elements.hotelStartDate.value = saved.startDate;
    elements.hotelEndDate.value = saved.endDate;
    return;
  }
  if (saved) removeStoredValue(HOTEL_SEARCH_STORAGE_KEY);
  state.hotelSearchCustom = false;
  elements.hotelStartDate.value = today;
  elements.hotelEndDate.value = today;
}

function persistHotelSearchPreferences() {
  writeStoredJson(HOTEL_SEARCH_STORAGE_KEY, {
    custom: state.hotelSearchCustom,
    startDate: elements.hotelStartDate.value,
    endDate: elements.hotelEndDate.value,
  });
}

function restoreVisualizationPreferences() {
  const saved = readStoredJson(VISUALIZATION_STORAGE_KEY, {}) || {};
  const legacyHotelFilters = readStoredJson(HOTEL_FILTER_STORAGE_KEY, {}) || {};
  const params = new URLSearchParams(window.location.search);
  const legacyUrlValue = params.get("hotel_groupOverlaps");
  const value = saved.groupOverlaps ?? legacyUrlValue ?? legacyHotelFilters.groupOverlaps;
  if ([true, false, "true", "false"].includes(value)) {
    state.groupOverlaps = value === true || value === "true";
  }
  if (["hourly", "shift_total"].includes(saved.payMetric)) state.payMetric = saved.payMetric;
  for (const category of ["actionable", "other"]) {
    if (typeof saved.albamonMarkers?.[category] === "boolean") {
      state.albamonMarkerVisibility[category] = saved.albamonMarkers[category];
    }
  }
  for (const category of ["frontier", "comparable"]) {
    if (typeof saved.hotelMarkers?.[category] === "boolean") {
      state.hotelMarkerVisibility[category] = saved.hotelMarkers[category];
    }
  }
  if (Object.prototype.hasOwnProperty.call(legacyHotelFilters, "groupOverlaps")) {
    const migratedHotelFilters = { ...legacyHotelFilters };
    delete migratedHotelFilters.groupOverlaps;
    writeStoredJson(HOTEL_FILTER_STORAGE_KEY, migratedHotelFilters);
  }
  if (legacyUrlValue !== null) {
    params.delete("hotel_groupOverlaps");
    const query = params.toString();
    window.history.replaceState(null, "", `${window.location.pathname}${query ? `?${query}` : ""}${window.location.hash}`);
  }
}

function persistVisualizationPreferences() {
  writeStoredJson(VISUALIZATION_STORAGE_KEY, {
    groupOverlaps: state.groupOverlaps,
    payMetric: state.payMetric,
    albamonMarkers: state.albamonMarkerVisibility,
    hotelMarkers: state.hotelMarkerVisibility,
  });
}

function compactFilterSummary(parts, fallback) {
  const visible = parts.filter(Boolean);
  if (!visible.length) return fallback;
  if (visible.length <= 3) return visible.join(" · ");
  return `${visible.slice(0, 3).join(" · ")} · +${visible.length - 3}`;
}

function selectedLabel(control) {
  return control?.selectedOptions?.[0]?.textContent?.trim() || "";
}

function updateAlbamonFilterSummary() {
  const parts = [];
  const query = cleanText(elements.search.value);
  if (query) parts.push(`“${query}”`);
  if (elements.status.value !== "apply") parts.push(selectedLabel(elements.status));
  if (elements.commute.value !== "policy") parts.push(selectedLabel(elements.commute));
  if (elements.pay.value !== "0") parts.push(selectedLabel(elements.pay));
  if (elements.schedule.value !== "all") parts.push(selectedLabel(elements.schedule));
  if (elements.district.value !== "all") parts.push(elements.district.value);
  if (elements.zone.value !== "all") parts.push(selectedLabel(elements.zone));
  if (elements.newOnly.checked) parts.push("New 24h");
  if (elements.closingOnly.checked) parts.push("Closing 72h");
  if (elements.requirements.checked) parts.push("No hard requirements");
  if (elements.easyApply.checked) parts.push("Easy apply");
  elements.albamonFilterSummary.textContent = compactFilterSummary(parts, "Default policy view");
}

function updateHotelFilterSummary() {
  const parts = [];
  const query = cleanText(elements.hotelSearchFilter.value);
  if (query) parts.push(`“${query}”`);
  if (elements.hotelFilterStartDate.value || elements.hotelFilterEndDate.value) {
    parts.push(`${elements.hotelFilterStartDate.value || "Any"}–${elements.hotelFilterEndDate.value || "Any"}`);
  }
  if (elements.hotelCommuteFilter.value) parts.push(selectedLabel(elements.hotelCommuteFilter));
  if (!["", configuredHotelMinimumPay()].includes(elements.hotelMinPayFilter.value)) {
    parts.push(selectedLabel(elements.hotelMinPayFilter));
  }
  if (elements.hotelShiftStart.value || elements.hotelShiftEnd.value) {
    parts.push(`${elements.hotelShiftStart.value || "Any"}–${elements.hotelShiftEnd.value || "Any"}`);
  }
  if (elements.hotelRoleFilter.value !== "all") parts.push(selectedLabel(elements.hotelRoleFilter));
  if (elements.hotelProviderFilter.value !== "all") parts.push(selectedLabel(elements.hotelProviderFilter));
  elements.hotelFilterSummary.textContent = compactFilterSummary(
    parts,
    `Minimum ${formatWon(configuredHotelMinimumPay())} · all monitored shifts`,
  );
}

function configuredHotelMinimumPay() {
  const configured = Number(state.hotelPayload?.policy?.minimum_actual_hourly_won);
  return Number.isFinite(configured) && configured > 0 ? String(configured) : "0";
}

function syncConfiguredHotelMinimumPay() {
  const configured = configuredHotelMinimumPay();
  const select = elements.hotelMinPayFilter;
  let configuredOption = [...select.options].find((option) => option.dataset.configured === "true");
  if (!configuredOption) {
    configuredOption = select.options[0];
    configuredOption.dataset.configured = "true";
  }
  configuredOption.value = configured;
  configuredOption.textContent = `${formatWon(configured)}+ · configured`;
  if (!select.value) select.value = configured;
}

function hotelFilterControls() {
  return {
    query: elements.hotelSearchFilter.value,
    dateStart: elements.hotelFilterStartDate.value,
    dateEnd: elements.hotelFilterEndDate.value,
    shiftStart: elements.hotelShiftStart.value,
    shiftEnd: elements.hotelShiftEnd.value,
    maxCommute: elements.hotelCommuteFilter.value,
    minimumPay: elements.hotelMinPayFilter.value,
    role: elements.hotelRoleFilter.value,
    provider: elements.hotelProviderFilter.value,
    payMode: state.hotelPayMode,
    granularity: state.hotelGranularity,
  };
}

function setHotelFilterControls(filters = {}) {
  elements.hotelSearchFilter.value = filters.query || "";
  elements.hotelFilterStartDate.value = filters.dateStart || "";
  elements.hotelFilterEndDate.value = filters.dateEnd || "";
  elements.hotelShiftStart.value = filters.shiftStart || "";
  elements.hotelShiftEnd.value = filters.shiftEnd || "";
  elements.hotelCommuteFilter.value = filters.maxCommute || "";
  elements.hotelMinPayFilter.value = filters.minimumPay ?? "";
  elements.hotelRoleFilter.value = filters.role || "all";
  elements.hotelProviderFilter.value = filters.provider || "all";
  if (["hotel", "provider"].includes(filters.granularity)) {
    state.hotelGranularity = filters.granularity;
  }
}

function hotelFiltersFromUrl() {
  const params = new URLSearchParams(window.location.search);
  const result = {};
  for (const key of [
    "query", "dateStart", "dateEnd", "shiftStart", "shiftEnd",
    "maxCommute", "minimumPay", "role", "provider", "payMode", "granularity",
  ]) {
    const value = params.get(`hotel_${key}`);
    if (value !== null) result[key] = value;
  }
  return result;
}

function restoreHotelFilters() {
  const stored = readStoredJson(HOTEL_FILTER_STORAGE_KEY, {}) || {};
  const fromUrl = hotelFiltersFromUrl();
  const filters = { ...stored, ...fromUrl };
  if (["actual", "weekly_allowance"].includes(filters.payMode)) {
    state.hotelPayMode = filters.payMode;
  }
  setHotelFilterControls(filters);
}

function persistHotelFilters({ updateUrl = true } = {}) {
  const filters = hotelFilterControls();
  writeStoredJson(HOTEL_FILTER_STORAGE_KEY, filters);
  if (!updateUrl) return;
  const params = new URLSearchParams(window.location.search);
  for (const key of [
    "query", "dateStart", "dateEnd", "shiftStart", "shiftEnd",
    "maxCommute", "minimumPay", "role", "provider", "payMode", "granularity", "groupOverlaps",
  ]) params.delete(`hotel_${key}`);
  for (const [key, value] of Object.entries(serializeHotelFilters(filters))) {
    params.set(`hotel_${key}`, value);
  }
  const query = params.toString();
  window.history.replaceState(null, "", `${window.location.pathname}${query ? `?${query}` : ""}${window.location.hash}`);
}

function savedHotelSearches() {
  const saved = readStoredJson(HOTEL_SAVED_SEARCH_STORAGE_KEY, {});
  return saved && typeof saved === "object" && !Array.isArray(saved) ? saved : {};
}

function renderSavedHotelSearches(selected = "") {
  const fragment = document.createDocumentFragment();
  const current = createElement("option", "", "Current filters");
  current.value = "";
  fragment.append(current);
  for (const name of Object.keys(savedHotelSearches()).sort((a, b) => a.localeCompare(b, "ko"))) {
    const option = createElement("option", "", name);
    option.value = name;
    fragment.append(option);
  }
  elements.hotelSavedSearch.replaceChildren(fragment);
  elements.hotelSavedSearch.value = selected;
  elements.hotelDeleteSearch.disabled = !selected;
  elements.hotelDeleteSearch.hidden = !selected;
}

function saveCurrentHotelSearch() {
  const name = cleanText(window.prompt("Name this hotel filter view:"));
  if (!name) return;
  const saved = savedHotelSearches();
  saved[name.slice(0, 60)] = hotelFilterControls();
  if (!writeStoredJson(HOTEL_SAVED_SEARCH_STORAGE_KEY, saved)) {
    showToast("This browser could not save the view.", true);
    return;
  }
  renderSavedHotelSearches(name.slice(0, 60));
  showToast(`Saved “${name.slice(0, 60)}”.`);
}

function applySavedHotelSearch(name) {
  if (!name) {
    renderSavedHotelSearches();
    return;
  }
  const filters = savedHotelSearches()[name];
  if (!filters) return;
  if (["actual", "weekly_allowance"].includes(filters.payMode)) state.hotelPayMode = filters.payMode;
  setHotelFilterControls(filters);
  renderSavedHotelSearches(name);
  persistHotelFilters();
  renderHotelView();
}

function deleteSavedHotelSearch() {
  const name = elements.hotelSavedSearch.value;
  if (!name) return;
  const saved = savedHotelSearches();
  delete saved[name];
  writeStoredJson(HOTEL_SAVED_SEARCH_STORAGE_KEY, saved);
  renderSavedHotelSearches();
  showToast(`Deleted “${name}”.`);
}

async function copyHotelSearchLink() {
  persistHotelFilters();
  try {
    await navigator.clipboard.writeText(window.location.href);
    showToast("Filter link copied.");
  } catch {
    showToast("Could not copy automatically; copy the browser URL instead.", true);
  }
}

function formatWon(value) {
  return hasNumericValue(value) ? `₩${formatNumber(value)}` : "—";
}

function formatComparablePay(value, metric = state.payMetric) {
  const formatted = formatWon(value);
  if (formatted === "—") return formatted;
  return metric === "shift_total" ? `${formatted} / shift` : `${formatted}/hr`;
}

function payMetricLabel(metric = state.payMetric) {
  return metric === "shift_total" ? "shift total" : "hourly pay";
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
  const measure = state.payMetric === "shift_total"
    ? "Shift totals use advertised daily pay where available; otherwise they are scheduled gross estimates before any unpaid break."
    : "The chart compares work-hour rates, before commute time.";
  if (state.hotelPayMode === "weekly_allowance") {
    return `Using the published conditional 주휴 rate where stated; hotels without one retain actual pay as a clearly marked fallback. ${measure} Eligibility is not guaranteed.`;
  }
  return `Using actual/base pay; ${formatWon(configuredHotelMinimumPay())}/hr remains the eligibility basis. ${measure}`;
}

function configureOverlapControl(control, count, providerBreakdown = []) {
  if (count <= 1) return;
  if (!state.groupOverlaps) {
    control.classList.add("pareto-point-button--exact-stack");
    return;
  }
  control.dataset.count = String(count);
  if (providerBreakdown.length) {
    control.classList.add("pareto-point-button--provider-cluster");
    control.style.setProperty("--cluster-source-ring", providerBreakdownConicGradient(providerBreakdown));
  }
}

function syncOverlapToggle(button, hidden) {
  button.hidden = hidden;
  button.classList.toggle("is-active", state.groupOverlaps);
  button.setAttribute("aria-pressed", String(state.groupOverlaps));
}

function renderHotelChart(hotels) {
  const providerMode = state.hotelGranularity === "provider";
  const points = providerMode
    ? hotelProviderParetoPoints(hotels, state.hotelPayMode, state.payMetric)
    : hotelParetoPoints(hotels, state.hotelPayMode, state.payMetric);
  state.hotelPointsById = new Map(
    points.map((point) => [hotelPointId(point), point]),
  );
  if (!points.length) {
    elements.hotelChart.replaceChildren(
      createElement("p", "chart-empty", providerMode
        ? "No provider-specific pay and commute pairs."
        : "No verified pay and commute pairs."),
      elements.hotelParetoTooltip,
    );
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
  const plot = createElement("div", "pareto-plot");
  const controlsLayer = createElement("div", "pareto-point-controls");
  controlsLayer.setAttribute("role", "group");
  controlsLayer.setAttribute(
    "aria-label",
    providerMode ? "Hotel provider pay and commute data points" : "Hotel pay and commute data points",
  );

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
    svgElement("text", { x: 15, y: margin.top + plotHeight / 2, class: "chart-axis-title", transform: `rotate(-90 15 ${margin.top + plotHeight / 2})`, "text-anchor": "middle" }, state.payMetric === "shift_total" ? "Comparable shift total →" : "Comparable hourly pay →"),
  );

  const frontierPoints = points.filter((point) => point.frontier).sort((left, right) => left.commute - right.commute);
  // Only dots that visually occupy the same position share a chooser. Wider
  // hit-target overlaps stay independently anchored to their exact data points.
  const projectPoint = (point) => ({ x: x(point.commute), y: y(point.pay) });
  const clusters = clusterProjectedPoints(points, projectPoint, PARETO_SHARED_POSITION_DISTANCE);
  const labelPointIds = new Set();
  const labelRects = [];
  const labelCandidates = [...points]
    .filter((point) => point.frontier || String(point.hotel.hotel_id) === state.selectedHotelId)
    .sort((left, right) =>
      Number(String(right.hotel.hotel_id) === state.selectedHotelId) -
        Number(String(left.hotel.hotel_id) === state.selectedHotelId) ||
      right.pay - left.pay ||
      left.commute - right.commute
    );
  for (const point of labelCandidates) {
    const selected = String(point.hotel.hotel_id) === state.selectedHotelId;
    const base = cleanText(point.hotel.employer).replace(" 서울강남", "").replace(" 서울", "");
    const provider = point.provider ? ` · ${truncateLabel(point.provider.label, 8)}` : "";
    const widthEstimate = Math.min(180, 12 + `${base}${provider}`.length * 6);
    const position = { x: x(point.commute), y: y(point.pay) };
    const rect = {
      left: position.x + 8,
      right: position.x + 8 + widthEstimate,
      top: position.y - 22,
      bottom: position.y - 5,
    };
    if (!selected && labelRects.some((current) => rectanglesOverlap(rect, current, 2))) continue;
    labelRects.push(rect);
    labelPointIds.add(hotelPointId(point));
  }
  state.hotelPointClusters = new Map();
  if (frontierPoints.length > 1) {
    svg.append(svgElement("polyline", {
      points: frontierPoints.map((point) => `${x(point.commute)},${y(point.pay)}`).join(" "),
      class: "chart-frontier-line",
    }));
  }
  const labelsLayer = svgElement("g", {
    class: "chart-label-layer",
    "aria-hidden": "true",
  });
  for (const point of [...points.filter((item) => !item.frontier), ...frontierPoints]) {
    const summary = hotelPointSummary(point);
    const position = { x: x(point.commute), y: y(point.pay) };
    const classes = ["chart-point"];
    if (point.provider) classes.push("chart-point--provider");
    if (point.frontier) classes.push("chart-point--frontier");
    if (summary.hotelId === state.selectedHotelId) classes.push("chart-point--selected");
    const group = svgElement("g", {
      class: classes.join(" "),
      "aria-hidden": "true",
      "data-hotel-visual-hotel-id": summary.hotelId,
      "data-hotel-visual-point-id": hotelPointId(point),
    });
    if (point.provider?.color) group.style.setProperty("--provider-color", point.provider.color);
    group.append(
      svgElement("circle", { cx: position.x, cy: position.y, r: point.frontier ? 7 : 4.5, class: "chart-point-dot" }),
    );
    const pointCluster = clusters.find((cluster) => cluster.points.includes(point));
    const clusterLabelPoint = pointCluster?.points.find((candidate) =>
      String(candidate.hotel.hotel_id) === state.selectedHotelId
    ) || pointCluster?.points.find((candidate) => labelPointIds.has(hotelPointId(candidate)));
    if (labelPointIds.has(hotelPointId(point)) && clusterLabelPoint === point) {
      const label = cleanText(point.hotel.employer).replace(" 서울강남", "").replace(" 서울", "");
      const providerLabel = point.provider
        ? ` · ${truncateLabel(point.provider.label, 8)}`
        : "";
      labelsLayer.append(svgElement("text", { x: position.x + 10, y: position.y - 10, class: "chart-point-label" }, `${label}${providerLabel}`));
    }
    svg.append(group);
  }
  svg.append(labelsLayer);
  for (const [index, cluster] of clusters.entries()) {
    state.hotelPointClusters.set(`hotel-${index}`, cluster.points);
  }
  const pointControls = interactivePointControls(clusters, projectPoint, state.groupOverlaps);
  for (const layout of pointControls) {
    const summaries = layout.members.map(hotelPointSummary);
    const primarySummary = hotelPointSummary(layout.point);
    const providerBreakdown = hotelPointProviderBreakdown(layout.members, state.hotelPayMode, state.payMetric);
    const providerSummary = providerBreakdown
      .map((provider) => `${provider.label} ${provider.pointCount}`)
      .join(", ");
    const selected = state.groupOverlaps
      ? summaries.some((summary) => summary.hotelId === state.selectedHotelId)
      : primarySummary.hotelId === state.selectedHotelId;
    const clusterId = `hotel-${layout.clusterIndex}`;
    const control = createElement("button", `pareto-point-button${selected ? " is-selected" : ""}`);
    control.type = "button";
    control.dataset.hotelPointId = hotelPointId(layout.point);
    control.dataset.pointClusterId = clusterId;
    control.dataset.hotelPointMembers = layout.members.map(hotelPointId).join(" ");
    control.dataset.hotelPointHotelIds = summaries.map((summary) => summary.hotelId).join(" ");
    configureOverlapControl(control, summaries.length, providerBreakdown);
    control.setAttribute("aria-pressed", String(selected));
    control.setAttribute("aria-label", summaries.length === 1
      ? `Open ${summaries[0].employer}; ${formatComparablePay(summaries[0].pay)}; ${summaries[0].commuteLabel}; ${summaries[0].optionLabel}; ${summaries[0].providerLabel}`
      : `Open overlap chooser from ${primarySummary.employer}; choose among ${summaries.length} ${providerMode ? "provider offers" : "hotels"}${providerSummary ? `; sources: ${providerSummary}` : ""}`);
    control.style.left = `${(layout.x / width) * 100}%`;
    control.style.top = `${(layout.y / height) * 100}%`;
    control.addEventListener("click", (event) => {
      event.stopPropagation();
      hideHotelParetoTooltip();
      if (summaries.length === 1) selectHotel(summaries[0].hotelId, control);
      else openPointChoices(layout.members, "hotel");
    });
    controlsLayer.append(control);
  }
  elements.hotelParetoTooltip.hidden = true;
  plot.append(svg, controlsLayer);
  elements.hotelChart.replaceChildren(plot, elements.hotelParetoTooltip);
  elements.hotelChart.setAttribute(
    "aria-label",
    `${points.length} ${providerMode ? "hotel-provider observations" : "hotels"} plotted by ${payMetricLabel()} and verified commute; ${frontierPoints.length} are on the selected Pareto frontier`,
  );
}

function truncateLabel(value, maximum = 18) {
  const text = cleanText(value);
  return text.length > maximum ? `${text.slice(0, maximum - 1)}…` : text;
}

function renderAlbamonParetoChart() {
  const points = albamonParetoPoints(state.payload?.jobs || [], state.payMetric);
  state.paretoPointsById = new Map(
    points.map((point) => [Number(point.job.recruit_no), point]),
  );
  if (!points.length) {
    elements.albamonParetoChart.replaceChildren(
      createElement("p", "chart-empty", "No actionable jobs have both verified commute and comparable pay."),
      elements.albamonParetoTooltip,
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
  const plot = createElement("div", "pareto-plot");
  const controlsLayer = createElement("div", "pareto-point-controls");
  controlsLayer.setAttribute("role", "group");
  controlsLayer.setAttribute("aria-label", "Albamon pay and commute data points");

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
    svgElement("text", { x: 16, y: margin.top + plotHeight / 2, class: "chart-axis-title", transform: `rotate(-90 16 ${margin.top + plotHeight / 2})`, "text-anchor": "middle" }, state.payMetric === "shift_total" ? "Reliable total pay for one shift →" : "Reliable work-hour equivalent pay →"),
  );

  const frontierGroups = albamonFrontierGroups(points);
  const projectPoint = (point) => ({ x: x(point.commute), y: y(point.pay) });
  const clusters = clusterProjectedPoints(points, projectPoint, PARETO_SHARED_POSITION_DISTANCE);
  state.albamonPointClusters = new Map();
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
  const labelsLayer = svgElement("g", {
    class: "chart-label-layer",
    "aria-hidden": "true",
  });
  for (const [index, point] of ordered.entries()) {
    const summary = albamonPointSummary(point);
    const classes = ["chart-point"];
    if (point.frontier) classes.push("chart-point--frontier");
    if (point.mode === "remote") classes.push("chart-point--remote");
    if (summary.recruitNo === state.selectedId) classes.push("chart-point--selected");
    const group = svgElement("g", {
      class: classes.join(" "),
      "aria-hidden": "true",
      "data-pareto-visual-job-id": summary.recruitNo,
    });
    group.append(
      svgElement("circle", { cx: x(point.commute), cy: y(point.pay), r: point.frontier ? 7 : 4.5, class: "chart-point-dot" }),
    );
    const pointCluster = clusters.find((cluster) => cluster.points.includes(point));
    if (point.frontier && pointCluster?.points[0] === point) {
      const direction = index % 2 === 0 ? -1 : 1;
      labelsLayer.append(svgElement("text", {
        x: x(point.commute) + 10,
        y: y(point.pay) + direction * 12,
        class: "chart-point-label",
      }, `${point.mode === "remote" ? "Remote · " : ""}${truncateLabel(point.job.company || point.job.title)}`));
    }
    svg.append(group);
  }
  svg.append(labelsLayer);
  for (const [index, cluster] of clusters.entries()) {
    state.albamonPointClusters.set(`albamon-${index}`, cluster.points);
  }
  const pointControls = interactivePointControls(clusters, projectPoint, state.groupOverlaps);
  for (const layout of pointControls) {
    const summaries = layout.members.map(albamonPointSummary);
    const primarySummary = albamonPointSummary(layout.point);
    const selected = state.groupOverlaps
      ? summaries.some((summary) => summary.recruitNo === state.selectedId)
      : primarySummary.recruitNo === state.selectedId;
    const clusterId = `albamon-${layout.clusterIndex}`;
    const control = createElement("button", `pareto-point-button${selected ? " is-selected" : ""}`);
    control.type = "button";
    control.dataset.paretoJobId = String(primarySummary.recruitNo);
    control.dataset.pointClusterId = clusterId;
    control.dataset.paretoJobMembers = summaries.map((summary) => summary.recruitNo).join(" ");
    configureOverlapControl(control, summaries.length);
    control.setAttribute("aria-pressed", String(selected));
    control.setAttribute("aria-label", summaries.length === 1
      ? `Open ${summaries[0].title} at ${summaries[0].company}; ${formatComparablePay(summaries[0].pay)}; ${summaries[0].commuteLabel}; ${summaries[0].schedule}`
      : `Open overlap chooser from ${primarySummary.company}; choose among ${summaries.length} jobs`);
    control.style.left = `${(layout.x / width) * 100}%`;
    control.style.top = `${(layout.y / height) * 100}%`;
    control.addEventListener("click", (event) => {
      event.stopPropagation();
      hideParetoTooltip();
      if (summaries.length === 1) selectJob(summaries[0].recruitNo, control);
      else openPointChoices(layout.members, "albamon");
    });
    controlsLayer.append(control);
  }
  const frontierCount = frontierPoints.length;
  elements.albamonParetoTooltip.hidden = true;
  plot.append(svg, controlsLayer);
  elements.albamonParetoChart.replaceChildren(plot, elements.albamonParetoTooltip);
  elements.albamonParetoChart.setAttribute(
    "aria-label",
    `${points.length} actionable Albamon jobs plotted by ${payMetricLabel()} and commute; ${frontierCount} are on the Pareto frontier`,
  );
}

function paretoPointElement(target) {
  return target instanceof Element ? target.closest("[data-pareto-job-id]") : null;
}

function positionParetoTooltip(pointElement, pointerEvent = null) {
  const chartBounds = elements.albamonParetoChart.getBoundingClientRect();
  const pointBounds = pointElement.getBoundingClientRect();
  const tooltip = elements.albamonParetoTooltip;
  const anchorX = pointerEvent?.clientX ?? pointBounds.left + pointBounds.width / 2;
  const anchorY = pointerEvent?.clientY ?? pointBounds.top + pointBounds.height / 2;
  const margin = 10;
  const gap = 14;
  const width = tooltip.offsetWidth;
  const height = tooltip.offsetHeight;
  const preferredLeft = anchorX - chartBounds.left + gap;
  const left = Math.min(Math.max(margin, preferredLeft), Math.max(margin, chartBounds.width - width - margin));
  const above = anchorY - chartBounds.top - height - gap;
  const below = anchorY - chartBounds.top + gap;
  const top = above >= margin
    ? above
    : Math.min(below, Math.max(margin, chartBounds.height - height - margin));
  tooltip.style.left = `${left}px`;
  tooltip.style.top = `${top}px`;
}

function showParetoTooltip(pointElement, pointerEvent = null) {
  const recruitNo = Number(pointElement?.dataset.paretoJobId);
  const point = state.paretoPointsById.get(recruitNo);
  if (!point) return;
  const summary = albamonPointSummary(point);
  const cluster = state.albamonPointClusters.get(pointElement.dataset.pointClusterId) || [point];
  const clusterSummaries = cluster.map(albamonPointSummary);
  const tooltip = elements.albamonParetoTooltip;
  const heading = createElement("div", "pareto-tooltip-heading");
  heading.append(
    badge(summary.frontier ? "Pareto frontier" : "Comparable", summary.frontier ? "pareto" : ""),
    createElement("span", "pareto-tooltip-area", summary.area),
  );
  const metrics = createElement("div", "pareto-tooltip-metrics");
  metrics.append(
    createElement("strong", "", formatComparablePay(summary.pay, summary.payMetric)),
    createElement("strong", "", summary.commuteLabel),
  );
  tooltip.replaceChildren(
    heading,
    createElement("strong", "pareto-tooltip-company", clusterSummaries.length > 1
      ? `${clusterSummaries.length} jobs share this position`
      : summary.company),
    createElement("span", "pareto-tooltip-title", clusterSummaries.length > 1
      ? clusterSummaries.map((item) => item.company).join(" · ")
      : summary.title),
    metrics,
    createElement("span", "pareto-tooltip-schedule", summary.schedule),
    createElement("span", "pareto-tooltip-hint", clusterSummaries.length > 1
      ? "Click to choose the job"
      : "Click to open full job details"),
  );
  tooltip.hidden = false;
  tooltip.dataset.jobId = String(recruitNo);
  positionParetoTooltip(pointElement, pointerEvent);
}

function hideParetoTooltip() {
  elements.albamonParetoTooltip.hidden = true;
  delete elements.albamonParetoTooltip.dataset.jobId;
}

function hotelPointElement(target) {
  return target instanceof Element ? target.closest("[data-hotel-point-id]") : null;
}

function positionHotelParetoTooltip(pointElement, pointerEvent = null) {
  const chartBounds = elements.hotelChart.getBoundingClientRect();
  const pointBounds = pointElement.getBoundingClientRect();
  const tooltip = elements.hotelParetoTooltip;
  const anchorX = pointerEvent?.clientX ?? pointBounds.left + pointBounds.width / 2;
  const anchorY = pointerEvent?.clientY ?? pointBounds.top + pointBounds.height / 2;
  const margin = 10;
  const gap = 14;
  const width = tooltip.offsetWidth;
  const height = tooltip.offsetHeight;
  const preferredLeft = anchorX - chartBounds.left + gap;
  const left = Math.min(
    Math.max(margin, preferredLeft),
    Math.max(margin, chartBounds.width - width - margin),
  );
  const above = anchorY - chartBounds.top - height - gap;
  const below = anchorY - chartBounds.top + gap;
  const top = above >= margin
    ? above
    : Math.min(below, Math.max(margin, chartBounds.height - height - margin));
  tooltip.style.left = `${left}px`;
  tooltip.style.top = `${top}px`;
}

function showHotelParetoTooltip(pointElement, pointerEvent = null) {
  const pointId = String(pointElement?.dataset.hotelPointId || "");
  const point = state.hotelPointsById.get(pointId);
  if (!point) return;
  const summary = hotelPointSummary(point);
  const cluster = state.hotelPointClusters.get(pointElement.dataset.pointClusterId) || [point];
  const clusterSummaries = cluster.map(hotelPointSummary);
  const providerCluster = cluster.some((item) => item.provider);
  const clusterProviders = hotelPointProviderBreakdown(cluster, state.hotelPayMode, state.payMetric);
  const heading = createElement("div", "pareto-tooltip-heading");
  heading.append(
    badge(summary.frontier ? "Pareto frontier" : "Comparable", summary.frontier ? "pareto" : ""),
    ...(clusterProviders.length
      ? clusterProviders.map((provider) => providerLegendBadge(provider))
      : point.provider ? [providerLegendBadge(point.provider)] : []),
    createElement("span", "pareto-tooltip-area", clusterSummaries.length > 1 ? "Same chart position" : summary.area),
  );
  const metrics = createElement("div", "pareto-tooltip-metrics");
  metrics.append(
    createElement("strong", "", formatComparablePay(summary.pay, summary.payMetric)),
    createElement("strong", "", summary.commuteLabel),
  );
  elements.hotelParetoTooltip.replaceChildren(
    heading,
    createElement("strong", "pareto-tooltip-company", clusterSummaries.length > 1
      ? `${clusterSummaries.length} ${providerCluster ? "provider offers" : "hotels"} share this position`
      : summary.employer),
    metrics,
    createElement("span", "pareto-tooltip-schedule", clusterSummaries.length > 1
      ? clusterSummaries.map((item) => `${item.employer} · ${item.providerLabel}`).join(" · ")
      : `${summary.optionLabel} · ${summary.providerLabel}`),
    createElement("span", "pareto-tooltip-hint", clusterSummaries.length > 1
      ? `Click to choose the ${providerCluster ? "provider offer" : "hotel"}`
      : "Click to inspect every shift and provider"),
  );
  elements.hotelParetoTooltip.hidden = false;
  elements.hotelParetoTooltip.dataset.hotelId = summary.hotelId;
  elements.hotelParetoTooltip.dataset.pointId = pointId;
  positionHotelParetoTooltip(pointElement, pointerEvent);
}

function hideHotelParetoTooltip() {
  elements.hotelParetoTooltip.hidden = true;
  delete elements.hotelParetoTooltip.dataset.hotelId;
  delete elements.hotelParetoTooltip.dataset.pointId;
}

function openPointChoices(points, kind) {
  const isHotel = kind === "hotel";
  const providerChoices = isHotel && points.some((point) => point.provider);
  elements.pointChoiceTitle.textContent = `Choose one of ${points.length} ${providerChoices ? "provider offers" : isHotel ? "hotels" : "jobs"}`;
  const providerBreakdown = isHotel ? hotelPointProviderBreakdown(points, state.hotelPayMode, state.payMetric) : [];
  elements.pointChoiceLegend.hidden = !providerBreakdown.length;
  elements.pointChoiceLegend.replaceChildren();
  if (providerBreakdown.length) {
    elements.pointChoiceLegend.append(createElement("strong", "point-choice-legend-title", "Sources in this group"));
    for (const provider of providerBreakdown) {
      const item = providerLegendBadge(provider);
      item.classList.add("point-choice-source");
      item.style.setProperty("--provider-color", provider.color || "#73847d");
      item.append(createElement("span", "point-choice-source-count", String(provider.pointCount)));
      item.title = `${provider.pointCount} plotted ${provider.label} source point${provider.pointCount === 1 ? "" : "s"}; best ${formatComparablePay(provider.bestPay)}`;
      elements.pointChoiceLegend.append(item);
    }
  }
  const fragment = document.createDocumentFragment();
  for (const point of points) {
    const summary = isHotel ? hotelPointSummary(point) : albamonPointSummary(point);
    const button = createElement("button");
    button.type = "button";
    const identity = createElement("div", "point-choice-identity");
    const rowProviders = isHotel ? hotelPointProviderBreakdown([point], state.hotelPayMode, state.payMetric) : [];
    if (rowProviders.length) {
      identity.append(...rowProviders.map((provider) => providerLegendBadge(provider)));
      button.classList.add("point-choice-provider");
      button.style.setProperty("--provider-color", rowProviders[0].color || "#73847d");
    }
    identity.append(createElement("strong", "", isHotel ? summary.employer : summary.title));
    button.append(
      identity,
      createElement("span", "", `${formatComparablePay(summary.pay, summary.payMetric)} · ${summary.commuteLabel}`),
      createElement("small", "", isHotel
        ? `${summary.area} · ${summary.optionLabel} · ${summary.providerLabel}`
        : `${summary.company} · ${summary.area} · ${summary.schedule}`),
    );
    button.addEventListener("click", () => {
      elements.pointChoiceDialog.close();
      if (isHotel) selectHotel(summary.hotelId, button);
      else selectJob(summary.recruitNo, button);
    });
    fragment.append(button);
  }
  elements.pointChoiceList.replaceChildren(fragment);
  elements.pointChoiceDialog.showModal();
}

function mapPointTooltip({ label, labelKind = "", area, heading, title = "", pay, payMetric = "hourly", commute, detail, hint }) {
  const content = createElement("div", "map-point-tooltip");
  const tooltipHeading = createElement("div", "pareto-tooltip-heading");
  tooltipHeading.append(
    badge(label, labelKind),
    createElement("span", "pareto-tooltip-area", area),
  );
  const metrics = createElement("div", "pareto-tooltip-metrics");
  metrics.append(
    createElement("strong", "", formatComparablePay(pay, payMetric)),
    createElement("strong", "", commute),
  );
  content.append(
    tooltipHeading,
    createElement("strong", "pareto-tooltip-company", heading),
  );
  if (title) content.append(createElement("span", "pareto-tooltip-title", title));
  content.append(
    metrics,
    createElement("span", "pareto-tooltip-schedule", detail),
    createElement("span", "pareto-tooltip-hint", hint),
  );
  return content;
}

function albamonMapTooltip(job, frontierIds = null) {
  const point = albamonPointForJob(job, state.payMetric);
  point.frontier = frontierIds?.has(Number(job.recruit_no)) === true;
  const summary = albamonPointSummary(point);
  const actionable = job.application_disposition === "apply";
  return mapPointTooltip({
    label: actionable
      ? summary.frontier ? "Pareto frontier" : "Comparable"
      : STATUS_LABELS[job.application_disposition] || "Review",
    labelKind: actionable && summary.frontier ? "pareto" : actionable ? "" : "warning",
    area: summary.area,
    heading: summary.company,
    title: summary.title,
    pay: summary.pay,
    payMetric: summary.payMetric,
    commute: summary.commuteLabel,
    detail: summary.schedule,
    hint: "Click to open full job details",
  });
}

function hotelProviderTooltipList(hotel) {
  const providers = hotelProviderComparisons(hotel, state.hotelPayMode, state.payMetric);
  if (!providers.length) return null;
  const list = createElement("div", "hotel-provider-tooltip-list");
  for (const provider of providers) {
    const row = createElement("div", "hotel-provider-tooltip-row");
    const swatch = createElement("i", "provider-swatch");
    swatch.style.setProperty("--provider-color", provider.color);
    const identity = createElement("span", "hotel-provider-tooltip-name", provider.label);
    if (provider.usesActualFallback) {
      identity.append(createElement("small", "", "actual fallback"));
    }
    row.append(
      swatch,
      identity,
      createElement("strong", "hotel-provider-tooltip-pay", formatComparablePay(provider.comparablePay)),
    );
    list.append(row);
  }
  return list;
}

function hotelMapTooltip(hotel) {
  const summary = hotelPointSummary(hotelPointForHotel(hotel, state.hotelPayMode, state.payMetric));
  const content = mapPointTooltip({
    label: !summary.pay ? "Pay unavailable" : summary.frontier ? "Pareto frontier" : "Comparable",
    labelKind: summary.frontier ? "pareto" : !summary.pay ? "warning" : "",
    area: summary.area,
    heading: summary.employer,
    pay: summary.pay,
    payMetric: summary.payMetric,
    commute: summary.commuteLabel,
    detail: summary.optionLabel,
    hint: "Click to inspect every shift and provider",
  });
  const providers = hotelProviderTooltipList(hotel);
  if (providers) content.insertBefore(providers, content.lastElementChild);
  return content;
}

function richMapTooltipOptions() {
  const compactLayout = window.matchMedia("(max-width: 900px)").matches;
  return {
    className: "rich-map-tooltip",
    direction: compactLayout ? "top" : "auto",
    offset: compactLayout ? [0, -18] : [10, 0],
    opacity: 1,
  };
}

function renderHotelRows(hotels) {
  const rows = hotelRows(hotels, {
    mode: state.hotelPayMode,
    metric: state.payMetric,
    showAll: true,
  });
  const fragment = document.createDocumentFragment();
  for (const hotel of rows) {
    const row = createElement("tr");
    row.dataset.hotelId = hotel.hotel_id;
    row.tabIndex = 0;
    row.setAttribute("aria-label", `Inspect ${hotel.employer}`);
    if (isHotelOnFrontier(hotel, state.hotelPayMode, state.payMetric)) row.classList.add("is-frontier");
    if (String(hotel.hotel_id) === state.selectedHotelId) row.classList.add("is-selected");
    const nameCell = createElement("td", "hotel-name-cell");
    const badges = createElement("div", "badge-row");
    if (isHotelOnFrontier(hotel, state.hotelPayMode, state.payMetric)) badges.append(badge("Frontier", "pareto"));
    if (hotel.source_snapshot_stale) badges.append(badge("Cached source", "warning"));
    const decision = hotelDecisionSummary(hotel);
    if (decision.hasLateReturnRisk || decision.reviewPayCount) badges.append(badge("Review details", "warning"));
    nameCell.append(badges, createElement("strong", "", hotel.employer));
    appendText(nameCell, "small", "", hotelLocationLabel(hotel));

    const comparableCell = createElement("td", "hotel-comparable-cell");
    comparableCell.dataset.label = state.payMetric === "shift_total" ? "Shift total" : "Work-hour pay";
    appendText(comparableCell, "strong", "hotel-comparable-pay", formatComparablePay(hotelComparablePay(hotel, state.hotelPayMode, state.payMetric)));
    const comparableBasis = state.hotelPayMode === "weekly_allowance" && hotel.weekly_allowance_rate_stated
      ? "conditional 주휴"
      : state.hotelPayMode === "weekly_allowance" ? "actual fallback" : "actual/base";
    appendText(
      comparableCell,
      "small",
      "cell-secondary",
      `${comparableBasis}${state.payMetric === "shift_total" ? " · advertised or scheduled gross" : ""}`,
    );
    appendText(
      comparableCell,
      "small",
      "cell-secondary",
      decision.adjustedHourly
        ? `${formatWon(decision.adjustedHourly)}/hr door-to-door`
        : "Door-to-door rate unavailable",
    );

    const payCell = createElement("td", "hotel-pay-cell");
    payCell.dataset.label = "Actual / 주휴*";
    appendText(payCell, "strong", "", formatComparablePay(hotel.actual_hourly_max, "hourly"));
    appendText(payCell, "small", "cell-secondary", `주휴* ${formatComparablePay(hotel.weekly_allowance_hourly_max, "hourly")}`);
    appendText(
      payCell,
      "small",
      "cell-secondary",
      hotel.actual_shift_total_max
        ? `Best shift ${formatWon(hotel.actual_shift_total_max)}${hotel.shift_total_includes_scheduled_estimates ? " · scheduled gross estimate" : ""}`
        : "Shift total unavailable",
    );

    const commuteCell = createElement("td", "hotel-commute-cell");
    commuteCell.dataset.label = "Commute";
    const commute = hotelVerifiedCommuteMinutes(hotel.transit_minutes);
    appendText(commuteCell, "strong", "commute-value", commute === null ? "—" : `${commute} min`);
    appendText(commuteCell, "small", "cell-secondary", commute === null ? "route not verified" : "verified transit");

    const optionsCell = createElement("td", "hotel-options-cell");
    optionsCell.dataset.label = "Available shifts";
    appendText(optionsCell, "strong", "", `${hotel.date_count} days · ${hotel.shift_count} times`);
    const providers = (hotel.providers || []).map((provider) => provider.label).join(" · ") || "Provider unavailable";
    appendText(optionsCell, "small", "cell-secondary", `${hotel.job_count} choices · ${providers}`);
    row.append(nameCell, comparableCell, payCell, commuteCell, optionsCell);
    fragment.append(row);
  }
  elements.hotelRows.replaceChildren(fragment);
  elements.hotelComparableHeading.textContent = state.payMetric === "shift_total" ? "Shift total" : "Work-hour pay";
  elements.hotelEmpty.hidden = rows.length > 0;
  elements.hotelTableHeading.textContent = "All matching hotels";
  elements.hotelTableFeedback.textContent = `${rows.length} of ${hotels.length} aggregated hotels · updated ${formatDateTime(state.hotelPayload.generated_at)}`;
}

function renderAgencyCatalog(catalog, sources, coverage) {
  const trackedSources = new Set(Object.keys(sources || {}));
  const coverageByAgency = coverage || {};
  const fragment = document.createDocumentFragment();
  for (const agency of catalog || []) {
    const row = createElement("tr");
    const agencyCell = createElement("td", "agency-name-cell");
    const badges = createElement("div", "badge-row");
    const sourceState = sources?.[agency.agency_id]?.status;
    const sourceCoverage = coverageByAgency[agency.agency_id] || {};
    const tracked = trackedSources.has(agency.agency_id);
    const configured = sourceCoverage.status === "configured_public_source";
    const coverageLabel = sourceState === "live"
      ? "Live source"
      : tracked ? "Cached source"
        : configured ? "Configured"
          : sourceCoverage.status === "catalog_only_auth_required" ? "Login gated"
            : sourceCoverage.status === "catalog_only_low_yield" ? "Disabled"
              : "Reference";
    badges.append(badge(coverageLabel, sourceState === "live" || configured ? "evidence" : ""));
    agencyCell.append(badges);
    const displayName = agency.english_name
      ? `${agency.name} (${agency.english_name})`
      : agency.name;
    appendText(agencyCell, "strong", "", displayName || agency.agency_id);
    if (sourceCoverage.reason) {
      appendText(agencyCell, "small", "cell-secondary", sourceCoverage.reason);
    }
    const sourceMetrics = sources?.[agency.agency_id] || {};
    if (Number.isInteger(sourceMetrics.raw_listing_rows)) {
      appendText(
        agencyCell,
        "small",
        "cell-secondary",
        `${formatNumber(sourceMetrics.pages_fetched)} pages · ${formatNumber(sourceMetrics.raw_listing_rows)} rows · ${formatNumber(sourceMetrics.duplicate_listing_rows)} repeats · ${formatNumber(sourceMetrics.unique_listing_ids)} unique · ${formatNumber(sourceMetrics.unique_open_listing_ids)} open · ${formatNumber(sourceMetrics.target_date_offer_count)} target-date offers`,
      );
    }

    const featuresCell = createElement("td", "agency-features-cell");
    appendText(
      featuresCell,
      "span",
      "",
      (agency.featured_hotels || []).join(" · ") || "No named hotel supplied",
    );
    if ((agency.specialties || []).length) {
      appendText(featuresCell, "small", "cell-secondary", `Specialties · ${agency.specialties.join(" · ")}`);
    }

    row.append(agencyCell, featuresCell);
    fragment.append(row);
  }
  elements.agencyCatalogRows.replaceChildren(fragment);
}

function populateHotelFilterOptions(hotels) {
  const restored = { ...(readStoredJson(HOTEL_FILTER_STORAGE_KEY, {}) || {}), ...hotelFiltersFromUrl() };
  const selectedRole = elements.hotelRoleFilter.value || restored.role || "all";
  const selectedProvider = elements.hotelProviderFilter.value || restored.provider || "all";
  const roles = new Set();
  const providers = new Map();
  for (const hotel of hotels || []) {
    for (const option of hotel.job_options || []) {
      if (option.role_category) roles.add(option.role_category);
      for (const agency of option.agencies || []) providers.set(agency, agency);
    }
    for (const provider of hotel.providers || []) {
      if (provider.agency) providers.set(provider.agency, provider.label || provider.agency);
    }
  }
  const roleFragment = document.createDocumentFragment();
  const allRoles = createElement("option", "", "All roles");
  allRoles.value = "all";
  roleFragment.append(allRoles);
  for (const role of [...roles].sort()) {
    const option = createElement("option", "", HOTEL_ROLE_LABELS[role] || role);
    option.value = role;
    roleFragment.append(option);
  }
  elements.hotelRoleFilter.replaceChildren(roleFragment);
  elements.hotelRoleFilter.value = roles.has(selectedRole) ? selectedRole : "all";

  const providerFragment = document.createDocumentFragment();
  const allProviders = createElement("option", "", "All providers");
  allProviders.value = "all";
  providerFragment.append(allProviders);
  for (const [agency, label] of [...providers].sort((a, b) => a[1].localeCompare(b[1], "ko"))) {
    const option = createElement("option", "", label);
    option.value = agency;
    providerFragment.append(option);
  }
  elements.hotelProviderFilter.replaceChildren(providerFragment);
  elements.hotelProviderFilter.value = providers.has(selectedProvider) ? selectedProvider : "all";
}

function renderSourceHealth(items = []) {
  const fragment = document.createDocumentFragment();
  for (const source of items) {
    const card = createElement("article", `source-health-card source-health-card--${source.status || "unknown"}`);
    const heading = createElement("div", "source-health-heading");
    heading.append(
      createElement("strong", "", source.source),
      badge(source.status === "live" ? "Live" : source.status === "stale_cache" ? "Cached" : "Check source", source.status === "live" ? "evidence" : "warning"),
    );
    const age = Number.isFinite(Number(source.age_minutes)) ? `${formatNumber(source.age_minutes)} min old` : "age unavailable";
    const coverage = source.raw_listing_rows === null || source.raw_listing_rows === undefined
      ? `${formatNumber(source.fetched_offer_count)} offers`
      : `${formatNumber(source.raw_listing_rows)} rows · ${formatNumber(source.unique_open_listing_ids)} open · ${formatNumber(source.target_date_offer_count)} matching offers`;
    card.append(
      heading,
      createElement("span", "", `${coverage} · ${age}`),
      createElement("small", "", source.zero_result_confidence === "observed_empty"
        ? "Live source returned a valid empty result for this date range."
        : `Last source pass ${source.elapsed_seconds ?? "?"}s`),
    );
    fragment.append(card);
  }
  elements.hotelSourceHealth.replaceChildren(fragment);
  if (!items.length) elements.hotelSourceHealth.append(createElement("p", "", "No source diagnostics were published with this report."));
}

function renderHotelVisualizationLegends(hotels) {
  const providers = mergedHotelProviderComparisons(hotels, state.hotelPayMode, state.payMetric);
  const providerItems = providers.map((provider) => providerLegendItem(provider));
  const frontierItem = createElement("span", "");
  frontierItem.append(createElement("i", "dot dot--frontier"), document.createTextNode("Frontier halo"));
  if (state.hotelGranularity === "provider") {
    elements.hotelChartLegend.replaceChildren(...providerItems, frontierItem);
  } else {
    const comparable = createElement("span", "");
    comparable.append(createElement("i", "dot dot--ready"), document.createTextNode("Aggregated hotel"));
    elements.hotelChartLegend.replaceChildren(comparable, frontierItem);
  }

  const mapItems = providers.map((provider) => providerLegendItem(provider));
  const encoding = createElement("span", "");
  encoding.append(
    createElement("i", "provider-best-key"),
    document.createTextNode(state.hotelGranularity === "provider"
      ? "Each dot = provider"
      : "Center = highest rate"),
  );
  const selected = createElement("span", "");
  selected.append(createElement("i", "provider-selected-key"), document.createTextNode("Selected"));
  const frontierToggle = markerLegendToggle(
    "Frontier hotels",
    "dot--frontier",
    state.hotelMarkerVisibility.frontier,
    () => setHotelMarkerVisibility("frontier", !state.hotelMarkerVisibility.frontier),
  );
  const comparableToggle = markerLegendToggle(
    state.payMetric === "shift_total" ? "Other / total unavailable" : "Other hotels",
    "dot--muted",
    state.hotelMarkerVisibility.comparable,
    () => setHotelMarkerVisibility("comparable", !state.hotelMarkerVisibility.comparable),
  );
  elements.hotelMapLegend.replaceChildren(...mapItems, encoding, frontierToggle, comparableToggle, selected);
}

function syncHotelVisualization() {
  const showingMap = state.hotelVisualization === "map";
  const providerMode = state.hotelGranularity === "provider";
  elements.hotelGranularityHotel.classList.toggle("is-active", !providerMode);
  elements.hotelGranularityHotel.setAttribute("aria-pressed", String(!providerMode));
  elements.hotelGranularityProvider.classList.toggle("is-active", providerMode);
  elements.hotelGranularityProvider.setAttribute("aria-pressed", String(providerMode));
  elements.hotelMapVisual.classList.toggle("is-active", showingMap);
  elements.hotelMapVisual.setAttribute("aria-pressed", String(showingMap));
  elements.hotelParetoVisual.classList.toggle("is-active", !showingMap);
  elements.hotelParetoVisual.setAttribute("aria-pressed", String(!showingMap));
  syncOverlapToggle(elements.hotelGroupOverlaps, showingMap);
  elements.hotelChartLegend.hidden = showingMap;
  elements.hotelMapLegend.hidden = !showingMap;
  elements.hotelVisualTitle.textContent = showingMap
    ? providerMode ? "Hotel provider locations" : "Hotel locations"
    : providerMode ? `Provider ${payMetricLabel()} versus commute` : `Hotel ${payMetricLabel()} versus commute`;
  const visualDescription = showingMap
    ? providerMode
      ? "Each colored dot is a provider at the hotel's true location. Labels reveal progressively with zoom."
      : "Ring slices are providers; the center marks the highest shown rate. Labels reveal progressively with zoom."
    : providerMode
      ? state.groupOverlaps
        ? "Each hotel-provider rate is a separate observation; visually coincident points share a numbered source ring. Hover or click to inspect the group."
        : "Every dot stays on its exact pay and commute coordinates. Clicking an overlap still opens the chooser, without a numbered badge."
      : state.groupOverlaps
        ? "Each point uses the hotel's best shown rate; visually coincident hotels share a numbered source ring. Hover or click to inspect the group."
        : "Every hotel dot stays on its exact pay and commute coordinates. Clicking an overlap still opens the chooser, without a numbered badge.";
  elements.hotelVisualDescription.textContent = visualDescription;
  elements.hotelChart.hidden = showingMap;
  if (!showingMap) {
    elements.hotelMap.hidden = true;
    elements.hotelMapFallback.hidden = true;
    renderHotelChart(state.filteredHotels);
    return;
  }
  if (state.hotelMap) {
    elements.hotelMap.hidden = false;
    elements.hotelMapFallback.hidden = true;
    renderHotelMapMarkers();
    scheduleLeafletRefresh(state.hotelMap, fitHotelMap);
  } else {
    elements.hotelMap.hidden = true;
    elements.hotelMapFallback.hidden = false;
    requestHotelMapLoad();
  }
}

function setHotelVisualization(mode) {
  state.hotelVisualization = mode === "map" ? "map" : "pareto";
  hideHotelParetoTooltip();
  syncHotelVisualization();
}

function setHotelGranularity(mode) {
  const next = mode === "provider" ? "provider" : "hotel";
  if (state.hotelGranularity === next) return;
  state.hotelGranularity = next;
  hideHotelParetoTooltip();
  persistHotelFilters();
  renderHotelView();
}

function setGroupOverlaps(enabled) {
  const next = enabled === true;
  if (state.groupOverlaps === next) return;
  state.groupOverlaps = next;
  hideHotelParetoTooltip();
  hideParetoTooltip();
  persistVisualizationPreferences();
  if (state.payload) syncWorksiteVisualization();
  if (state.hotelPayload) syncHotelVisualization();
}

function syncPayMetricControls() {
  for (const [button, active] of [
    [elements.albamonPayHourly, state.payMetric === "hourly"],
    [elements.albamonPayTotal, state.payMetric === "shift_total"],
    [elements.hotelPayHourly, state.payMetric === "hourly"],
    [elements.hotelPayTotal, state.payMetric === "shift_total"],
  ]) {
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-pressed", String(active));
  }
}

function setPayMetric(metric) {
  const next = metric === "shift_total" ? "shift_total" : "hourly";
  if (state.payMetric === next) return;
  state.payMetric = next;
  hideHotelParetoTooltip();
  hideParetoTooltip();
  persistVisualizationPreferences();
  syncPayMetricControls();
  if (state.payload) {
    renderResults();
    syncWorksiteVisualization();
  }
  if (state.hotelPayload) renderHotelView();
}

function syncAlbamonMarkerToggles() {
  for (const [button, active] of [
    [elements.albamonMarkerActionable, state.albamonMarkerVisibility.actionable],
    [elements.albamonMarkerOther, state.albamonMarkerVisibility.other],
  ]) {
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-pressed", String(active));
  }
}

function setAlbamonMarkerVisibility(category, visible) {
  if (!Object.hasOwn(state.albamonMarkerVisibility, category)) return;
  state.albamonMarkerVisibility[category] = visible === true;
  persistVisualizationPreferences();
  syncAlbamonMarkerToggles();
  renderMapMarkers();
}

function setHotelMarkerVisibility(category, visible) {
  if (!Object.hasOwn(state.hotelMarkerVisibility, category)) return;
  state.hotelMarkerVisibility[category] = visible === true;
  persistVisualizationPreferences();
  renderHotelVisualizationLegends(state.filteredHotels);
  renderHotelMapMarkers();
}

function renderHotelView() {
  const payload = state.hotelPayload;
  if (!payload) return;
  syncConfiguredHotelMinimumPay();
  populateHotelFilterOptions(payload.hotels || []);
  const hotels = filterHotels(payload.hotels || [], hotelFilterControls());
  state.filteredHotels = hotels;
  renderHotelVisualizationLegends(hotels);
  if (state.selectedHotelId && !hotels.some((hotel) => String(hotel.hotel_id) === state.selectedHotelId)) closeDrawer();
  const frontier = state.hotelGranularity === "provider"
    ? hotelProviderParetoPoints(hotels, state.hotelPayMode, state.payMetric).filter((point) => point.frontier)
    : hotels.filter((hotel) => isHotelOnFrontier(hotel, state.hotelPayMode, state.payMetric));
  const highestPay = Math.max(0, ...frontier.map((item) =>
    item.hotel ? item.pay : hotelComparablePay(item, state.hotelPayMode, state.payMetric) || 0
  ));
  const verifiedCommutes = hotels
    .map((hotel) => hotelVerifiedCommuteMinutes(hotel.transit_minutes))
    .filter((minutes) => minutes !== null);
  elements.hotelSummaryFrontier.textContent = formatNumber(frontier.length);
  elements.hotelSummaryPay.textContent = formatComparablePay(highestPay);
  elements.hotelSummaryPayLabel.textContent = state.payMetric === "shift_total"
    ? "Highest frontier shift total"
    : "Highest frontier hourly";
  elements.hotelSummaryCommute.textContent = verifiedCommutes.length
    ? `${Math.min(...verifiedCommutes)} min`
    : "—";
  elements.refreshState.textContent = `Hotel report refreshed ${formatDateTime(payload.generated_at)}`;
  renderOriginControl(payload.origin);
  const searchDates = payload.policy?.search_dates || payload.policy?.weekend_dates || [];
  elements.hotelDateStatus.textContent = payload.exploration
    ? `Exploring ${searchDates[0] || "?"} through ${searchDates.at(-1) || "?"} across all weekdays and shifts. This does not change Discord.`
    : `Showing the monitored report for ${(payload.monitored_dates || []).join(", ")}. Expired dates are hidden.`;
  elements.hotelBasisNote.textContent = hotelPayModeCopy();
  syncPayMetricControls();
  elements.hotelPayActual.classList.toggle("is-active", state.hotelPayMode === "actual");
  elements.hotelPayActual.setAttribute("aria-pressed", String(state.hotelPayMode === "actual"));
  elements.hotelPayWeekly.classList.toggle("is-active", state.hotelPayMode === "weekly_allowance");
  elements.hotelPayWeekly.setAttribute("aria-pressed", String(state.hotelPayMode === "weekly_allowance"));
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
  syncHotelVisualization();
  syncHotelMobileView();
  renderHotelRows(hotels);
  updateHotelFilterSummary();
  elements.hotelFilterFeedback.textContent = `${hotels.length} of ${payload.hotels?.length || 0} hotels · ${hotels.reduce((sum, hotel) => sum + Number(hotel.job_count || 0), 0)} matching shift options. Filters apply to table, map, and frontier.`;
  renderAgencyCatalog(
    payload.agency_catalog || [],
    payload.sources || {},
    payload.agency_source_coverage || {},
  );
  renderSourceHealth(payload.source_health || []);
}

function describeOperationDetail(detail = {}) {
  const source = cleanText(detail.source);
  const offers = Number.isFinite(Number(detail.offers)) ? `${formatNumber(detail.offers)} offers` : "";
  return [source, offers, detail.cache === "hit" ? "recent cache" : ""].filter(Boolean).join(" · ");
}

function updateHotelOperation(operation) {
  elements.hotelOperationProgress.hidden = false;
  elements.hotelOperationMessage.textContent = operation.message || "Working…";
  elements.hotelOperationDetail.textContent = describeOperationDetail(operation.detail);
  elements.hotelOperationMeter.value = Number(operation.progress) || 0;
  elements.hotelOperationMeter.textContent = `${Number(operation.progress) || 0}%`;
  elements.hotelOperationCancel.disabled = !["queued", "running"].includes(operation.status);
}

async function waitForOperation(operation, onProgress = () => {}) {
  let current = operation;
  while (["queued", "running"].includes(current.status)) {
    onProgress(current);
    await new Promise((resolve) => window.setTimeout(resolve, 700));
    const response = await fetch(`/api/operations/${current.operation_id}`, { cache: "no-store" });
    current = await response.json();
    if (!response.ok) throw new Error(current.error || "Background operation disappeared");
  }
  onProgress(current);
  if (current.status === "failed") throw new Error(current.error || "Background operation failed");
  if (current.status === "cancelled") throw new Error("Operation cancelled");
  if (current.status !== "succeeded" || !current.result) throw new Error("Background operation returned no result");
  return current.result;
}

async function loadHotelData({ force = false } = {}) {
  if (state.hotelPayload && !force) {
    renderHotelView();
    return state.hotelPayload;
  }
  if (state.hotelLoadingPromise && !force) return state.hotelLoadingPromise;
  elements.hotelTableFeedback.textContent = state.hotelSearchCustom
    ? "Searching public agency feeds across the selected dates…"
    : "Loading the separate monitored report…";
  elements.reload.disabled = true;
  state.hotelLoadingPromise = (async () => {
    const response = state.hotelSearchCustom
      ? await fetch("/api/weekend-agencies/search", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          start_date: elements.hotelStartDate.value,
          end_date: elements.hotelEndDate.value,
          force,
        }),
      })
      : await fetch("/api/weekend-agencies", { cache: "no-store" });
    let payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Hotel agency data is unavailable");
    if (state.hotelSearchCustom) {
      state.hotelOperationId = payload.operation_id;
      payload = await waitForOperation(payload, updateHotelOperation);
    }
    if (payload.payload_version !== 2) throw new Error("Restart the dashboard server to load hotel agency data");
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
    state.hotelOperationId = null;
    elements.hotelOperationProgress.hidden = true;
    elements.reload.disabled = false;
  }
}

function setDesk(view) {
  const deskChanged = state.activeDesk !== view;
  if (deskChanged && elements.drawer.classList.contains("is-open")) closeDrawer();
  state.activeDesk = view;
  const hotelsActive = view === "hotels";
  elements.albamonView.hidden = hotelsActive;
  elements.hotelsView.hidden = !hotelsActive;
  elements.albamonDeskTab.classList.toggle("is-active", !hotelsActive);
  elements.albamonDeskTab.setAttribute("aria-selected", String(!hotelsActive));
  elements.hotelsDeskTab.classList.toggle("is-active", hotelsActive);
  elements.hotelsDeskTab.setAttribute("aria-selected", String(hotelsActive));
  elements.reportLinks.hidden = hotelsActive;
  if (hotelsActive) {
    elements.compareTray.hidden = true;
    document.querySelector(".skip-link").href = "#hotel-table-heading";
    loadHotelData();
  } else {
    document.querySelector(".skip-link").href = "#results";
    if (state.payload) renderHeader();
    renderComparisonTray();
  }
}

function syncWorksiteVisualization() {
  if (!state.payload) return;
  syncPayMetricControls();
  syncAlbamonMarkerToggles();
  const showingPareto = state.albamonVisualization === "pareto";
  elements.albamonMapVisual.classList.toggle("is-active", !showingPareto);
  elements.albamonMapVisual.setAttribute("aria-pressed", String(!showingPareto));
  elements.albamonParetoVisual.classList.toggle("is-active", showingPareto);
  elements.albamonParetoVisual.setAttribute("aria-pressed", String(showingPareto));
  syncOverlapToggle(elements.albamonGroupOverlaps, !showingPareto);
  elements.mapLegend.hidden = showingPareto;
  elements.albamonChartLegend.hidden = !showingPareto;
  elements.albamonParetoChart.hidden = !showingPareto;

  if (showingPareto) {
    const currentPoints = albamonParetoPoints(state.payload.jobs || [], state.payMetric);
    const frontierCount = currentPoints.filter((point) => point.frontier).length;
    elements.map.hidden = true;
    elements.mapFallback.hidden = true;
    elements.visualizationEyebrow.textContent = "PAY ↑ · COMMUTE ↓";
    elements.mapHeading.textContent = `${formatNumber(frontierCount)} unbeaten choices by ${payMetricLabel()}`;
    const basis = state.payMetric === "shift_total"
      ? "Only jobs with a verified total for one shift are plotted."
      : "The y-axis uses reliable work-hour equivalent pay.";
    elements.visualizationCaption.textContent = state.groupOverlaps
      ? `${basis} Every dot uses exact pay and commute coordinates; numbered groups only combine visually coincident hit targets. Remote roles are compared separately at 0 minutes.`
      : `${basis} Number badges are hidden; clicking an exact overlap still opens its chooser.`;
    renderAlbamonParetoChart();
    return;
  }

  elements.visualizationEyebrow.textContent = "WORKSITE MAP";
  elements.mapHeading.textContent = `From ${state.payload.origin.name}`;
  elements.visualizationCaption.textContent = `Markers use actual worksite coordinates and show ${payMetricLabel()} in their details. Legend buttons can hide actionable or other matching statuses.`;
  elements.map.hidden = !state.map;
  elements.mapFallback.hidden = Boolean(state.map);
  renderMapMarkers();
  if (state.map) scheduleLeafletRefresh(state.map, fitAlbamonMap);
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

function syncQueueCardSelection() {
  const status = elements.status.value;
  const selected = {
    new: status === "apply" && elements.newOnly.checked,
    actionable: status === "apply" && !elements.newOnly.checked,
    review: ["needs_review", "review_requirements", "maybe_later"].includes(status),
  };
  for (const [card, active] of [
    [elements.summaryNewCard, selected.new],
    [elements.summaryActionableCard, selected.actionable],
    [elements.summaryAttentionCard, selected.review],
  ]) {
    card.classList.toggle("is-selected", active);
    card.setAttribute("aria-pressed", String(active));
  }
}

function renderHeader() {
  const { payload } = state;
  renderOriginControl(payload.origin);
  elements.refreshState.textContent = payload.route_origin_current
    ? `Report refreshed ${formatDateTime(payload.generated_at)}`
    : "Routes need refresh from the new origin";
  elements.summaryNew.textContent = formatNumber(payload.summary.new_actionable_count);
  elements.summaryActionable.textContent = formatNumber(payload.summary.actionable_count);
  elements.summaryAttention.textContent = formatNumber(payload.summary.needs_review_count);
  const reviewBreakdown = payload.summary.needs_review_breakdown || {};
  elements.summaryActionableDetail.textContent =
    `${formatNumber(payload.summary.actionable_clear_count)} clear · ` +
    `${formatNumber(reviewBreakdown.verify_requirements)} need a condition check`;
  elements.summaryAttentionDetail.textContent =
    `${formatNumber(reviewBreakdown.verify_requirements)} condition checks · ` +
    `${formatNumber(reviewBreakdown.deferred)} deferred`;
  elements.summaryNewCard.setAttribute(
    "aria-label",
    `${formatNumber(payload.summary.new_actionable_count)} actionable jobs first seen in the last ${payload.new_window_hours || 24} hours. Show this queue.`,
  );
  elements.summaryActionableCard.setAttribute(
    "aria-label",
    `${formatNumber(payload.summary.actionable_count)} jobs passed automatic eligibility filters; ${formatNumber(reviewBreakdown.verify_requirements)} still need a posting-condition check. Show actionable jobs.`,
  );
  elements.summaryAttentionCard.setAttribute(
    "aria-label",
    `${formatNumber(payload.summary.needs_review_count)} manual review items: ${formatNumber(reviewBreakdown.verify_requirements)} posting-condition checks and ${formatNumber(reviewBreakdown.deferred)} deferred decisions. Show this queue.`,
  );
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
      paretoOnly: false,
      newOnly: elements.newOnly.checked,
      closingOnly: elements.closingOnly.checked,
      requirementsOnly: elements.requirements.checked,
      easyApplyOnly: elements.easyApply.checked,
      sort: elements.sort.value,
    },
    state.comparedIds,
    state.shortlistOnly,
  );
  elements.resultsEyebrow.textContent = "RANKED RESULTS";
  updateAlbamonFilterSummary();
  syncQueueCardSelection();
  renderResults();
  syncWorksiteVisualization();
}

function badge(text, variant = "") {
  return createElement("span", `badge${variant ? ` badge--${variant}` : ""}`, text);
}

function appendLimitedBadges(container, specs, maximum = 3) {
  const items = specs.filter(Boolean);
  const visible = items.length > maximum ? items.slice(0, maximum - 1) : items;
  for (const item of visible) container.append(badge(item.text, item.variant));
  if (items.length > maximum) {
    const overflow = badge(`+${items.length - maximum + 1}`);
    overflow.title = items.slice(maximum - 1).map((item) => item.text).join(" · ");
    overflow.setAttribute("aria-label", `Also: ${overflow.title}`);
    container.append(overflow);
  }
}

function jobBadgeSpecs(job) {
  const specs = [];
  if (job.needs_review_reason === "verify_requirements") specs.push({ text: "Check condition", variant: "warning" });
  else if (job.needs_review_reason === "deferred") specs.push({ text: "Deferred", variant: "warning" });
  else if (job.review_resolution_status === "requirements_verified") specs.push({ text: "Check complete", variant: "evidence" });
  else if ((job.hard_requirements || []).length > 0) specs.push({ text: "Requirements", variant: "warning" });
  if (job.is_new) specs.push({ text: "New", variant: "new" });
  if (job.pareto_frontier) specs.push({ text: "Pareto", variant: "pareto" });
  if (job.is_closing_soon) specs.push({ text: "Closing soon", variant: "warning" });
  if ((job.eligibility_lanes || []).includes("remote_short_term")) specs.push({ text: "Remote", variant: "evidence" });
  else if ((job.eligibility_lanes || []).includes("songpa_late_night")) specs.push({ text: "Station evening", variant: "evidence" });
  else if (job.weekend_evidence_type === "exact") specs.push({ text: "Exact weekend", variant: "evidence" });
  else if (job.weekend_evidence_type === "negotiable") specs.push({ text: "Weekend flexible", variant: "" });
  if (job.pay_adjusted_commute) specs.push({ text: "Premium commute", variant: "warning" });
  if (job.application_disposition === "applied") specs.push({ text: "History", variant: "applied" });
  return specs;
}

function providerLegendBadge(provider) {
  const item = createElement("span", "provider-legend-badge");
  const swatch = createElement("i", "provider-swatch");
  swatch.style.setProperty("--provider-color", provider.color || "#73847d");
  item.append(swatch, createElement("span", "", provider.label || provider.agency || "Provider"));
  return item;
}

function providerLegendItem(provider, { includePay = true } = {}) {
  const item = providerLegendBadge(provider);
  if (includePay) item.append(createElement("strong", "", formatComparablePay(provider.comparablePay)));
  item.title = `${provider.label}: best visible ${formatComparablePay(provider.comparablePay)} across ${provider.hotelCount || 1} hotel${provider.hotelCount === 1 ? "" : "s"}`;
  return item;
}

function markerLegendToggle(label, dotClass, active, onClick) {
  const button = createElement("button", `legend-toggle${active ? " is-active" : ""}`);
  button.type = "button";
  button.setAttribute("aria-pressed", String(active));
  button.append(createElement("i", `dot ${dotClass}`), document.createTextNode(label));
  button.addEventListener("click", onClick);
  return button;
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
  elements.filterFeedback.textContent =
    `${formatNumber(state.filteredJobs.length)} ${state.shortlistOnly ? "shortlisted" : "matches"} · ` +
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
    appendLimitedBadges(badges, jobBadgeSpecs(job));
    if (badges.childElementCount) jobCell.append(badges);
    appendText(jobCell, "strong", "", job.title || "Untitled job");
    appendText(jobCell, "small", "", `${job.company || "Unknown company"} · ${job.target_district || job.area || "Area unknown"}`);
    row.append(jobCell);

    const payCell = createElement("td", "job-pay-cell");
    payCell.dataset.label = "Pay";
    appendText(
      payCell,
      "span",
      "pay-value",
      hasNumericValue(job.work_hourly_pay)
        ? `₩${formatNumber(job.work_hourly_pay)}/hr work`
        : "Work-hour rate unverified",
    );
    appendText(
      payCell,
      "span",
      "cell-secondary",
      hasNumericValue(job.door_to_door_hourly_pay)
        ? `₩${formatNumber(job.door_to_door_hourly_pay)}/hr door-to-door`
        : "Door-to-door rate unavailable",
    );
    appendText(
      payCell,
      "span",
      "cell-secondary",
      hasNumericValue(job.estimated_shift_earnings_won)
        ? `${formatWon(job.estimated_shift_earnings_won)} total for the verified shift`
        : "Shift total unavailable",
    );
    appendText(payCell, "span", "cell-secondary", `${job.salary_type || "Pay"} ${job.salary || "not stated"}`);
    row.append(payCell);

    const scheduleCell = createElement("td", "job-schedule-cell");
    scheduleCell.dataset.label = "Schedule";
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

    const commuteCell = createElement("td", "job-commute-cell");
    commuteCell.dataset.label = "Commute";
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

    const statusCell = createElement("td", "job-decision-cell");
    statusCell.dataset.label = "Decision";
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
      comparisonFact(
        "Work-hour equivalent",
        hasNumericValue(job.work_hourly_pay) ? `₩${formatNumber(job.work_hourly_pay)}/hr` : "Unverified",
      ),
      comparisonFact(
        "Door-to-door rate",
        hasNumericValue(job.door_to_door_hourly_pay) ? `₩${formatNumber(job.door_to_door_hourly_pay)}/hr` : "Unavailable",
      ),
      comparisonFact(
        "Total shift pay",
        hasNumericValue(job.estimated_shift_earnings_won) ? formatWon(job.estimated_shift_earnings_won) : "Unavailable",
      ),
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
    const selected = recruitNo === nextId;
    const pointControl = [...elements.albamonParetoChart.querySelectorAll("[data-pareto-job-members]")]
      .find((control) => (control.dataset.paretoJobMembers || "").split(" ").includes(String(recruitNo)));
    if (pointControl) {
      pointControl.classList.toggle("is-selected", selected);
      pointControl.setAttribute("aria-pressed", String(selected));
      const visualPoint = elements.albamonParetoChart.querySelector(`[data-pareto-visual-job-id="${recruitNo}"]`);
      if (visualPoint) visualPoint.classList.toggle("chart-point--selected", selected);
    }
  }
  if (state.albamonVisualization === "map" && state.map) renderMapMarkers();
}

function hotelById(hotelId) {
  return (state.filteredHotels || []).find(
    (hotel) => String(hotel.hotel_id) === String(hotelId),
  ) || (state.hotelPayload?.hotels || []).find(
    (hotel) => String(hotel.hotel_id) === String(hotelId),
  ) || null;
}

function updateHotelSelection(previousId, nextId) {
  for (const hotelId of [previousId, nextId]) {
    if (!hotelId) continue;
    const selected = String(hotelId) === String(nextId || "");
    const row = elements.hotelRows.querySelector(`[data-hotel-id="${hotelId}"]`);
    if (row) row.classList.toggle("is-selected", selected);
    const controls = [...elements.hotelChart.querySelectorAll("[data-hotel-point-hotel-ids]")]
      .filter((candidate) => (candidate.dataset.hotelPointHotelIds || "").split(" ").includes(String(hotelId)));
    for (const control of controls) {
      control.classList.toggle("is-selected", selected);
      control.setAttribute("aria-pressed", String(selected));
    }
    const visualPoints = [...elements.hotelChart.querySelectorAll("[data-hotel-visual-hotel-id]")]
      .filter((candidate) => candidate.dataset.hotelVisualHotelId === String(hotelId));
    for (const visualPoint of visualPoints) {
      visualPoint.classList.toggle("chart-point--selected", selected);
    }
  }
  if (state.hotelVisualization === "map" && state.hotelMap) renderHotelMapMarkers();
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

function visibleAlbamonMapJobs() {
  return state.filteredJobs.filter((job) => {
    const category = job.application_disposition === "apply" ? "actionable" : "other";
    return state.albamonMarkerVisibility[category] && hasPlausibleMapLocation(job);
  });
}

function visibleHotelMapHotels() {
  return state.filteredHotels.filter((hotel) => {
    const category = isHotelOnFrontier(hotel, state.hotelPayMode, state.payMetric)
      ? "frontier"
      : "comparable";
    return state.hotelMarkerVisibility[category] && hasPlausibleMapLocation(hotel);
  });
}

function clusteredMarkerIcon(count, selected, frontier = false) {
  const classes = ["map-marker", "map-marker--cluster"];
  if (frontier) classes.push("map-marker--frontier");
  if (selected) classes.push("map-marker--selected");
  return window.L.divIcon({
    className: "",
    html: `<div class="${classes.join(" ")}"><span>${count}</span></div>`,
    iconSize: selected ? [34, 34] : [30, 30],
    iconAnchor: selected ? [10, 30] : [8, 27],
  });
}

function mapClusterTooltip(items, kind) {
  const content = createElement("div", "map-point-tooltip map-cluster-tooltip");
  appendText(content, "strong", "pareto-tooltip-company", `${items.length} ${kind === "hotel" ? "hotels" : "jobs"} at this location`);
  const list = createElement("ul", "detail-list");
  for (const item of items.slice(0, 6)) {
    const pay = kind === "hotel"
      ? hotelComparablePay(item, state.hotelPayMode, state.payMetric)
      : Number(state.payMetric === "shift_total" ? item.estimated_shift_earnings_won : item.comparable_hourly_pay) || 0;
    const provider = kind === "hotel"
      ? hotelProviderComparisons(item, state.hotelPayMode, state.payMetric)[0]
      : null;
    appendText(
      list,
      "li",
      "",
      `${item.employer || item.company || item.title} · ${formatComparablePay(pay)} · ${item.transit_minutes ?? "?"} min${provider ? ` · ${provider.label}` : ""}`,
    );
  }
  if (items.length > 6) appendText(list, "li", "", `+ ${items.length - 6} more`);
  content.append(list, createElement("span", "pareto-tooltip-hint", "Click to choose a result"));
  return content;
}

function loadMapLibrary() {
  if (window.L) return Promise.resolve();
  if (state.mapLoadPromise) return state.mapLoadPromise;

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

function scheduleLeafletRefresh(map, fit) {
  if (!map) return;
  window.requestAnimationFrame(() => {
    map.invalidateSize({ pan: false });
    fit();
    window.setTimeout(() => {
      map.invalidateSize({ pan: false });
      fit();
    }, 80);
  });
}

function observeLeafletContainer(element, map, fit) {
  if (!("ResizeObserver" in window)) return null;
  const observer = new ResizeObserver(() => {
    if (!element.hidden && element.offsetWidth > 0 && element.offsetHeight > 0) {
      scheduleLeafletRefresh(map, fit);
    }
  });
  observer.observe(element);
  return observer;
}

async function requestMapLoad() {
  if (state.map) {
    syncWorksiteVisualization();
    return;
  }
  elements.mapStatusTitle.textContent = "Loading worksite map…";
  elements.mapStatusMessage.textContent = "The shortlist remains fully usable while map assets load.";
  try {
    await loadMapLibrary();
    if (state.albamonVisualization !== "map" || state.activeDesk !== "albamon") return;
    elements.map.hidden = false;
    elements.mapFallback.hidden = true;
    await new Promise((resolve) => window.requestAnimationFrame(resolve));
    initializeMap();
    renderMapMarkers();
    scheduleLeafletRefresh(state.map, fitAlbamonMap);
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
    html: '<div class="origin-marker">ORIGIN</div>',
    iconSize: [34, 34],
    iconAnchor: [17, 17],
  });
  state.originMarker = window.L.marker([origin.latitude, origin.longitude], { icon: originIcon, zIndexOffset: 900 })
    .addTo(state.map)
    .bindTooltip(`Commute origin · ${origin.name}`, { direction: "top" });
  state.map.on("click", handleOriginMapClick);
  syncOriginCandidateMarkers();
  state.mapResizeObserver = observeLeafletContainer(elements.map, state.map, fitAlbamonMap);
}

function fitAlbamonMap() {
  if (!state.map || !state.payload || elements.map.hidden) return;
  const bounds = [[state.payload.origin.latitude, state.payload.origin.longitude]];
  for (const job of visibleAlbamonMapJobs()) {
    bounds.push([Number(job.latitude), Number(job.longitude)]);
  }
  if (bounds.length > 1) state.map.fitBounds(bounds, { padding: [28, 28], maxZoom: 13 });
  else state.map.setView(bounds[0], 12);
}

function renderMapMarkers() {
  if (state.albamonVisualization !== "map") return;
  initializeMap();
  if (!state.map) return;
  for (const marker of new Set(state.markers.values())) marker.remove();
  state.markers.clear();
  const groups = new Map();
  const frontierIds = new Set(
    albamonParetoPoints(state.filteredJobs, state.payMetric)
      .filter((point) => point.frontier)
      .map((point) => Number(point.job.recruit_no)),
  );
  for (const job of visibleAlbamonMapJobs()) {
    const key = `${Number(job.latitude).toFixed(5)},${Number(job.longitude).toFixed(5)}`;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(job);
  }
  for (const jobs of groups.values()) {
    const first = jobs[0];
    const selected = jobs.some((job) => Number(job.recruit_no) === state.selectedId);
    const marker = window.L.marker([Number(first.latitude), Number(first.longitude)], {
      icon: jobs.length === 1 ? markerIcon(first, selected) : clusteredMarkerIcon(jobs.length, selected, jobs.some((job) => frontierIds.has(Number(job.recruit_no)))),
      zIndexOffset: selected ? 1000 : 0,
      title: jobs.length === 1 ? `${first.company} — ${first.title}` : `${jobs.length} jobs at this worksite`,
    });
    marker.bindTooltip(jobs.length === 1 ? albamonMapTooltip(first, frontierIds) : mapClusterTooltip(jobs, "job"), richMapTooltipOptions());
    marker.on("click", () => jobs.length === 1
      ? selectJob(Number(first.recruit_no), elements.map)
      : openPointChoices(jobs.map((job) => albamonPointForJob(job, state.payMetric)), "albamon"));
    marker.addTo(state.map);
    for (const job of jobs) state.markers.set(Number(job.recruit_no), marker);
  }
  fitAlbamonMap();
}

function hotelMarkerIcon(hotels, selected) {
  const providers = mergedHotelProviderComparisons(hotels, state.hotelPayMode, state.payMetric);
  const frontier = hotels.some((hotel) => isHotelOnFrontier(hotel, state.hotelPayMode, state.payMetric));
  if (state.hotelGranularity === "provider") {
    const size = providers.length > 1 || selected ? 46 : 30;
    const radius = providers.length <= 1 ? 0 : providers.length === 2 ? 10 : 13;
    const dots = providers.map((provider, index) => {
      const angle = -Math.PI / 2 + (2 * Math.PI * index) / providers.length;
      const x = Math.round(Math.cos(angle) * radius);
      const y = Math.round(Math.sin(angle) * radius);
      return `<span class="hotel-provider-cloud__dot" style="--provider-color:${provider.color};--provider-x:${x}px;--provider-y:${y}px"></span>`;
    }).join("");
    const classes = ["hotel-provider-cloud"];
    if (frontier) classes.push("hotel-provider-cloud--frontier");
    if (selected) classes.push("hotel-provider-cloud--selected");
    const count = hotels.length > 1
      ? `<span class="hotel-provider-cloud__count">${hotels.length}</span>`
      : "";
    return window.L.divIcon({
      className: "",
      html: `<div class="${classes.join(" ")}" style="--cloud-size:${size}px"><span class="hotel-provider-cloud__anchor"></span>${dots}${count}</div>`,
      iconSize: [size, size],
      iconAnchor: [size / 2, size / 2],
    });
  }
  const size = selected || hotels.length > 1 ? 34 : 28;
  const classes = ["hotel-source-marker"];
  if (frontier) classes.push("hotel-source-marker--frontier");
  if (selected) classes.push("hotel-source-marker--selected");
  if (hotels.length > 1) classes.push("hotel-source-marker--cluster");
  const center = hotels.length > 1 ? String(hotels.length) : "";
  return window.L.divIcon({
    className: "",
    html: `<div class="${classes.join(" ")}" style="--provider-ring:${providerConicGradient(providers)};--provider-best:${providers[0]?.color || "#73847d"}"><span class="hotel-source-marker__core">${center}</span></div>`,
    iconSize: [size, size],
    iconAnchor: [size / 2, size / 2],
  });
}

function initializeHotelMap() {
  if (state.hotelMap || !window.L || !state.hotelPayload) return;
  const { origin } = state.hotelPayload;
  state.hotelMap = window.L.map(elements.hotelMap, { zoomControl: true })
    .setView([origin.latitude, origin.longitude], 12);
  window.L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: "&copy; OpenStreetMap contributors",
    maxZoom: 19,
  }).addTo(state.hotelMap);
  const originIcon = window.L.divIcon({
    className: "",
    html: '<div class="origin-marker">ORIGIN</div>',
    iconSize: [34, 34],
    iconAnchor: [17, 17],
  });
  state.hotelOriginMarker = window.L.marker(
    [origin.latitude, origin.longitude],
    { icon: originIcon, zIndexOffset: 900 },
  ).addTo(state.hotelMap).bindTooltip(`Commute origin · ${origin.name}`, { direction: "top" });
  state.hotelMap.on("click", handleOriginMapClick);
  state.hotelMap.on("zoomend moveend", scheduleHotelMapLabelRefresh);
  syncOriginCandidateMarkers();
  state.hotelMapResizeObserver = observeLeafletContainer(
    elements.hotelMap,
    state.hotelMap,
    fitHotelMap,
  );
}

function clearHotelMapLabels() {
  if (state.hotelLabelFrame !== null) {
    window.cancelAnimationFrame(state.hotelLabelFrame);
    state.hotelLabelFrame = null;
  }
  for (const label of state.hotelMapLabels) label.remove();
  state.hotelMapLabels = [];
}

function scheduleHotelMapLabelRefresh() {
  if (!state.hotelMap || state.hotelVisualization !== "map") return;
  if (state.hotelLabelFrame !== null) window.cancelAnimationFrame(state.hotelLabelFrame);
  state.hotelLabelFrame = window.requestAnimationFrame(() => {
    state.hotelLabelFrame = null;
    renderHotelMapLabels();
  });
}

function rectanglesOverlap(left, right, padding = 4) {
  return !(
    left.right + padding < right.left ||
    right.right + padding < left.left ||
    left.bottom + padding < right.top ||
    right.bottom + padding < left.top
  );
}

function renderHotelMapLabels() {
  if (!state.hotelMap || state.hotelVisualization !== "map" || elements.hotelMap.hidden) return;
  for (const label of state.hotelMapLabels) label.remove();
  state.hotelMapLabels = [];

  const zoom = state.hotelMap.getZoom();
  const groups = new Map();
  for (const hotel of visibleHotelMapHotels()) {
    const key = `${Number(hotel.latitude).toFixed(5)},${Number(hotel.longitude).toFixed(5)}`;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(hotel);
  }
  let candidates = [...groups.values()].map((hotels) => {
    const selected = hotels.some((hotel) => String(hotel.hotel_id) === state.selectedHotelId);
    const frontier = hotels.some((hotel) => isHotelOnFrontier(hotel, state.hotelPayMode, state.payMetric));
    const pay = Math.max(...hotels.map((hotel) => hotelComparablePay(hotel, state.hotelPayMode, state.payMetric) || 0));
    return { hotels, selected, frontier, pay };
  }).filter((candidate) => candidate.selected || candidate.frontier || zoom >= 14)
    .sort((left, right) =>
      Number(right.selected) - Number(left.selected) ||
      Number(right.frontier) - Number(left.frontier) ||
      right.pay - left.pay
    );
  if (zoom < 12) candidates = candidates.slice(0, 4);

  const occupied = [];
  const mapSize = state.hotelMap.getSize();
  for (const candidate of candidates) {
    const hotel = candidate.hotels[0];
    const point = state.hotelMap.latLngToContainerPoint([
      Number(hotel.latitude),
      Number(hotel.longitude),
    ]);
    const employer = candidate.hotels.length > 1
      ? `${candidate.hotels.length} hotels`
      : cleanText(hotel.employer) || "Hotel";
    const provider = hotelProviderComparisons(hotel, state.hotelPayMode, state.payMetric)[0];
    const suffix = state.hotelGranularity === "provider" && provider ? ` · ${provider.label}` : "";
    const text = `${truncateLabel(employer, 22)}${suffix}`;
    const width = Math.min(210, 30 + text.length * 7);
    const height = 25;
    const placeLeft = point.x + 22 + width > mapSize.x - 8;
    const rect = {
      left: placeLeft ? point.x - 22 - width : point.x + 22,
      right: placeLeft ? point.x - 22 : point.x + 22 + width,
      top: point.y - height / 2,
      bottom: point.y + height / 2,
    };
    if (!candidate.selected && occupied.some((current) => rectanglesOverlap(rect, current))) continue;
    occupied.push(rect);
    const content = createElement("span", "hotel-map-label-content");
    if (provider) {
      const swatch = createElement("i", "provider-swatch");
      swatch.style.setProperty("--provider-color", provider.color);
      content.append(swatch);
    }
    content.append(document.createTextNode(text));
    const label = window.L.tooltip({
      permanent: true,
      direction: placeLeft ? "left" : "right",
      offset: [placeLeft ? -18 : 18, 0],
      className: `hotel-map-label${candidate.selected ? " hotel-map-label--selected" : ""}`,
      opacity: 1,
      interactive: false,
    }).setLatLng([Number(hotel.latitude), Number(hotel.longitude)])
      .setContent(content)
      .addTo(state.hotelMap);
    state.hotelMapLabels.push(label);
  }
}

function fitHotelMap() {
  if (!state.hotelMap || !state.hotelPayload || elements.hotelMap.hidden) return;
  const { origin } = state.hotelPayload;
  const bounds = [[origin.latitude, origin.longitude]];
  for (const hotel of visibleHotelMapHotels()) {
    bounds.push([Number(hotel.latitude), Number(hotel.longitude)]);
  }
  if (bounds.length > 1) state.hotelMap.fitBounds(bounds, { padding: [28, 28], maxZoom: 13 });
  else state.hotelMap.setView(bounds[0], 12);
}

function renderHotelMapMarkers() {
  if (state.hotelVisualization !== "map") return;
  initializeHotelMap();
  if (!state.hotelMap) return;
  clearHotelMapLabels();
  for (const marker of new Set(state.hotelMarkers.values())) marker.remove();
  state.hotelMarkers.clear();
  const groups = new Map();
  const allProviderPoints = state.hotelGranularity === "provider"
    ? hotelProviderParetoPoints(state.filteredHotels, state.hotelPayMode, state.payMetric)
    : [];
  for (const hotel of visibleHotelMapHotels()) {
    const key = `${Number(hotel.latitude).toFixed(5)},${Number(hotel.longitude).toFixed(5)}`;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(hotel);
  }
  for (const hotels of groups.values()) {
    const first = hotels[0];
    const selected = hotels.some((hotel) => String(hotel.hotel_id) === state.selectedHotelId);
    const hotelIds = new Set(hotels.map((hotel) => String(hotel.hotel_id)));
    const providerPoints = allProviderPoints.filter((point) =>
      hotelIds.has(String(point.hotel.hotel_id))
    );
    const marker = window.L.marker([Number(first.latitude), Number(first.longitude)], {
      icon: hotelMarkerIcon(hotels, selected),
      zIndexOffset: selected ? 1000 : 0,
      title: hotels.length === 1
        ? `${first.employer} — ${hotelProviderComparisons(first, state.hotelPayMode, state.payMetric).map((provider) => provider.label).join(", ")}`
        : `${hotels.length} hotels at this worksite`,
    });
    marker.bindTooltip(hotels.length === 1 ? hotelMapTooltip(first) : mapClusterTooltip(hotels, "hotel"), richMapTooltipOptions());
    marker.on("click", () => {
      const choices = state.hotelGranularity === "provider"
        ? providerPoints
        : hotels.map((hotel) => hotelPointForHotel(hotel, state.hotelPayMode, state.payMetric));
      if (choices.length === 1) selectHotel(String(choices[0].hotel.hotel_id), elements.hotelMap);
      else openPointChoices(choices, "hotel");
    });
    marker.addTo(state.hotelMap);
    for (const hotel of hotels) state.hotelMarkers.set(String(hotel.hotel_id), marker);
  }
  fitHotelMap();
  scheduleHotelMapLabelRefresh();
}

async function requestHotelMapLoad() {
  if (state.hotelMap) {
    syncHotelVisualization();
    return;
  }
  try {
    await loadMapLibrary();
    if (state.hotelVisualization !== "map" || state.activeDesk !== "hotels") return;
    elements.hotelMap.hidden = false;
    elements.hotelMapFallback.hidden = true;
    await new Promise((resolve) => window.requestAnimationFrame(resolve));
    initializeHotelMap();
    renderHotelMapMarkers();
    scheduleLeafletRefresh(state.hotelMap, fitHotelMap);
  } catch {
    elements.hotelMap.hidden = true;
    elements.hotelMapFallback.hidden = false;
    const loader = elements.hotelMapFallback.querySelector(".map-loader");
    if (loader) loader.hidden = true;
    const title = elements.hotelMapFallback.querySelector("strong");
    const message = elements.hotelMapFallback.querySelector("span:last-child");
    if (title) title.textContent = "Hotel map unavailable";
    if (message) message.textContent = "The table and Pareto chart still contain every hotel and exact shift.";
  }
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

function renderHotelDrawer(hotel) {
  const frontier = isHotelOnFrontier(hotel, state.hotelPayMode, state.payMetric);
  elements.drawerKicker.textContent = `${frontier ? "Pareto frontier" : "Hotel option"} · Rank #${hotel.rank || "—"}`;
  elements.drawerTitle.textContent = hotel.employer || "Unknown hotel";
  elements.drawerCompany.textContent = `${hotelLocationLabel(hotel)} · ${hotel.transit_minutes ?? "?"} min from ${state.hotelPayload.origin.name}`;
  elements.drawerBody.replaceChildren();

  const visibleOptions = hotel.filtered_job_options || hotel.job_options || [];
  const visibleAgencies = new Set(visibleOptions.flatMap((option) => option.agencies || []));
  const visibleProviders = (hotel.providers || []).filter((provider) =>
    !visibleAgencies.size || visibleAgencies.has(provider.agency)
  );
  const providerLinks = visibleProviders.filter((provider) => provider.representative_url);
  const routes = directionLinks(hotel);
  if (providerLinks.length || routes) {
    const actions = createElement("div", "detail-actions");
    for (const [index, provider] of providerLinks.entries()) {
      actions.append(actionLink(
        `Open ${provider.label || provider.agency}`,
        provider.representative_url,
        index === 0,
      ));
    }
    if (routes) {
      actions.append(
        actionLink("Kakao directions", routes.kakao),
        actionLink("Naver directions", routes.naver),
      );
    }
    elements.drawerBody.append(actions);
  }

  const overview = detailSection("Hotel snapshot");
  const grid = createElement("div", "detail-grid");
  const decision = hotelDecisionSummary(hotel);
  grid.append(
    fact("Best actual/base", `${formatWon(hotel.actual_hourly_max)}/hr`),
    fact("Best daytime", hotel.daytime_actual_hourly_max ? `${formatWon(hotel.daytime_actual_hourly_max)}/hr` : "No 06:00–20:00 shift"),
    fact("Conditional 주휴*", hotel.weekly_allowance_hourly_max ? `${formatWon(hotel.weekly_allowance_hourly_max)}/hr` : "Not published"),
    fact("Verified commute", `${hotel.transit_minutes ?? "?"} min`),
    fact("Available dates", `${hotel.date_count || 0}`),
    fact("Exact shifts", `${hotel.shift_count || 0}`),
    fact(
      "Best shift total",
      hotel.actual_shift_total_max
        ? `${formatWon(hotel.actual_shift_total_max)}${hotel.shift_total_includes_scheduled_estimates ? " · scheduled gross estimate" : ""}`
        : "Unavailable",
    ),
    fact("Door-to-door rate", decision.adjustedHourly ? `${formatWon(decision.adjustedHourly)}/hr` : "Unavailable"),
  );
  overview.append(grid);
  elements.drawerBody.append(overview);

  const shifts = detailSection("Exact shift options");
  shifts.append(detailList(
    visibleOptions.map((option) => {
      const weekly = option.weekly_allowance_hourly_won
        ? ` · 주휴* ${formatComparablePay(option.weekly_allowance_hourly_won, "hourly")}`
        : "";
      const providers = (option.best_agencies || []).join(" · ") || "provider check";
      const fixedShiftText = option.estimated_shift_earnings_won
        ? ` · ${formatWon(option.estimated_shift_earnings_won)} fixed shift`
        : "";
      const doorToDoorText = option.commute_adjusted_hourly_won
        ? ` · ${formatWon(option.commute_adjusted_hourly_won)}/hr door-to-door`
        : "";
      const warnings = [option.late_return_risk ? "late-return check" : "", ...(option.pay_warnings || [])].filter(Boolean);
      return `${option.work_date || (option.dates || []).join(", ")} · ${option.shift} · actual ${formatComparablePay(option.actual_hourly_won ?? option.actual_hourly_max, "hourly")}${weekly}${fixedShiftText}${doorToDoorText} · ${providers}${warnings.length ? ` · Check: ${warnings.join("; ")}` : ""}`;
    }),
    "No exact shift options were preserved.",
  ));
  elements.drawerBody.append(shifts);

  const providers = detailSection("Provider comparison");
  const providerGrid = createElement("div", "detail-grid");
  for (const provider of visibleProviders) {
    const item = createElement("div", "fact provider-comparison-card");
    appendText(item, "span", "", provider.label || provider.agency || "Provider");
    appendText(item, "strong", "", `Actual ${formatComparablePay(provider.actual_hourly_max, "hourly")}`);
    appendText(
      item,
      "small",
      "cell-secondary",
      `주휴* ${formatComparablePay(provider.weekly_allowance_hourly_max, "hourly")} · ${provider.offer_count || 0} offers`,
    );
    appendText(
      item,
      "small",
      "cell-secondary",
      `Best shift ${formatWon(provider.actual_shift_total_max)}`,
    );
    if (provider.representative_url) {
      item.append(actionLink("View best listing", provider.representative_url));
    }
    providerGrid.append(item);
  }
  providers.append(providerGrid);
  elements.drawerBody.append(providers);

  const evidence = detailSection("Coverage and role evidence");
  evidence.append(detailList([
    `Dates: ${(hotel.dates || []).join(", ") || "not available"}`,
    `Roles: ${(hotel.roles || []).map((role) => HOTEL_ROLE_LABELS[role] || role).join(", ") || "not classified"}`,
    `Aggregated choices: ${hotel.job_count || 0} jobs / ${hotel.offer_count || 0} source offers`,
    hotel.cross_agency_duplicate_count
      ? `${hotel.cross_agency_duplicate_count} cross-agency duplicate group(s) compared`
      : "No cross-agency duplicate group for this hotel",
    hotel.source_snapshot_stale ? "Contains a visibly marked bounded source snapshot" : "All included source rows are live",
  ]));
  elements.drawerBody.append(evidence);

  const metadata = createElement("div", "detail-meta");
  metadata.append(
    createElement("span", "", `Hotel ID ${hotel.hotel_id}`),
    createElement("span", "", `Pay view: ${state.hotelPayMode === "actual" ? "actual/base" : "conditional 주휴 with fallback"} · ${payMetricLabel()}`),
  );
  elements.drawerBody.append(metadata);
}

function reviewWorkflowSection(job) {
  const openRequirementReview = job.needs_review_reason === "verify_requirements";
  const deferredReview = job.needs_review_reason === "deferred";
  const requirementsVerified = job.review_resolution_status === "requirements_verified";
  if (!openRequirementReview && !deferredReview && !requirementsVerified) return null;

  const section = detailSection("Manual review status");
  const messageClass = requirementsVerified ? "resolution-box" : "warning-box";
  if (openRequirementReview) {
    appendText(
      section,
      "div",
      messageClass,
      job.review_reason_summary || "This posting contains a condition JobScout cannot verify automatically.",
    );
    appendText(
      section,
      "p",
      "detail-help",
      "Resolve this only after checking every relevant item below against the posting or employer response.",
    );
    const blockingItems = [
      ...(job.hard_requirements || []),
      ...(job.certificate_requirements || []),
    ];
    const evidenceItems = blockingItems.length > 0
      ? blockingItems
      : (job.verify_before_sending || []);
    const reviewItems = [];
    const seenReviewItems = new Set();
    for (const value of evidenceItems) {
      const item = String(value || "")
        .trim()
        .replace(/^지원서 필수 질문 확인:\s*/, "지원서 필수 질문: ");
      const key = item.replace(/\s+/g, " ").toLocaleLowerCase("ko-KR");
      if (!item || seenReviewItems.has(key)) continue;
      seenReviewItems.add(key);
      reviewItems.push(item);
    }
    section.append(detailList(reviewItems, "No additional evidence was captured; inspect the original posting."));
    const confirm = createElement("button", "primary-button", "Mark this check complete");
    confirm.type = "button";
    confirm.addEventListener("click", () => saveReviewResolution(job, "confirm_requirements"));
    const actions = createElement("div", "decision-buttons");
    actions.append(confirm);
    section.append(actions);
  } else if (deferredReview) {
    appendText(
      section,
      "div",
      messageClass,
      `You marked this job “Maybe later”${job.disposition_reason ? `: ${job.disposition_reason}` : "."} Restore it to actionable now, or skip it in “Your decision” below.`,
    );
    const restore = createElement("button", "primary-button", "Restore actionable");
    restore.type = "button";
    restore.addEventListener("click", () => saveDisposition(job, "apply", ""));
    const actions = createElement("div", "decision-buttons");
    actions.append(restore);
    section.append(actions);
  } else {
    appendText(
      section,
      "div",
      messageClass,
      `Check completed ${formatDateTime(job.review_resolved_at)}. This exact evidence version is excluded from the manual review queue; changed evidence will reopen it automatically.`,
    );
    const reopen = createElement("button", "secondary-button", "Reopen posting-condition review");
    reopen.type = "button";
    reopen.addEventListener("click", () => saveReviewResolution(job, "reopen_requirements"));
    const actions = createElement("div", "decision-buttons");
    actions.append(reopen);
    section.append(actions);
  }
  return section;
}

function renderDrawer(job) {
  elements.drawerKicker.textContent = `${STATUS_LABELS[job.application_disposition] || "Job"} · Tier ${job.utility_tier || "—"} · Rank #${job.utility_rank || "—"}`;
  elements.drawerTitle.textContent = job.title || "Untitled job";
  elements.drawerCompany.textContent = `${job.company || "Unknown company"} · ${job.target_district || job.area || "Area unknown"}`;
  elements.drawerBody.replaceChildren();

  const actions = createElement("div", "detail-actions");
  actions.append(actionLink("Open posting", job.url, true));
  if (job.transit_route_url) actions.append(actionLink("Verified route", job.transit_route_url));
  const routes = directionLinks(job);
  if (routes) {
    actions.append(
      actionLink("Kakao directions", routes.kakao),
      actionLink("Naver directions", routes.naver),
    );
  }
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

  const reviewWorkflow = reviewWorkflowSection(job);
  if (reviewWorkflow) elements.drawerBody.append(reviewWorkflow);

  const overview = detailSection("Decision snapshot");
  const grid = createElement("div", "detail-grid");
  grid.append(
    fact(
      "Work-hour equivalent",
      hasNumericValue(job.work_hourly_pay) ? `₩${formatNumber(job.work_hourly_pay)}/hr` : "Unverified",
    ),
    fact(
      "Door-to-door rate",
      hasNumericValue(job.door_to_door_hourly_pay) ? `₩${formatNumber(job.door_to_door_hourly_pay)}/hr` : "Unavailable",
    ),
    fact(
      "Total shift pay",
      hasNumericValue(job.estimated_shift_earnings_won) ? formatWon(job.estimated_shift_earnings_won) : "Unavailable",
    ),
    fact(
      "Total time commitment",
      hasNumericValue(job.total_time_commitment_minutes)
        ? `${formatNumber(job.total_time_commitment_minutes)} min including round-trip commute`
        : "Unavailable",
    ),
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

function openDetailDrawer(trigger) {
  state.lastFocused = trigger || document.activeElement;
  elements.drawerScrim.hidden = false;
  elements.drawer.inert = false;
  elements.drawer.classList.add("is-open");
  elements.drawer.setAttribute("aria-hidden", "false");
  window.setTimeout(() => elements.closeDrawer.focus(), 50);
}

async function selectJob(recruitNo, trigger) {
  const job = jobById(recruitNo);
  if (!job) return;
  const previousHotelId = state.selectedHotelId;
  state.selectedHotelId = null;
  updateHotelSelection(previousHotelId, null);
  const previousId = state.selectedId;
  state.selectedId = recruitNo;
  updateSelection(previousId, recruitNo);
  renderDrawerLoading(job);
  openDetailDrawer(trigger);
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

function selectHotel(hotelId, trigger) {
  const hotel = hotelById(hotelId);
  if (!hotel) return;
  state.detailRequestId += 1;
  if (state.detailAbortController) state.detailAbortController.abort();
  state.detailAbortController = null;
  const previousJobId = state.selectedId;
  state.selectedId = null;
  updateSelection(previousJobId, null);
  const previousHotelId = state.selectedHotelId;
  state.selectedHotelId = String(hotelId);
  updateHotelSelection(previousHotelId, state.selectedHotelId);
  renderHotelDrawer(hotel);
  openDetailDrawer(trigger);
}

function closeDrawer() {
  state.detailRequestId += 1;
  if (state.detailAbortController) state.detailAbortController.abort();
  state.detailAbortController = null;
  const previousJobId = state.selectedId;
  const previousHotelId = state.selectedHotelId;
  state.selectedId = null;
  state.selectedHotelId = null;
  updateSelection(previousJobId, null);
  updateHotelSelection(previousHotelId, null);
  hideParetoTooltip();
  hideHotelParetoTooltip();
  elements.drawer.classList.remove("is-open");
  elements.drawer.setAttribute("aria-hidden", "true");
  elements.drawer.inert = true;
  elements.drawerScrim.hidden = true;
  if (state.lastFocused && typeof state.lastFocused.focus === "function") state.lastFocused.focus();
  state.lastFocused = null;
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

async function saveReviewResolution(job, action) {
  try {
    const response = await fetch("/api/reviews", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ recruit_no: job.recruit_no, action }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Could not update the review");
    showToast(
      action === "confirm_requirements"
        ? "Posting-condition check resolved."
        : "Posting-condition review reopened.",
    );
    await loadData({ keepSelection: true });
  } catch (error) {
    showToast(error.message || "Could not update the review.", true);
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

function syncHotelMobileView() {
  const showVisual = state.hotelMobileView === "visual";
  elements.hotelFrontierLayout.classList.toggle("show-hotel-visual", showVisual);
  elements.hotelListTab.classList.toggle("is-active", !showVisual);
  elements.hotelVisualTab.classList.toggle("is-active", showVisual);
  elements.hotelListTab.setAttribute("aria-selected", String(!showVisual));
  elements.hotelVisualTab.setAttribute("aria-selected", String(showVisual));
}

function setHotelMobileView(view) {
  state.hotelMobileView = view === "visual" ? "visual" : "list";
  syncHotelMobileView();
  if (state.hotelMobileView !== "visual") return;
  if (state.hotelVisualization === "map") {
    requestHotelMapLoad().then(() => {
      if (state.hotelMap) window.setTimeout(() => state.hotelMap.invalidateSize(), 20);
    });
  } else if (state.hotelPayload) {
    syncHotelVisualization();
  }
}

function setDefaultFilterControls() {
  elements.search.value = "";
  elements.status.value = "apply";
  elements.schedule.value = "all";
  elements.commute.value = "policy";
  elements.pay.value = "0";
  elements.district.value = "all";
  elements.zone.value = "all";
  elements.newOnly.checked = false;
  elements.closingOnly.checked = false;
  elements.requirements.checked = false;
  elements.easyApply.checked = false;
  elements.sort.value = "rank";
  state.shortlistOnly = false;
}

function resetFilters() {
  setDefaultFilterControls();
  try {
    window.localStorage.removeItem(FILTER_STORAGE_KEY);
  } catch {
    // Reset still applies to the current view when storage is unavailable.
  }
  renderShortlistFilter();
  filterJobs();
}

function showQueue(queue) {
  setDefaultFilterControls();
  if (queue === "new") {
    elements.newOnly.checked = true;
  } else if (queue === "review") {
    elements.status.value = "needs_review";
    elements.commute.value = "all";
  } else if (queue === "requirements") {
    elements.status.value = "review_requirements";
    elements.commute.value = "all";
  } else if (queue === "deferred") {
    elements.status.value = "maybe_later";
    elements.commute.value = "all";
  }
  persistFilterPreferences();
  renderShortlistFilter();
  filterJobs();
  elements.resultsPanel.scrollIntoView({
    behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth",
    block: "start",
  });
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

async function searchOriginAddress() {
  const query = cleanText(elements.originAddressInput.value);
  if (!query) {
    showToast("Enter a Korean address first.", true);
    return;
  }
  elements.originSearchButton.disabled = true;
  elements.originHelp.textContent = "Checking the address through Kakao’s shared free-tier budget…";
  try {
    const response = await fetch("/api/origin/lookup", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Address lookup failed");
    setOriginCandidate(result.origin);
    stopOriginPickMode();
    elements.originHelp.textContent = "Address found. Check the map point before confirming recalculation.";
  } catch (error) {
    elements.originHelp.textContent = error.message || "Address lookup failed.";
    showToast(error.message || "Address lookup failed.", true);
  } finally {
    elements.originSearchButton.disabled = false;
  }
}

async function startOriginPinMode() {
  state.originPickMode = true;
  elements.originPinButton.classList.add("is-active");
  elements.originPinButton.setAttribute("aria-pressed", "true");
  elements.originPinButton.textContent = "Click the map…";
  elements.originHelp.textContent = "Click the exact starting point on the map. The typed address will be used as its label.";
  if (state.activeDesk === "hotels") {
    setHotelMobileView("visual");
    setHotelVisualization("map");
    await requestHotelMapLoad();
  } else {
    setAlbamonVisualization("map");
    setMobileView("map");
    await requestMapLoad();
  }
}

function openOriginConfirmation() {
  const candidate = state.originCandidate;
  if (!candidate) return;
  elements.originConfirmAddress.textContent = `${candidate.name} — ${candidate.address}`;
  elements.originConfirmCoordinates.textContent = `${Number(candidate.latitude).toFixed(6)}, ${Number(candidate.longitude).toFixed(6)}`;
  elements.originConfirmDialog.showModal();
}

async function confirmOriginChange() {
  const candidate = state.originCandidate;
  if (!candidate) return;
  elements.originConfirmButton.disabled = true;
  elements.originConfirmButton.textContent = "Recalculating…";
  try {
    const response = await fetch("/api/origin", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...candidate, recalculate: true }),
    });
    let result = await response.json();
    if (!response.ok) throw new Error(result.error || "Could not update the commute origin");
    result = await waitForOperation(result, (operation) => {
      elements.originConfirmButton.textContent = `${operation.message || "Recalculating"} · ${operation.progress || 0}%`;
    });
    elements.originConfirmDialog.close();
    closeOriginEditor();
    state.hotelPayload = null;
    await loadData();
    if (state.activeDesk === "hotels") await loadHotelData({ force: true });
    showToast(`Commutes recalculated from ${result.origin.name}.`);
  } catch (error) {
    showToast(error.message || "Could not update the commute origin.", true);
  } finally {
    elements.originConfirmButton.disabled = false;
    elements.originConfirmButton.textContent = "Confirm and recalculate";
  }
}

function validHotelDateRange() {
  const start = elements.hotelStartDate.value;
  const end = elements.hotelEndDate.value;
  if (!start || !end || start < localIsoDate() || end < start) return false;
  const days = Math.round((new Date(`${end}T12:00:00`) - new Date(`${start}T12:00:00`)) / 86400000) + 1;
  return days >= 1 && days <= 31;
}

async function runHotelDateSearch({ force = false } = {}) {
  if (!validHotelDateRange()) {
    showToast("Choose today or later, with an end date no more than 31 days after the start.", true);
    return;
  }
  state.hotelSearchCustom = true;
  persistHotelSearchPreferences();
  state.hotelPayload = null;
  state.selectedHotelId = null;
  elements.hotelDateSubmit.disabled = true;
  elements.hotelDateStatus.textContent = "Searching every public source and exact shift; longer ranges can take a few minutes because source requests are paced.";
  await loadHotelData({ force });
  elements.hotelDateSubmit.disabled = false;
}

async function resetHotelDateSearch() {
  state.hotelSearchCustom = false;
  removeStoredValue(HOTEL_SEARCH_STORAGE_KEY);
  const today = localIsoDate();
  elements.hotelStartDate.value = today;
  elements.hotelEndDate.value = today;
  state.hotelPayload = null;
  state.selectedHotelId = null;
  await loadHotelData({ force: false });
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
elements.summaryNewCard.addEventListener("click", () => showQueue("new"));
elements.summaryActionableCard.addEventListener("click", () => showQueue("actionable"));
elements.summaryAttentionCard.addEventListener("click", () => showQueue("review"));
for (const button of elements.queueFilterButtons) {
  button.addEventListener("click", () => showQueue(button.dataset.queueFilter));
}
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
elements.albamonMapVisual.addEventListener("click", () => setAlbamonVisualization("map"));
elements.albamonParetoVisual.addEventListener("click", () => setAlbamonVisualization("pareto"));
elements.albamonPayHourly.addEventListener("click", () => setPayMetric("hourly"));
elements.albamonPayTotal.addEventListener("click", () => setPayMetric("shift_total"));
elements.hotelPayHourly.addEventListener("click", () => setPayMetric("hourly"));
elements.hotelPayTotal.addEventListener("click", () => setPayMetric("shift_total"));
elements.albamonMarkerActionable.addEventListener("click", () => setAlbamonMarkerVisibility("actionable", !state.albamonMarkerVisibility.actionable));
elements.albamonMarkerOther.addEventListener("click", () => setAlbamonMarkerVisibility("other", !state.albamonMarkerVisibility.other));
elements.hotelMapVisual.addEventListener("click", () => setHotelVisualization("map"));
elements.hotelParetoVisual.addEventListener("click", () => setHotelVisualization("pareto"));
elements.hotelListTab.addEventListener("click", () => setHotelMobileView("list"));
elements.hotelVisualTab.addEventListener("click", () => setHotelMobileView("visual"));
elements.hotelGranularityHotel.addEventListener("click", () => setHotelGranularity("hotel"));
elements.hotelGranularityProvider.addEventListener("click", () => setHotelGranularity("provider"));
elements.hotelGroupOverlaps.addEventListener("click", () => setGroupOverlaps(!state.groupOverlaps));
elements.albamonGroupOverlaps.addEventListener("click", () => setGroupOverlaps(!state.groupOverlaps));
elements.albamonParetoChart.addEventListener("pointerover", (event) => {
  const point = paretoPointElement(event.target);
  if (!point || point.contains(event.relatedTarget)) return;
  showParetoTooltip(point, event);
});
elements.albamonParetoChart.addEventListener("pointermove", (event) => {
  const point = paretoPointElement(event.target);
  if (!point || elements.albamonParetoTooltip.dataset.jobId !== point.dataset.paretoJobId) return;
  positionParetoTooltip(point, event);
});
elements.albamonParetoChart.addEventListener("pointerout", (event) => {
  const point = paretoPointElement(event.target);
  if (!point || point.contains(event.relatedTarget)) return;
  hideParetoTooltip();
});
elements.albamonParetoChart.addEventListener("focusin", (event) => {
  const point = paretoPointElement(event.target);
  if (point) showParetoTooltip(point);
});
elements.albamonParetoChart.addEventListener("focusout", (event) => {
  const point = paretoPointElement(event.target);
  if (!point || point.contains(event.relatedTarget)) return;
  hideParetoTooltip();
});
elements.albamonParetoChart.addEventListener("click", (event) => {
  const point = paretoPointElement(event.target);
  if (!point) return;
  hideParetoTooltip();
  selectJob(Number(point.dataset.paretoJobId), point);
});
elements.hotelChart.addEventListener("pointerover", (event) => {
  const point = hotelPointElement(event.target);
  if (!point || point.contains(event.relatedTarget)) return;
  showHotelParetoTooltip(point, event);
});
elements.hotelChart.addEventListener("pointermove", (event) => {
  const point = hotelPointElement(event.target);
  if (!point || elements.hotelParetoTooltip.dataset.pointId !== point.dataset.hotelPointId) return;
  positionHotelParetoTooltip(point, event);
});
elements.hotelChart.addEventListener("pointerout", (event) => {
  const point = hotelPointElement(event.target);
  if (!point || point.contains(event.relatedTarget)) return;
  hideHotelParetoTooltip();
});
elements.hotelChart.addEventListener("focusin", (event) => {
  const point = hotelPointElement(event.target);
  if (point) showHotelParetoTooltip(point);
});
elements.hotelChart.addEventListener("focusout", (event) => {
  const point = hotelPointElement(event.target);
  if (!point || point.contains(event.relatedTarget)) return;
  hideHotelParetoTooltip();
});
elements.hotelRows.addEventListener("click", (event) => {
  const row = event.target instanceof Element
    ? event.target.closest("tr[data-hotel-id]")
    : null;
  if (row) selectHotel(row.dataset.hotelId, row);
});
elements.hotelRows.addEventListener("keydown", (event) => {
  const row = event.target instanceof Element
    ? event.target.closest("tr[data-hotel-id]")
    : null;
  if (event.target !== row || !["Enter", " "].includes(event.key)) return;
  event.preventDefault();
  selectHotel(row.dataset.hotelId, row);
});
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
  persistHotelFilters();
  renderHotelView();
});
elements.hotelPayWeekly.addEventListener("click", () => {
  state.hotelPayMode = "weekly_allowance";
  persistHotelFilters();
  renderHotelView();
});
elements.hotelDateForm.addEventListener("submit", (event) => {
  event.preventDefault();
  runHotelDateSearch({ force: false });
});
elements.hotelDateRefresh.addEventListener("click", () => runHotelDateSearch({ force: true }));
elements.hotelDateReset.addEventListener("click", resetHotelDateSearch);
elements.hotelOperationCancel.addEventListener("click", async () => {
  if (!state.hotelOperationId) return;
  elements.hotelOperationCancel.disabled = true;
  try {
    const response = await fetch(`/api/operations/${state.hotelOperationId}`, { method: "DELETE" });
    const operation = await response.json();
    if (!response.ok) throw new Error(operation.error || "Could not cancel search");
    updateHotelOperation(operation);
  } catch (error) {
    showToast(error.message || "Could not cancel search.", true);
  }
});
for (const input of [
  elements.hotelFilterStartDate,
  elements.hotelFilterEndDate,
  elements.hotelShiftStart,
  elements.hotelShiftEnd,
  elements.hotelCommuteFilter,
  elements.hotelMinPayFilter,
  elements.hotelRoleFilter,
  elements.hotelProviderFilter,
]) {
  input.addEventListener("change", () => {
    persistHotelFilters();
    if (state.hotelPayload) renderHotelView();
  });
}
elements.hotelSearchFilter.addEventListener("input", () => {
  window.clearTimeout(state.hotelFilterTimer);
  state.hotelFilterTimer = window.setTimeout(() => {
    persistHotelFilters();
    if (state.hotelPayload) renderHotelView();
  }, 100);
});
elements.hotelClearFilters.addEventListener("click", () => {
  setHotelFilterControls({ minimumPay: configuredHotelMinimumPay() });
  state.hotelPayMode = "actual";
  renderSavedHotelSearches();
  removeStoredValue(HOTEL_FILTER_STORAGE_KEY);
  persistHotelFilters();
  if (state.hotelPayload) renderHotelView();
});
elements.hotelSavedSearch.addEventListener("change", () => applySavedHotelSearch(elements.hotelSavedSearch.value));
elements.hotelSaveSearch.addEventListener("click", saveCurrentHotelSearch);
elements.hotelDeleteSearch.addEventListener("click", deleteSavedHotelSearch);
elements.hotelShareSearch.addEventListener("click", copyHotelSearchLink);
elements.originEditButton.addEventListener("click", () => {
  elements.originEditor.hidden = false;
  elements.originAddressInput.focus();
});
elements.originEditor.addEventListener("submit", (event) => {
  event.preventDefault();
  searchOriginAddress();
});
elements.originPinButton.addEventListener("click", () => {
  if (state.originPickMode) stopOriginPickMode();
  else startOriginPinMode();
});
elements.originCancelButton.addEventListener("click", closeOriginEditor);
elements.originUseButton.addEventListener("click", openOriginConfirmation);
elements.originConfirmButton.addEventListener("click", (event) => {
  event.preventDefault();
  confirmOriginChange();
});
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
elements.closePointChoice.addEventListener("click", () => elements.pointChoiceDialog.close());
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

restoreHotelSearchPreferences();
restoreVisualizationPreferences();
restoreHotelFilters();
syncPayMetricControls();
syncAlbamonMarkerToggles();
renderSavedHotelSearches();
loadData();
