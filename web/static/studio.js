if (location.protocol === "file:") {
  location.replace(`http://localhost:8000/web/index.html${location.search}${location.hash}`);
}
const R = 6378137;
const initialCorners = {
  nw: [42.614491711416974, -5.563585388952936],
  ne: [42.61448202489248, -5.563453034080995],
  sw: [42.61438408328242, -5.563612444921233],
  se: [42.61436524833972, -5.563474971352587]
};
const poiGeojson = {
  type: "FeatureCollection",
  features: [
    {id:"pastizal_1", properties:{landcover:"pastizal"}, geometry:{type:"Point", coordinates:[-6.207228,42.310665]}},
    {id:"pastizal_2", properties:{landcover:"pastizal"}, geometry:{type:"Point", coordinates:[-6.201653,42.30961]}},
    {id:"pastizal_3", properties:{landcover:"pastizal"}, geometry:{type:"Point", coordinates:[-6.20393,42.310527]}},
    {id:"arbustivo_1", properties:{landcover:"arbustivo"}, geometry:{type:"Point", coordinates:[-6.204549,42.31036]}},
    {id:"arbustivo_2", properties:{landcover:"arbustivo"}, geometry:{type:"Point", coordinates:[-6.206416,42.310902]}},
    {id:"matorral_1", properties:{landcover:"matorral"}, geometry:{type:"Point", coordinates:[-6.203042,42.310832]}},
    {id:"matorral_2", properties:{landcover:"matorral"}, geometry:{type:"Point", coordinates:[-6.205465,42.311749]}},
    {id:"water_1", properties:{landcover:"water"}, geometry:{type:"Point", coordinates:[-6.204025,42.309282]}},
    {id:"water_2", properties:{landcover:"water"}, geometry:{type:"Point", coordinates:[-6.204347,42.312561]}},
    {id:"village_east", properties:{landcover:"pastizal"}, geometry:{type:"Point", coordinates:[-6.200150,42.312900]}}
  ]
};
const landcoverCost = {road:1, track:1.5, pastizal:10, arbustivo:30, matorral:80, forest:80, scrub:45, water:1000};

let appMode = "coverage";
let fieldMode = "rect";
let startCorner = "nw";
let currentWaypoints = [];
let currentPlan = null;
let currentBounds = null;
let missionStops = ["water_2", "village_east"];
let customWaypointCounter = 1;
let drawerOpen = false;
let activeBaseLayer = null;
let isDrawingRect = false;
let rectDrawStart = null;
let rectDrawMoved = false;
let polyVertices = [];
let furrowBearingManual = false;
let rectEditSnapshot = null;
let generationToken = 0;
let routeDirty = true;
let pendingGenerateTimer = null;
let loadingStartedAt = 0;
let loadingTimer = null;
let loadingBaseDetail = "";
let generationStartedAtMs = 0;
let lastProcessingMs = 0;
let manualObstacles = [];
let manualObstacleDraft = [];
let manualObstacleDrawMode = false;
let manualObstacleEditSnapshot = null;
let publicSemanticZones = [];
let publicSemanticMetadata = {};
let publicSemanticCoverageBbox = null;
let publicSemanticWaypointSummary = {created: 0, autoAdded: 0, rejectedUnsafe: 0, rejectedDuplicate: 0};

// Keep GPS/satellite as the default view; users can switch to the map layer.
const modeBaseView = {coverage: "gps", mission: "gps"};

function initCenter() {
  const pts = Object.values(initialCorners);
  return [pts.reduce((s,p)=>s+p[0],0)/pts.length, pts.reduce((s,p)=>s+p[1],0)/pts.length];
}
const map = L.map("map", {zoomControl: false, doubleClickZoom: false}).setView(initCenter(), 20);
map.createPane("terrainSlopePane");
map.getPane("terrainSlopePane").style.zIndex = "350";
const terrainSlopeRenderer = L.canvas({pane: "terrainSlopePane", padding: 0.2});
map.attributionControl.setPosition("bottomleft");
L.control.zoom({position: "topleft"}).addTo(map);
const baseLayers = {
  gps: L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}", {
    maxZoom: 25,
    maxNativeZoom: 19,
    attribution: "© Esri World Imagery"
  }),
  map: L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 22,
    attribution: "© OpenStreetMap contributors"
  })
};
const fieldLayer = L.layerGroup().addTo(map);
const resultLayer = L.layerGroup().addTo(map);
const missionLayer = L.layerGroup().addTo(map);
const publicSemanticLayer = L.layerGroup().addTo(map);
const manualObstacleLayer = L.layerGroup().addTo(map);
const terrainCostLayer = L.layerGroup().addTo(map);
const markerLayer = L.layerGroup().addTo(map);

const rectMarkers = {};
let rectPolygon = null;
let polyLayer = null;

function $(id) { return document.getElementById(id); }
function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, character => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  })[character]);
}
function safeVal(id, fallback = "") {
  const el = $(id);
  return el && typeof el.value !== "undefined" ? el.value : fallback;
}
function safeNum(id, fallback = 0) {
  const value = Number(safeVal(id, fallback));
  return Number.isFinite(value) ? value : fallback;
}
function setStatus(message, type = "") {
  const el = $("status");
  el.className = "status" + (type ? " " + type : "");
  el.textContent = message;
}
function setRouteDetails(lines = [], title = "Route details") {
  const panel = $("routeDetails");
  const body = $("routeDetailsBody");
  if (!panel || !body) return;
  const clean = (Array.isArray(lines) ? lines : [lines]).map(v => String(v || "").trim()).filter(Boolean);
  panel.classList.toggle("hidden", clean.length === 0);
  panel.open = false;
  const summary = panel.querySelector("summary");
  if (summary) summary.textContent = title;
  body.textContent = clean.join("\n");
}
function clearRouteDetails() {
  setRouteDetails([]);
}
function updateLegend() {
  const legend = $("mapLegend");
  const body = $("legendBody");
  if (!legend || !body) return;
  const items = [];
  if (appMode === "coverage") {
    if (currentCoveragePolygon()) items.push(["poly", "Field boundary"]);
    if (currentWaypoints.length) {
      items.push(["coverage", "Crop rows"]);
      items.push(["headland", "Headland turns"]);
    }
  } else {
    const strategy = safeVal("routeStrategy", "direct");
    if (currentWaypoints.length) {
      const cls = strategy === "osm" ? "osm" : strategy === "cost" ? "cost" : "";
      const label = strategy === "osm" ? "OSM route" : strategy === "cost" ? "Cost-aware route" : "Direct route";
      items.push([cls, label]);
    }
    if (missionStops.length || poiGeojson.features.length) items.push(["poly", "Waypoints"]);
    const kinds = new Set(
      manualObstacles.concat(publicSemanticZones).map(zone => zone.kind || "building")
    );
    if (manualObstacleDraft.length) kinds.add(safeVal("manualObstacleKind", "building"));
    for (const kind of ["building", "water", "forest", "scrub"]) {
      if (kinds.has(kind)) items.push([`semantic-${kind}`, SEMANTIC_ZONE_STYLE[kind].label]);
    }
    if (terrainCostLayer.getLayers().length) {
      const preferredSlope = Math.max(0.1, safeNum("terrainPreferredSlope", 5));
      const maximumSlope = Math.max(preferredSlope, safeNum("terrainMaxSlope", 20));
      items.push(["terrain-low", `DEM slope 0–${(preferredSlope / 2).toFixed(1)}%`]);
      items.push(["terrain-moderate", `DEM slope ${(preferredSlope / 2).toFixed(1)}–${preferredSlope.toFixed(1)}%`]);
      items.push(["terrain-cost", `Costly slope ${preferredSlope.toFixed(1)}–${maximumSlope.toFixed(1)}%`]);
      items.push(["terrain-blocked", `Blocked slope > ${maximumSlope.toFixed(1)}%`]);
    }
  }
  legend.classList.toggle("hidden", items.length === 0);
  body.innerHTML = items.map(([cls, label]) => `<div class="leg"><span class="swatch ${cls}"></span>${label}</div>`).join("");
}
function nextPaint() {
  return new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
}
function setLoadingVisible(visible, detail = "") {
  const overlay = $("routeLoadingOverlay");
  if (!overlay) return;
  overlay.classList.toggle("hidden", !visible);
  overlay.setAttribute("aria-busy", visible ? "true" : "false");
  if (visible) {
    loadingStartedAt = loadingStartedAt || Date.now();
    loadingBaseDetail = detail || loadingBaseDetail || "Calculating…";
    updateLoadingDetail(loadingBaseDetail);
    if (!loadingTimer) {
      loadingTimer = setInterval(() => updateLoadingDetail(loadingBaseDetail), 500);
    }
  } else {
    loadingStartedAt = 0;
    loadingBaseDetail = "";
    if (loadingTimer) clearInterval(loadingTimer);
    loadingTimer = null;
  }
}
function formatProcessingTime(ms) {
  if (!Number.isFinite(ms) || ms < 0) return "—";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  return `${(ms / 1000).toFixed(ms < 10000 ? 2 : 1)} s`;
}
function setTimingState(type, text) {
  const el = $("timingState");
  if (!el) return;
  el.className = "timing-state" + (type ? " " + type : "");
  el.textContent = text;
}
function updateLoadingDetail(detail) {
  const elapsedMs = generationStartedAtMs ? performance.now() - generationStartedAtMs : 0;
  const el = $("loadingDetail");
  const elapsedText = elapsedMs ? ` · ${formatProcessingTime(elapsedMs)}` : "";
  const extra = elapsedMs >= 12000 ? " · waiting for routing service" : "";
  if (el) el.textContent = `${detail}${elapsedText}${extra}`;
  setTimingState("busy", `Processing: ${formatProcessingTime(elapsedMs)}${extra}`);
}
async function loadingStep(detail) {
  loadingBaseDetail = detail;
  setLoadingVisible(true, detail);
  setRunState("busy", "Generating route…", detail);
  setStatus(detail, "busy");
  await nextPaint();
}
async function fetchWithTimeout(url, options = {}, timeoutMs = 15000, label = "Request") {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, {...options, signal: controller.signal});
  } catch (err) {
    if (err && err.name === "AbortError") throw new Error(`${label} timed out (${Math.round(timeoutMs / 1000)}s). Another server or a split bbox will be tried when available.`);
    throw err;
  } finally {
    clearTimeout(timeout);
  }
}
function setRunState(type, title, detail = "") {
  const box = $("runState");
  if (box) {
    box.className = "run-state " + type;
    box.innerHTML = `<div><strong>${title}</strong><span>${detail}</span></div>`;
  }
  const badge = $("mapStateBadge");
  if (badge) {
    const showOnMap = type !== "idle" && type !== "pending";
    badge.className = "map-state-badge " + type + (showOnMap ? "" : " hidden");
    badge.textContent = detail ? `${title}: ${detail}` : title;
  }
}
function setDownloadsEnabled(enabled) {
  const row = $("downloadsRow");
  if (row) row.classList.toggle("stale", !enabled);
  for (const id of [
    "downloadCsv", "downloadYaml", "downloadArduPilot", "downloadPlan"
  ]) {
    const a = $(id);
    if (!a) continue;
    a.classList.toggle("disabled", !enabled);
    a.setAttribute("aria-disabled", enabled ? "false" : "true");
    if (!enabled) a.title = "The current route is not generated or is out of date.";
    else a.title = "";
  }
}
function setGenerateBusy(isBusy) {
  const btn = $("generateBtn");
  if (!btn) return;
  btn.disabled = isBusy;
  btn.classList.toggle("busy", isBusy);
  btn.textContent = isBusy ? "Generating…" : "Generate route";
}
function markDirty(reason = "Pending changes.", auto = false) {
  generationToken += 1;
  setGenerateBusy(false);
  setLoadingVisible(false);
  currentPlan = null;
  routeDirty = true;
  document.body.classList.add("route-stale");
  setDownloadsEnabled(false);
  const detail = `${reason} Press Generate.`;
  setRunState("pending", "Pending changes", detail);
  setStatus(detail, "pending");
  setTimingState("", "Processing time: —");
  clearRouteDetails();
  updateLegend();
}
function setInitialReadyState() {
  routeDirty = true;
  document.body.classList.add("route-stale");
  setDownloadsEnabled(false);
  setRunState("idle", "No route generated", "Set the field or mission and press Generate.");
  setStatus("Ready.");
  setTimingState("", "Processing time: —");
  clearRouteDetails();
  updateLegend();
}
function scheduleGenerate(reason = "Change detected.", delay = 650) {
  if (pendingGenerateTimer) clearTimeout(pendingGenerateTimer);
  pendingGenerateTimer = null;
  markDirty(reason, false);
}
function finishGenerationState(result, statusClass) {
  routeDirty = false;
  document.body.classList.remove("route-stale");
  setDownloadsEnabled(currentWaypoints.length > 0);
  const d = result.distance_m || distanceRoute(result.waypoints || []);
  const elapsed = lastProcessingMs || (generationStartedAtMs ? performance.now() - generationStartedAtMs : 0);
  const timeText = formatProcessingTime(elapsed);
  const details = `${(result.waypoints || []).length} points · ${formatDistance(d)} · processed in ${timeText} · ${new Date().toLocaleTimeString()}`;
  setRunState(statusClass, statusClass === "warn" ? "Generated with warnings" : "Generated", details);
  setTimingState(statusClass === "err" ? "err" : "ok", `Processing time: ${timeText}`);
}
function failGenerationState(message) {
  routeDirty = true;
  document.body.classList.add("route-stale");
  setDownloadsEnabled(false);
  const elapsed = generationStartedAtMs ? performance.now() - generationStartedAtMs : lastProcessingMs;
  const suffix = elapsed ? ` · after ${formatProcessingTime(elapsed)}` : "";
  setRunState("err", "Route could not be generated", `Check route details.${suffix}`);
  setStatus(`Route could not be generated. ${message}`, "err");
  currentPlan = null;
  setRouteDetails([message], "Error details");
  $("routeDetails").open = true;
  if (elapsed) setTimingState("err", `Time to error: ${formatProcessingTime(elapsed)}`);
  updateLegend();
}
function llToXY(lat, lon, origin) {
  const scale = Math.cos(origin[0] * Math.PI / 180);
  return [(lon - origin[1]) * Math.PI / 180 * R * scale, (lat - origin[0]) * Math.PI / 180 * R];
}
function xyToLL(x, y, origin) {
  const scale = Math.cos(origin[0] * Math.PI / 180);
  return [origin[0] + y / R * 180 / Math.PI, origin[1] + x / (R * scale) * 180 / Math.PI];
}
function normAngle(a) { return Math.atan2(Math.sin(a), Math.cos(a)); }
function yawBetween(a, b) {
  const xy = llToXY(b[0], b[1], a);
  return normAngle(Math.atan2(xy[1], xy[0]));
}
function yawToQuaternion(yaw) {
  const half = yaw / 2;
  return {qx: 0, qy: 0, qz: Math.sin(half), qw: Math.cos(half)};
}
function toWaypoints(points) {
  const cleaned = [];
  for (const p of points) {
    const last = cleaned[cleaned.length - 1];
    if (!last || Math.abs(p[0] - last[0]) > 1e-11 || Math.abs(p[1] - last[1]) > 1e-11) cleaned.push(p);
  }
  return cleaned.map((p, i) => ({
    latitude: p[0],
    longitude: p[1],
    yaw: i < cleaned.length - 1 ? yawBetween(p, cleaned[i + 1]) : (i ? yawBetween(cleaned[i - 1], p) : 0)
  }));
}
function distanceLatLng(a, b) {
  const xy = llToXY(b[0], b[1], a);
  return Math.hypot(xy[0], xy[1]);
}
function distancePoints(points) {
  let total = 0;
  for (let i = 0; i < points.length - 1; i++) total += distanceLatLng(points[i], points[i + 1]);
  return total;
}
function distanceRoute(wps) {
  return distancePoints(wps.map(w => [w.latitude, w.longitude]));
}
function appendRoutePoints(route, points) {
  points.forEach(point => {
    const last = route[route.length - 1];
    if (!last || Math.abs(point[0] - last[0]) > 1e-11 || Math.abs(point[1] - last[1]) > 1e-11) route.push(point);
  });
}
function bearingToUnit(bearingDeg) {
  const rad = Number(bearingDeg) * Math.PI / 180;
  return [Math.sin(rad), Math.cos(rad)];
}
function normalizeAngle180(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return 0;
  return ((n % 180) + 180) % 180;
}
function vectorAzimuthDeg(v) {
  return (Math.atan2(v[0], v[1]) * 180 / Math.PI + 360) % 180;
}
function polygonAreaLatLng(latlngs) {
  let area = 0;
  for (let i = 0; i < latlngs.length; i++) {
    const p1 = Array.isArray(latlngs[i]) ? L.latLng(latlngs[i][0], latlngs[i][1]) : latlngs[i];
    const p2raw = latlngs[(i + 1) % latlngs.length];
    const p2 = Array.isArray(p2raw) ? L.latLng(p2raw[0], p2raw[1]) : p2raw;
    area += (p2.lng - p1.lng) * Math.PI / 180 * (2 + Math.sin(p1.lat * Math.PI / 180) + Math.sin(p2.lat * Math.PI / 180));
  }
  return Math.abs(area * R * R / 2.0);
}
function formatDistance(m) {
  return m >= 1000 ? `${(m/1000).toFixed(2)} km` : `${m.toFixed(1)} m`;
}
function getSelectedBearing() {
  if (!furrowBearingManual) return null;
  return normalizeAngle180(safeNum("furrowBearingNumber", safeNum("furrowBearing", 0)));
}
function setAngleControls(rangeId, numberId, labelId, value, suffix = "°") {
  const angle = normalizeAngle180(value);
  const range = $(rangeId);
  const number = $(numberId);
  if (range) range.value = String(Math.round(angle));
  if (number) number.value = String(Math.round(angle));
  if (labelId && $(labelId)) $(labelId).textContent = `${Math.round(angle)}${suffix}`;
  return angle;
}
function currentCoveragePolygon() {
  if (fieldMode === "poly") return polyVertices.length >= 3 ? polyVertices.slice() : null;
  try {
    const c = getCorners();
    return [c.nw, c.ne, c.se, c.sw];
  } catch (_) {
    return null;
  }
}
function polygonOrigin(poly) {
  return [poly.reduce((s,p)=>s+p[0],0)/poly.length, poly.reduce((s,p)=>s+p[1],0)/poly.length];
}
function getEffectiveFurrowBearing(poly, origin) {
  const manual = getSelectedBearing();
  return manual === null ? longestEdgeBearing(poly, origin) : manual;
}
function syncFurrowControls() {
  const poly = currentCoveragePolygon();
  let shown = safeNum("furrowBearing", 0);
  if (poly && poly.length >= 3) {
    shown = getEffectiveFurrowBearing(poly, polygonOrigin(poly));
  }
  setAngleControls("furrowBearing", "furrowBearingNumber", null, shown);
  const label = $("furrowBearingLbl");
  if (label) label.textContent = furrowBearingManual ? `${Math.round(normalizeAngle180(shown))}°` : `Auto ${Math.round(normalizeAngle180(shown))}°`;
  const autoBtn = $("furrowAutoBtn");
  if (autoBtn) autoBtn.classList.toggle("active", !furrowBearingManual);
  document.querySelectorAll("[data-furrow-bearing]").forEach(btn => {
    const b = normalizeAngle180(btn.dataset.furrowBearing);
    btn.classList.toggle("active", furrowBearingManual && Math.round(b) === Math.round(normalizeAngle180(shown)));
  });
}
function setFurrowBearing(value, manual = true, reason = "Furrow rotation changed.") {
  furrowBearingManual = manual;
  setAngleControls("furrowBearing", "furrowBearingNumber", null, value);
  syncFurrowControls();
  if (appMode === "coverage") scheduleGenerate(reason);
}

