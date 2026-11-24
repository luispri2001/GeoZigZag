// Run against a local GeoZigZag server with Node.js and Playwright available.
// Set STUDIO_URL, CHROME_PATH or SCREENSHOT_DIR to override the defaults.
const assert = require("node:assert/strict");
const { mkdir, readFile } = require("node:fs/promises");
const { join } = require("node:path");
const { test } = require("node:test");
const { chromium } = require("playwright");

const studioUrl = process.env.STUDIO_URL || "http://127.0.0.1:8000/";
const screenshotDir = process.env.SCREENSHOT_DIR;
const sizes = [
  { width: 1440, height: 900 },
  { width: 1024, height: 600 },
  { width: 768, height: 1024 },
  { width: 390, height: 844 },
  { width: 320, height: 568 },
];

async function withPage(viewport, run) {
  const browser = await chromium.launch({
    executablePath: process.env.CHROME_PATH,
  });
  try {
    const page = await browser.newPage({
      viewport,
      hasTouch: viewport.width <= 480,
      reducedMotion: "reduce",
      ignoreHTTPSErrors: true,
    });
    const errors = [];
    page.on("pageerror", error => errors.push(error.message));
    await run(page);
    assert.deepEqual(errors, []);
  } finally {
    await browser.close();
  }
}

async function open(page, mode, strategy = "direct") {
  const url = new URL(studioUrl);
  url.search = new URLSearchParams({ mode, strategy, terrain: "off" });
  await page.goto(url.href, { waitUntil: "domcontentloaded" });
  await page.locator(".leaflet-zoom-animated").first().waitFor({ state: "attached" });
}

async function screenshot(page, name) {
  if (!screenshotDir) return;
  await mkdir(screenshotDir, { recursive: true });
  await page.screenshot({ path: join(screenshotDir, name + ".png"), animations: "disabled" });
}

async function mapPoint(page, x, y) {
  const { x: left, y: top, width, height } = await page.locator("#map").boundingBox();
  return [left + width * x, top + height * y];
}

async function change(page, id, value) {
  const input = page.locator("#" + id);
  await input.fill(value);
  await input.press("Tab");
}

async function generate(page) {
  await page.locator("#generateBtn").click();
  await page.locator("#runState:is(.ok, .warn, .err)").waitFor();
  assert.equal(await page.locator("#runState.err").count(), 0, await page.locator("#status").textContent());
  assert.ok(Number(await page.locator("#statPoints").textContent()) > 0);
}

async function download(page, id) {
  const event = page.waitForEvent("download");
  await page.locator("#" + id).click();
  return readFile(await (await event).path(), "utf8");
}

test("map, route controls and accessible inputs fit across both modes", async () => {
  await withPage(sizes[0], async page => {
    for (const viewport of sizes) {
      await page.setViewportSize(viewport);
      for (const mode of ["coverage", "mission"]) {
        await open(page, mode, "cost");
        const issues = await page.evaluate(() => {
          const bounds = selector => document.querySelector(selector).getBoundingClientRect();
          const map = bounds("#map");
          const rail = bounds("#mapControlRail");
          const mapbar = bounds(".mapbar");
          const attribution = bounds(".leaflet-control-attribution");
          const actions = bounds(".map-action-card");
          return {
            horizontalOverflow: document.documentElement.scrollWidth > innerWidth,
            railBlocksMap: rail.top < map.bottom - 1,
            toolbarOutsideMap: mapbar.right > map.right || mapbar.bottom > map.bottom,
            attributionOutsideMap: attribution.bottom > map.bottom + 1,
            actionClipping: [...document.querySelectorAll(".map-action-card button, a.download")]
              .some(el => el.getClientRects().length && el.getBoundingClientRect().right > actions.right + 1),
            unnamedInputs: [...document.querySelectorAll("input, select")]
              .filter(el => el.getClientRects().length && !el.labels.length && !el.hasAttribute("aria-label"))
              .map(el => el.id),
          };
        });
        assert.deepEqual(issues, {
          horizontalOverflow: false,
          railBlocksMap: false,
          toolbarOutsideMap: false,
          attributionOutsideMap: false,
          actionClipping: false,
          unnamedInputs: [],
        }, `${viewport.width} × ${viewport.height} ${mode}`);
        await screenshot(page, `${viewport.width}-${mode}`);
      }
    }
  });
});

