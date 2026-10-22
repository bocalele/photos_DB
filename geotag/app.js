(() => {
  const pageSize = 500;
  const photos = [];
  const selectedNames = new Set();
  let lastClickedIndex = -1;
  let totalPhotos = 0;
  let map;
  let marker;
  let loaded = false;
  let loading = false;
  let searchTimer;
  let searchSequence = 0;
  let thumbnailObserver;

  const byId = id => document.getElementById(id);

  async function requestJson(url, options) {
    const response = await fetch(url, options);
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || `Erreur HTTP ${response.status}`);
    return data;
  }

  function initializeMap() {
    if (map) return;
    map = L.map("map").setView([20, 0], 2);
    const streetLayer = L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: "© OpenStreetMap contributors",
      maxZoom: 19,
    });
    const hikingLayer = L.tileLayer("https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png", {
      attribution: "© OpenStreetMap contributors · SRTM | © OpenTopoMap (CC-BY-SA)",
      maxZoom: 17,
    });
    const lightLayer = L.tileLayer(
      "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}",
      {
        attribution: "Esri, HERE, Garmin, © OpenStreetMap contributors, and the GIS user community",
        maxZoom: 16,
      }
    );
    hikingLayer.addTo(map);
    L.control.layers({
      "Plan de rue": streetLayer,
      "Carte de randonnée": hikingLayer,
      "Carte claire": lightLayer,
    }, null, { collapsed: false }).addTo(map);
    map.on("click", event => {
      proposeLocation(event.latlng.lat, event.latlng.lng);
    });
    map.on("contextmenu", event => {
      event.originalEvent.preventDefault();
      proposeLocation(event.latlng.lat, event.latlng.lng);
    });
  }

  async function loadPhotos() {
    if (loading || loaded) return;
    loading = true;
    try {
      let metadataError = "";
      do {
        const data = await requestJson(`/api/photos?offset=${photos.length}&limit=${pageSize}`);
        photos.push(...data.photos);
        totalPhotos = data.total;
        metadataError ||= data.metadataError || "";
        byId("photo-count").textContent = `${photos.length} / ${totalPhotos} photo(s) chargée(s)…`;
        if (data.directory) {
          byId("directory-path").textContent = data.directory;
        }
      } while (photos.length < totalPhotos);
      loaded = true;
      if (metadataError) {
        byId("photo-count").title = `Lecture des métadonnées impossible : ${metadataError}`;
      }
      renderPhotos();
    } catch (error) {
      showMessage(error.message, true);
    } finally {
      loading = false;
    }
  }

  function showMessage(message, isError = false) {
    byId("photo-count").textContent = message;
    if (isError) byId("photo-count").classList.add("error");
  }

  function handleCardClick(name, index, event) {
    if (event.shiftKey && lastClickedIndex >= 0) {
      // Shift-click: select range
      const start = Math.min(lastClickedIndex, index);
      const end = Math.max(lastClickedIndex, index);
      for (let i = start; i <= end; i++) {
        selectedNames.add(photos[i].name);
      }
    } else {
      // Normal click: toggle
      if (selectedNames.has(name)) selectedNames.delete(name);
      else selectedNames.add(name);
    }
    lastClickedIndex = index;
    renderPhotos();
  }

  function renderPhotos() {
    const grid = byId("photo-grid");
    thumbnailObserver?.disconnect();
    thumbnailObserver = "IntersectionObserver" in window
      ? new IntersectionObserver((entries, observer) => {
          for (const entry of entries) {
            if (!entry.isIntersecting) continue;
            const image = entry.target;
            image.src = image.dataset.src;
            image.removeAttribute("data-src");
            observer.unobserve(image);
          }
        }, { root: grid, rootMargin: "300px 0px" })
      : null;
    byId("photo-count").textContent =
      `${photos.length} photo(s) · ${selectedNames.size} sélectionnée(s)`;
    byId("deselect-photos").disabled = selectedNames.size === 0;
    grid.replaceChildren();
    if (!photos.length) {
      const message = document.createElement("div");
      message.className = "message";
      message.textContent = loaded ? "Aucune photo dans ce dossier." : "Chargement des photos…";
      grid.appendChild(message);
      return;
    }

    for (let i = 0; i < photos.length; i++) {
      const photo = photos[i];
      const card = document.createElement("figure");
      const selected = selectedNames.has(photo.name);
      card.className = `photo-card${selected ? " selected" : ""}`;
      card.title = photo.name;
      card.dataset.index = i;
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.className = "photo-checkbox";
      checkbox.checked = selected;
      checkbox.setAttribute("aria-label", `Sélectionner ${photo.name}`);
      const badges = document.createElement("span");
      badges.className = "metadata-badges";
      for (const [present, label, symbol] of [
        [photo.gpsPresent, "GPS", "⌖"],
        [photo.locationPresent, "Sous-lieu et pays", "⌂"],
      ]) {
        const badge = document.createElement("span");
        const unknown = !photo.metadataRead;
        badge.className = `metadata-badge ${unknown ? "unknown" : present ? "present" : "missing"}`;
        badge.textContent = `${symbol} ${unknown ? "?" : present ? "✓" : "−"}`;
        badge.title = unknown
          ? `${label} : état inconnu, lecture des métadonnées en échec`
          : `${label} : ${present ? "présents" : "absents"} dans le fichier`;
        badge.setAttribute("aria-label", badge.title);
        // Click sur le badge GPS pour centrer la carte sur les coordonnées
        if (present && label === "GPS" && photo.latitude != null && photo.longitude != null) {
          badge.classList.add("clickable");
          badge.addEventListener("click", (event) => {
            event.stopPropagation(); // Empêche la sélection de la photo
            map.setView([photo.latitude, photo.longitude], 14);
            showLocationMarker(photo.latitude, photo.longitude);
            map.invalidateSize({ pan: false });
          });
        }
        badges.appendChild(badge);
      }
      const image = document.createElement("img");
      image.dataset.src = photo.thumbnail;
      image.alt = photo.name;
      image.loading = "lazy";
      image.decoding = "async";
      image.addEventListener("error", () => {
        image.removeAttribute("src");
        image.alt = "Aperçu indisponible";
      });
      const caption = document.createElement("figcaption");
      caption.textContent = photo.name;
      card.append(checkbox, badges, image, caption);
      if (photo.subLocation || photo.country) {
        card.title += `\n${[photo.subLocation, photo.country].filter(Boolean).join(", ")}`;
      }
      card.addEventListener("click", (event) => {
        handleCardClick(photo.name, i, event);
      });
      grid.appendChild(card);
      if (thumbnailObserver) thumbnailObserver.observe(image);
      else image.src = image.dataset.src;
    }
  }

  function searchPlaces() {
    clearTimeout(searchTimer);
    const sequence = ++searchSequence;
    const query = byId("place-search").value.trim();
    const suggestions = byId("place-suggestions");
    suggestions.replaceChildren();
    if (query.length < 3) return;
    searchTimer = setTimeout(async () => {
      try {
        const data = await requestJson(`/api/search?q=${encodeURIComponent(query)}`);
        if (sequence !== searchSequence) return;
        for (const place of data.places) {
          const button = document.createElement("button");
          button.type = "button";
          button.className = "suggestion";
          button.textContent = place.label;
          button.addEventListener("click", () => {
            suggestions.replaceChildren();
            byId("place-search").value = place.label;
            if (place.bounds) {
              map.fitBounds(place.bounds, { maxZoom: 13, padding: [24, 24] });
            } else {
              map.setView([place.latitude, place.longitude], 12);
            }
            map.invalidateSize({ pan: false });
            showLocationMarker(place.latitude, place.longitude);
          });
          suggestions.appendChild(button);
        }
      } catch (error) {
        suggestions.textContent = error.message;
      }
    }, 350);
  }

  function showLocationMarker(latitude, longitude) {
    if (marker) map.removeLayer(marker);
    marker = L.circleMarker([latitude, longitude], {
      radius: 8,
      color: "#f1be4c",
      fillColor: "#f1be4c",
      fillOpacity: 0.9,
    }).addTo(map);
  }

  async function proposeLocation(latitude, longitude) {
    if (!selectedNames.size) {
      showMessage("Sélectionnez au moins une photo avant de choisir un lieu.");
      return;
    }
    showLocationMarker(latitude, longitude);
    byId("latitude").value = latitude.toFixed(7);
    byId("longitude").value = longitude.toFixed(7);
    byId("sub-location").value = "";
    byId("country").value = "";
    byId("save-progress").hidden = true;
    byId("save-errors").replaceChildren();
    const status = byId("save-status");
    status.textContent = "Recherche du nom du lieu…";
    status.className = "";
    byId("selected-count").textContent = `${selectedNames.size} photo(s) sélectionnée(s)`;
    const overlay = byId("editor-overlay");
    overlay.classList.add("open");
    overlay.setAttribute("aria-hidden", "false");
    try {
      const location = await requestJson(
        `/api/reverse?latitude=${encodeURIComponent(latitude)}&longitude=${encodeURIComponent(longitude)}`
      );
      byId("sub-location").value = location.subLocation || "";
      byId("country").value = location.country || "";
      status.textContent = "";
    } catch (error) {
      status.textContent = `Coordonnées conservées ; lieu non trouvé : ${error.message}`;
      status.className = "error";
    }
  }

  function closeEditor() {
    const overlay = byId("editor-overlay");
    overlay.classList.remove("open");
    overlay.setAttribute("aria-hidden", "true");
  }

  async function saveLocation() {
    if (!selectedNames.size || !byId("location-form").reportValidity()) return;
    const savedNames = Array.from(selectedNames);
    const button = byId("save-location");
    const status = byId("save-status");
    button.disabled = true;
    byId("save-progress").hidden = false;
    byId("save-progress-bar").removeAttribute("value");
    byId("save-errors").replaceChildren();
    status.textContent = "Préparation de l’écriture dans les fichiers…";
    status.className = "";
    try {
      const started = await requestJson("/api/save-location", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          photoNames: savedNames,
          latitude: Number(byId("latitude").value),
          longitude: Number(byId("longitude").value),
          subLocation: byId("sub-location").value.trim(),
          country: byId("country").value.trim(),
        }),
      });
      let job;
      do {
        await new Promise(resolve => setTimeout(resolve, 500));
        job = await requestJson(
          `/api/save-location?jobId=${encodeURIComponent(started.jobId)}`
        );
        const progress = byId("save-progress-bar");
        progress.max = job.total || 1;
        progress.value = job.done;
        status.textContent = `Écriture des métadonnées : ${job.done}/${job.total} photo(s) traitée(s), ${job.updated.length} enregistrée(s), ${job.errors.length} erreur(s).`;
      } while (!job.complete);

      const successfulNames = new Set(job.updated);
      for (const photo of photos) {
        if (!successfulNames.has(photo.name)) continue;
        photo.gpsPresent = true;
        photo.locationPresent = Boolean(
          byId("sub-location").value.trim() && byId("country").value.trim()
        );
        photo.metadataRead = true;
        photo.subLocation = byId("sub-location").value.trim();
        photo.country = byId("country").value.trim();
      }
      for (const name of successfulNames) selectedNames.delete(name);
      for (const error of job.errors) {
        const item = document.createElement("li");
        item.textContent = `${error.filename} : ${error.message}`;
        byId("save-errors").appendChild(item);
      }

      if (!job.errors.length) {
        renderPhotos();
        closeEditor();
      } else {
        status.textContent = `${job.updated.length} photo(s) enregistrée(s), ${job.errors.length} en erreur. Les erreurs sont détaillées ci-dessous.`;
        status.className = "error";
        renderPhotos();
      }
    } catch (error) {
      status.textContent = error.message;
      status.className = "error";
    } finally {
      button.disabled = false;
    }
  }

  byId("thumbnail-size").addEventListener("input", event => {
    byId("photo-grid").style.setProperty("--tile-size", `${event.target.value}px`);
  });
  byId("deselect-photos").addEventListener("click", () => {
    selectedNames.clear();
    lastClickedIndex = -1;
    renderPhotos();
  });
  byId("change-directory").addEventListener("click", async () => {
    try {
      byId("change-directory").disabled = true;
      byId("change-directory").textContent = "Chargement…";
      const data = await requestJson("/api/change-directory", { method: "POST" });
      if (data.directory) {
        byId("directory-path").textContent = data.directory;
        photos.length = 0;
        totalPhotos = 0;
        loaded = false;
        selectedNames.clear();
        lastClickedIndex = -1;
        loadPhotos();
      }
    } catch (error) {
      showMessage(error.message, true);
    } finally {
      byId("change-directory").disabled = false;
      byId("change-directory").textContent = "Changer de dossier";
    }
  });
  byId("place-search").addEventListener("input", searchPlaces);
  byId("cancel").addEventListener("click", closeEditor);
  byId("save-location").addEventListener("click", saveLocation);
  byId("editor-overlay").addEventListener("click", event => {
    if (event.target.id === "editor-overlay") closeEditor();
  });
  byId("location-form").addEventListener("submit", event => event.preventDefault());
  document.addEventListener("keydown", event => {
    if (event.key === "Escape") closeEditor();
  });

  initializeMap();
  loadPhotos();
  const mapResizeObserver = new ResizeObserver(() => map.invalidateSize({ pan: false }));
  mapResizeObserver.observe(byId("map"));
})();