function setBaseView(next, remember = true) {
  if (!baseLayers[next]) next = "gps";
  if (activeBaseLayer) map.removeLayer(activeBaseLayer);
  activeBaseLayer = baseLayers[next].addTo(map);
  if (remember) modeBaseView[appMode] = next;
  $("gpsViewBtn").classList.toggle("active", next === "gps");
  $("mapViewBtn").classList.toggle("active", next === "map");
  const modeName = appMode === "coverage" ? "Coverage planning" : "Mission route";
  $("modePill").textContent = `${modeName} · ${next === "gps" ? "GPS" : "Map"}`;
}
function syncBaseView() {
  setBaseView(modeBaseView[appMode], false);
}

function getCorner(k) {
  const lat = Number($(k + "_lat").value);
  const lon = Number($(k + "_lon").value);
  return Number.isFinite(lat) && Number.isFinite(lon) ? [lat, lon] : null;
}
function getCorners() {
  const out = {};
  for (const k of ["nw", "ne", "sw", "se"]) {
    const p = getCorner(k);
  if (!p) throw new Error(`Invalid coordinates in ${k.toUpperCase()}`);
    out[k] = p;
  }
  return out;
}
function setCorner(k, lat, lon) {
  $(k + "_lat").value = Number(lat).toFixed(12);
  $(k + "_lon").value = Number(lon).toFixed(12);
}
function setCornerInputsFromBounds(bounds) {
  setCorner("nw", bounds.n, bounds.w);
  setCorner("ne", bounds.n, bounds.e);
  setCorner("sw", bounds.s, bounds.w);
  setCorner("se", bounds.s, bounds.e);
}
function metersPerDegLon(lat) {
  return Math.max(1e-6, 111320 * Math.cos(lat * Math.PI / 180));
}
function rectMetricsFromCorners() {
  const c = getCorners();
  const centerLat = (c.nw[0] + c.ne[0] + c.sw[0] + c.se[0]) / 4;
  const centerLon = (c.nw[1] + c.ne[1] + c.sw[1] + c.se[1]) / 4;
  const origin = [centerLat, centerLon];
  const xy = Object.fromEntries(Object.entries(c).map(([key, p]) => [key, llToXY(p[0], p[1], origin)]));
  const width = Math.max(0.1, (Math.hypot(xy.ne[0] - xy.nw[0], xy.ne[1] - xy.nw[1]) + Math.hypot(xy.se[0] - xy.sw[0], xy.se[1] - xy.sw[1])) / 2);
  const height = Math.max(0.1, (Math.hypot(xy.nw[0] - xy.sw[0], xy.nw[1] - xy.sw[1]) + Math.hypot(xy.ne[0] - xy.se[0], xy.ne[1] - xy.se[1])) / 2);
  const northMid = [(xy.nw[0] + xy.ne[0]) / 2, (xy.nw[1] + xy.ne[1]) / 2];
  const southMid = [(xy.sw[0] + xy.se[0]) / 2, (xy.sw[1] + xy.se[1]) / 2];
  const axis = [northMid[0] - southMid[0], northMid[1] - southMid[1]];
  const bearing = Math.hypot(axis[0], axis[1]) > 1e-6 ? vectorAzimuthDeg(axis) : 0;
  return {centerLat, centerLon, width, height, bearing};
}
function rectCornersFromMetrics(metrics) {
  const center = [metrics.centerLat, metrics.centerLon];
  const longUnit = bearingToUnit(normalizeAngle180(metrics.bearing));
  const widthUnit = [longUnit[1], -longUnit[0]];
  const halfW = Math.max(0.1, Number(metrics.width)) / 2;
  const halfH = Math.max(0.1, Number(metrics.height)) / 2;
  const make = (w, h) => xyToLL(widthUnit[0] * w + longUnit[0] * h, widthUnit[1] * w + longUnit[1] * h, center);
  return {
    nw: make(-halfW, halfH),
    ne: make(halfW, halfH),
    sw: make(-halfW, -halfH),
    se: make(halfW, -halfH)
  };
}
function setCornerInputsFromOrientedRect(metrics) {
  const c = rectCornersFromMetrics(metrics);
  setCorner("nw", c.nw[0], c.nw[1]);
  setCorner("ne", c.ne[0], c.ne[1]);
  setCorner("sw", c.sw[0], c.sw[1]);
  setCorner("se", c.se[0], c.se[1]);
}
function setRectSlider(id, value, label) {
  const sl = $(id);
  if (value < Number(sl.min)) sl.min = Math.floor(value - Math.max(5, Math.abs(value) * .2));
  if (value > Number(sl.max)) sl.max = Math.ceil(value + Math.max(5, Math.abs(value) * .2));
  sl.value = value;
  $(label).textContent = value.toFixed(1) + " m";
}
function setRectBearingControl(value) {
  setAngleControls("rectBearing", "rectBearingNumber", "rectBearingLbl", value);
}
function syncRectSlidersFromInputs(resetCenter = true) {
  let m;
  try { m = rectMetricsFromCorners(); } catch (_) { return; }
  if (resetCenter) {
    setRectSlider("rectCenterNS", 0, "rectCenterNSLbl");
    setRectSlider("rectCenterEW", 0, "rectCenterEWLbl");
    $("rectCenterNS").dataset.base = m.centerLat;
    $("rectCenterEW").dataset.base = m.centerLon;
  }
  setRectSlider("rectHeight", m.height, "rectHeightLbl");
  setRectSlider("rectWidth", m.width, "rectWidthLbl");
  setRectBearingControl(m.bearing);
}
function applyRectFromSliders() {
  let m;
  try { m = rectMetricsFromCorners(); } catch (_) { return; }
  const baseLat = Number($("rectCenterNS").dataset.base || m.centerLat);
  const baseLon = Number($("rectCenterEW").dataset.base || m.centerLon);
  const centerLat = baseLat + Number($("rectCenterNS").value || 0) / 111320;
  const centerLon = baseLon + Number($("rectCenterEW").value || 0) / metersPerDegLon(baseLat);
  setCornerInputsFromOrientedRect({
    centerLat,
    centerLon,
    width: Number($("rectWidth").value),
    height: Number($("rectHeight").value),
    bearing: safeNum("rectBearingNumber", safeNum("rectBearing", m.bearing))
  });
  updateRectFromInputs(false);
  updateLiveStats();
  syncFurrowControls();
  if (appMode === "coverage") scheduleGenerate("Rectangle adjusted.", 500);
}
function markerIcon(key) {
  const isStart = key === startCorner;
  const color = isStart ? "#b65d13" : "#3a7d44";
  return L.divIcon({
    className: "",
    html: `<div style="width:18px;height:18px;border-radius:50%;background:${color};border:3px solid #fff;box-shadow:0 2px 8px rgba(0,0,0,.35)"></div>`,
    iconSize: [18,18], iconAnchor: [9,9]
  });
}
function createRectMarkers() {
  for (const k of ["nw", "ne", "sw", "se"]) {
    const p = getCorner(k);
    rectMarkers[k] = L.marker(p, {draggable: true, icon: markerIcon(k)}).addTo(markerLayer);
    rectMarkers[k].bindTooltip(k.toUpperCase() + (k === startCorner ? " (start)" : ""), {direction:"right", permanent:false});
    rectMarkers[k].on("drag", e => {
      setCorner(k, e.latlng.lat, e.latlng.lng);
      updateRectFromInputs(false);
      updateLiveStats();
    });
    rectMarkers[k].on("dragend", () => { syncRectSlidersFromInputs(true); scheduleGenerate("Rectangle corner moved."); });
  }
}
function updateRectFromInputs(resetSliders = false) {
  let c;
  try { c = getCorners(); } catch (e) { return; }
  markerLayer.clearLayers();
  for (const k of ["nw", "ne", "sw", "se"]) rectMarkers[k] = null;
  createRectMarkers();
  fieldLayer.clearLayers();
  rectPolygon = L.polygon([c.nw, c.ne, c.se, c.sw], {color:"#f2c94c", weight:2, fillColor:"#f2c94c", fillOpacity:.12}).addTo(fieldLayer);
  currentBounds = L.latLngBounds([c.nw, c.ne, c.se, c.sw]);
  if (resetSliders) syncRectSlidersFromInputs(true);
  syncFurrowControls();
  updateLegend();
}
function applyRectBounds(bounds) {
  setCornerInputsFromBounds(bounds);
  updateRectFromInputs(true);
  updateLiveStats();
}
function snapshotRectCorners() {
  try {
    const c = getCorners();
    return JSON.parse(JSON.stringify(c));
  } catch (_) {
    return null;
  }
}
function beginRectEdit() {
  if (!rectEditSnapshot) rectEditSnapshot = snapshotRectCorners();
}
function restoreRectSnapshot(snapshot) {
  if (!snapshot) return;
  for (const key of ["nw", "ne", "sw", "se"]) setCorner(key, snapshot[key][0], snapshot[key][1]);
  updateRectFromInputs(true);
  updateLiveStats();
  syncFurrowControls();
}
function applyRectMetrics(metrics, reason = "Rectangle alignment changed.") {
  beginRectEdit();
  setCornerInputsFromOrientedRect(metrics);
  updateRectFromInputs(true);
  updateLiveStats();
  syncFurrowControls();
  if (appMode === "coverage") scheduleGenerate(reason, 500);
}
function squareCurrentRectangle(reason = "Rectangle squared.") {
  let m;
  try { m = rectMetricsFromCorners(); } catch (_) { return; }
  applyRectMetrics(m, reason);
}
function orientCurrentRectangle(bearing, reason) {
  let m;
  try { m = rectMetricsFromCorners(); } catch (_) { return; }
  applyRectMetrics({...m, bearing: normalizeAngle180(bearing)}, reason);
}
function perfectRect() {
  const panel = $("rectToolsPanel");
  if (panel) panel.open = true;
  squareCurrentRectangle("Rectangle squared.");
}
function applyRectToolChanges() {
  rectEditSnapshot = null;
  syncRectSlidersFromInputs(true);
  setStatus("Rectangle settings applied.", "ok");
  if (appMode === "coverage") scheduleGenerate("Rectangle settings applied.", 500);
}
function cancelRectToolChanges() {
  if (rectEditSnapshot) restoreRectSnapshot(rectEditSnapshot);
  rectEditSnapshot = null;
  setStatus("Rectangle settings cancelled.", "warn");
  if (appMode === "coverage") scheduleGenerate("Rectangle settings cancelled.", 500);
}
function setStartCorner(corner) {
  startCorner = corner;
  ["nw","ne","sw","se"].forEach(k => {
    $("sc-" + k).classList.toggle("active", k === corner);
    $("card-" + k).classList.toggle("active-corner", k === corner);
  });
  updateRectFromInputs(false);
  if (appMode === "coverage") scheduleGenerate(`Start corner changed to ${corner.toUpperCase()}.`);
}
function boundsFromLatLngs(a, b) {
  return {n: Math.max(a.lat, b.lat), s: Math.min(a.lat, b.lat), e: Math.max(a.lng, b.lng), w: Math.min(a.lng, b.lng)};
}
function startRectDraw() {
  if (fieldMode !== "rect") return;
  if (isDrawingRect) return cancelRectDraw();
  isDrawingRect = true;
  rectDrawStart = null;
  rectDrawMoved = false;
  map.dragging.disable();
  map.getContainer().style.cursor = "crosshair";
  $("drawRectBtn").textContent = "Cancel drawing";
  $("drawRectBtn").classList.add("active");
  setStatus("Drawing mode enabled. Click and drag on the map to draw the field.", "info");
  updateLegend();
  map.on("mousedown", onRectDrawStart);
}
function cancelRectDraw(message = true) {
  isDrawingRect = false;
  map.dragging.enable();
  map.getContainer().style.cursor = "";
  $("drawRectBtn").textContent = "Draw rectangle";
  $("drawRectBtn").classList.remove("active");
  map.off("mousedown", onRectDrawStart);
  document.removeEventListener("mousemove", onRectDrawMove);
  document.removeEventListener("mouseup", onRectDrawEnd);
  rectDrawStart = null;
  if (message) setStatus("Drawing cancelled.", "info");
  updateLegend();
}
function eventLatLng(e) {
  const rect = map.getContainer().getBoundingClientRect();
  return map.containerPointToLatLng(L.point(e.clientX - rect.left, e.clientY - rect.top));
}
function onRectDrawStart(e) {
  if (!isDrawingRect) return;
  L.DomEvent.preventDefault(e.originalEvent);
  rectDrawStart = e.latlng;
  rectDrawMoved = false;
  document.addEventListener("mousemove", onRectDrawMove);
  document.addEventListener("mouseup", onRectDrawEnd);
}
function onRectDrawMove(e) {
  if (!isDrawingRect || !rectDrawStart) return;
  const current = eventLatLng(e);
  const bounds = boundsFromLatLngs(rectDrawStart, current);
  if (Math.abs(bounds.n - bounds.s) < 1e-12 || Math.abs(bounds.e - bounds.w) < 1e-12) return;
  rectDrawMoved = true;
  applyRectBounds(bounds);
}
function onRectDrawEnd(e) {
  if (!isDrawingRect || !rectDrawStart) return;
  const current = eventLatLng(e);
  const bounds = boundsFromLatLngs(rectDrawStart, current);
  cancelRectDraw(false);
  if (!rectDrawMoved || Math.abs(bounds.n - bounds.s) < 1e-12 || Math.abs(bounds.e - bounds.w) < 1e-12) {
    setStatus("Invalid rectangle. Drag to define an area.", "err");
    return;
  }
  applyRectBounds(bounds);
  scheduleGenerate("Rectangle drawn.");
}

function switchFieldMode(mode) {
  fieldMode = mode;
  if (isDrawingRect && mode !== "rect") cancelRectDraw(false);
  $("fieldRectTab").classList.toggle("active", mode === "rect");
  $("fieldPolyTab").classList.toggle("active", mode === "poly");
  $("rectFields").classList.toggle("hidden", mode !== "rect");
  $("polyFields").classList.toggle("hidden", mode !== "poly");
  $("rectHint").classList.toggle("hidden", mode !== "rect");
  $("polyHint").classList.toggle("hidden", mode !== "poly");
  markerLayer.clearLayers();
  fieldLayer.clearLayers();
  if (mode === "rect") {
    map.off("click", onPolyClick);
    map.off("dblclick", onPolyDblClick);
    updateRectFromInputs(false);
    startRectDraw();
  } else {
    if (isDrawingRect) cancelRectDraw(false);
    drawPolyDraft();
    map.on("click", onPolyClick);
    map.on("dblclick", onPolyDblClick);
  }
  syncFurrowControls();
  updateLiveStats();
  if (appMode === "coverage") {
    if (mode === "rect" || polyVertices.length >= 3) scheduleGenerate("Field mode changed.");
    else markDirty("Polygon mode active: add at least 3 vertices.", false);
  }
}
function onPolyClick(e) {
  if (appMode !== "coverage" || fieldMode !== "poly") return;
  polyVertices.push([e.latlng.lat, e.latlng.lng]);
  drawPolyDraft();
  updateLiveStats();
  if (polyVertices.length >= 3) scheduleGenerate("Polygon vertex added.", 500);
  else markDirty(`Polygon has ${polyVertices.length} vertex/vertices: ${3 - polyVertices.length} more needed.`, false);
}
function onPolyDblClick(e) {
  if (appMode !== "coverage" || fieldMode !== "poly") return;
  e.originalEvent.preventDefault();
  closePoly();
}
function drawPolyDraft(closed = false) {
  markerLayer.clearLayers();
  fieldLayer.clearLayers();
  polyVertices.forEach((v, i) => {
    const icon = L.divIcon({
      className: "",
      html: `<div style="width:18px;height:18px;border-radius:50%;background:#087f7a;border:3px solid #fff;box-shadow:0 2px 8px rgba(0,0,0,.35);color:#fff;font-size:10px;font-weight:900;display:flex;align-items:center;justify-content:center">${i+1}</div>`,
      iconSize: [18,18], iconAnchor: [9,9]
    });
    L.marker(v, {icon, draggable: true}).addTo(markerLayer).on("drag", ev => {
      polyVertices[i] = [ev.latlng.lat, ev.latlng.lng];
      drawPolyDraft(closed);
      updateLiveStats();
      if (polyVertices.length >= 3) scheduleGenerate("Polygon vertex moved.", 500);
      else markDirty("Polygon changed: more vertices are needed.", false);
    });
  });
  if (polyVertices.length >= 2) {
    polyLayer = closed && polyVertices.length >= 3
      ? L.polygon(polyVertices, {color:"#f2c94c", weight:2, fillColor:"#f2c94c", fillOpacity:.12}).addTo(fieldLayer)
      : L.polyline(polyVertices, {color:"#087f7a", weight:2, dashArray:"5 4"}).addTo(fieldLayer);
  }
  if (polyVertices.length) currentBounds = L.latLngBounds(polyVertices);
  $("polyVList").innerHTML = polyVertices.length
    ? polyVertices.map((v,i) => `<div class="vrow"><span class="vrow-num">${i+1}</span><span>${v[0].toFixed(7)}, ${v[1].toFixed(7)}</span></div>`).join("")
    : '<span style="color:var(--muted)">No vertices yet…</span>';
}
function clearPoly() {
  polyVertices = [];
  drawPolyDraft(false);
  updateLiveStats();
  markDirty("Polygon cleared.", false);
}
function closePoly() {
  if (polyVertices.length < 3) {
    setStatus("Add at least 3 vertices to close the polygon.", "err");
    return;
  }
  drawPolyDraft(true);
  updateLiveStats();
  scheduleGenerate(`Polygon closed with ${polyVertices.length} vertices.`);
}