test("coverage draws rectangles and polygons and exports the real demo plan", async () => {
  await withPage(sizes[0], async page => {
    await open(page, "coverage");
    assert.equal(await page.locator("#statDistanceLabel").textContent(), "Area");
    assert.match(await page.locator("#statDistance").textContent(), /m²$/);
    const corner = page.locator("#nw_lat");
    const original = await corner.inputValue();
    await page.getByRole("button", { name: "Draw rectangle" }).click();
    await page.mouse.move(...await mapPoint(page, .25, .25));
    await page.mouse.down();
    await page.mouse.move(...await mapPoint(page, .7, .65), { steps: 10 });
    await page.mouse.up();
    assert.notEqual(await corner.inputValue(), original);

    await page.locator("#rectToolsPanel summary").click();
    await change(page, "rectBearingNumber", "25");
    assert.equal(await page.locator("#rectBearingLbl").textContent(), "25°");
    await page.locator("#rectCancelBtn").click();
    assert.notEqual(await page.locator("#rectBearingLbl").textContent(), "25°");

    await page.locator("#fieldPolyTab").click();
    for (const point of [[.25, .25], [.7, .25], [.7, .65], [.25, .65]]) {
      await page.mouse.click(...await mapPoint(page, ...point));
    }
    assert.equal(await page.locator("#polyVList .vrow").count(), 4);
    await page.locator("#closePolyBtn").click();
    assert.equal(await page.locator("#statDistanceLabel").textContent(), "Area");
    await page.locator("#clearPolyBtn").click();
    assert.match(await page.locator("#polyVList").textContent(), /No vertices/);

    await page.locator("#loadCoverageDemo").click();
    await page.locator("#ackermannSettings").waitFor();
    assert.equal(await page.locator("#robotType").inputValue(), "ackermann");
    await generate(page);
    assert.equal(await page.locator("#statDistanceLabel").textContent(), "Distance");
    assert.match(await page.locator("#statDistance").textContent(), / m$/);
    const plan = JSON.parse(await download(page, "downloadPlan"));
    assert.equal(plan.request.robot.kind, "ackermann");
    assert.ok(plan.headland_paths.length > 0);
    assert.match(await download(page, "downloadCsv"), /^latitude,longitude,yaw/);
    assert.match(await download(page, "downloadYaml"), /^waypoints:/);
    assert.match(await download(page, "downloadArduPilot"), /^QGC WPL 110/);
    await screenshot(page, "coverage-generated");

    const spacing = page.getByRole("slider", { name: /Row spacing/ });
    const priorSpacing = await spacing.inputValue();
    await spacing.press("ArrowRight");
    assert.notEqual(await spacing.inputValue(), priorSpacing);
    assert.equal(await page.locator("#downloadPlan").getAttribute("href"), null);
    assert.equal(await page.locator("#downloadPlan").getAttribute("aria-disabled"), "true");
    assert.match(await page.locator("#runState").textContent(), /Pending changes/);
  });
});

