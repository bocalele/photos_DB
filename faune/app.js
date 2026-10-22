(() => {
  const pageSize = 200;
  const photos = [];
  const selectedNames = new Set();
  let lastClickedIndex = -1;
  const classifications = new Map();
  const cardsByName = new Map();
  const chosenTaxa = new Map();
  const savedTaxa = new Map();
  const taxonSaveErrors = new Map();
  const taxonSearchTimers = new Map();
  let loaded = false;
  let loading = false;
  let classifying = false;
  let totalPhotos = 0;
  let thumbnailObserver;

  const byId = id => document.getElementById(id);

  async function requestJson(url, options) {
    const response = await fetch(url, options);
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || `Erreur HTTP ${response.status}`);
    return data;
  }

  async function loadPhotos() {
    if (loading || loaded) return;
    loading = true;
    try {
      do {
        const data = await requestJson(
          `/api/photos?offset=${photos.length}&limit=${pageSize}`
        );
        photos.push(...data.photos);
        for (const photo of data.photos) {
          if (photo.keywordTaxon) savedTaxa.set(photo.name, photo.keywordTaxon);
          if (!photo.metadataRead && photo.metadataError) {
            byId("photo-count").title =
              `Lecture de métadonnées impossible : ${photo.metadataError}`;
          }
        }
        totalPhotos = data.total;
        byId("directory-path").textContent = data.directory;
        updatePhotoCount();
      } while (photos.length < totalPhotos);
      loaded = true;
      renderPhotos();
    } catch (error) {
      showMessage(`Impossible de charger les photos : ${error.message}`, true);
    } finally {
      loading = false;
    }
  }

  function updatePhotoCount() {
    byId("photo-count").textContent =
      `${photos.length}${photos.length === totalPhotos ? "" : ` / ${totalPhotos}`} photo(s) · ` +
      `${selectedNames.size} sélectionnée(s)`;
    byId("select-all").disabled = photos.length === 0;
    byId("deselect-photos").disabled = selectedNames.size === 0;
    byId("classify-photos").disabled = selectedNames.size === 0 || classifying;
  }

  function isValidatedChoice(name) {
    const saved = savedTaxa.get(name);
    const current = chosenTaxa.get(name)
      || classifications.get(name)?.results?.[0]
      || saved;
    return Boolean(saved && current?.scientificName === saved.scientificName);
  }

  function showMessage(message, isError = false) {
    byId("photo-count").textContent = message;
    byId("photo-count").classList.toggle("error", isError);
  }

  function handleCardClick(name, index, event) {
    if (event.shiftKey && lastClickedIndex >= 0) {
      // Shift-click: select range
      const start = Math.min(lastClickedIndex, index);
      const end = Math.max(lastClickedIndex, index);
      for (let i = start; i <= end; i++) {
        selectedNames.add(photos[i].name);
      }
      // Update all cards in range
      for (let i = start; i <= end; i++) {
        updateCardSelection(photos[i].name);
        updateClassificationCard(photos[i].name);
      }
    } else {
      // Normal click: toggle
      toggleSelection(name);
    }
    lastClickedIndex = index;
    updatePhotoCount();
  }

  function renderPhotos() {
    const grid = byId("photo-grid");
    cardsByName.clear();
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
    updatePhotoCount();
    grid.replaceChildren();
    if (!photos.length) {
      const empty = document.createElement("p");
      empty.className = "message";
      empty.textContent = loaded ? "Aucune photo dans ce dossier." : "Chargement des photos…";
      grid.appendChild(empty);
      return;
    }

    for (let i = 0; i < photos.length; i++) {
      const photo = photos[i];
      const card = document.createElement("article");
      const selected = selectedNames.has(photo.name);
      const classification = classifications.get(photo.name);
      card.className =
        `photo-card${selected ? " selected" : ""}${isValidatedChoice(photo.name) ? " validated" : ""}`;
      card.title = photo.name;
      card.dataset.index = i;
      const selectionControls = document.createElement("div");
      selectionControls.className = "photo-selection-controls";
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.className = "photo-checkbox";
      checkbox.checked = selected;
      checkbox.setAttribute("aria-label", `Sélectionner ${photo.name}`);
      checkbox.addEventListener("change", () => toggleSelection(photo.name));
      selectionControls.appendChild(checkbox);
      selectionControls.append(
        createMetadataBadge(
          photo,
          "keywordPresent",
          "Mot-clé vernaculaire présent dans TAXREF",
          "#",
          true
        ),
        createMetadataBadge(
          photo,
          "hierarchyPresent",
          "Arborescence présente dans TAXREF",
          "↳",
          false
        )
      );

      const image = document.createElement("img");
      image.dataset.src = photo.thumbnail;
      image.alt = photo.name;
      image.loading = "lazy";
      image.decoding = "async";
      image.addEventListener("error", () => {
        image.removeAttribute("src");
        image.alt = "Aperçu indisponible";
        image.classList.add("thumbnail-error");
      });

      const caption = document.createElement("div");
      caption.className = "photo-caption";
      const filename = document.createElement("div");
      filename.className = "filename";
      filename.textContent = photo.name;
      caption.appendChild(filename);
      const taxonomyDisplay = createClassificationDisplay(photo.name, classification);
      if (taxonomyDisplay) caption.appendChild(taxonomyDisplay);
      card.append(selectionControls, image, caption);
      card.addEventListener("click", event => {
        if (event.target.closest("details") || event.target === checkbox) return;
        if (event.target.closest(".photo-selection-controls")) return;
        if (event.target.closest(".classification-display")) return;
        handleCardClick(photo.name, i, event);
      });
      grid.appendChild(card);
      cardsByName.set(photo.name, card);
      if (thumbnailObserver) thumbnailObserver.observe(image);
      else image.src = image.dataset.src;
    }
  }

  function createMetadataBadge(photo, field, label, symbol, isClickable) {
    const present = photo[field];
    const unknown = !photo.metadataRead;
    const badge = document.createElement(isClickable ? "button" : "span");
    if (isClickable) badge.type = "button";
    badge.className =
      `metadata-badge ${unknown ? "unknown" : present ? "present" : "missing"}`;
    badge.textContent = `${symbol} ${unknown ? "?" : present ? "✓" : "−"}`;
    badge.title = unknown
      ? `${label} : état inconnu${photo.metadataError ? ` (${photo.metadataError})` : ""}`
      : `${label} : ${present ? "présent" : "absent"}`;
    badge.setAttribute("aria-label", badge.title);
    if (isClickable) {
      badge.disabled = unknown || !present || !photo.keywordTaxon;
      badge.addEventListener("click", event => {
        event.stopPropagation();
        if (!photo.keywordTaxon) return;
        const taxon = photo.keywordTaxon;
        chosenTaxa.set(photo.name, taxon);
        classifications.set(photo.name, { results: [{ ...taxon, score: 1 }] });
        updateClassificationCard(photo.name);
      });
    }
    return badge;
  }

  function createClassificationDisplay(photoName, classification) {
    const isSaved = savedTaxa.has(photoName);
    const isSelected = selectedNames.has(photoName);
    const isProcessed = classifications.has(photoName);
    if (!isSaved && !isSelected && !isProcessed) return null;

    const display = document.createElement("div");
    display.className = "classification-display";
    if (classification?.error) {
      const error = document.createElement("div");
      error.className = "species-label classification-error";
      error.textContent = "Classification impossible";
      error.title = classification.error;
      display.appendChild(error);
    }
    const predictions = classification?.results || [];
    const currentTaxon = chosenTaxa.get(photoName)
      || savedTaxa.get(photoName)
      || predictions[0]
      || null;
    const decision = document.createElement("div");
    decision.className = "species-decision";
    const label = document.createElement("span");
    label.className = "species-label";
    label.textContent = currentTaxon
      ? currentTaxon.vernacularName || currentTaxon.scientificName
      : "Espèce non déterminée";
    label.title = currentTaxon?.scientificName || "Choisir une espèce dans la taxonomie";
    decision.appendChild(label);

    const confirmLabel = document.createElement("label");
    confirmLabel.className = "confirm-label";
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = Boolean(
      currentTaxon
      && savedTaxa.get(photoName)?.scientificName === currentTaxon.scientificName
    );
    checkbox.setAttribute(
      "aria-label",
      `Valider ${currentTaxon?.vernacularName || currentTaxon?.scientificName || "l’espèce"} pour ${photoName}`
    );
    checkbox.title = checkbox.getAttribute("aria-label");
    confirmLabel.appendChild(checkbox);
    decision.appendChild(confirmLabel);
    checkbox.addEventListener("change", () => saveTaxon(photoName, currentTaxon, checkbox));
    display.appendChild(decision);

    const controls = document.createElement("div");
    controls.className = "taxonomy-controls";
    const menu = document.createElement("details");
    menu.className = "taxon-menu";
    const summary = document.createElement("summary");
    summary.textContent = currentTaxon
      ? `Changer d’espèce — ${currentTaxon.vernacularName || currentTaxon.scientificName}`
      : "Choisir une espèce";
    summary.setAttribute("aria-label", `Choisir une espèce pour ${photoName}`);
    menu.appendChild(summary);

    const menuContent = document.createElement("div");
    menuContent.className = "taxon-menu-content";
    const searchLabel = document.createElement("label");
    searchLabel.className = "control-label";
    searchLabel.textContent = "Rechercher par nom vernaculaire";
    const manualInput = document.createElement("input");
    manualInput.type = "search";
    manualInput.className = "taxon-search";
    manualInput.placeholder = "Nom français…";
    manualInput.setAttribute("aria-label", `Rechercher par nom vernaculaire pour ${photoName}`);
    searchLabel.appendChild(manualInput);
    menuContent.appendChild(searchLabel);

    const choices = document.createElement("div");
    choices.className = "taxon-suggestions";
    choices.setAttribute("role", "listbox");
    for (const prediction of predictions) {
      const button = makeTaxonChoice(
        photoName,
        prediction,
        currentTaxon,
        `BioCLIP · ${(prediction.score * 100).toFixed(1)} %`,
        menu
      );
      choices.appendChild(button);
    }
    if (
      currentTaxon
      && !predictions.some(item => item.scientificName === currentTaxon.scientificName)
    ) {
      choices.appendChild(
        makeTaxonChoice(photoName, currentTaxon, currentTaxon, "Choix actuel", menu)
      );
    }
    if (predictions.length) {
      const heading = document.createElement("div");
      heading.className = "taxon-list-heading";
      heading.textContent = "Propositions BioCLIP";
      menuContent.appendChild(heading);
      menuContent.appendChild(choices);
    }
    const searchResults = document.createElement("div");
    searchResults.className = "taxon-suggestions";
    searchResults.setAttribute("role", "listbox");
    menuContent.appendChild(searchResults);
    manualInput.addEventListener("input", () => {
      searchTaxa(photoName, manualInput, searchResults, menu);
    });
    menu.appendChild(menuContent);
    controls.appendChild(menu);
    display.appendChild(controls);
    const saveError = taxonSaveErrors.get(photoName);
    if (saveError) {
      const error = document.createElement("div");
      error.className = "taxon-save-error";
      error.textContent = `Impossible d’enregistrer : ${saveError}`;
      display.appendChild(error);
    }

    return display;
  }

  function makeTaxonChoice(photoName, taxon, currentTaxon, detail, menu) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "taxon-suggestion";
    button.setAttribute("role", "option");
    button.setAttribute(
      "aria-selected",
      String(currentTaxon?.scientificName === taxon.scientificName)
    );
    const vernacular = taxon.vernacularName || taxon.scientificName;
    button.textContent = `${vernacular} — ${taxon.scientificName}${detail ? ` · ${detail}` : ""}`;
    button.title = taxon.lineage?.join(" › ") || taxon.scientificName;
    button.addEventListener("click", () => {
      chosenTaxa.set(photoName, taxon);
      taxonSaveErrors.delete(photoName);
      menu.open = false;
      updateClassificationCard(photoName);
    });
    return button;
  }

  async function searchTaxa(photoName, input, suggestions, menu) {
    clearTimeout(taxonSearchTimers.get(photoName));
    suggestions.replaceChildren();
    const query = input.value.trim();
    if (query.length < 2) return;
    const timer = setTimeout(async () => {
      try {
        const data = await requestJson(`/api/taxa?q=${encodeURIComponent(query)}`);
        if (input.value.trim() !== query) return;
        for (const taxon of data.taxa) {
          suggestions.appendChild(
            makeTaxonChoice(
              photoName,
              taxon,
              chosenTaxa.get(photoName) || savedTaxa.get(photoName),
              "",
              menu
            )
          );
        }
        if (!data.taxa.length) {
          const empty = document.createElement("span");
          empty.className = "no-taxa";
          empty.textContent = "Aucun nom vernaculaire correspondant dans TAXREF.";
          suggestions.appendChild(empty);
        }
      } catch (error) {
        const message = document.createElement("span");
        message.className = "no-taxa";
        message.textContent = error.message;
        suggestions.appendChild(message);
      }
    }, 250);
    taxonSearchTimers.set(photoName, timer);
  }

  async function saveTaxon(photoName, taxon, checkbox) {
    if (checkbox.checked && !taxon) {
      checkbox.checked = false;
      checkbox.title = "Choisis une espèce avant de la valider.";
      return;
    }
    checkbox.disabled = true;
    try {
      const data = await requestJson("/api/confirm", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          photoName,
          scientificName: taxon.scientificName,
          validated: checkbox.checked,
        }),
      });
      const photo = photos.find(item => item.name === photoName);
      if (photo) Object.assign(photo, data.metadata);
      if (data.metadata.keywordTaxon) savedTaxa.set(photoName, data.metadata.keywordTaxon);
      else savedTaxa.delete(photoName);
      taxonSaveErrors.delete(photoName);
      updatePhotoIndicators(photoName);
      updateCardSelection(photoName);
      checkbox.title = checkbox.checked
        ? `Validé : ${taxon.vernacularName || taxon.scientificName}`
        : "Retiré des mots-clés de cette photo";
      updateClassificationCard(photoName);
    } catch (error) {
      checkbox.checked = Boolean(
        taxon && savedTaxa.get(photoName)?.scientificName === taxon.scientificName
      );
      checkbox.title = error.message;
      checkbox.setAttribute("aria-invalid", "true");
      taxonSaveErrors.set(photoName, error.message);
      updateClassificationCard(photoName);
    } finally {
      if (checkbox.isConnected) checkbox.disabled = false;
    }
  }

  function updateClassificationCard(name) {
    const caption = cardsByName.get(name)?.querySelector(".photo-caption");
    if (!caption) return;
    const existing = caption.querySelector(".classification-display");
    const display = createClassificationDisplay(name, classifications.get(name));
    existing?.remove();
    if (display) caption.appendChild(display);
    updateCardSelection(name);
  }

  function updatePhotoIndicators(name) {
    const card = cardsByName.get(name);
    const photo = photos.find(item => item.name === name);
    const controls = card?.querySelector(".photo-selection-controls");
    if (!photo || !controls) return;
    controls.querySelectorAll(".metadata-badge").forEach(badge => badge.remove());
    controls.append(
      createMetadataBadge(
        photo,
        "keywordPresent",
        "Mot-clé vernaculaire présent dans TAXREF",
        "#",
        true
      ),
      createMetadataBadge(
        photo,
        "hierarchyPresent",
        "Arborescence présente dans TAXREF",
        "↳",
        false
      )
    );
  }

  function toggleSelection(name) {
    if (selectedNames.has(name)) selectedNames.delete(name);
    else selectedNames.add(name);
    updateCardSelection(name);
    updateClassificationCard(name);
    updatePhotoCount();
  }

  function updateCardSelection(name) {
    const card = cardsByName.get(name);
    if (!card) return;
    const selected = selectedNames.has(name);
    card.classList.toggle("selected", selected);
    card.classList.toggle("validated", isValidatedChoice(name));
    card.querySelector(".photo-checkbox").checked = selected;
  }

  async function classifySelected() {
    if (!selectedNames.size) return;
    const button = byId("classify-photos");
    const panel = byId("classification-progress");
    const progress = byId("progress-bar");
    const status = byId("progress-status");
    const errors = byId("classification-errors");
    classifying = true;
    button.disabled = true;
    byId("select-all").disabled = true;
    byId("deselect-photos").disabled = true;
    panel.hidden = false;
    progress.value = 0;
    errors.replaceChildren();
    status.textContent = "Préparation de BioCLIP… Le premier lancement peut télécharger les fichiers du modèle.";
    try {
      for (const name of selectedNames) {
        classifications.delete(name);
        updateClassificationCard(name);
      }
      const started = await requestJson("/api/classify", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ photoNames: [...selectedNames] }),
      });
      let finished = false;
      while (!finished) {
        await new Promise(resolve => setTimeout(resolve, 700));
        const job = await requestJson(
          `/api/classify?jobId=${encodeURIComponent(started.jobId)}`
        );
        progress.max = job.total || 1;
        progress.value = job.done;
        status.textContent = job.status;
        for (const [name, results] of Object.entries(job.results)) {
          if (classifications.has(name)) continue;
          classifications.set(name, { results });
          updateClassificationCard(name);
        }
        for (const [name, error] of Object.entries(job.errors)) {
          if (classifications.has(name)) continue;
          classifications.set(name, { error });
          updateClassificationCard(name);
        }
        finished = job.complete;
        if (finished) {
          for (const [name, message] of Object.entries(job.errors)) {
            const item = document.createElement("li");
            item.textContent = `${name} : ${message}`;
            errors.appendChild(item);
          }
        }
      }
    } catch (error) {
      status.textContent = `Erreur : ${error.message}`;
      status.className = "error";
    } finally {
      classifying = false;
      button.disabled = selectedNames.size === 0;
      byId("select-all").disabled = photos.length === 0;
      byId("deselect-photos").disabled = selectedNames.size === 0;
    }
  }

  byId("select-all").addEventListener("click", () => {
    for (const photo of photos) {
      selectedNames.add(photo.name);
      updateCardSelection(photo.name);
      updateClassificationCard(photo.name);
    }
    lastClickedIndex = photos.length - 1;
    updatePhotoCount();
  });
  byId("deselect-photos").addEventListener("click", () => {
    selectedNames.clear();
    for (const photo of photos) {
      updateCardSelection(photo.name);
      updateClassificationCard(photo.name);
    }
    lastClickedIndex = -1;
    updatePhotoCount();
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
        savedTaxa.clear();
        chosenTaxa.clear();
        classifications.clear();
        taxonSaveErrors.clear();
        loadPhotos();
      }
    } catch (error) {
      showMessage(`Impossible de changer de dossier : ${error.message}`, true);
    } finally {
      byId("change-directory").disabled = false;
      byId("change-directory").textContent = "Changer de dossier";
    }
  });
  byId("classify-photos").addEventListener("click", classifySelected);
  byId("thumbnail-size").addEventListener("input", event => {
    byId("photo-grid").style.setProperty("--tile-size", `${event.target.value}px`);
  });

  loadPhotos();
})();