function longestEdgeBearing(poly, origin) {
  let best = {d: -1, bearing: 0};
  for (let i = 0; i < poly.length; i++) {
    const a = poly[i];
    const b = poly[(i + 1) % poly.length];
    const av = llToXY(a[0], a[1], origin);
    const bv = llToXY(b[0], b[1], origin);
    const dx = bv[0] - av[0], dy = bv[1] - av[1];
    const d = Math.hypot(dx, dy);
    if (d > best.d) best = {d, bearing: (Math.atan2(dx, dy) * 180 / Math.PI + 360) % 180};
  }
  return best.bearing;
}
function interpolateRoute(points, interval) {
  if (points.length < 2) return points;
  const origin = [points.reduce((s,p)=>s+p[0],0)/points.length, points.reduce((s,p)=>s+p[1],0)/points.length];
  const xy = points.map(p => llToXY(p[0], p[1], origin));
  const route = [];
  for (let i = 0; i < xy.length - 1; i++) {
    const a = xy[i], b = xy[i + 1];
    const d = Math.hypot(b[0] - a[0], b[1] - a[1]);
    const steps = Math.max(1, Math.ceil(d / interval));
    for (let j = i === 0 ? 0 : 1; j <= steps; j++) route.push(xyToLL(a[0] + (b[0]-a[0])*j/steps, a[1] + (b[1]-a[1])*j/steps, origin));
  }
  return route;
}
function featureById(id) {
  const f = poiGeojson.features.find(x => x.id === id);
  if (!f) throw new Error(`Unknown POI: ${id}`);
  return f;
}
function poiById(id) {
  const f = featureById(id);
  return [f.geometry.coordinates[1], f.geometry.coordinates[0]];
}
function poiLabel(id) {
  const f = featureById(id);
  return {id: f.properties.name || f.id, landcover: f.properties.landcover || "unknown"};
}
function uniqueWaypointId(rawName) {
  const fallback = `waypoint_${customWaypointCounter}`;
  const slug = (rawName || fallback).trim().toLowerCase().normalize("NFD").replace(/[\u0300-\u036f]/g, "").replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
  const base = slug || fallback;
  customWaypointCounter += 1;
  let candidate = base;
  let suffix = 2;
  while (poiGeojson.features.some(feature => feature.id === candidate)) {
    candidate = `${base}_${suffix}`;
    suffix += 1;
  }
  return candidate;
}
function addCustomWaypoint(name, landcover, lat, lon) {
  const id = uniqueWaypointId(name);
  poiGeojson.features.push({id, type:"Feature", properties:{landcover, custom:true}, geometry:{type:"Point", coordinates:[lon, lat]}});
  return id;
}
function osmSegmentUrl(start, end, profile, alternatives = false) {
  const coords = `${start[1]},${start[0]};${end[1]},${end[0]}`;
  const altParam = alternatives ? "&alternatives=true" : "&alternatives=false";
  return `https://router.project-osrm.org/route/v1/${profile}/${coords}?overview=full&geometries=geojson&steps=false${altParam}`;
}
function endpointBridgeExact(fromPoint, toPoint, interval, endpointMode, buildings, bufferM, label, warnings, unsafeFallback = true) {
  if (!toPoint) return [fromPoint];
  const d = distanceLatLng(fromPoint, toPoint);
  if (d < 0.25) return [fromPoint, toPoint];
  if (endpointMode === "exact_avoid") {
    if (!buildings || !buildings.length) {
      const msg = `Exact connection ${label}: no manual forbidden zones are available to validate the exact bridge.`;
      if (!unsafeFallback) {
        throw new Error(`${msg} In "Block generation" mode, draw a manual forbidden zone around the obstacle and generate again.`);
      }
      warnings.push(`${msg} A direct bridge is used only as a visual fallback.`);
      return interpolateRoute([fromPoint, toPoint], interval);
    }
    try {
      const directHits = routeBuildingHits([fromPoint, toPoint], buildings, bufferM);
      if (!directHits.length) return interpolateRoute([fromPoint, toPoint], interval);
      const safe = localAstarAroundBuildings(fromPoint, toPoint, buildings, bufferM, Math.max(1.5, interval / 2));
      warnings.push(`Exact connection ${label}: local A* is used around a forbidden zone to reach the exact point.`);
      return safe;
    } catch (err) {
      const msg = `Exact connection ${label}: local A* could not avoid the obstacle (${err.message || err}).`;
      if (!unsafeFallback) throw new Error(`${msg} In "Block generation" mode, generation is cancelled to avoid crossing obstacles.`);
      warnings.push(`${msg} A direct bridge is used only as a visual fallback.`);
      return interpolateRoute([fromPoint, toPoint], interval);
    }
  }
  return interpolateRoute([fromPoint, toPoint], interval);
}
function connectSelectedRouteToTargets(start, roadPoints, end, interval, endpointMode, buildings, bufferM, label, warnings, unsafeFallback = true) {
  if (!roadPoints.length) return interpolateRoute([start, end], interval);
  if (endpointMode === "snap") return roadPoints;
  const safeMode = endpointMode === "exact_avoid";
  const directMode = endpointMode === "exact_direct" || endpointMode === "exact";
  if (!safeMode && !directMode) return roadPoints;
  const segment = [];
  appendRoutePoints(segment, endpointBridgeExact(start, roadPoints[0], interval, endpointMode, buildings, bufferM, `${label} start`, warnings, unsafeFallback));
  appendRoutePoints(segment, roadPoints);
  appendRoutePoints(segment, endpointBridgeExact(roadPoints[roadPoints.length - 1], end, interval, endpointMode, buildings, bufferM, `${label} end`, warnings, unsafeFallback));
  return segment;
}
function routeBBox(points) {
  const lats = points.map(p => p[0]);
  const lons = points.map(p => p[1]);
  return {south: Math.min(...lats), west: Math.min(...lons), north: Math.max(...lats), east: Math.max(...lons)};
}
function expandBBoxMeters(bbox, meters) {
  const latPad = meters / 111320;
  const midLat = (bbox.south + bbox.north) / 2;
  const lonPad = meters / (111320 * Math.max(0.2, Math.cos(midLat * Math.PI / 180)));
  return {south: bbox.south - latPad, west: bbox.west - lonPad, north: bbox.north + latPad, east: bbox.east + lonPad};
}
function bboxOverlap(a, b) {
  return !(a.east < b.west || a.west > b.east || a.north < b.south || a.south > b.north);
}

const MANUAL_OBSTACLE_STORAGE_KEY = "geozigzag_manual_obstacles_v1";
const SEMANTIC_ZONE_STYLE = {
  building: {label: "Building", color: "#b65d13", cost: Infinity},
  water: {label: "Water", color: "#2f80ed", cost: Infinity},
  forest: {label: "Forest", color: "#3a7d44", cost: 80},
  scrub: {label: "Scrub", color: "#8a6d3b", cost: 45}
};

function manualObstacleToBuilding(obstacle) {
  const ring = obstacle.ring || [];
  const kind = SEMANTIC_ZONE_STYLE[obstacle.kind] ? obstacle.kind : "building";
  return {id: obstacle.id || `manual_${Date.now()}`, manual: true, kind, ring, bbox: obstacle.bbox || routeBBox(ring)};
}
function normalizeManualObstacles(items) {
  return (items || [])
    .filter(o => o && Array.isArray(o.ring) && o.ring.length >= 4)
    .map(o => manualObstacleToBuilding(o));
}
function loadManualObstacles() {
  try { manualObstacles = normalizeManualObstacles(JSON.parse(localStorage.getItem(MANUAL_OBSTACLE_STORAGE_KEY) || "[]")); }
  catch (_) { manualObstacles = []; }
  drawManualObstacles();
  updateManualObstacleStatus();
}
function saveManualObstacles() {
  try { localStorage.setItem(MANUAL_OBSTACLE_STORAGE_KEY, JSON.stringify(manualObstacles)); } catch (_) {}
}
function updateManualObstacleStatus() {
  const el = $("manualObstacleStatus");
  if (el) {
    const count = manualObstacles.length;
    const action = manualObstacleEditSnapshot ? "editing" : "drawing";
    el.textContent = `${count} semantic zone${count === 1 ? "" : "s"}` + (manualObstacleDrawMode ? ` · ${action} ${manualObstacleDraft.length}` : "");
  }
  const drawBtn = $("drawManualObstacleBtn");
  const finishBtn = $("finishManualObstacleBtn");
  const cancelBtn = $("cancelManualObstacleBtn");
  if (drawBtn) drawBtn.classList.toggle("active", manualObstacleDrawMode);
  if (finishBtn) finishBtn.disabled = !manualObstacleDrawMode;
  if (cancelBtn) cancelBtn.disabled = !manualObstacleDrawMode;
  const list = $("manualObstacleList");
  if (list) {
    list.innerHTML = manualObstacles.length
      ? manualObstacles.map((zone, i) => {
          const style = SEMANTIC_ZONE_STYLE[zone.kind] || SEMANTIC_ZONE_STYLE.building;
          return `<div class="zone-row"><span><strong>${style.label} ${i + 1}</strong><br>${Math.max(0, (zone.ring || []).length - 1)} points</span><span class="zone-actions"><button class="secondary" type="button" data-zone-edit="${i}">Edit</button><button class="secondary danger" type="button" data-zone-delete="${i}">Delete</button></span></div>`;
        }).join("")
      : "";
  }
}
function drawManualObstacles() {
  if (!manualObstacleLayer) return;
  manualObstacleLayer.clearLayers();
  for (const obstacle of manualObstacles) {
    const style = SEMANTIC_ZONE_STYLE[obstacle.kind] || SEMANTIC_ZONE_STYLE.building;
    L.polygon(obstacle.ring, {
      color: style.color,
      weight: 3,
      fillColor: style.color,
      fillOpacity: 0.28,
      dashArray: "6,4"
    }).addTo(manualObstacleLayer).bindTooltip(
      `${style.label} · ${Number.isFinite(style.cost) ? "high cost" : "blocked"}`,
      {direction:"center"}
    );
  }
  if (manualObstacleDraft.length) {
    const draftKind = safeVal("manualObstacleKind", "building");
    const draftStyle = SEMANTIC_ZONE_STYLE[draftKind] || SEMANTIC_ZONE_STYLE.building;
    L.polyline(manualObstacleDraft, {color: draftStyle.color, weight: 3, dashArray: "4,4"}).addTo(manualObstacleLayer);
    manualObstacleDraft.forEach((p, i) => {
      const icon = L.divIcon({
        className: "",
        html: `<div style="width:18px;height:18px;border-radius:50%;background:${draftStyle.color};border:3px solid #fff;box-shadow:0 2px 8px rgba(0,0,0,.35);color:#fff;font-size:10px;font-weight:900;display:flex;align-items:center;justify-content:center">${i+1}</div>`,
        iconSize: [18,18], iconAnchor: [9,9]
      });
      L.marker(p, {icon}).addTo(manualObstacleLayer);
    });
  }
  updateLegend();
}
function startManualObstacleDraw() {
  if (manualObstacleDrawMode) cancelManualObstacleDraw();
  if (appMode !== "mission") setMode("mission");
  manualObstacleDrawMode = true;
  manualObstacleDraft = [];
  manualObstacleEditSnapshot = null;
  map.getContainer().style.cursor = "crosshair";
  setDrawer(false);
  const style = SEMANTIC_ZONE_STYLE[safeVal("manualObstacleKind", "building")];
  setStatus(`Drawing ${style.label.toLowerCase()} zone. Click the map to outline it.`, "info");
  updateManualObstacleStatus();
  drawManualObstacles();
}
function finishManualObstacleDraw() {
  if (!manualObstacleDrawMode) return;
  if (manualObstacleDraft.length < 3) {
    setStatus("Invalid zone: add at least 3 points.", "err");
    return;
  }
  const ring = [...manualObstacleDraft];
  const first = ring[0], last = ring[ring.length - 1];
  if (Math.abs(first[0] - last[0]) > 1e-12 || Math.abs(first[1] - last[1]) > 1e-12) ring.push(first);
  const editing = manualObstacleEditSnapshot;
  const kind = safeVal("manualObstacleKind", editing?.obstacle?.kind || "building");
  const zone = manualObstacleToBuilding({id: editing?.obstacle?.id || `manual_${Date.now()}`, kind, ring});
  if (editing) manualObstacles.splice(Math.min(editing.index, manualObstacles.length), 0, zone);
  else manualObstacles.push(zone);
  manualObstacleDraft = [];
  manualObstacleDrawMode = false;
  manualObstacleEditSnapshot = null;
  map.getContainer().style.cursor = "";
  saveManualObstacles();
  drawManualObstacles();
  updateManualObstacleStatus();
  setStatus(`${SEMANTIC_ZONE_STYLE[kind].label} zone ${editing ? "updated" : "saved"}. Press Generate to use it in the route.`, "ok");
  if (appMode === "mission") scheduleGenerate(`Semantic zone ${editing ? "updated" : "added"}.`, 500);
}
function cancelManualObstacleDraw() {
  const editing = manualObstacleEditSnapshot;
  if (editing) manualObstacles.splice(Math.min(editing.index, manualObstacles.length), 0, editing.obstacle);
  manualObstacleDrawMode = false;
  manualObstacleDraft = [];
  manualObstacleEditSnapshot = null;
  map.getContainer().style.cursor = "";
  if (editing) saveManualObstacles();
  drawManualObstacles();
  updateManualObstacleStatus();
  setStatus(editing ? "Zone edit cancelled." : "Zone drawing cancelled.", "info");
}
function clearManualObstacles() {
  manualObstacles = [];
  manualObstacleDraft = [];
  manualObstacleDrawMode = false;
  manualObstacleEditSnapshot = null;
  map.getContainer().style.cursor = "";
  saveManualObstacles();
  drawManualObstacles();
  updateManualObstacleStatus();
  setStatus("Semantic zones cleared.", "warn");
  if (appMode === "mission") scheduleGenerate("Semantic zones cleared.", 500);
}
function deleteManualObstacle(index) {
  if (index < 0 || index >= manualObstacles.length) return;
  manualObstacles.splice(index, 1);
  saveManualObstacles();
  drawManualObstacles();
  updateManualObstacleStatus();
  setStatus("Semantic zone deleted.", "ok");
  if (appMode === "mission") scheduleGenerate("Semantic zone deleted.", 500);
}
function editManualObstacle(index) {
  if (index < 0 || index >= manualObstacles.length) return;
  if (manualObstacleDrawMode) cancelManualObstacleDraw();
  if (appMode !== "mission") setMode("mission");
  const obstacle = manualObstacles.splice(index, 1)[0];
  if ($("manualObstacleKind")) $("manualObstacleKind").value = obstacle.kind || "building";
  const ring = (obstacle.ring || []).map(p => [p[0], p[1]]);
  const first = ring[0], last = ring[ring.length - 1];
  if (first && last && Math.abs(first[0] - last[0]) < 1e-12 && Math.abs(first[1] - last[1]) < 1e-12) ring.pop();
  manualObstacleEditSnapshot = {index, obstacle};
  manualObstacleDraft = ring;
  manualObstacleDrawMode = true;
  map.getContainer().style.cursor = "crosshair";
  saveManualObstacles();
  drawManualObstacles();
  updateManualObstacleStatus();
  setDrawer(false);
  setStatus("Editing semantic zone. Add points on the map, then finish or cancel.", "info");
}
function onManualObstacleMapClick(latlng) {
  manualObstacleDraft.push([latlng.lat, latlng.lng]);
  drawManualObstacles();
  updateManualObstacleStatus();
  setStatus(`Zone draft: ${manualObstacleDraft.length} point(s). Press Finish drawing when done.`, "info");
}
function manualHardObstaclesForBbox(bbox) {
  return semanticZonesForBbox(bbox).filter(o => ["building", "water"].includes(o.kind));
}

const PUBLIC_SEMANTIC_STORAGE_KEY = "geozigzag_public_semantic_v2";
const PUBLIC_SEMANTIC_MAX_AREA_M2 = 4_000_000;
const PUBLIC_SEMANTIC_MAX_FEATURES = 1200;