test("mobile mission supports map controls, keyboard waypoints and semantic zone editing", async () => {
  await withPage(sizes[3], async page => {
    await open(page, "mission");
    await page.getByRole("button", { name: "Street map view" }).click();
    assert.equal(await page.locator("#mapViewBtn").getAttribute("aria-pressed"), "true");
    const zoom = page.locator(".leaflet-control-zoom-in");
    await zoom.click();
    await page.locator("#fitBtn").click();

    const toggle = page.locator("#drawerToggle");
    await toggle.press("Enter");
    const drawer = page.getByRole("dialog", { name: "Mission waypoints" });
    await drawer.waitFor();
    assert.equal(await zoom.evaluate(el => {
      const { x, y, width, height } = el.getBoundingClientRect();
      return el.contains(document.elementFromPoint(x + width / 2, y + height / 2));
    }), false);
    assert.equal(await drawer.evaluate(el => el.contains(document.activeElement)), true);
    const route = page.locator("#missionList .mission-step");
    assert.equal(await route.count(), 2);
    const first = await route.first().locator("strong").textContent();
    await drawer.getByRole("button", { name: "Move waypoint 1 down" }).press("Enter");
    assert.equal(await route.nth(1).locator("strong").textContent(), first);
    assert.equal(await drawer.evaluate(el => el.contains(document.activeElement)), true);
    const point = drawer.locator(".poi-card").first();
    await point.press("Enter");
    assert.equal(await route.count(), 3);
    assert.equal(await point.evaluate(el => document.activeElement === el), true);
    await drawer.getByRole("button", { name: "Remove waypoint 3" }).press("Enter");
    await screenshot(page, "mobile-waypoints");
    await page.keyboard.press("Escape");
    assert.equal(await toggle.evaluate(el => document.activeElement === el), true);
    await drawer.waitFor({ state: "hidden" });

    await page.locator("#map").scrollIntoViewIfNeeded();
    await page.touchscreen.tap(...await mapPoint(page, .52, .4));
    const popup = page.locator(".waypoint-form");
    await popup.getByRole("textbox", { name: "Name" }).fill("UI test point");
    await popup.getByRole("button", { name: "Add waypoint" }).click();
    await popup.waitFor({ state: "hidden" });

    await page.getByRole("link", { name: "Settings", exact: true }).click();
    await page.locator("#drawManualObstacleBtn").click();
    await page.getByRole("link", { name: "Back to map" }).click();
    for (const point of [[.25, .3], [.35, .3], [.34, .43]]) {
      await page.mouse.click(...await mapPoint(page, ...point));
    }
    await page.locator("#finishManualObstacleBtn").click();
    await page.locator(".zone-row").waitFor();
    await page.locator("[data-zone-edit]").click();
    await page.locator("#cancelManualObstacleBtn").click();
    assert.equal(await page.locator(".zone-row").count(), 1);
    await page.locator("[data-zone-delete]").click();
    assert.equal(await page.locator(".zone-row").count(), 0);

    await change(page, "routeSpacing", "15");
    await generate(page);
    assert.match(await page.locator("#routeDetailsBody").textContent(), /generated with direct/);
    await page.locator("#routeDetails summary").click();
    await screenshot(page, "mobile-mission-generated");

    await toggle.click();
    await drawer.getByRole("button", { name: "Reverse" }).click();
    assert.match(await route.first().locator("strong").textContent(), /UI_test_point/i);
    await drawer.getByRole("button", { name: "Clear" }).click();
    assert.equal(await route.count(), 0);
  });
});

test("OSM and costmap mission controls generate with deterministic service responses", async () => {
  await withPage(sizes[1], async page => {
    await page.route("**/route/v1/**", async route => {
      const coordinates = new URL(route.request().url()).pathname.split("/").pop()
        .split(";").map(point => point.split(",").map(Number));
      await route.fulfill({
        json: { code: "Ok", routes: [{ geometry: { coordinates } }], waypoints: [{ distance: 0 }, { distance: 0 }] },
      });
    });
    await page.route("**/api/osm/semantic?*", route => route.fulfill({
      json: { features: [{
        kind: "building",
        ring: [[42.313, -6.202], [42.3131, -6.202], [42.3131, -6.2019], [42.313, -6.202]],
      }] },
    }));
    await page.route("**/api/catastro/buildings?*", route => route.fulfill({
      json: { schema: "geozigzag-catastro-buildings-response-v1", features: [] },
    }));
    await page.route("**/api/dem/status", route => route.fulfill({
      json: { available: true, source: { dataset: "Browser test DEM" } },
    }));
    await page.route("**/api/dem/grid?*", route => {
      const query = new URL(route.request().url()).searchParams;
      const elevations = Array.from({ length: Number(query.get("rows")) },
        () => Array(Number(query.get("cols"))).fill(900));
      return route.fulfill({ json: { schema: "geozigzag-dem-grid-v1", elevations_m: elevations } });
    });

    await open(page, "mission", "osm");
    await page.locator("#osmSafetyPreset").selectOption("strict");
    await page.locator("#osmValidationMode").waitFor();
    await page.locator("#osmSafetyPreset").selectOption("quick");
    await generate(page);
    assert.match(await page.locator("#routeDetailsBody").textContent(), /osm snap/);

    await page.locator("#routeStrategy").selectOption("cost");
    await page.locator("#loadPublicSemanticBtn").click();
    await page.locator("#publicSemanticStatus").getByText(/1 buildings/).waitFor();
    await page.locator("#clearPublicSemanticBtn").click();
    assert.match(await page.locator("#publicSemanticStatus").textContent(), /Not loaded/);
    await page.locator("#terrainMode").selectOption("server");
    await page.locator("#checkTerrainBtn").click();
    await page.getByText("DEM ready: Browser test DEM.").waitFor();
    await generate(page);
    assert.match(await page.locator("#routeDetailsBody").textContent(), /Route terrain profile/);
    await page.locator("#routeDetails summary").click();
    await screenshot(page, "costmap-generated");
  });
});

