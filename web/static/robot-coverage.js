// Coverage uses the same Python planner as the reproducible CLI.
function updateRobotControls() {
  const ackermann = $("robotType").value === "ackermann";
  $("ackermannSettings").classList.toggle("hidden", !ackermann);
  const wheelbase = Number($("robotWheelbase").value);
  const steering = Number($("robotSteering").value);
  const radius = wheelbase / Math.tan(steering * Math.PI / 180);
  $("robotStrategy").textContent = ackermann
    ? `Forward Dubins turns · minimum radius ${Number.isFinite(radius) ? radius.toFixed(2) : "—"} m. Increase headland if turns do not fit.`
    : "Stop → rotate → advance → rotate. Skid-steer shares this ideal geometry; soil slip is not modelled.";
}
function bindRobotControls() {
  for (const id of ["robotType", "robotClearance", "robotHeadland", "robotWheelbase", "robotSteering"]) {
    $(id).addEventListener("input", () => {
      updateRobotControls();
      if (appMode === "coverage") scheduleGenerate("Robot or headland parameters changed.");
    });
  }
  $("loadCoverageDemo").addEventListener("click", async () => {
    try {
      const response = await fetchWithTimeout("../configs/robot_coverage_demo.json");
      if (!response.ok) throw new Error("Could not load coverage demo.");
      const demo = await response.json();
      cancelRectDraw(false);
      polyVertices = demo.vertices;
      switchFieldMode("poly");
      $("rowSpacing").value = demo.row_spacing_m;
      $("pointSpacing").value = demo.point_spacing_m;
      $("robotType").value = demo.robot.kind;
      $("robotClearance").value = demo.robot.clearance_m;
      $("robotHeadland").value = demo.robot.headland_m;
      $("robotWheelbase").value = demo.robot.wheelbase_m;
      $("robotSteering").value = demo.robot.max_steering_deg;
      setFurrowBearing(demo.row_direction_deg, true, "Demo loaded.");
      setStartCorner(demo.start_corner);
      updateRobotControls();
      updateSpacingLabels();
      drawPolyDraft(true);
      map.fitBounds(L.latLngBounds(polyVertices).pad(.2));
      markDirty("Demo loaded. Select a robot and generate its route.");
    } catch (error) { failGenerationState(error.message); }
  });
  updateRobotControls();
}
async function generateRobotCoverage() {
  const vertices = currentCoveragePolygon();
  if (!vertices || vertices.length < 3) throw new Error("Select a field with at least three vertices.");
  const numeric = id => {
    const element = $(id);
    const value = Number(element.value);
    if (!element.value.trim() || !Number.isFinite(value) || !element.checkValidity()) {
      throw new Error(`Check ${element.labels[0]?.textContent || id}.`);
    }
    return value;
  };
  const payload = {
    vertices,
    row_spacing_m: numeric("rowSpacing"),
    point_spacing_m: numeric("pointSpacing"),
    row_direction_deg: getEffectiveFurrowBearing(vertices, polygonOrigin(vertices)),
    start_corner: startCorner,
    robot: {
      kind: $("robotType").value,
      clearance_m: numeric("robotClearance"),
      headland_m: numeric("robotHeadland"),
      wheelbase_m: numeric("robotWheelbase"),
      max_steering_deg: numeric("robotSteering")
    }
  };
  const response = await fetchWithTimeout("/api/coverage", {
    method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload)
  }, 60000);
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || `Coverage server returned ${response.status}.`);
  return {...result, request: payload};
}