function semanticZonesForBbox(bbox) {
  return manualObstacles.concat(publicSemanticZones)
    .filter(zone => bboxOverlap(zone.bbox || routeBBox(zone.ring), bbox));
}
function closeSemanticRing(ring) {
  const clean = (ring || []).filter(
    point => Array.isArray(point) && Number.isFinite(point[0]) && Number.isFinite(point[1])
  );
  if (clean.length < 3) return [];
  const first = clean[0], last = clean[clean.length - 1];
  if (Math.abs(first[0] - last[0]) > 1e-10 || Math.abs(first[1] - last[1]) > 1e-10) {
    clean.push([first[0], first[1]]);
  }
  return clean.length >= 4 ? clean : [];
}
function sameSemanticPoint(a, b, eps = 1e-9) {
  return Math.abs(a[0] - b[0]) <= eps && Math.abs(a[1] - b[1]) <= eps;
}
function joinSemanticFragments(fragments) {
  const remaining = fragments.map(fragment => fragment.slice()).filter(fragment => fragment.length >= 2);
  const rings = [];
  while (remaining.length) {
    let ring = remaining.shift();
    let changed = true;
    while (changed) {
      changed = false;
      for (let index = 0; index < remaining.length; index++) {
        const fragment = remaining[index];
        if (sameSemanticPoint(ring[ring.length - 1], fragment[0])) {
          ring = ring.concat(fragment.slice(1));
        } else if (sameSemanticPoint(ring[ring.length - 1], fragment[fragment.length - 1])) {
          ring = ring.concat(fragment.slice(0, -1).reverse());
        } else if (sameSemanticPoint(ring[0], fragment[fragment.length - 1])) {
          ring = fragment.slice(0, -1).concat(ring);
        } else if (sameSemanticPoint(ring[0], fragment[0])) {
          ring = fragment.slice(1).reverse().concat(ring);
        } else {
          continue;
        }
        remaining.splice(index, 1);
        changed = true;
        break;
      }
    }
    const closed = closeSemanticRing(ring);
    if (closed.length) rings.push(closed);
  }
  return rings;
}
function publicSemanticKind(tags = {}) {
  if (tags.building) return "building";
  if (tags.natural === "water" || tags.landuse === "reservoir") return "water";
  if (tags.natural === "wood" || tags.landuse === "forest") return "forest";
  if (["scrub", "shrubbery", "heath"].includes(tags.natural) || tags.landcover === "shrubs") return "scrub";
  return null;
}
function clipSemanticRingToBbox(ring, bbox) {
  let points = closeSemanticRing(ring).slice(0, -1);
  const clip = (inside, intersect) => {
    if (!points.length) return;
    const output = [];
    let previous = points[points.length - 1];
    for (const current of points) {
      const currentInside = inside(current);
      const previousInside = inside(previous);
      if (currentInside) {
        if (!previousInside) output.push(intersect(previous, current));
        output.push(current);
      } else if (previousInside) {
        output.push(intersect(previous, current));
      }
      previous = current;
    }
    points = output;
  };
  const atLon = (a, b, lon) => {
    const ratio = (lon - a[1]) / ((b[1] - a[1]) || 1e-15);
    return [a[0] + (b[0] - a[0]) * ratio, lon];
  };
  const atLat = (a, b, lat) => {
    const ratio = (lat - a[0]) / ((b[0] - a[0]) || 1e-15);
    return [lat, a[1] + (b[1] - a[1]) * ratio];
  };
  clip(point => point[1] >= bbox.west, (a, b) => atLon(a, b, bbox.west));
  clip(point => point[1] <= bbox.east, (a, b) => atLon(a, b, bbox.east));
  clip(point => point[0] >= bbox.south, (a, b) => atLat(a, b, bbox.south));
  clip(point => point[0] <= bbox.north, (a, b) => atLat(a, b, bbox.north));
  return closeSemanticRing(points);
}
function semanticRingAreaAndCenter(ring) {
  const origin = polygonOrigin(ring);
  const xy = ring.slice(0, -1).map(point => llToXY(point[0], point[1], origin));
  let twiceArea = 0;
  let centerX = 0;
  let centerY = 0;
  for (let index = 0; index < xy.length; index++) {
    const first = xy[index], second = xy[(index + 1) % xy.length];
    const cross = first[0] * second[1] - second[0] * first[1];
    twiceArea += cross;
    centerX += (first[0] + second[0]) * cross;
    centerY += (first[1] + second[1]) * cross;
  }
  const areaM2 = Math.abs(twiceArea) / 2;
  let centerXY;
  if (Math.abs(twiceArea) > 1e-9) {
    centerXY = [centerX / (3 * twiceArea), centerY / (3 * twiceArea)];
  } else {
    centerXY = [
      xy.reduce((sum, point) => sum + point[0], 0) / Math.max(1, xy.length),
      xy.reduce((sum, point) => sum + point[1], 0) / Math.max(1, xy.length)
    ];
  }
  if (!pointInPolygonXY(centerXY, xy)) {
    const xs = xy.map(point => point[0]), ys = xy.map(point => point[1]);
    const candidates = [];
    for (let row = 1; row < 10; row++) {
      for (let col = 1; col < 10; col++) {
        const candidate = [
          Math.min(...xs) + (Math.max(...xs) - Math.min(...xs)) * col / 10,
          Math.min(...ys) + (Math.max(...ys) - Math.min(...ys)) * row / 10
        ];
        if (pointInPolygonXY(candidate, xy)) candidates.push(candidate);
      }
    }
    if (candidates.length) {
      candidates.sort((a, b) =>
        Math.hypot(a[0] - centerXY[0], a[1] - centerXY[1]) -
        Math.hypot(b[0] - centerXY[0], b[1] - centerXY[1])
      );
      centerXY = candidates[0];
    }
  }
  return {areaM2, center: xyToLL(centerXY[0], centerXY[1], origin)};
}
function parseOverpassSemantic(data, clipBbox = null) {
  const zones = [];
  const seen = new Set();
  function addZone(osmId, tags, ring) {
    const kind = publicSemanticKind(tags);
    const original = closeSemanticRing(ring);
    const closed = clipBbox ? clipSemanticRingToBbox(original, clipBbox) : original;
    if (!kind || !closed.length) return;
    const id = `osm/${osmId}/${kind}`;
    const signature = id + ":" + closed.slice(0, 4).map(point => point.join(",")).join("|");
    if (seen.has(signature)) return;
    const geometry = semanticRingAreaAndCenter(closed);
    if (geometry.areaM2 < 1) return;
    zones.push({
      id,
      source: "OpenStreetMap",
      manual: false,
      kind,
      ring: closed,
      bbox: routeBBox(closed),
      center: geometry.center,
      areaM2: geometry.areaM2,
      name: tags.name || null
    });
    seen.add(signature);
  }
  for (const element of data.elements || []) {
    if (element.type !== "way") continue;
    addZone(
      `way/${element.id}`,
      element.tags || {},
      (element.geometry || []).map(point => [Number(point.lat), Number(point.lon)])
    );
  }
  for (const element of data.elements || []) {
    if (element.type !== "relation" || !Array.isArray(element.members)) continue;
    const fragments = element.members
      .filter(member => (!member.role || member.role === "outer") && Array.isArray(member.geometry))
      .map(member => member.geometry.map(point => [Number(point.lat), Number(point.lon)]))
      .filter(fragment => fragment.length >= 2);
    joinSemanticFragments(fragments).forEach((ring, index) => {
      addZone(`relation/${element.id}/outer/${index + 1}`, element.tags || {}, ring);
    });
  }
  if (zones.length > PUBLIC_SEMANTIC_MAX_FEATURES) {
    throw new Error(`OpenStreetMap returned ${zones.length} polygons. Zoom in to keep the result below ${PUBLIC_SEMANTIC_MAX_FEATURES}.`);
  }
  return zones;
}
function publicSemanticQuery(bbox) {
  const bounds = `${bbox.south.toFixed(8)},${bbox.west.toFixed(8)},${bbox.north.toFixed(8)},${bbox.east.toFixed(8)}`;
  return `[out:json][timeout:45];
(
  way["building"](${bounds});
  relation["building"](${bounds});
  way["natural"="water"](${bounds});
  relation["natural"="water"](${bounds});
  way["landuse"="reservoir"](${bounds});
  relation["landuse"="reservoir"](${bounds});
  way["natural"="wood"](${bounds});
  relation["natural"="wood"](${bounds});
  way["landuse"="forest"](${bounds});
  relation["landuse"="forest"](${bounds});
  way["natural"="scrub"](${bounds});
  relation["natural"="scrub"](${bounds});
  way["natural"="shrubbery"](${bounds});
  relation["natural"="shrubbery"](${bounds});
  way["natural"="heath"](${bounds});
  relation["natural"="heath"](${bounds});
  way["landcover"="shrubs"](${bounds});
  relation["landcover"="shrubs"](${bounds});
);
out geom;`;
}
function visibleSemanticBbox() {
  const bounds = map.getBounds();
  return {
    south: bounds.getSouth(),
    west: bounds.getWest(),
    north: bounds.getNorth(),
    east: bounds.getEast()
  };
}
function semanticBboxAreaM2(bbox) {
  const midLat = (bbox.south + bbox.north) / 2;
  const height = Math.max(0, (bbox.north - bbox.south) * 111320);
  const width = Math.max(0, (bbox.east - bbox.west) * 111320 * Math.max(0.2, Math.cos(midLat * Math.PI / 180)));
  return width * height;
}
function validatePublicSemanticBbox(bbox) {
  const area = semanticBboxAreaM2(bbox);
  if (area > PUBLIC_SEMANTIC_MAX_AREA_M2) {
    throw new Error(
      `Public-data area is ${(area / 1e6).toFixed(1)} km²; keep it below ${(PUBLIC_SEMANTIC_MAX_AREA_M2 / 1e6).toFixed(0)} km².`
    );
  }
  return area;
}
async function downloadOsmSemanticZones(bbox) {
  const endpoints = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter"
  ];
  const query = publicSemanticQuery(bbox);
  const errors = [];
  let emptyResponseSeen = false;
  const apiQuery = new URLSearchParams({
    south: bbox.south,
    west: bbox.west,
    north: bbox.north,
    east: bbox.east
  });
  try {
    const response = await fetchWithTimeout(
      `/api/osm/semantic?${apiQuery}`,
      {headers: {"Accept": "application/json"}},
      120000,
      "Local semantic-data service"
    );
    if (response.ok) {
      const payload = await response.json();
      const zones = (payload.features || []).filter(
        zone => zone && SEMANTIC_ZONE_STYLE[zone.kind] && Array.isArray(zone.ring)
      );
      if (zones.length || payload.features) {
        return {zones, metadata: {source: payload.source || "OpenStreetMap", count: zones.length}};
      }
    } else if (response.status !== 404) {
      const payload = await response.json().catch(() => ({}));
      errors.push(`local service: ${payload.error || `HTTP ${response.status}`}`);
    }
  } catch (error) {
    errors.push(`local service: ${error.message || error}`);
  }
  for (const endpoint of endpoints) {
    try {
      const response = await fetchWithTimeout(endpoint, {
        method: "POST",
        headers: {"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"},
        body: "data=" + encodeURIComponent(query)
      }, 25000, "OpenStreetMap semantic data");
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const zones = parseOverpassSemantic(await response.json(), bbox);
      if (zones.length) return {zones, metadata: {source: "OpenStreetMap/Overpass", count: zones.length}};
      emptyResponseSeen = true;
      errors.push(`${endpoint.replace(/^https?:\/\//, "")}: no supported polygons`);
    } catch (error) {
      errors.push(`${endpoint.replace(/^https?:\/\//, "")}: ${error.message || error}`);
    }
  }
  if (emptyResponseSeen) return {zones: [], metadata: {source: "OpenStreetMap/Overpass", count: 0}};
  throw new Error(`OpenStreetMap download failed. ${errors.join(" | ")}`);
}
function bboxCenterLatLng(bbox) {
  return [(bbox.south + bbox.north) / 2, (bbox.west + bbox.east) / 2];
}
function semanticPointInsideZone(point, zone) {
  const origin = point;
  const polygon = zone.ring.map(vertex => llToXY(vertex[0], vertex[1], origin));
  return pointInPolygonXY([0, 0], polygon);
}
function semanticZonesDuplicate(first, second) {
  const firstBox = first.bbox || routeBBox(first.ring);
  const secondBox = second.bbox || routeBBox(second.ring);
  if (!bboxOverlap(firstBox, secondBox)) return false;
  const firstCenter = first.center || bboxCenterLatLng(firstBox);
  const secondCenter = second.center || bboxCenterLatLng(secondBox);
  return semanticPointInsideZone(firstCenter, second) ||
    semanticPointInsideZone(secondCenter, first) ||
    distanceLatLng(firstCenter, secondCenter) < 12;
}
async function downloadCatastroBuildings(bbox) {
  const query = new URLSearchParams({
    south: bbox.south,
    west: bbox.west,
    north: bbox.north,
    east: bbox.east
  });
  const response = await fetchWithTimeout(
    `/api/catastro/buildings?${query}`,
    {headers: {"Accept": "application/json"}},
    150000,
    "Spanish Cadastre building data"
  );
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || `Catastro server returned HTTP ${response.status}.`);
  if (payload.schema !== "geozigzag-catastro-buildings-response-v1" || !Array.isArray(payload.features)) {
    throw new Error("Catastro server returned an invalid building response.");
  }
  const zones = payload.features.filter(
    zone => zone && zone.kind === "building" && Array.isArray(zone.ring) && zone.ring.length >= 4
  );
  return {
    zones,
    metadata: {
      source: payload.source || "Dirección General del Catastro",
      dataset: payload.dataset || "Catastro INSPIRE Buildings (BU)",
      count: zones.length,
      cached: Boolean(payload.cached),
      queryEpsg: payload.queryEpsg || null,
      supported: payload.queryEpsg !== 0
    }
  };
}
function bboxMayBeInSpain(bbox) {
  return !(bbox.east < -18.5 || bbox.west > 5 || bbox.north < 27 || bbox.south > 44.5);
}
async function downloadPublicSemanticZones(bbox) {
  const [osmResult, catastroResult] = await Promise.allSettled([
    downloadOsmSemanticZones(bbox),
    downloadCatastroBuildings(bbox)
  ]);
  if (osmResult.status === "rejected" && catastroResult.status === "rejected") {
    throw new Error(`Public semantic download failed. OSM: ${osmResult.reason?.message || osmResult.reason}; Catastro: ${catastroResult.reason?.message || catastroResult.reason}`);
  }
  if (catastroResult.status === "rejected" && bboxMayBeInSpain(bbox)) {
    throw new Error(`Official Catastro buildings are required for safe routing in Spain: ${catastroResult.reason?.message || catastroResult.reason}`);
  }
  const osm = osmResult.status === "fulfilled" ? osmResult.value : {zones: [], metadata: {error: String(osmResult.reason)}};
  const catastro = catastroResult.status === "fulfilled" ? catastroResult.value : {zones: [], metadata: {supported: false, error: String(catastroResult.reason)}};
  const cadastralBuildings = catastro.zones;
  const complementaryOsm = osm.zones.filter(zone =>
    zone.kind !== "building" || !cadastralBuildings.some(building => semanticZonesDuplicate(zone, building))
  );
  const zones = cadastralBuildings.concat(complementaryOsm);
  if (zones.length > PUBLIC_SEMANTIC_MAX_FEATURES) {
    throw new Error(`Public sources returned ${zones.length} polygons. Reduce the corridor or visible area below ${PUBLIC_SEMANTIC_MAX_FEATURES}.`);
  }
  return {
    zones,
    metadata: {
      catastro: catastro.metadata,
      osm: osm.metadata,
      droppedDuplicateOsmBuildings: osm.zones.length + cadastralBuildings.length - zones.length,
      warnings: [
        ...(osmResult.status === "rejected" ? [`OSM unavailable: ${osmResult.reason?.message || osmResult.reason}`] : []),
        ...(catastroResult.status === "rejected" ? [`Catastro unavailable outside mandatory coverage: ${catastroResult.reason?.message || catastroResult.reason}`] : [])
      ]
    }
  };
}
function missionPublicSemanticBbox() {
  if (missionStops.length < 2) throw new Error("Add at least two mission waypoints before loading public data.");
  const bufferM = Math.max(20, Math.min(1000, safeNum("publicRouteBuffer", 100)));
  return expandBBoxMeters(routeBBox(missionStops.map(poiById)), bufferM);
}
async function refreshPublicSemanticZones(bbox, rebuildResources = false) {
  validatePublicSemanticBbox(bbox);
  const downloaded = await downloadPublicSemanticZones(bbox);
  publicSemanticZones = downloaded.zones;
  publicSemanticMetadata = downloaded.metadata;
  publicSemanticCoverageBbox = {...bbox};
  const waypointCount = rebuildResources
    ? rebuildPublicSemanticWaypoints()
    : poiGeojson.features.filter(feature => feature.properties?.publicSemantic).length;
  drawPublicSemanticZones();
  savePublicSemanticZones();
  updatePublicSemanticStatus(waypointCount);
  return {
    counts: publicSemanticCounts(),
    sources: publicSemanticSourceCounts(),
    metadata: publicSemanticMetadata,
    waypointCount,
    autoWaypointCount: publicSemanticWaypointSummary.autoAdded,
    waypointSummary: {...publicSemanticWaypointSummary},
    bbox
  };
}
async function refreshMissionPublicSemanticZones() {
  const bbox = missionPublicSemanticBbox();
  await loadingStep("Loading Catastro buildings and complementary OSM data…");
  return refreshPublicSemanticZones(bbox, true);
}
function removePublicSemanticWaypoints() {
  poiGeojson.features = poiGeojson.features.filter(feature => !feature.properties?.publicSemantic);
  const available = new Set(poiGeojson.features.map(feature => feature.id));
  missionStops = missionStops.filter(id => available.has(id));
}
function insertMissionStopWithMinimumDetour(id) {
  if (missionStops.includes(id)) return;
  if (missionStops.length < 2) {
    missionStops.push(id);
    return;
  }
  const point = poiById(id);
  let bestIndex = 1;
  let bestDetourM = Infinity;
  for (let index = 1; index < missionStops.length; index++) {
    const before = poiById(missionStops[index - 1]);
    const after = poiById(missionStops[index]);
    const detourM = distanceLatLng(before, point) + distanceLatLng(point, after) - distanceLatLng(before, after);
    if (detourM < bestDetourM - 1e-6) {
      bestDetourM = detourM;
      bestIndex = index;
    }
  }
  missionStops.splice(bestIndex, 0, id);
}
function rebuildPublicSemanticWaypoints() {
  const manuallySelectedZoneIds = missionStops
    .map(id => poiGeojson.features.find(feature => feature.id === id))
    .filter(feature => feature?.properties?.publicSemantic && !feature.properties.autoSelected)
    .map(feature => feature.properties.semantic_zone_id);
  removePublicSemanticWaypoints();
  const minimumArea = Math.max(25, safeNum("publicWaypointMinArea", 100));
  const safetyBufferM = Math.max(0.5, Math.min(25, safeNum("semanticSafetyBuffer", 2)));
  const hardZones = publicSemanticZones.filter(zone => ["building", "water"].includes(zone.kind));
  let rejectedUnsafe = 0;
  const resources = publicSemanticZones
    .filter(zone => {
      if (!["forest", "scrub"].includes(zone.kind) || zone.areaM2 < minimumArea) return false;
      const unsafe = hardZones.some(hardZone => pointHitsSemanticZone(zone.center, hardZone, safetyBufferM));
      if (unsafe) rejectedUnsafe += 1;
      return !unsafe;
    })
    .sort((a, b) => b.areaM2 - a.areaM2 || String(a.id).localeCompare(String(b.id)))
    .slice(0, 80);
  const kindCounters = {forest: 0, scrub: 0};
  for (const zone of resources) {
    kindCounters[zone.kind] += 1;
    const id = `public_${zone.kind}_${kindCounters[zone.kind]}`;
    poiGeojson.features.push({
      id,
      type: "Feature",
      properties: {
        landcover: zone.kind,
        publicSemantic: true,
        source: zone.source || "Public semantic data",
        area_m2: Math.round(zone.areaM2),
        semantic_zone_id: zone.id,
        name: zone.name || `${SEMANTIC_ZONE_STYLE[zone.kind].label} ${kindCounters[zone.kind]}`,
        autoSelected: false
      },
      geometry: {type: "Point", coordinates: [zone.center[1], zone.center[0]]}
    });
  }
  for (const semanticZoneId of manuallySelectedZoneIds) {
    const feature = poiGeojson.features.find(candidate =>
      candidate.properties?.publicSemantic && candidate.properties.semantic_zone_id === semanticZoneId
    );
    if (feature) insertMissionStopWithMinimumDetour(feature.id);
  }
  const automaticallySelected = [];
  let rejectedDuplicate = 0;
  if (safeVal("publicAutoWaypoints", "yes") === "yes" && missionStops.length >= 2) {
    const limit = Math.max(1, Math.min(20, Math.round(safeNum("publicAutoWaypointLimit", 6))));
    for (const resource of resources) {
      const closeToExistingStop = missionStops.some(stopId => distanceLatLng(resource.center, poiById(stopId)) < 20);
      if (closeToExistingStop || automaticallySelected.some(selected => distanceLatLng(resource.center, selected.center) < 20)) {
        rejectedDuplicate += 1;
        continue;
      }
      automaticallySelected.push(resource);
      if (automaticallySelected.length >= limit) break;
    }
    for (const resource of automaticallySelected) {
      const feature = poiGeojson.features.find(candidate =>
        candidate.properties?.publicSemantic && candidate.properties.semantic_zone_id === resource.id
      );
      if (!feature) continue;
      feature.properties.autoSelected = true;
      insertMissionStopWithMinimumDetour(feature.id);
    }
  }
  publicSemanticWaypointSummary = {
    created: resources.length,
    autoAdded: automaticallySelected.length,
    rejectedUnsafe,
    rejectedDuplicate
  };
  renderPoiPicker();
  renderMissionList();
  if (appMode === "mission") renderMissionMarkers();
  return resources.length;
}
function drawPublicSemanticZones() {
  publicSemanticLayer.clearLayers();
  for (const zone of publicSemanticZones) {
    const style = SEMANTIC_ZONE_STYLE[zone.kind];
    L.polygon(zone.ring, {
      color: style.color,
      weight: 2,
      opacity: 0.9,
      fillColor: style.color,
      fillOpacity: 0.18,
      interactive: true
    }).addTo(publicSemanticLayer).bindTooltip(
      `${escapeHtml(zone.name || style.label)} · ${escapeHtml(zone.source || "public data")} · ${Math.round(zone.areaM2)} m²`,
      {direction: "center"}
    );
  }
  updateLegend();
}
function publicSemanticCounts() {
  const counts = {building: 0, water: 0, forest: 0, scrub: 0};
  publicSemanticZones.forEach(zone => {
    if (Object.prototype.hasOwnProperty.call(counts, zone.kind)) counts[zone.kind] += 1;
  });
  return counts;
}
function publicSemanticSourceCounts() {
  const counts = {catastro: 0, osm: 0, manual: manualObstacles.length};
  publicSemanticZones.forEach(zone => {
    if (/catastro/i.test(zone.source || "")) counts.catastro += 1;
    else if (/openstreetmap|osm/i.test(zone.source || "")) counts.osm += 1;
  });
  return counts;
}
function updatePublicSemanticStatus(waypointCount = null) {
  const element = $("publicSemanticStatus");
  if (!element) return;
  if (!publicSemanticZones.length) {
    element.textContent = "Not loaded";
    return;
  }
  const counts = publicSemanticCounts();
  const resources = waypointCount === null
    ? poiGeojson.features.filter(feature => feature.properties?.publicSemantic).length
    : waypointCount;
  const sources = publicSemanticSourceCounts();
  element.textContent = `${counts.building} buildings · ${counts.water} water · ${counts.forest + counts.scrub} vegetation`;
  element.title = `Catastro ${sources.catastro}, OSM ${sources.osm}. Buildings ${counts.building}, water ${counts.water}, forest ${counts.forest}, scrub ${counts.scrub}; resource waypoints ${resources}, automatically selected ${publicSemanticWaypointSummary.autoAdded}, unsafe centers rejected ${publicSemanticWaypointSummary.rejectedUnsafe}, nearby duplicates skipped ${publicSemanticWaypointSummary.rejectedDuplicate}.`;
}
function savePublicSemanticZones() {
  try {
    localStorage.setItem(PUBLIC_SEMANTIC_STORAGE_KEY, JSON.stringify({
      savedAt: Date.now(),
      zones: publicSemanticZones,
      metadata: publicSemanticMetadata,
      coverageBbox: publicSemanticCoverageBbox
    }));
  } catch (_) {}
}
function loadSavedPublicSemanticZones() {
  try {
    const payload = JSON.parse(localStorage.getItem(PUBLIC_SEMANTIC_STORAGE_KEY) || "{}");
    publicSemanticZones = (payload.zones || []).filter(
      zone => zone && SEMANTIC_ZONE_STYLE[zone.kind] && Array.isArray(zone.ring) && zone.ring.length >= 4
    );
    publicSemanticMetadata = payload.metadata || {};
    publicSemanticCoverageBbox = payload.coverageBbox || null;
  } catch (_) {
    publicSemanticZones = [];
    publicSemanticMetadata = {};
    publicSemanticCoverageBbox = null;
  }
  drawPublicSemanticZones();
  updatePublicSemanticStatus(rebuildPublicSemanticWaypoints());
}
function clearPublicSemanticZones() {
  publicSemanticZones = [];
  publicSemanticMetadata = {};
  publicSemanticCoverageBbox = null;
  publicSemanticWaypointSummary = {created: 0, autoAdded: 0, rejectedUnsafe: 0, rejectedDuplicate: 0};
  try { localStorage.removeItem(PUBLIC_SEMANTIC_STORAGE_KEY); } catch (_) {}
  removePublicSemanticWaypoints();
  drawPublicSemanticZones();
  renderPoiPicker();
  renderMissionList();
  if (appMode === "mission") renderMissionMarkers();
  updatePublicSemanticStatus(0);
  setStatus("Public semantic data cleared.", "warn");
  if (appMode === "mission") scheduleGenerate("Public semantic data cleared.", 500);
}
async function loadVisiblePublicSemanticZones() {
  const bbox = visibleSemanticBbox();
  try {
    validatePublicSemanticBbox(bbox);
  } catch (error) {
    setStatus(error.message || String(error), "err");
    return;
  }
  setGenerateBusy(true);
  setLoadingVisible(true, "Downloading Catastro buildings and complementary OSM data…");
  try {
    const {counts, sources, waypointCount, autoWaypointCount} = await refreshPublicSemanticZones(bbox, true);
    setStatus(
      `Public data loaded: ${counts.building} buildings (${sources.catastro} Catastro, ${sources.osm} OSM zones), ${counts.water} water, ${counts.forest} forest, ${counts.scrub} scrub; ${waypointCount} resource waypoints, ${autoWaypointCount} added automatically.`,
      "ok"
    );
    if (appMode === "mission") scheduleGenerate("Public semantic data loaded.", 500);
  } catch (error) {
    setStatus(error.message || String(error), "err");
  } finally {
    setGenerateBusy(false);
    setLoadingVisible(false);
  }
}

