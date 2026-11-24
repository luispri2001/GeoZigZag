// The search marker belongs to the map view, independently of route and field layers.
(() => {
  const toggle = $("placeSearchToggle");
  const form = $("placeSearchForm");
  const input = $("placeQuery");
  const submit = $("placeSearchSubmit");
  const status = $("placeSearchStatus");
  const results = $("placeResults");
  const mapStatus = $("placeMapStatus");
  const marker = L.circleMarker([0, 0], {
    radius: 7, color: "#fff", weight: 2, fillColor: "#1764aa",
    fillOpacity: 1, interactive: false,
  });
  let requestId = 0;

  function setOpen(open) {
    toggle.setAttribute("aria-expanded", String(open));
    form.classList.toggle("hidden", !open);
    if (open) input.focus({preventScroll: true});
    else toggle.focus({preventScroll: true});
  }

  function goToPlace(latitude, longitude, label) {
    marker.setLatLng([latitude, longitude]).addTo(map);
    map.setView([latitude, longitude], 14, {animate: false});
    mapStatus.textContent = `Map centered on ${label}.`;
    setOpen(false);
  }

  function validCoordinates(latitude, longitude) {
    return Number.isFinite(latitude) && Number.isFinite(longitude)
      && Math.abs(latitude) <= 90 && Math.abs(longitude) <= 180;
  }

  function appendResult(place) {
    const item = document.createElement("li");
    const button = document.createElement("button");
    button.type = "button";
    button.className = "place-result";
    const name = document.createElement("strong");
    name.textContent = place.name;
    button.append(name);
    const region = [...new Set([place.admin2, place.admin1, place.country])]
      .filter(value => value && value !== place.name).join(", ");
    if (region) {
      const location = document.createElement("small");
      location.textContent = region;
      button.append(location);
    }
    button.addEventListener("click", () => goToPlace(place.latitude, place.longitude, place.name));
    item.append(button);
    results.append(item);
  }

  toggle.addEventListener("click", () => setOpen(form.classList.contains("hidden")));
  form.addEventListener("keydown", event => {
    if (event.key === "Escape") {
      event.preventDefault();
      setOpen(false);
    }
  });
  input.addEventListener("input", () => {
    requestId++;
    results.replaceChildren();
    status.textContent = "";
    submit.disabled = false;
  });
  form.addEventListener("submit", async event => {
    event.preventDefault();
    const token = ++requestId;
    const query = input.value.trim();
    results.replaceChildren();
    const parts = query.split(",").map(part => part.trim());
    if (parts.length === 2 && parts.every(part => /^[-+]?(?:\d+(?:\.\d*)?|\.\d+)$/.test(part))) {
      const [latitude, longitude] = parts.map(Number);
      if (validCoordinates(latitude, longitude)) goToPlace(latitude, longitude, query);
      else status.textContent = "Coordinates are out of range. Use latitude ±90° and longitude ±180°.";
      return;
    }
    if (query.length < 2) {
      status.textContent = "Enter at least two characters.";
      return;
    }

    submit.disabled = true;
    status.textContent = "Searching…";
    const params = new URLSearchParams({
      name: query, count: "5", language: navigator.language.split("-")[0],
    });
    try {
      const response = await fetchWithTimeout(`https://geocoding-api.open-meteo.com/v1/search?${params}`, {}, 8000, "Place search");
      if (!response.ok) throw new Error("Place search request failed.");
      const payload = await response.json();
      if (token !== requestId) return;
      const places = (payload.results || []).filter(place =>
        place.name && validCoordinates(place.latitude, place.longitude)
      );
      places.forEach(appendResult);
      status.textContent = places.length ? "Select a result to center the map." : "No places found. Try adding a province or country.";
    } catch {
      if (token === requestId) status.textContent = "Place search is unavailable. Try again or enter latitude, longitude.";
    } finally {
      if (token === requestId) submit.disabled = false;
    }
  });
})();