test("cancelling a delayed preview keeps stale exports disabled", async () => {
  await withPage(sizes[3], async page => {
    let release;
    const wait = new Promise(resolve => { release = resolve; });
    await page.route("**/api/coverage", async route => {
      await wait;
      await route.fulfill({ status: 503, json: { error: "Cancelled test request" } });
    });
    await open(page, "coverage");
    await page.locator("#generateBtn").click();
    try {
      const cancel = page.getByRole("button", { name: "Cancel preview" });
      await cancel.press("Enter");
      assert.match(await page.locator("#runState").textContent(), /Pending changes/);
      assert.equal(await page.locator("#downloadPlan").getAttribute("href"), null);
      assert.equal(await page.locator("#generateBtn").evaluate(el => document.activeElement === el), true);
    } finally {
      release();
    }
  });
});

test("place search offers disambiguated results and moves only the map", async () => {
  await withPage(sizes[3], async page => {
    const query = "León";
    let requests = 0;
    await page.route("https://geocoding-api.open-meteo.com/v1/search?*", route => {
      requests++;
      assert.equal(new URL(route.request().url()).searchParams.get("name"), query);
      return route.fulfill({
        json: { results: [
          { name: query, admin2: "Provincia de León", country: "España", latitude: 42.60003, longitude: -5.57032 },
          { name: query, admin1: "Guanajuato", country: "México", latitude: 21.12908, longitude: -101.67374 },
        ] },
      });
    });
    await open(page, "mission");
    await generate(page);
    const exportUrl = await page.locator("#downloadPlan").getAttribute("href");
    const toggle = page.getByRole("button", { name: "Find a place" });
    await toggle.click();
    const field = page.getByRole("searchbox", { name: "Place name or coordinates" });
    await field.fill(query);
    assert.equal(requests, 0);
    await field.press("Enter");
    await page.getByRole("button", { name: /León.*Guanajuato.*México/ }).waitFor();
    await screenshot(page, "mobile-place-results");
    await page.getByRole("button", { name: /León.*Provincia de León.*España/ }).click();
    const center = await page.evaluate(() => map.getCenter());
    assert.ok(Math.abs(center.lat - 42.60003) < .0001);
    assert.ok(Math.abs(center.lng + 5.57032) < .0001);
    assert.equal(await toggle.getAttribute("aria-expanded"), "false");
    assert.equal(await toggle.evaluate(el => el === document.activeElement), true);
    assert.equal(await page.locator("#downloadPlan").getAttribute("href"), exportUrl);
    await page.locator("#fitBtn").click();
    assert.ok(Math.abs((await page.evaluate(() => map.getCenter())).lng + 5.57032) > .1);
  });
});

test("place search accepts coordinates offline and handles lookup failures", async () => {
  await withPage(sizes[0], async page => {
    let fail = false;
    let requests = 0;
    await page.route("https://geocoding-api.open-meteo.com/v1/search?*", route => {
      requests++;
      return fail ? route.abort() : route.fulfill({ json: {} });
    });
    await open(page, "coverage");
    const toggle = page.getByRole("button", { name: "Find a place" });
    await toggle.click();
    const field = page.getByRole("searchbox", { name: "Place name or coordinates" });
    const previousCorner = await page.locator("#nw_lat").inputValue();
    await field.fill("42.93932, -6.31943");
    await field.press("Enter");
    const center = await page.evaluate(() => map.getCenter());
    assert.ok(Math.abs(center.lat - 42.93932) < .0001);
    assert.equal(requests, 0);
    assert.equal(await page.locator("#nw_lat").inputValue(), previousCorner);

    await toggle.click();
    await field.fill("91, 10");
    await field.press("Enter");
    await page.getByText(/Coordinates are out of range/).waitFor();
    assert.equal(requests, 0);
    await field.fill("Unmatched place");
    await field.press("Enter");
    await page.getByText(/No places found/).waitFor();
    fail = true;
    await field.fill("Villablino");
    await field.press("Enter");
    await page.getByText(/Place search is unavailable/).waitFor();
    await field.press("Escape");
    assert.equal(await toggle.getAttribute("aria-expanded"), "false");
  });
});