function uniqueBuildings(buildings) {
  const seen = new Set();
  const out = [];
  for (const building of buildings) {
    if (seen.has(building.id)) continue;
    seen.add(building.id);
    out.push(building);
  }
  return out;
}
function localOriginForGeometries(routePoints, buildings) {
  const pts = routePoints.slice();
  for (const b of buildings) pts.push(...b.ring);
  if (!pts.length) return [0, 0];
  return [pts.reduce((s,p)=>s+p[0],0)/pts.length, pts.reduce((s,p)=>s+p[1],0)/pts.length];
}
function orient(a, b, c) {
  const v = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]);
  if (Math.abs(v) < 1e-9) return 0;
  return v > 0 ? 1 : -1;
}
function onSegment(a, b, c) {
  return Math.min(a[0], b[0]) - 1e-9 <= c[0] && c[0] <= Math.max(a[0], b[0]) + 1e-9 &&
         Math.min(a[1], b[1]) - 1e-9 <= c[1] && c[1] <= Math.max(a[1], b[1]) + 1e-9;
}
function segmentsIntersect(a, b, c, d) {
  const o1 = orient(a, b, c), o2 = orient(a, b, d), o3 = orient(c, d, a), o4 = orient(c, d, b);
  if (o1 !== o2 && o3 !== o4) return true;
  if (o1 === 0 && onSegment(a, b, c)) return true;
  if (o2 === 0 && onSegment(a, b, d)) return true;
  if (o3 === 0 && onSegment(c, d, a)) return true;
  if (o4 === 0 && onSegment(c, d, b)) return true;
  return false;
}
function pointInPolygonXY(point, poly) {
  let inside = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const xi = poly[i][0], yi = poly[i][1];
    const xj = poly[j][0], yj = poly[j][1];
    const intersect = ((yi > point[1]) !== (yj > point[1])) &&
      (point[0] < (xj - xi) * (point[1] - yi) / ((yj - yi) || 1e-12) + xi);
    if (intersect) inside = !inside;
  }
  return inside;
}
function distancePointSegmentXY(p, a, b) {
  const dx = b[0] - a[0], dy = b[1] - a[1];
  const len2 = dx * dx + dy * dy;
  if (len2 < 1e-12) return Math.hypot(p[0] - a[0], p[1] - a[1]);
  let t = ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / len2;
  t = Math.max(0, Math.min(1, t));
  return Math.hypot(p[0] - (a[0] + t * dx), p[1] - (a[1] + t * dy));
}
function segmentDistanceXY(a, b, c, d) {
  if (segmentsIntersect(a, b, c, d)) return 0;
  return Math.min(
    distancePointSegmentXY(a, c, d), distancePointSegmentXY(b, c, d),
    distancePointSegmentXY(c, a, b), distancePointSegmentXY(d, a, b)
  );
}
function routeHitsBuilding(routePoints, building, bufferM) {
  if (routePoints.length < 2) return false;
  const routeBox = expandBBoxMeters(routeBBox(routePoints), bufferM + 1);
  const buildBox = expandBBoxMeters(building.bbox, bufferM + 1);
  if (!bboxOverlap(routeBox, buildBox)) return false;

  const origin = localOriginForGeometries(routePoints, [building]);
  const routeXY = routePoints.map(p => llToXY(p[0], p[1], origin));
  const polyXY = building.ring.map(p => llToXY(p[0], p[1], origin));
  for (let i = 0; i < routeXY.length - 1; i++) {
    const a = routeXY[i], b = routeXY[i + 1];
    if (pointInPolygonXY(a, polyXY) || pointInPolygonXY(b, polyXY)) return true;
    for (let j = 0; j < polyXY.length - 1; j++) {
      if (segmentDistanceXY(a, b, polyXY[j], polyXY[j + 1]) <= bufferM) return true;
    }
  }
  return false;
}
function routeBuildingHits(routePoints, buildings, bufferM) {
  const hits = [];
  for (const building of buildings) {
    if (routeHitsBuilding(routePoints, building, bufferM)) hits.push(building);
  }
  return hits;
}
function pointHitsSemanticZone(point, zone, bufferM = 0) {
  const zoneBox = expandBBoxMeters(zone.bbox || routeBBox(zone.ring), bufferM + 0.5);
  if (!bboxOverlap(routeBBox([point]), zoneBox)) return false;
  const polygon = zone.ring.map(vertex => llToXY(vertex[0], vertex[1], point));
  if (pointInPolygonXY([0, 0], polygon)) return true;
  for (let index = 0; index < polygon.length - 1; index++) {
    if (distancePointSegmentXY([0, 0], polygon[index], polygon[index + 1]) <= bufferM) return true;
  }
  return false;
}
function validateHardObstacleEndpoints(start, end, zones, bufferM) {
  const hardZones = zones.filter(zone => ["building", "water"].includes(zone.kind));
  const startHits = hardZones.filter(zone => pointHitsSemanticZone(start, zone, bufferM));
  const endHits = hardZones.filter(zone => pointHitsSemanticZone(end, zone, bufferM));
  if (startHits.length || endHits.length) {
    const labels = [];
    if (startHits.length) labels.push(`start (${startHits.length})`);
    if (endHits.length) labels.push(`destination (${endHits.length})`);
    throw new Error(`Mission ${labels.join(" and ")} lies inside or within ${bufferM.toFixed(1)} m of a mapped building/water obstacle. Move the affected waypoint.`);
  }
}
function cellIsBlocked(value) {
  return value === true || !Number.isFinite(value);
}
function cellTraversalCost(value) {
  return typeof value === "number" ? value : 1;
}
function nearestFreeCell(grid, start, maxRadius = 12) {
  const h = grid.length, w = grid[0].length;
  if (start[0] >= 0 && start[0] < h && start[1] >= 0 && start[1] < w && !cellIsBlocked(grid[start[0]][start[1]])) return start;
  for (let r = 1; r <= maxRadius; r++) {
    for (let dy = -r; dy <= r; dy++) {
      for (let dx = -r; dx <= r; dx++) {
        if (Math.abs(dx) !== r && Math.abs(dy) !== r) continue;
        const yy = start[0] + dy, xx = start[1] + dx;
        if (yy >= 0 && yy < h && xx >= 0 && xx < w && !cellIsBlocked(grid[yy][xx])) return [yy, xx];
      }
    }
  }
  return null;
}
function astarGrid(grid, start, goal) {
  const h = grid.length, w = grid[0].length;
  const key = c => `${c[0]},${c[1]}`;
  const heap = [];
  function pushHeap(entry) {
    heap.push(entry);
    let index = heap.length - 1;
    while (index > 0) {
      const parent = Math.floor((index - 1) / 2);
      if (heap[parent][0] <= entry[0]) break;
      heap[index] = heap[parent];
      index = parent;
    }
    heap[index] = entry;
  }
  function popHeap() {
    const first = heap[0];
    const last = heap.pop();
    if (heap.length && last) {
      let index = 0;
      while (true) {
        const left = index * 2 + 1;
        const right = left + 1;
        if (left >= heap.length) break;
        const child = right < heap.length && heap[right][0] < heap[left][0] ? right : left;
        if (heap[child][0] >= last[0]) break;
        heap[index] = heap[child];
        index = child;
      }
      heap[index] = last;
    }
    return first;
  }
  const came = new Map();
  const cost = new Map([[key(start), 0]]);
  const dirs = [[1,0],[-1,0],[0,1],[0,-1],[1,1],[1,-1],[-1,1],[-1,-1]];
  pushHeap([0, start, 0]);
  while (heap.length) {
    const entry = popHeap();
    const current = entry[1];
    if (entry[2] !== cost.get(key(current))) continue;
    if (current[0] === goal[0] && current[1] === goal[1]) {
      const path = [current];
      let ck = key(current);
      while (came.has(ck)) {
        const prev = came.get(ck);
        path.push(prev);
        ck = key(prev);
      }
      return path.reverse();
    }
    for (const [dy, dx] of dirs) {
      const ny = current[0] + dy, nx = current[1] + dx;
      if (ny < 0 || ny >= h || nx < 0 || nx >= w || cellIsBlocked(grid[ny][nx])) continue;
      if (dy && dx && (
        cellIsBlocked(grid[current[0]][nx]) ||
        cellIsBlocked(grid[ny][current[1]])
      )) continue;
      const nk = key([ny, nx]);
      const step = dy && dx ? Math.SQRT2 : 1;
      const traversal = (cellTraversalCost(grid[current[0]][current[1]]) + cellTraversalCost(grid[ny][nx])) / 2;
      const newCost = cost.get(key(current)) + step * traversal;
      if (newCost < (cost.get(nk) ?? Infinity)) {
        cost.set(nk, newCost);
        came.set(nk, current);
        const priority = newCost + Math.hypot(goal[0] - ny, goal[1] - nx);
        pushHeap([priority, [ny, nx], newCost]);
      }
    }
  }
  return [];
}
function buildLocalSemanticGrid(start, end, buildings, bufferM, preferredResolution = 3, semanticCosts = false, planningBbox = null) {
  const bbox = planningBbox
    ? {...planningBbox}
    : expandBBoxMeters(routeBBox([start, end, ...buildings.flatMap(b => b.ring)]), Math.max(20, bufferM + 10));
  const origin = [(bbox.south + bbox.north) / 2, (bbox.west + bbox.east) / 2];
  const minXY = llToXY(bbox.south, bbox.west, origin);
  const maxXY = llToXY(bbox.north, bbox.east, origin);
  const widthM = Math.max(1, maxXY[0] - minXY[0]);
  const heightM = Math.max(1, maxXY[1] - minXY[1]);
  let resolution = Math.max(preferredResolution, widthM / 260, heightM / 260, 1.5);
  let cols = Math.max(3, Math.ceil(widthM / resolution));
  let rows = Math.max(3, Math.ceil(heightM / resolution));
  const maximumDemCells = 39000;
  if (cols * rows > maximumDemCells) {
    resolution *= Math.sqrt((cols * rows) / maximumDemCells) * 1.01;
    cols = Math.max(3, Math.ceil(widthM / resolution));
    rows = Math.max(3, Math.ceil(heightM / resolution));
  }
  // A* connects cell centres, so rasterise obstacles with half a cell diagonal
  // of extra clearance. This makes the continuous polyline honour bufferM
  // instead of merely keeping sampled cell centres outside the footprint.
  const rasterBufferM = bufferM + resolution * Math.SQRT2 / 2;
  if (cols * rows > 60000) throw new Error("The local A* fallback would be too large for this area. Move the waypoints closer together or increase the resolution.");
  const buildingXY = buildings.map(b => ({
    kind: SEMANTIC_ZONE_STYLE[b.kind] ? b.kind : "building",
    ring: b.ring.map(p => llToXY(p[0], p[1], origin))
  }));
  function cellCenter(row, col) {
    return [minXY[0] + (col + 0.5) * resolution, minXY[1] + (row + 0.5) * resolution];
  }
  function llToCell(p) {
    const xy = llToXY(p[0], p[1], origin);
    return [Math.max(0, Math.min(rows - 1, Math.floor((xy[1] - minXY[1]) / resolution))), Math.max(0, Math.min(cols - 1, Math.floor((xy[0] - minXY[0]) / resolution)))];
  }
  const grid = Array.from(
    {length: rows},
    () => Array(cols).fill(semanticCosts ? landcoverCost.pastizal : false)
  );
  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < cols; c++) {
      const center = cellCenter(r, c);
      for (const zone of buildingXY) {
        const style = SEMANTIC_ZONE_STYLE[zone.kind];
        let inside = pointInPolygonXY(center, zone.ring);
        for (let i = 0; !inside && i < zone.ring.length - 1; i++) {
          if (distancePointSegmentXY(center, zone.ring[i], zone.ring[i + 1]) <= rasterBufferM) inside = true;
        }
        if (!inside) continue;
        if (!semanticCosts || !Number.isFinite(style.cost)) {
          grid[r][c] = semanticCosts ? Infinity : true;
          break;
        }
        grid[r][c] = Math.max(grid[r][c], style.cost);
      }
    }
  }
  return {bbox, origin, minXY, maxXY, widthM, heightM, resolution, cols, rows, grid, cellCenter, llToCell};
}
function solveLocalGrid(context, start, end, strictTerrainEndpoints = false) {
  const {grid, llToCell, cellCenter} = context;
  const rawStart = llToCell(start), rawGoal = llToCell(end);
  if (strictTerrainEndpoints && (cellIsBlocked(grid[rawStart[0]][rawStart[1]]) || cellIsBlocked(grid[rawGoal[0]][rawGoal[1]]))) {
    throw new Error("The start or destination lies on terrain above the configured slope limit.");
  }
  const sCell = nearestFreeCell(grid, rawStart);
  const gCell = nearestFreeCell(grid, rawGoal);
  if (!sCell || !gCell) throw new Error("The start or destination is inside a blocked semantic zone or too close to it.");
  const path = astarGrid(grid, sCell, gCell);
  if (!path.length) throw new Error("No local A* route satisfies the semantic and terrain constraints.");
  return path.map(([r, c]) => {
    const xy = cellCenter(r, c);
    return xyToLL(xy[0], xy[1], context.origin);
  });
}
function localAstarAroundBuildings(start, end, buildings, bufferM, preferredResolution = 3, semanticCosts = false, planningBbox = null) {
  const context = buildLocalSemanticGrid(start, end, buildings, bufferM, preferredResolution, semanticCosts, planningBbox);
  return solveLocalGrid(context, start, end, false);
}
function currentTerrainSettings() {
  const mode = safeVal("terrainMode", "off");
  const preferredSlopePct = Math.max(0.1, safeNum("terrainPreferredSlope", 5));
  const maxSlopePct = Math.max(0.1, safeNum("terrainMaxSlope", 20));
  const costMultiplier = Math.max(0, safeNum("terrainCostMultiplier", 20));
  if (mode !== "off" && maxSlopePct < preferredSlopePct) {
    throw new Error("Blocked slope must be greater than or equal to preferred slope.");
  }
  return {
    mode,
    preferredSlopePct,
    maxSlopePct,
    costMultiplier,
    showLayer: safeVal("terrainShowLayer", "yes") === "yes"
  };
}
async function loadElevationGrid(context, settings, runtime = null) {
  // Request the exact grid footprint rather than the route bbox. Because the
  // grid dimensions use ceil(), its final cell can extend slightly past that
  // bbox; these bounds keep server samples aligned with cellCenter().
  const gridSouthwest = xyToLL(context.minXY[0], context.minXY[1], context.origin);
  const gridNortheast = xyToLL(
    context.minXY[0] + context.cols * context.resolution,
    context.minXY[1] + context.rows * context.resolution,
    context.origin
  );
  const query = new URLSearchParams({
    south: gridSouthwest[0],
    west: gridSouthwest[1],
    north: gridNortheast[0],
    east: gridNortheast[1],
    rows: context.rows,
    cols: context.cols
  });
  const cacheKey = query.toString();
  const cached = runtime?.elevationGrids.get(cacheKey);
  if (cached) return {...cached, cacheKey, reused: true};
  const response = await fetchWithTimeout(`/api/dem/grid?${query}`, {}, 30000, "DEM grid");
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || `DEM server returned HTTP ${response.status}.`);
  if (payload.schema !== "geozigzag-dem-grid-v1" || !Array.isArray(payload.elevations_m) || payload.elevations_m.length !== context.rows) {
    throw new Error("DEM server returned an invalid grid.");
  }
  if (payload.elevations_m.some(row => !Array.isArray(row) || row.length !== context.cols)) {
    throw new Error("DEM grid dimensions do not match the local costmap.");
  }
  const loaded = {elevations: payload.elevations_m, source: payload.source?.dataset || payload.source?.type || "terrain server"};
  runtime?.elevationGrids.set(cacheKey, loaded);
  return {...loaded, cacheKey, reused: false};
}
function applyElevationGrid(context, elevations, settings, source) {
  const cells = [];
  let blockedCells = 0, maxSlopePct = 0, maximumPenalty = 0;
  for (let row = 0; row < context.rows; row++) {
    const south = Math.max(0, row - 1), north = Math.min(context.rows - 1, row + 1);
    const ySpan = Math.max(context.resolution, (north - south) * context.resolution);
    for (let col = 0; col < context.cols; col++) {
      const west = Math.max(0, col - 1), east = Math.min(context.cols - 1, col + 1);
      const xSpan = Math.max(context.resolution, (east - west) * context.resolution);
      const dzdx = (Number(elevations[row][east]) - Number(elevations[row][west])) / xSpan;
      const dzdy = (Number(elevations[north][col]) - Number(elevations[south][col])) / ySpan;
      const slopePct = 100 * Math.hypot(dzdx, dzdy);
      if (!Number.isFinite(slopePct)) throw new Error(`DEM produced an invalid slope at cell ${row},${col}.`);
      const blocked = slopePct > settings.maxSlopePct;
      const penalty = settings.costMultiplier * (slopePct / settings.preferredSlopePct) ** 2;
      maxSlopePct = Math.max(maxSlopePct, slopePct);
      maximumPenalty = Math.max(maximumPenalty, penalty);
      if (blocked) {
        context.grid[row][col] = Infinity;
        blockedCells += 1;
      } else if (!cellIsBlocked(context.grid[row][col])) {
        context.grid[row][col] += penalty;
      }
      cells.push({row, col, blocked, penalty, slopePct});
    }
  }
  return {cells, blockedCells, cellCount: context.rows * context.cols, maxSlopePct, maximumPenalty, source};
}
function terrainRouteProfile(context, elevations, points) {
  const samples = points.map(point => {
    const [row, col] = context.llToCell(point);
    const elevationM = Number(elevations[row][col]);
    if (!Number.isFinite(elevationM)) throw new Error("DEM produced an invalid route elevation sample.");
    return {point, elevationM};
  });
  let distanceM = 0, ascentM = 0, descentM = 0, maxSlopePct = 0, absoluteRiseM = 0;
  for (let index = 1; index < samples.length; index++) {
    const horizontalM = distanceLatLng(samples[index - 1].point, samples[index].point);
    if (horizontalM < 0.25) continue;
    const riseM = samples[index].elevationM - samples[index - 1].elevationM;
    distanceM += horizontalM;
    absoluteRiseM += Math.abs(riseM);
    if (riseM > 0) ascentM += riseM;
    else descentM += -riseM;
    maxSlopePct = Math.max(maxSlopePct, 100 * Math.abs(riseM) / horizontalM);
  }
  const elevationsM = samples.map(sample => sample.elevationM);
  return {
    distanceM,
    ascentM,
    descentM,
    meanAbsoluteSlopePct: distanceM > 0 ? 100 * absoluteRiseM / distanceM : 0,
    maxSlopePct,
    minElevationM: Math.min(...elevationsM),
    maxElevationM: Math.max(...elevationsM)
  };
}
function renderTerrainCostCells(context, terrain, settings, runtime = null, cacheKey = null) {
  if (!settings.showLayer) return;
  if (cacheKey && runtime?.renderedGridKeys.has(cacheKey)) return;
  if (cacheKey) runtime?.renderedGridKeys.add(cacheKey);
  const limit = 2200;
  const stride = Math.max(1, Math.ceil(Math.sqrt(terrain.cellCount / limit)));
  for (let row = 0; row < context.rows; row += stride) {
    for (let col = 0; col < context.cols; col += stride) {
      const lastRow = Math.min(context.rows - 1, row + stride - 1);
      const lastCol = Math.min(context.cols - 1, col + stride - 1);
      let displayedSlopePct = 0, blocked = false;
      for (let sampleRow = row; sampleRow <= lastRow; sampleRow++) {
        for (let sampleCol = col; sampleCol <= lastCol; sampleCol++) {
          const sample = terrain.cells[sampleRow * context.cols + sampleCol];
          displayedSlopePct = Math.max(displayedSlopePct, sample.slopePct);
          blocked = blocked || sample.blocked;
        }
      }
      const [minX, minY] = context.cellCenter(row, col);
      const [maxX, maxY] = context.cellCenter(lastRow, lastCol);
      const half = context.resolution * 0.5;
      const southwest = xyToLL(minX - half, minY - half, context.origin);
      const northeast = xyToLL(maxX + half, maxY + half, context.origin);
      const lowLimit = settings.preferredSlopePct / 2;
      const fillColor = blocked
        ? "#d62828"
        : displayedSlopePct <= lowLimit
          ? "#2a9d8f"
          : displayedSlopePct <= settings.preferredSlopePct
            ? "#f2c94c"
            : "#f4a261";
      const fillOpacity = blocked
        ? 0.52
        : displayedSlopePct <= lowLimit
          ? 0.16
          : displayedSlopePct <= settings.preferredSlopePct
            ? 0.27
            : 0.4;
      L.rectangle([southwest, northeast], {
        pane: "terrainSlopePane",
        renderer: terrainSlopeRenderer,
        stroke: false,
        fillColor,
        fillOpacity,
        interactive: true
      })
        .bindTooltip(`DEM slope: up to ${displayedSlopePct.toFixed(1)}%${blocked ? " · blocked" : ""}`, {sticky: true})
        .addTo(terrainCostLayer);
    }
  }
}
async function localAstarWithTerrain(start, end, buildings, bufferM, preferredResolution, settings, planningBbox = null, runtime = null) {
  const context = buildLocalSemanticGrid(start, end, buildings, bufferM, preferredResolution, true, planningBbox);
  const semanticStart = context.llToCell(start), semanticGoal = context.llToCell(end);
  if (cellIsBlocked(context.grid[semanticStart[0]][semanticStart[1]]) || cellIsBlocked(context.grid[semanticGoal[0]][semanticGoal[1]])) {
    throw new Error("The start or destination occupies a semantic obstacle cell. Move the waypoint or reduce the configured safety margin.");
  }
  await loadingStep("Loading real DEM costmap…");
  const loaded = await loadElevationGrid(context, settings, runtime);
  const terrain = applyElevationGrid(context, loaded.elevations, settings, loaded.source);
  terrain.gridKey = loaded.cacheKey;
  terrain.reusedElevationGrid = loaded.reused;
  renderTerrainCostCells(context, terrain, settings, runtime, loaded.cacheKey);
  const points = solveLocalGrid(context, start, end, true);
  terrain.routeProfile = terrainRouteProfile(context, loaded.elevations, [start, ...points, end]);
  return {points, terrain};
}
async function generateOsmRoute(ids, interval) {
  const preset = safeVal("osmSafetyPreset", "quick");
  const profile = safeVal("osmProfile", "foot") || "foot";

  let endpointMode = safeVal("osmEndpointMode", "snap") || "snap";
  let maxSnap = Math.max(1, safeNum("osmMaxSnap", 50));
  let userAvoidBuildings = safeVal("osmAvoidBuildings", "no") === "yes";
  let buildingBuffer = Math.max(0, safeNum("osmBuildingBuffer", 2));
  let useAlternatives = safeVal("osmAlternatives", "no") === "yes";
  let useAstarFallback = safeVal("osmAstarFallback", "no") === "yes";
  let unsafeFallback = safeVal("osmUnsafeFallback", "warn") !== "block";
  let validationMode = safeVal("osmValidationMode", "soft") || "soft";

  // Presets override hidden values. Quick mode never activates forbidden-zone
  // validation or local A* even if a hidden selector kept an older value.
  if (preset === "quick") {
    endpointMode = "snap";
    userAvoidBuildings = false;
    useAlternatives = false;
    useAstarFallback = false;
    unsafeFallback = true;
    validationMode = "soft";
  } else if (preset === "balanced") {
    // Balanced mode keeps exact endpoints and manual forbidden-zone checks, but
    // leaves OSRM alternatives off by default so the UI stays responsive.
    endpointMode = endpointMode || "exact_avoid";
    unsafeFallback = true;
    validationMode = validationMode || "soft";
  } else if (preset === "strict") {
    endpointMode = endpointMode || "exact_avoid";
    userAvoidBuildings = true;
    useAstarFallback = true;
    unsafeFallback = false;
    validationMode = "strict";
  }

  const endpointExactAvoid = endpointMode === "exact_avoid";
  const avoidBuildings = preset !== "quick" && (userAvoidBuildings || endpointExactAvoid);
  const effectiveBuildingBuffer = validationMode === "strict" ? buildingBuffer : Math.max(0, buildingBuffer * 0.35);
  const targets = ids.map(poiById);
  const fullRoute = [];
  const snapWarnings = [];
  const allForbiddenZones = [];
  let alternativesUsed = 0;
  let astarFallbacks = 0;

  for (let i = 0; i < targets.length - 1; i++) {
    const start = targets[i];
    const end = targets[i + 1];
    await loadingStep(`Querying OSRM ${ids[i]} → ${ids[i + 1]} (${profile})…`);
    const response = await fetchWithTimeout(osmSegmentUrl(start, end, profile, useAlternatives), {}, 12000, `OSRM ${ids[i]} → ${ids[i + 1]}`);
    if (!response.ok) throw new Error(`OSRM HTTP ${response.status}`);
    const data = await response.json();
    if (data.code !== "Ok" || !data.routes || !data.routes.length) throw new Error(data.message || data.code || "OSRM route unavailable");

    const osrmWaypoints = data.waypoints || [];
    const snapStart = Number(osrmWaypoints[0]?.distance || 0);
    const snapEnd = Number(osrmWaypoints[1]?.distance || 0);
    const worstSnap = Math.max(snapStart, snapEnd);

    if (worstSnap > maxSnap) {
      const stopA = ids[i];
      const stopB = ids[i + 1];
      const msg = `OSM had to snap ${stopA} → ${stopB} by up to ${worstSnap.toFixed(1)} m to find a path. Move the waypoint closer to a track/road or increase Max snap.`;
      if (endpointMode === "snap" && !unsafeFallback) throw new Error(msg);
      snapWarnings.push(unsafeFallback ? `${msg} The route continues only for preview, using the OSRM-snapped point.` : msg);
    } else if (worstSnap > 8) {
      snapWarnings.push(`OSM snapped ${ids[i]} → ${ids[i + 1]} by up to ${worstSnap.toFixed(1)} m.`);
    }

    const candidateRoutes = data.routes.map((route, idx) => ({
      idx,
      distance: Number(route.distance || 0),
      points: route.geometry.coordinates.map(coord => [coord[1], coord[0]])
    })).sort((a,b) => a.distance - b.distance);

    let selected = candidateRoutes[0];
    let segmentBuildings = [];
    let selectedHits = [];

    if (!avoidBuildings) {
      await loadingStep(`Fast OSRM route selected for ${ids[i]} → ${ids[i + 1]}.`);
    }

    if (avoidBuildings) {
      const allCandidatePoints = candidateRoutes.flatMap(candidate => candidate.points);
      const bbox = expandBBoxMeters(routeBBox([start, end, ...allCandidatePoints]), Math.max(30, maxSnap + buildingBuffer + 10));
      await loadingStep(`Checking manual forbidden zones for ${ids[i]} → ${ids[i + 1]}…`);
      segmentBuildings = manualHardObstaclesForBbox(bbox);
      if (segmentBuildings.length) snapWarnings.push(`${segmentBuildings.length} manual forbidden zone(s) applied in ${ids[i]} → ${ids[i + 1]}.`);
      if (!segmentBuildings.length && !unsafeFallback) {
        throw new Error(`No manual forbidden zones are available for ${ids[i]} → ${ids[i + 1]}. Draw a zone around the obstacle or change the unsafe route policy.`);
      }
      allForbiddenZones.push(...segmentBuildings);

      await loadingStep(`${useAlternatives ? "Checking alternatives" : "Checking route"} against obstacles for ${ids[i]} → ${ids[i + 1]}…`);
      selected = null;
      for (const candidate of candidateRoutes) {
        const hits = routeBuildingHits(candidate.points, segmentBuildings, effectiveBuildingBuffer);
        if (!hits.length) {
          selected = candidate;
          if (candidate.idx !== 0) alternativesUsed += 1;
          break;
        }
        if (!selectedHits.length) selectedHits = hits;
      }

      if (!selected) {
        const shortest = candidateRoutes[0];
        if (!useAstarFallback) {
          if (!unsafeFallback) throw new Error(`All OSRM alternatives for ${ids[i]} → ${ids[i + 1]} cross or touch forbidden zones (${selectedHits.length} zone(s)). Enable local fallback or move the waypoints.`);
          selected = shortest;
          selected.unsafe = true;
          snapWarnings.push(`All OSRM alternatives for ${ids[i]} → ${ids[i + 1]} touch forbidden zones (${selectedHits.length} zone(s)). The best OSM route is shown with a warning.`);
        } else {
          const astarStart = endpointMode === "snap" && shortest.points.length ? shortest.points[0] : start;
          const astarEnd = endpointMode === "snap" && shortest.points.length ? shortest.points[shortest.points.length - 1] : end;
          try {
            await loadingStep(`Calculating local A* fallback for ${ids[i]} → ${ids[i + 1]}…`);
            const fallbackPoints = localAstarAroundBuildings(astarStart, astarEnd, segmentBuildings, effectiveBuildingBuffer, Math.max(2, interval / 2));
            selected = {idx: -1, distance: distancePoints(fallbackPoints), points: fallbackPoints};
            astarFallbacks += 1;
            snapWarnings.push(`Local A* fallback used in ${ids[i]} → ${ids[i + 1]} because OSRM crossed a forbidden zone.`);
          } catch (err) {
            if (!unsafeFallback) throw err;
            selected = shortest;
            selected.unsafe = true;
            snapWarnings.push(`No fully safe alternative was found in ${ids[i]} → ${ids[i + 1]} (${err.message || err}). The best OSM route is shown with a warning; review it before exporting or using it on the robot.`);
          }
        }
      }
    }

    const connectedSegment = connectSelectedRouteToTargets(
      start, selected.points, end, interval, endpointMode, segmentBuildings, effectiveBuildingBuffer, `${ids[i]} → ${ids[i + 1]}`, snapWarnings, unsafeFallback
    );
    appendRoutePoints(fullRoute, connectedSegment);
  }

  const finalZones = uniqueBuildings(allForbiddenZones);

  const details = [];
  if (avoidBuildings) details.push(`${finalZones.length} manual forbidden zone(s) checked`);
  if (alternativesUsed) details.push(`${alternativesUsed} OSRM alternative(s) used`);
  if (astarFallbacks) details.push(`${astarFallbacks} local A* fallback(s)`);
  if (endpointMode === "exact_direct") details.push("exact endpoints with direct bridge");
  if (endpointMode === "exact_avoid") details.push("exact endpoints with validation/local A*");
  if (details.length) snapWarnings.push(details.join("; ") + ".");

  return {points: fullRoute, distance_m: distancePoints(fullRoute), profile, endpointMode, warnings: snapWarnings, zones: finalZones.length, alternativesUsed, astarFallbacks, quickMode: !avoidBuildings};
}
async function localCostSegment(a, b, interval, startType, endType, semanticZones, terrainSettings, safetyBufferM, planningBbox, terrainRuntime = null) {
  validateHardObstacleEndpoints(a, b, semanticZones, safetyBufferM);
  if (terrainSettings.mode !== "off") {
    const planned = await localAstarWithTerrain(
      a,
      b,
      semanticZones,
      safetyBufferM,
      Math.max(1.5, Math.min(2, interval / 2)),
      terrainSettings,
      planningBbox,
      terrainRuntime
    );
    return {
      points: interpolateRoute([a, ...planned.points, b], interval),
      terrain: planned.terrain
    };
  }
  if (semanticZones.length) {
    const astar = localAstarAroundBuildings(
      a,
      b,
      semanticZones,
      safetyBufferM,
      Math.max(1.5, Math.min(2, interval / 2)),
      true,
      planningBbox
    );
    return {points: interpolateRoute([a, ...astar, b], interval), terrain: null};
  }
  const direct = interpolateRoute([a, b], interval);
  const cost = ((landcoverCost[startType] || 10) + (landcoverCost[endType] || 10)) / 2;
  if (cost < 30) return {points: direct, terrain: null};
  const origin = a;
  const xy = llToXY(b[0], b[1], origin);
  const length = Math.hypot(xy[0], xy[1]);
  const normal = length > 0 ? [-xy[1]/length, xy[0]/length] : [0, 0];
  const detour = Math.min(30, Math.max(5, length * 0.18));
  const mid = xyToLL(xy[0] * .5 + normal[0] * detour, xy[1] * .5 + normal[1] * detour, origin);
  return {points: interpolateRoute([a, mid, b], interval), terrain: null};
}
async function generateCostRoute(ids, interval) {
  const route = [];
  const usedZoneIds = new Set();
  const terrainSettings = currentTerrainSettings();
  const safetyBufferM = Math.max(0.5, Math.min(25, safeNum("semanticSafetyBuffer", 2)));
  const terrainRuntime = {elevationGrids: new Map(), renderedGridKeys: new Set()};
  const terrainSummary = {
    segments: 0,
    blockedCells: 0,
    cellCount: 0,
    maxSlopePct: 0,
    routeDistanceM: 0,
    routeAscentM: 0,
    routeDescentM: 0,
    routeAbsoluteRiseM: 0,
    routeMaxSlopePct: 0,
    routeMinElevationM: Infinity,
    routeMaxElevationM: -Infinity,
    preferredSlopePct: terrainSettings.preferredSlopePct,
    blockedAboveSlopePct: terrainSettings.maxSlopePct,
    costMultiplier: terrainSettings.costMultiplier,
    gridKeys: new Set(),
    reusedElevationGrids: 0,
    sources: new Set()
  };
  terrainCostLayer.clearLayers();
  if (terrainSettings.mode === "off" && $("terrainStatus")) {
    $("terrainStatus").classList.remove("warn");
    $("terrainStatus").textContent = "Elevation layer disabled for this route.";
  }
  for (let i = 0; i < ids.length - 1; i++) {
    const aId = ids[i], bId = ids[i + 1];
    const a = poiById(aId), b = poiById(bId);
    const at = featureById(aId).properties.landcover;
    const bt = featureById(bId).properties.landcover;
    const searchBufferM = Math.max(50, Math.min(1000, safeNum("publicRouteBuffer", 100)));
    const searchBox = expandBBoxMeters(routeBBox([a, b]), searchBufferM);
    const planningBbox = publicSemanticCoverageBbox || searchBox;
    const zones = semanticZonesForBbox(planningBbox);
    zones.forEach(zone => usedZoneIds.add(zone.id));
    const segment = await localCostSegment(a, b, interval, at, bt, zones, terrainSettings, safetyBufferM, planningBbox, terrainRuntime);
    appendRoutePoints(route, segment.points);
    if (segment.terrain) {
      terrainSummary.segments += 1;
      if (!terrainSummary.gridKeys.has(segment.terrain.gridKey)) {
        terrainSummary.gridKeys.add(segment.terrain.gridKey);
        terrainSummary.blockedCells += segment.terrain.blockedCells;
        terrainSummary.cellCount += segment.terrain.cellCount;
      }
      if (segment.terrain.reusedElevationGrid) terrainSummary.reusedElevationGrids += 1;
      terrainSummary.maxSlopePct = Math.max(terrainSummary.maxSlopePct, segment.terrain.maxSlopePct);
      terrainSummary.sources.add(segment.terrain.source);
      const profile = segment.terrain.routeProfile;
      terrainSummary.routeDistanceM += profile.distanceM;
      terrainSummary.routeAscentM += profile.ascentM;
      terrainSummary.routeDescentM += profile.descentM;
      terrainSummary.routeAbsoluteRiseM += profile.ascentM + profile.descentM;
      terrainSummary.routeMaxSlopePct = Math.max(terrainSummary.routeMaxSlopePct, profile.maxSlopePct);
      terrainSummary.routeMinElevationM = Math.min(terrainSummary.routeMinElevationM, profile.minElevationM);
      terrainSummary.routeMaxElevationM = Math.max(terrainSummary.routeMaxElevationM, profile.maxElevationM);
    }
  }
  const routeArea = expandBBoxMeters(routeBBox(route), safetyBufferM + 2);
  const hardObstacles = semanticZonesForBbox(routeArea).filter(
    zone => ["building", "water"].includes(zone.kind)
  );
  const hardObstacleHits = routeBuildingHits(route, hardObstacles, safetyBufferM);
  if (hardObstacleHits.length) {
    throw new Error(
      `Generated route violates the ${safetyBufferM.toFixed(1)} m safety margin around ${hardObstacleHits.length} mapped building/water zone(s); no unsafe route was exported.`
    );
  }
  updateLegend();
  const terrainStatus = $("terrainStatus");
  if (terrainStatus && terrainSummary.segments) {
    terrainStatus.classList.remove("warn");
    terrainStatus.textContent = `Costmap ready: ${terrainSummary.blockedCells}/${terrainSummary.cellCount} steep cells blocked · route max ${terrainSummary.routeMaxSlopePct.toFixed(1)}% · grid max ${terrainSummary.maxSlopePct.toFixed(1)}%.`;
  }
  const {gridKeys, ...terrainMetrics} = terrainSummary;
  return {
    points: route,
    distance_m: distancePoints(route),
    semanticZones: usedZoneIds.size,
    validatedHardObstacles: hardObstacles.length,
    safetyBufferM,
    terrain: terrainSummary.segments ? {
      ...terrainMetrics,
      gridCount: gridKeys.size,
      routeMeanAbsoluteSlopePct: terrainSummary.routeDistanceM > 0
        ? 100 * terrainSummary.routeAbsoluteRiseM / terrainSummary.routeDistanceM
        : 0,
      sources: [...terrainSummary.sources]
    } : null
  };
}
async function generateMissionRoute() {
  if (missionStops.length < 2) throw new Error("Add at least two waypoints to build a mission route.");
  const interval = Math.max(1, Number($("routeSpacing").value || 10));
  const strategy = $("routeStrategy").value;
  let result;
  if (strategy === "osm") {
    const preset = safeVal("osmSafetyPreset", "quick");
    const avoid = preset !== "quick" && (safeVal("osmAvoidBuildings", "no") === "yes" || safeVal("osmEndpointMode", "snap") === "exact_avoid");
    await loadingStep(avoid ? "Calculating OSM route with obstacle checks…" : "Calculating fast OSM route…");
    result = await generateOsmRoute(missionStops, interval);
  }
  else if (strategy === "cost") {
    const publicData = await refreshMissionPublicSemanticZones();
    await loadingStep("Calculating local cost route…");
    result = await generateCostRoute(missionStops, interval);
    result.publicData = publicData;
  } else {
    await loadingStep("Interpolating direct route…");
    const points = interpolateRoute(missionStops.map(poiById), interval);
    result = {points, distance_m: distancePoints(points)};
  }
  const modeText = strategy === "osm"
    ? (result.endpointMode === "exact_avoid" ? "osm exact + A*" : result.endpointMode === "exact_direct" ? "osm exact-direct" : "osm snap")
    : strategy;
  const details = [`Mission route generated with ${modeText}.`];
  if (strategy === "cost") {
    if (result.publicData) {
      const counts = result.publicData.counts;
      const sources = result.publicData.sources;
      details.push(
        `Fused public corridor: ${counts.building} buildings, ${counts.water} water, ${counts.forest} forest, ${counts.scrub} scrub.`
      );
      details.push(`Source provenance: ${sources.catastro} Catastro polygon(s), ${sources.osm} complementary OSM polygon(s).`);
      details.push(
        `Vegetation targets: ${result.publicData.waypointCount} forest/scrub waypoint(s) created, ${result.publicData.autoWaypointCount} inserted automatically into the mission.`
      );
      const waypointSummary = result.publicData.waypointSummary;
      if (waypointSummary?.rejectedUnsafe || waypointSummary?.rejectedDuplicate) {
        details.push(
          `Vegetation target filtering: ${waypointSummary.rejectedUnsafe} unsafe center(s) rejected, ${waypointSummary.rejectedDuplicate} nearby duplicate(s) skipped.`
        );
      }
      for (const warning of result.publicData.metadata?.warnings || []) details.push(warning);
    }
    details.push(
      `Hard-obstacle validation: ${result.safetyBufferM.toFixed(1)} m clearance across ${result.validatedHardObstacles} mapped building/water zone(s).`
    );
    details.push(
      result.semanticZones
        ? `${result.semanticZones} semantic zone(s) included in the local costmap.`
        : "No semantic zones intersected the local planning area."
    );
    if (result.terrain) {
      details.push(
        `DEM costmap: ${result.terrain.segments} segment(s), ${result.terrain.blockedCells}/${result.terrain.cellCount} blocked cells, grid maximum ${result.terrain.maxSlopePct.toFixed(1)}%; preferred ${result.terrain.preferredSlopePct.toFixed(1)}%, blocked above ${result.terrain.blockedAboveSlopePct.toFixed(1)}%, cost multiplier ${result.terrain.costMultiplier.toFixed(1)}.`
      );
      details.push(
        `Route terrain profile: ${result.terrain.routeAscentM.toFixed(1)} m ascent, ${result.terrain.routeDescentM.toFixed(1)} m descent, mean absolute slope ${result.terrain.routeMeanAbsoluteSlopePct.toFixed(1)}%, maximum ${result.terrain.routeMaxSlopePct.toFixed(1)}%, elevation ${result.terrain.routeMinElevationM.toFixed(1)}–${result.terrain.routeMaxElevationM.toFixed(1)} m.`
      );
      details.push(`DEM source: ${result.terrain.sources.join(", ")}.`);
    }
  }
  if (strategy === "osm" && result.quickMode) details.push("Fast OSM mode: snapped endpoints, no forbidden-zone validation.");
  if (result.warnings && result.warnings.length) details.push(...result.warnings);
  const warningLines = (result.warnings || []).filter(line => !/manual forbidden zone\(s\) applied|manual forbidden zone\(s\) checked|exact endpoints/i.test(line));
  const hasWarnings = warningLines.length > 0;
  const summary = hasWarnings
    ? "Route generated with warnings. Some waypoints were snapped or fallback routing was used. Open route details for more information."
    : result.zones
      ? `Route generated successfully while avoiding ${result.zones} blocked semantic zone${result.zones === 1 ? "" : "s"}.`
      : result.terrain && result.publicData
        ? `Route generated with Catastro + OSM + DEM costs; ${result.publicData.counts.building + result.publicData.counts.water} mapped hard obstacle(s) and ${result.terrain.blockedCells} steep cells were blocked.`
      : result.terrain
        ? `Route generated successfully with semantic and DEM terrain costs; ${result.terrain.blockedCells} steep cells were blocked.`
      : result.semanticZones
        ? `Route generated successfully with ${result.semanticZones} semantic zone${result.semanticZones === 1 ? "" : "s"} in the costmap.`
      : "Route generated successfully.";
  const displayedMode = strategy === "cost" && result.terrain ? "cost + DEM" : modeText;
  return {waypoints: toWaypoints(result.points), rows: displayedMode, strategy, message: summary, details, statusClass: hasWarnings ? "warn" : "ok", detailTitle: "Route details"};
}
function landColor(type) {
  return {
    pastizal:"#3a7d44",
    arbustivo:"#b65d13",
    matorral:"#734a9d",
    forest:"#3a7d44",
    scrub:"#8a6d3b",
    water:"#2f80ed",
    custom:"#17202a"
  }[type] || "#555";
}
function routeColor(strategy) {
  return {osm:"#087f7a", cost:"#b65d13", direct:"#1764aa"}[strategy] || "#1764aa";
}
function renderPoiPicker() {
  const grid = $("poiGrid");
  grid.innerHTML = poiGeojson.features.map(f => {
    const selected = missionStops.includes(f.id);
    const type = f.properties.landcover || "unknown";
    const name = f.properties.name || f.id;
    return `<button type="button" class="poi-card ${selected ? "selected" : ""}" data-id="${f.id}">
      <span class="poi-dot" style="background:${landColor(type)}"></span>
      <span class="poi-main"><span class="poi-name">${escapeHtml(name)}</span><span class="poi-type">${escapeHtml(type)}</span></span>
      <span class="poi-add">+</span>
    </button>`;
  }).join("");
}
function renderMissionList() {
  const root = $("missionList");
  if (!missionStops.length) {
    root.innerHTML = '<div class="empty-state">No waypoints selected. Open the picker and add points.</div>';
    return;
  }
  root.innerHTML = missionStops.map((id, i) => {
    const label = poiLabel(id);
    return `<div class="mission-step">
      <span class="mission-index">${i+1}</span>
      <span><strong>${escapeHtml(label.id)}</strong><br><small style="color:var(--muted)">${escapeHtml(label.landcover)}</small></span>
      <span class="mission-controls">
        <button class="icon-btn" type="button" data-action="up" data-index="${i}" title="Up">↑</button>
        <button class="icon-btn" type="button" data-action="down" data-index="${i}" title="Down">↓</button>
        <button class="icon-btn" type="button" data-action="remove" data-index="${i}" title="Remove">×</button>
      </span>
    </div>`;
  }).join("");
}
function addMissionStop(id) {
  missionStops.push(id);
  renderPoiPicker();
  renderMissionList();
  if (appMode === "mission") scheduleGenerate("Waypoint added.");
}
function removeMissionStop(index) {
  missionStops.splice(index, 1);
  renderPoiPicker();
  renderMissionList();
  if (appMode === "mission") scheduleGenerate("Waypoint removed.");
}
function moveMissionStop(index, delta) {
  const next = index + delta;
  if (next < 0 || next >= missionStops.length) return;
  [missionStops[index], missionStops[next]] = [missionStops[next], missionStops[index]];
  renderPoiPicker();
  renderMissionList();
  if (appMode === "mission") scheduleGenerate("Route order changed.");
}
function reverseMissionStops() {
  missionStops.reverse();
  renderPoiPicker();
  renderMissionList();
  if (appMode === "mission") scheduleGenerate("Route order changed.");
}
function clearMissionStops() {
  missionStops = [];
  renderPoiPicker();
  renderMissionList();
  if (appMode === "mission") renderEmptyMission("Mission route cleared.");
}
function openWaypointCreatePopup(latlng) {
  if (appMode !== "mission") return;
  const form = document.createElement("form");
  form.className = "waypoint-form";
  form.innerHTML = `<h3>New waypoint</h3>
    <label>Name<input name="name" type="text" placeholder="inspection_1" autocomplete="off" required></label>
    <label>Type<select name="landcover"><option value="pastizal">pastizal</option><option value="arbustivo">arbustivo</option><option value="matorral">matorral</option><option value="water">water</option><option value="custom">custom</option></select></label>
    <label class="check-row"><input name="useNow" type="checkbox" checked>Use in route now</label>
    <button type="submit">Add waypoint</button>`;
  form.addEventListener("submit", event => {
    event.preventDefault();
    const data = new FormData(form);
    const id = addCustomWaypoint(data.get("name"), data.get("landcover"), latlng.lat, latlng.lng);
    renderPoiPicker();
    if (data.get("useNow")) addMissionStop(id);
    map.closePopup();
  });
  L.popup({maxWidth:260}).setLatLng(latlng).setContent(form).openOn(map);
  setTimeout(() => form.elements.name.focus(), 60);
}
function renderMissionMarkers() {
  missionLayer.clearLayers();
  poiGeojson.features.forEach(f => {
    const type = f.properties.landcover || "unknown";
    const name = f.properties.name || f.id;
    const marker = L.circleMarker([f.geometry.coordinates[1], f.geometry.coordinates[0]], {
      radius: 7, color: "#fff", weight: 2, fillColor: landColor(type), fillOpacity: .95
    }).bindTooltip(`${escapeHtml(name)} - ${escapeHtml(type)}`).addTo(missionLayer);
    marker.on("click", event => {
      L.DomEvent.stopPropagation(event);
      addMissionStop(f.id);
    });
  });
}
function setDrawer(open) {
  drawerOpen = Boolean(open);
  const isOpen = appMode === "mission" && drawerOpen;
  const drawer = $("waypointDrawer");
  const backdrop = $("waypointBackdrop");
  if (drawer) {
    drawer.classList.toggle("hidden", !isOpen);
    drawer.classList.toggle("open", isOpen);
  }
  if (backdrop) backdrop.classList.toggle("hidden", !isOpen);
  document.body.classList.toggle("waypoint-modal-open", isOpen);
  $("drawerToggle").classList.toggle("active", isOpen);
  $("drawerToggle").setAttribute("aria-expanded", isOpen ? "true" : "false");
}
function syncMissionDrawer() {
  const isMission = appMode === "mission";
  $("drawerToggle").classList.toggle("hidden", !isMission);
  if (!isMission) drawerOpen = false;
  setDrawer(isMission && drawerOpen);
}

function renderEmptyMission(message) {
  currentWaypoints = [];
  resultLayer.clearLayers();
  renderMissionMarkers();
  currentBounds = L.latLngBounds(poiGeojson.features.map(f => [f.geometry.coordinates[1], f.geometry.coordinates[0]]));
  updateStats(0, 0, "Mode", "-");
  if (/cleared|add at least/i.test(message)) {
    markDirty(message, false);
  } else {
    failGenerationState(message);
  }
  updateDownloads();
}
function renderCoverageField(polygon) {
  fieldLayer.clearLayers();
  L.polygon(polygon, {color:"#f2c94c", weight:2, fillColor:"#f2c94c", fillOpacity:.12}).addTo(fieldLayer);
}
function renderResult(result) {
  currentPlan = result;
  currentWaypoints = result.waypoints;
  resultLayer.clearLayers();
  missionLayer.clearLayers();
  const latlngs = result.waypoints.map(w => [w.latitude, w.longitude]);
  if (appMode === "coverage") {
    markerLayer.clearLayers();
    if (fieldMode === "rect") createRectMarkers();
    else drawPolyDraft(true);
    renderCoverageField(result.polygon);
    if (result.working_polygon) L.polygon(result.working_polygon, {color:"#3a7d44", weight:1, dashArray:"5 5", fill:false}).addTo(resultLayer);
    (result.row_paths || [latlngs]).forEach(path => L.polyline(path, {color:"#3a7d44", weight:4, opacity:.95}).addTo(resultLayer));
    (result.headland_paths || []).forEach(path => L.polyline(path, {color:"#d97706", weight:4, opacity:.95}).addTo(resultLayer));
  } else {
    renderMissionMarkers();
    L.polyline(latlngs, {color: routeColor(result.strategy), weight:4, opacity:.95}).addTo(resultLayer);
  }
  latlngs.filter((_,i)=>currentWaypoints[i].phase !== "headland" && i % Math.max(1, Math.floor(latlngs.length / 25)) === 0).forEach((p, idx) => {
    const color = idx === 0 ? "#b65d13" : "#111";
    L.circleMarker(p, {radius:2.8, color:"#fff", weight:1, fillColor:color, fillOpacity:1}).addTo(resultLayer);
  });
  if (latlngs.length) {
    currentBounds = L.latLngBounds(latlngs.concat(appMode === "coverage" ? result.polygon : []));
    fitMap();
    setTimeout(fitMap, 160);
  }
  const d = result.distance_m || distanceRoute(result.waypoints);
  updateStats(result.waypoints.length, d, appMode === "coverage" ? "Rows" : "Mode", result.rows);
  const message = result.message || `Generated ${result.waypoints.length} waypoints.`;
  const statusClass = result.statusClass || (/warning|unsafe|review|snapped|fallback/i.test(message) ? "warn" : "ok");
  setStatus(message, statusClass);
  setRouteDetails(result.details || [], result.detailTitle || "Route details");
  updateDownloads();
  finishGenerationState({...result, distance_m: d}, statusClass);
  updateLegend();
}
function updateStats(points, distance, label, rows) {
  $("statPoints").textContent = String(points);
  $("statDistance").textContent = typeof distance === "number" ? formatDistance(distance) : String(distance);
  $("statRowsLabel").textContent = label;
  $("statRows").textContent = String(rows);
}
function fitMap() {
  if (currentBounds && currentBounds.isValid()) {
    map.invalidateSize(false);
    map.fitBounds(currentBounds.pad(.18), {animate:false, maxZoom: appMode === "coverage" ? 21 : 17});
  }
}
function updateDownloads() {
  const rows = currentWaypoints.map(w => ({...w, ...yawToQuaternion(w.yaw)}));
  const hasActions = rows.some(w => w.action);
  const fields = ["latitude", "longitude", "yaw", "qx", "qy", "qz", "qw", ...(hasActions ? ["action", "phase"] : [])];
  const csv = [fields.join(","), ...rows.map(w => fields.map(key => typeof w[key] === "number" ? w[key].toFixed(12) : w[key]).join(","))].join("\n") + "\n";
  const yaml = "waypoints:\n" + rows.map(w => {
    let text = `  - latitude: ${w.latitude.toFixed(12)}\n    longitude: ${w.longitude.toFixed(12)}\n    yaw: ${w.yaw.toFixed(12)}\n    orientation:\n`;
    for (const key of ["qx", "qy", "qz", "qw"]) text += `      ${key}: ${w[key].toFixed(12)}\n`;
    if (hasActions) text += `    action: ${w.action}\n    phase: ${w.phase}\n`;
    return text;
  }).join("");
  const missionPoints = [];
  for (const waypoint of rows) {
    const previous = missionPoints[missionPoints.length - 1];
    if (previous &&
      Math.abs(waypoint.latitude - previous.latitude) <= 1e-12 &&
      Math.abs(waypoint.longitude - previous.longitude) <= 1e-12) continue;
    missionPoints.push(waypoint);
  }
  const wplItem = (sequence, current, frame, waypoint) => [
    sequence, current, frame, 16, 0, 0, 0, 0,
    waypoint.latitude.toFixed(12), waypoint.longitude.toFixed(12), 0, 1
  ].join("\t");
  const home = missionPoints[0];
  const ardupilot = missionPoints.length > 1
    ? [
      "QGC WPL 110",
      wplItem(0, 1, 0, home),
      ...missionPoints.map((waypoint, index) => wplItem(index + 1, 0, 3, waypoint))
    ].join("\n") + "\n"
    : "";
  const set = (id, text, name, type) => {
    const blob = new Blob([text], {type});
    const a = $(id);
    if (a.dataset.url) URL.revokeObjectURL(a.dataset.url);
    a.dataset.url = URL.createObjectURL(blob);
    a.href = a.dataset.url;
    a.download = name;
  };
  set("downloadCsv", csv, `${appMode}_${fieldMode}_waypoints.csv`, "text/csv");
  set("downloadYaml", yaml, `${appMode}_${fieldMode}_waypoints.yaml`, "text/yaml");
  set("downloadArduPilot", ardupilot, `${appMode}_${fieldMode}_mission.waypoints`, "text/plain");
  set("downloadPlan", JSON.stringify(currentPlan || {}, null, 2), `${appMode}_plan.json`, "application/json");
  setDownloadsEnabled(currentWaypoints.length > 0 && !routeDirty);
}
async function generate() {
  if (pendingGenerateTimer) {
    clearTimeout(pendingGenerateTimer);
    pendingGenerateTimer = null;
  }
  const token = ++generationToken;
  setGenerateBusy(true);
  generationStartedAtMs = performance.now();
  lastProcessingMs = 0;
  loadingStartedAt = Date.now();
  setTimingState("busy", "Processing: 0 ms");
  await loadingStep(appMode === "coverage" ? "Calculating furrows…" : "Calculating route…");
  try {
    let result;
    if (appMode === "coverage") {
      result = await generateRobotCoverage();
    } else {
      result = await generateMissionRoute();
    }
    if (token === generationToken) {
      lastProcessingMs = generationStartedAtMs ? performance.now() - generationStartedAtMs : 0;
      renderResult(result);
    }
  } catch (err) {
    if (token !== generationToken) return;
    const message = err && err.message ? err.message : String(err);
    if (appMode === "mission") renderEmptyMission(message);
    else failGenerationState(message);
  } finally {
    if (token === generationToken) {
      lastProcessingMs = generationStartedAtMs ? performance.now() - generationStartedAtMs : lastProcessingMs;
      setGenerateBusy(false);
      setLoadingVisible(false);
      generationStartedAtMs = 0;
    }
  }
}
function updateLiveStats() {
  try {
    if (appMode !== "coverage") return;
    syncFurrowControls();
    if (fieldMode === "rect") {
      const c = getCorners();
      const width = (distanceLatLng(c.nw, c.ne) + distanceLatLng(c.sw, c.se)) / 2;
      const height = (distanceLatLng(c.nw, c.sw) + distanceLatLng(c.ne, c.se)) / 2;
      const area = polygonAreaLatLng([c.nw, c.ne, c.se, c.sw]);
      updateStats(currentWaypoints.length || 0, area, "Field", `${Math.max(width, height).toFixed(1)}×${Math.min(width, height).toFixed(1)} m`);
    } else {
      if (polyVertices.length < 2) {
        updateStats(currentWaypoints.length || 0, 0, "Vertices", polyVertices.length);
        return;
      }
      const perimeter = distancePoints(polyVertices.concat(polyVertices.length >= 3 ? [polyVertices[0]] : []));
      updateStats(currentWaypoints.length || 0, polyVertices.length >= 3 ? polygonAreaLatLng(polyVertices) : perimeter, "Vertices", polyVertices.length);
    }
  } catch (e) {}
}
function setMode(mode) {
  if (mode !== "coverage" && isDrawingRect) cancelRectDraw(false);
  appMode = mode;
  markDirty("Planning mode selected.", false);
  $("coverageTab").classList.toggle("active", mode === "coverage");
  $("missionTab").classList.toggle("active", mode === "mission");
  $("coveragePanel").classList.toggle("hidden", mode !== "coverage");
  $("missionPanel").classList.toggle("hidden", mode !== "mission");
  updateVisibleMissionSettings();
  resultLayer.clearLayers();
  missionLayer.clearLayers();
  markerLayer.clearLayers();
  fieldLayer.clearLayers();
  currentWaypoints = [];
  syncBaseView();
  syncMissionDrawer();
  map.off("click", onPolyClick);
  map.off("dblclick", onPolyDblClick);
  if (mode === "coverage") {
    if (fieldMode === "rect") updateRectFromInputs(false);
    else {
      drawPolyDraft(polyVertices.length >= 3);
      map.on("click", onPolyClick);
      map.on("dblclick", onPolyDblClick);
    }
    updateLiveStats();
    if (fieldMode === "rect" && !isDrawingRect) startRectDraw();
  } else {
    renderMissionMarkers();
    renderMissionList();
    if (missionStops.length >= 2) {
      currentWaypoints = [];
      currentBounds = L.latLngBounds(missionStops.map(poiById));
      updateStats(0, 0, "Stops", missionStops.length);
      updateDownloads();
      fitMap();
      setTimeout(fitMap, 160);
      markDirty("Mission route ready.", false);
    } else {
      renderEmptyMission("Add at least two waypoints.");
    }
  }
  updateLegend();
}
function setFieldVisible(inputId, visible) {
  const input = $(inputId);
  if (!input) return;
  const field = input.closest(".field");
  if (field) field.classList.toggle("hidden", !visible);
}
function setGroupVisibleBySelector(selector, visible) {
  document.querySelectorAll(selector).forEach(el => el.classList.toggle("hidden", !visible));
}
function currentOsmSettings() {
  const strategy = safeVal("routeStrategy", "direct");
  const preset = safeVal("osmSafetyPreset", "quick");
  const rawEndpoint = safeVal("osmEndpointMode", "snap");
  const endpoint = preset === "quick" ? "snap" : rawEndpoint;
  const avoidBuildings = preset !== "quick" && safeVal("osmAvoidBuildings", "no") === "yes";
  const usesOsm = strategy === "osm";
  const usesCost = strategy === "cost";
  const exactAvoid = endpoint === "exact_avoid";
  const exactDirect = endpoint === "exact_direct";
  const zonesNeeded = usesOsm && preset !== "quick" && (avoidBuildings || exactAvoid || preset === "strict");
  const advancedNeeded = usesOsm && preset !== "quick";
  const strictMode = usesOsm && preset === "strict";
  const balancedMode = usesOsm && preset === "balanced";
  const quickMode = usesOsm && preset === "quick";
  return {strategy, preset, endpoint, rawEndpoint, usesOsm, usesCost, exactAvoid, exactDirect, zonesNeeded, advancedNeeded, strictMode, balancedMode, quickMode};
}
function updateTerrainUi() {
  const mode = safeVal("terrainMode", "off");
  const section = $("terrainSection");
  if (section) {
    section.querySelectorAll(".terrain-setting").forEach(element => {
      element.classList.toggle("hidden", mode === "off");
    });
  }
  const status = $("terrainStatus");
  if (!status) return;
  status.classList.remove("warn");
  if (mode === "off") status.textContent = "Elevation layer disabled.";
  else status.textContent = "Real DEM selected. Press Check DEM server or Generate route.";
}
async function checkTerrainServer(announce = true) {
  const status = $("terrainStatus");
  if (safeVal("terrainMode", "off") !== "server") {
    updateTerrainUi();
    if (announce) setStatus("Select Real DEM (server) to check terrain data.", "warn");
    return;
  }
  if (status) status.textContent = "Checking terrain server…";
  try {
    const response = await fetchWithTimeout("/api/dem/status", {}, 5000, "DEM status");
    const payload = await response.json().catch(() => ({}));
    if (!response.ok || !payload.available) throw new Error(payload.error || "No DEM is configured on this server.");
    const source = payload.source?.dataset || payload.source?.type || "configured DEM";
    if (status) status.textContent = `DEM ready: ${source}${payload.source?.zoom !== undefined ? ` · zoom ${payload.source.zoom}` : ""}.`;
    if (announce) setStatus("DEM server is ready for local costmap generation.", "ok");
  } catch (error) {
    if (status) {
      status.textContent = error.message || String(error);
      status.classList.add("warn");
    }
    if (announce) setStatus(error.message || String(error), "err");
  }
}
function updateVisibleMissionSettings() {
  const s = currentOsmSettings();
  const isMission = appMode === "mission";
  const osmSafetySection = $("osmSafetySection");
  const obstaclesSection = $("obstaclesSection");
  const mapRoutingPanel = $("mapRoutingPanel");
  const manualObstacleTools = $("manualObstacleTools");
  const terrainSection = $("terrainSection");
  const advancedPanel = $("osmAdvancedPanel");
  if (mapRoutingPanel) mapRoutingPanel.classList.toggle("hidden", !isMission);
  if (osmSafetySection) osmSafetySection.classList.toggle("hidden", !(isMission && s.usesOsm));
  if (obstaclesSection) {
    obstaclesSection.classList.toggle("hidden", !isMission);
    if (isMission) obstaclesSection.open = true;
  }
  if (manualObstacleTools) manualObstacleTools.classList.toggle("hidden", !isMission);
  if (terrainSection) terrainSection.classList.toggle("hidden", !(isMission && s.usesCost));
  if (!(isMission && s.usesCost)) terrainCostLayer.clearLayers();
  if (advancedPanel) {
    advancedPanel.classList.toggle("hidden", !(isMission && s.advancedNeeded));
    if (s.strictMode) advancedPanel.open = true;
    else if (!s.advancedNeeded) advancedPanel.open = false;
  }

  setGroupVisibleBySelector(".osm-setting", isMission && s.usesOsm);
  setFieldVisible("osmSafetyPreset", isMission && s.usesOsm);
  setFieldVisible("osmProfile", isMission && s.usesOsm);
  setFieldVisible("osmEndpointMode", isMission && s.usesOsm);
  setFieldVisible("osmMaxSnap", isMission && s.usesOsm);
  setFieldVisible("osmAvoidBuildings", isMission && s.usesOsm && s.advancedNeeded);
  setFieldVisible("osmAlternatives", isMission && s.usesOsm && s.advancedNeeded);
  setFieldVisible("osmAstarFallback", isMission && s.usesOsm && s.zonesNeeded);
  setFieldVisible("osmBuildingBuffer", isMission && s.usesOsm && s.zonesNeeded);
  setFieldVisible("osmValidationMode", isMission && s.strictMode);
  setFieldVisible("osmUnsafeFallback", isMission && s.usesOsm && s.advancedNeeded);

  const endpointSelect = $("osmEndpointMode");
  if (endpointSelect) {
    if (s.quickMode) endpointSelect.value = "snap";
    endpointSelect.disabled = Boolean(s.quickMode);
    endpointSelect.title = s.quickMode ? "Fast mode uses snapped OSM endpoints." : "";
  }
  const costNote = $("costRouteNote");
  if (costNote) costNote.classList.toggle("hidden", !(isMission && s.usesCost));
  updateTerrainUi();

  updateManualObstacleStatus();
  const hint = $("osmSafetyHint");
  if (hint) {
    hint.classList.toggle("warn", s.strictMode);
    if (s.quickMode) {
      hint.textContent = "Fast mode: OSM route only, snapped endpoints, no zone checks.";
    } else if (s.balancedMode) {
      hint.textContent = "Balanced mode: exact endpoints, manual zone margin and local A* fallback when needed.";
    } else if (s.strictMode) {
      hint.textContent = "Strict mode may be slower: full validation, alternatives and blocking on unsafe routes.";
    } else {
      hint.textContent = "";
    }
  }
  const obstacleHint = $("obstacleHint");
  if (obstacleHint) {
    if (s.strictMode) {
      obstacleHint.textContent = "Strict mode blocks unsafe routes that cross mapped buildings or water.";
    } else if (s.preset === "balanced") {
      obstacleHint.textContent = "Mapped buildings and water are checked during route generation.";
    } else if (s.usesOsm) {
      obstacleHint.textContent = "Fast mode shows semantic zones; building and water avoidance is active in Balanced or Strict.";
    } else {
      obstacleHint.textContent = s.usesCost
        ? "Local A* combines semantic zones with the optional DEM slope layer."
        : "Semantic zones stay saved here; choose Local costmap A* to account for forest.";
    }
  }
  updateLegend();
}

function applyOsmSafetyPreset(triggerGenerate = true) {
  const preset = $("osmSafetyPreset") ? $("osmSafetyPreset").value : "quick";
  const setValue = (id, value) => { const el = $(id); if (el) el.value = value; };
  if (preset === "quick") {
    setValue("osmEndpointMode", "snap");
    setValue("osmAvoidBuildings", "no");
    setValue("osmAlternatives", "no");
    setValue("osmAstarFallback", "no");
    setValue("osmUnsafeFallback", "warn");
    setValue("osmValidationMode", "soft");
  } else if (preset === "balanced") {
    setValue("osmEndpointMode", "exact_avoid");
    setValue("osmAvoidBuildings", "yes");
    setValue("osmAlternatives", "no");
    setValue("osmAstarFallback", "yes");
    setValue("osmUnsafeFallback", "warn");
    setValue("osmValidationMode", "soft");
  } else {
    setValue("osmEndpointMode", "exact_avoid");
    setValue("osmAvoidBuildings", "yes");
    setValue("osmAlternatives", "yes");
    setValue("osmAstarFallback", "yes");
    setValue("osmUnsafeFallback", "block");
    setValue("osmValidationMode", "strict");
  }
  updateVisibleMissionSettings();
  if (triggerGenerate && appMode === "mission") scheduleGenerate(`OSM mode changed to ${preset}.`, 600);
}
function updateSpacingLabels() {
  const row = safeNum("rowSpacing", 0.75);
  const point = safeNum("pointSpacing", 5);
  if ($("rowSpacingLbl")) $("rowSpacingLbl").textContent = `${row.toFixed(2)} m`;
  if ($("pointSpacingLbl")) $("pointSpacingLbl").textContent = `${point.toFixed(1)} m`;
}
function bindEvents() {
  $("coverageTab").addEventListener("click", () => setMode("coverage"));
  $("missionTab").addEventListener("click", () => setMode("mission"));
  $("gpsViewBtn").addEventListener("click", () => setBaseView("gps"));
  $("mapViewBtn").addEventListener("click", () => setBaseView("map"));
  $("generateBtn").addEventListener("click", () => generate());
  $("cancelGenerateBtn").addEventListener("click", () => {
    generationToken += 1;
    setGenerateBusy(false);
    setLoadingVisible(false);
    markDirty("Preview cancelled.", false);
  });
  $("fitBtn").addEventListener("click", fitMap);
  $("fieldRectTab").addEventListener("click", () => switchFieldMode("rect"));
  $("fieldPolyTab").addEventListener("click", () => switchFieldMode("poly"));
  $("drawRectBtn").addEventListener("click", startRectDraw);
  $("perfectRectBtn").addEventListener("click", perfectRect);
  $("rectSquareBtn").addEventListener("click", () => squareCurrentRectangle("Rectangle squared."));
  $("rectNorthBtn").addEventListener("click", () => orientCurrentRectangle(0, "Rectangle aligned North-South."));
  $("rectEastBtn").addEventListener("click", () => orientCurrentRectangle(90, "Rectangle aligned East-West."));
  $("rectAlignFurrowsBtn").addEventListener("click", () => {
    const poly = currentCoveragePolygon();
    if (!poly) return;
    orientCurrentRectangle(getEffectiveFurrowBearing(poly, polygonOrigin(poly)), "Rectangle aligned to furrows.");
  });
  $("rectResetBtn").addEventListener("click", () => orientCurrentRectangle(0, "Rectangle angle reset."));
  $("rectApplyBtn").addEventListener("click", applyRectToolChanges);
  $("rectCancelBtn").addEventListener("click", cancelRectToolChanges);
  const updateRectAngle = value => {
    beginRectEdit();
    setRectBearingControl(value);
    applyRectFromSliders();
  };
  $("rectBearing").addEventListener("input", () => updateRectAngle($("rectBearing").value));
  $("rectBearingNumber").addEventListener("change", () => updateRectAngle($("rectBearingNumber").value));
  $("clearPolyBtn").addEventListener("click", clearPoly);
  $("closePolyBtn").addEventListener("click", closePoly);
  ["nw","ne","sw","se"].forEach(k => {
    $("sc-" + k).addEventListener("click", () => setStartCorner(k));
    $(k + "_lat").addEventListener("change", () => { updateRectFromInputs(true); updateLiveStats(); if (appMode === "coverage") scheduleGenerate(`${k.toUpperCase()} coordinate changed.`); });
    $(k + "_lon").addEventListener("change", () => { updateRectFromInputs(true); updateLiveStats(); if (appMode === "coverage") scheduleGenerate(`${k.toUpperCase()} coordinate changed.`); });
  });
  ["rectCenterNS", "rectCenterEW", "rectHeight", "rectWidth"].forEach(id => {
    $(id).addEventListener("input", () => {
      $(id + (id === "rectCenterNS" ? "Lbl" : id === "rectCenterEW" ? "Lbl" : "Lbl"));
      $(id + "Lbl").textContent = Number($(id).value).toFixed(1) + " m";
      applyRectFromSliders();
    });
  });
  ["rowSpacing", "pointSpacing"].forEach(id => {
    $(id).addEventListener("input", () => {
      updateSpacingLabels();
      if (appMode === "coverage") scheduleGenerate("Furrow spacing changed.");
    });
  });
  const updateFurrowAngle = value => setFurrowBearing(value, true, "Furrow rotation changed.");
  $("furrowBearing").addEventListener("input", () => updateFurrowAngle($("furrowBearing").value));
  $("furrowBearingNumber").addEventListener("change", () => updateFurrowAngle($("furrowBearingNumber").value));
  $("furrowAutoBtn").addEventListener("click", () => setFurrowBearing(safeNum("furrowBearing", 0), false, "Furrow rotation reset to auto."));
  document.querySelectorAll("[data-furrow-bearing]").forEach(btn => {
    btn.addEventListener("click", () => setFurrowBearing(btn.dataset.furrowBearing, true, "Furrow rotation changed."));
  });
  $("furrowAlignRectBtn").addEventListener("click", () => {
    try { setFurrowBearing(rectMetricsFromCorners().bearing, true, "Furrows aligned to rectangle."); } catch (_) {}
  });
  $("routeStrategy").addEventListener("change", () => { updateVisibleMissionSettings(); if (appMode === "mission") scheduleGenerate("Route strategy changed."); });
  $("routeSpacing").addEventListener("change", () => { if (appMode === "mission") scheduleGenerate("Waypoint spacing changed."); });
  $("osmProfile").addEventListener("change", () => { if (appMode === "mission") scheduleGenerate("OSM profile changed."); });
  if ($("osmSafetyPreset")) $("osmSafetyPreset").addEventListener("change", () => applyOsmSafetyPreset(true));
  for (const id of ["osmEndpointMode", "osmMaxSnap", "osmAvoidBuildings", "osmBuildingBuffer", "osmAlternatives", "osmAstarFallback", "osmUnsafeFallback", "osmValidationMode"]) {
    const el = $(id);
    if (el) el.addEventListener("change", () => { updateVisibleMissionSettings(); if (appMode === "mission") scheduleGenerate("OSM safety setting changed."); });
  }
  if ($("drawManualObstacleBtn")) $("drawManualObstacleBtn").addEventListener("click", startManualObstacleDraw);
  if ($("manualObstacleKind")) $("manualObstacleKind").addEventListener("change", () => {
    if (manualObstacleDrawMode) drawManualObstacles();
  });
  if ($("finishManualObstacleBtn")) $("finishManualObstacleBtn").addEventListener("click", finishManualObstacleDraw);
  if ($("cancelManualObstacleBtn")) $("cancelManualObstacleBtn").addEventListener("click", cancelManualObstacleDraw);
  if ($("clearManualObstacleBtn")) $("clearManualObstacleBtn").addEventListener("click", clearManualObstacles);
  if ($("loadPublicSemanticBtn")) $("loadPublicSemanticBtn").addEventListener("click", loadVisiblePublicSemanticZones);
  if ($("clearPublicSemanticBtn")) $("clearPublicSemanticBtn").addEventListener("click", clearPublicSemanticZones);
  for (const id of ["publicWaypointMinArea", "publicAutoWaypoints", "publicAutoWaypointLimit"]) {
    if ($(id)) $(id).addEventListener("change", () => {
      updatePublicSemanticStatus(rebuildPublicSemanticWaypoints());
      if (appMode === "mission") scheduleGenerate("Automatic vegetation waypoint settings changed.", 500);
    });
  }
  for (const id of ["publicRouteBuffer", "semanticSafetyBuffer"]) {
    if ($(id)) $(id).addEventListener("change", () => {
      if (appMode === "mission") scheduleGenerate("Public-data safety setting changed.", 500);
    });
  }
  if ($("checkTerrainBtn")) $("checkTerrainBtn").addEventListener("click", checkTerrainServer);
  for (const id of ["terrainMode", "terrainPreferredSlope", "terrainMaxSlope", "terrainCostMultiplier", "terrainShowLayer"]) {
    const element = $(id);
    if (element) element.addEventListener("change", () => {
      terrainCostLayer.clearLayers();
      updateTerrainUi();
      updateLegend();
      if (appMode === "mission") scheduleGenerate("Terrain costmap setting changed.", 500);
    });
  }
  if ($("manualObstacleList")) $("manualObstacleList").addEventListener("click", event => {
    const deleteButton = event.target.closest("button[data-zone-delete]");
    if (deleteButton) {
      deleteManualObstacle(Number(deleteButton.dataset.zoneDelete));
      return;
    }
    const editButton = event.target.closest("button[data-zone-edit]");
    if (editButton) editManualObstacle(Number(editButton.dataset.zoneEdit));
  });
  $("reverseMissionBtn").addEventListener("click", reverseMissionStops);
  $("clearMissionBtn").addEventListener("click", clearMissionStops);
  $("drawerToggle").addEventListener("click", () => setDrawer(!drawerOpen));
  $("drawerClose").addEventListener("click", () => setDrawer(false));
  $("waypointBackdrop").addEventListener("click", () => setDrawer(false));
  document.addEventListener("keydown", event => {
    if (event.key === "Escape" && drawerOpen) setDrawer(false);
  });
  $("poiGrid").addEventListener("click", event => {
    const card = event.target.closest(".poi-card");
    if (card) addMissionStop(card.dataset.id);
  });
  $("missionList").addEventListener("click", event => {
    const button = event.target.closest("button[data-action]");
    if (!button) return;
    const index = Number(button.dataset.index);
    if (button.dataset.action === "up") moveMissionStop(index, -1);
    if (button.dataset.action === "down") moveMissionStop(index, 1);
    if (button.dataset.action === "remove") removeMissionStop(index);
  });
  map.on("click", event => {
    if (manualObstacleDrawMode) {
      onManualObstacleMapClick(event.latlng);
      return;
    }
    openWaypointCreatePopup(event.latlng);
  });
}
function init() {
  bindEvents();
  bindRobotControls();
  const params = new URLSearchParams(location.search);
  const strategyParam = params.get("strategy");
  if (["direct", "osm", "cost"].includes(strategyParam)) $("routeStrategy").value = strategyParam;
  const presetParam = params.get("preset");
  if (["quick", "balanced", "strict"].includes(presetParam)) $("osmSafetyPreset").value = presetParam;
  const terrainParam = params.get("terrain");
  if (["off", "server"].includes(terrainParam)) $("terrainMode").value = terrainParam;
  applyOsmSafetyPreset(false);
  syncBaseView();
  renderPoiPicker();
  renderMissionList();
  loadManualObstacles();
  loadSavedPublicSemanticZones();
  updateSpacingLabels();
  updateRectFromInputs(true);
  syncFurrowControls();
  updateLiveStats();
  updateDownloads();
  setInitialReadyState();
  if (params.get("mode") === "coverage") {
    setMode("coverage");
    if (fieldMode === "rect") startRectDraw();
  } else setMode("mission");
  if (params.get("generate") === "1") {
    window.setTimeout(() => generate(), 350);
  }
  if (safeVal("terrainMode", "server") === "server") {
    window.setTimeout(() => checkTerrainServer(false), 50);
  }
}
init();
