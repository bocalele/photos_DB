(() => {
  const pageSize = 200;
  const photos = [];
  const photosByName = new Map();
  const cardsByName = new Map();
  const selectedNames = new Set();
  const validatedNames = new Set();
  const ratingSaveTokens = new Map();
  let lastClickedIndex = -1;
  let totalPhotos = 0;
  let loaded = false;
  let loading = false;
  let saving = false;
  let thumbnailObserver;
  let defaultAuthor = "Pierre";
  let defaultRating = 3;

  // Editor state
  let editorKeywords = [];
  let editorKeywordsTouched = false;
  let editorAuthorInitial = "";
  let editorRating = null;
  let editorRatingMixed = false;
  let editorRatingTouched = false;

  const byId = id => document.getElementById(id);

  async function requestJson(url, options) {
    const response = await fetch(url, options);
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || `Erreur HTTP ${response.status}`);
    return data;
  }

  async function loadPreferences() {
    try {
      const data = await requestJson("/api/preferences");
      defaultAuthor = data.default_author || "Pierre";
      defaultRating = data.default_rating ?? 3;
    } catch (error) {
      console.warn("Impossible de charger les préférences :", error);
    }
  }

  async function loadPhotos() {
    if (loading || loaded) return;
    loading = true;
    try {
      await loadPreferences();
      resetGrid();
      do {
        const startIndex = photos.length;
        const data = await requestJson(
          `/api/photos?offset=${photos.length}&limit=${pageSize}`
        );
        if (!data.photos.length) break;
        for (const photo of data.photos) {
          photos.push(photo);
          photosByName.set(photo.name, photo);
        }
        totalPhotos = data.total;
        byId("directory-path").textContent = data.directory;
        appendPhotoCards(data.photos, startIndex);
        updatePhotoCount();
      } while (photos.length < totalPhotos);
      loaded = true;
      if (!photos.length) {
        const empty = document.createElement("p");
        empty.className = "message";
        empty.textContent = "Aucune photo dans ce dossier.";
        byId("photo-grid").appendChild(empty);
      }
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
    byId("edit-keywords").disabled = selectedNames.size === 0 || saving;
  }

  function showMessage(message, isError = false) {
    byId("photo-count").textContent = message;
    byId("photo-count").classList.toggle("error", isError);
  }

  function resetGrid() {
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
        }, { root: byId("photo-grid"), rootMargin: "300px 0px" })
      : null;
    byId("photo-grid").replaceChildren();
  }

  function appendPhotoCards(pagePhotos, startIndex) {
    const grid = byId("photo-grid");
    for (let i = 0; i < pagePhotos.length; i++) {
      const photo = pagePhotos[i];
      const index = startIndex + i;
      const card = document.createElement("article");
      card.className = "photo-card";
      card.title = photo.name;
      card.dataset.index = index;
      card.dataset.name = photo.name;

      const selectionControls = document.createElement("div");
      selectionControls.className = "photo-selection-controls";
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.className = "photo-checkbox";
      checkbox.checked = selectedNames.has(photo.name);
      checkbox.setAttribute("aria-label", `Sélectionner ${photo.name}`);
      checkbox.addEventListener("change", () => toggleSelection(photo.name, index));
      selectionControls.appendChild(checkbox);

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

      card.append(selectionControls, image, buildCaption(photo));
      card.addEventListener("click", event => {
        if (event.target.closest(".photo-selection-controls")) return;
        if (event.target.closest(".photo-rating")) return;
        handleCardClick(photo.name, index, event);
      });

      grid.appendChild(card);
      cardsByName.set(photo.name, card);
      updateCardSelection(photo.name);
      if (thumbnailObserver) thumbnailObserver.observe(image);
      else image.src = image.dataset.src;
    }
  }

  function buildCaption(photo) {
    const caption = document.createElement("div");
    caption.className = "photo-caption";

    const filename = document.createElement("div");
    filename.className = "filename";
    filename.textContent = photo.name;
    caption.appendChild(filename);

    caption.appendChild(buildCardRating(photo));

    if (photo.keywords && photo.keywords.length) {
      const keywords = document.createElement("div");
      keywords.className = "photo-keywords";
      for (const keyword of photo.keywords.slice(0, 6)) {
        const tag = document.createElement("span");
        tag.className = "keyword-tag";
        tag.textContent = keyword;
        keywords.appendChild(tag);
      }
      if (photo.keywords.length > 6) {
        const more = document.createElement("span");
        more.className = "keyword-tag";
        more.textContent = `+${photo.keywords.length - 6}`;
        keywords.appendChild(more);
      }
      caption.appendChild(keywords);
    }

    if (photo.author) {
      const author = document.createElement("div");
      author.className = "photo-author";
      author.textContent = `👤 ${photo.author}`;
      caption.appendChild(author);
    }

    return caption;
  }

  function buildCardRating(photo) {
    const row = document.createElement("div");
    row.className = "photo-rating";
    row.setAttribute("role", "group");
    row.setAttribute("aria-label", `Note de ${photo.name}`);
    const value = photo.rating || 0;
    for (let star = 1; star <= 5; star++) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = `star${star <= value ? " filled" : ""}`;
      button.dataset.value = star;
      button.title = `Noter ${star} étoile${star > 1 ? "s" : ""}`;
      button.setAttribute("aria-label", `Noter ${photo.name} ${star} étoile${star > 1 ? "s" : ""}`);
      button.textContent = "★";
      button.addEventListener("click", event => {
        event.stopPropagation();
        saveRating(photo.name, star);
      });
      row.appendChild(button);
    }
    row.addEventListener("mouseleave", () => highlightCardStars(row, value, null));
    row.addEventListener("mouseover", event => {
      const button = event.target.closest(".star");
      if (!button) return;
      highlightCardStars(row, value, Number(button.dataset.value));
    });
    return row;
  }

  function highlightCardStars(row, value, hover) {
    row.querySelectorAll(".star").forEach(button => {
      const star = Number(button.dataset.value);
      button.classList.toggle("filled", star <= value);
      button.classList.toggle("hover", hover !== null && star <= hover);
    });
  }

  function refreshCard(name) {
    const card = cardsByName.get(name);
    const photo = photosByName.get(name);
    if (!card || !photo) return;
    const oldCaption = card.querySelector(".photo-caption");
    const newCaption = buildCaption(photo);
    oldCaption.replaceWith(newCaption);
  }

  function handleCardClick(name, index, event) {
    if (event.shiftKey && lastClickedIndex >= 0) {
      const start = Math.min(lastClickedIndex, index);
      const end = Math.max(lastClickedIndex, index);
      for (let i = start; i <= end; i++) {
        selectedNames.add(photos[i].name);
        updateCardSelection(photos[i].name);
      }
    } else {
      if (selectedNames.has(name)) selectedNames.delete(name);
      else selectedNames.add(name);
      updateCardSelection(name);
    }
    lastClickedIndex = index;
    updatePhotoCount();
  }

  function toggleSelection(name, index) {
    if (selectedNames.has(name)) selectedNames.delete(name);
    else selectedNames.add(name);
    lastClickedIndex = index;
    updateCardSelection(name);
    updatePhotoCount();
  }

  function updateCardSelection(name) {
    const card = cardsByName.get(name);
    if (!card) return;
    const selected = selectedNames.has(name);
    card.classList.toggle("selected", selected);
    card.classList.toggle("validated", validatedNames.has(name));
    const checkbox = card.querySelector(".photo-checkbox");
    if (checkbox) checkbox.checked = selected;
  }

  async function saveRating(name, value) {
    const photo = photosByName.get(name);
    if (!photo) return;
    const token = (ratingSaveTokens.get(name) || 0) + 1;
    ratingSaveTokens.set(name, token);
    const previous = photo.rating;
    photo.rating = value;
    refreshCard(name);
    try {
      const data = await requestJson("/api/update-photo", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ photoName: name, rating: value }),
      });
      if (ratingSaveTokens.get(name) !== token) return;
      Object.assign(photo, data.metadata);
      validatedNames.add(name);
      refreshCard(name);
      updateCardSelection(name);
      updatePhotoCount();
    } catch (error) {
      if (ratingSaveTokens.get(name) !== token) return;
      photo.rating = previous;
      refreshCard(name);
      showMessage(`Impossible d’enregistrer la note de ${name} : ${error.message}`, true);
    }
  }

  // ------------------------------------------------------------------ //
  // Selection helpers
  // ------------------------------------------------------------------ //
  function collectSelectedData() {
    const selectedPhotos = photos.filter(photo => selectedNames.has(photo.name));
    if (!selectedPhotos.length) return null;

    const keywordSets = selectedPhotos.map(photo => new Set(photo.keywords || []));
    const allKeywords = new Set();
    const commonKeywords = new Set(keywordSets[0]);
    for (const set of keywordSets) {
      for (const keyword of set) allKeywords.add(keyword);
    }
    for (const set of keywordSets) {
      for (const keyword of [...commonKeywords]) {
        if (!set.has(keyword)) commonKeywords.delete(keyword);
      }
    }

    const authors = [...new Set(selectedPhotos.map(photo => photo.author).filter(Boolean))];
    const ratings = [...new Set(
      selectedPhotos.map(photo => photo.rating).filter(value => value != null)
    )];

    return {
      allKeywords: [...allKeywords].sort((a, b) => a.localeCompare(b, "fr")),
      commonKeywords,
      authors,
      ratings,
    };
  }

  // ------------------------------------------------------------------ //
  // Editor
  // ------------------------------------------------------------------ //
  function openEditor() {
    const data = collectSelectedData();
    if (!data) return;

    byId("selected-count").textContent = `${selectedNames.size} photo(s) sélectionnée(s)`;

    editorKeywords = data.allKeywords.map(keyword => ({
      value: keyword,
      common: data.commonKeywords.has(keyword),
    }));
    editorKeywordsTouched = false;
    renderKeywordChips();

    const authorInput = byId("author-input");
    if (data.authors.length === 1) {
      editorAuthorInitial = data.authors[0];
      authorInput.value = data.authors[0];
    } else if (data.authors.length > 1) {
      editorAuthorInitial = "";
      authorInput.value = "";
    } else {
      editorAuthorInitial = "";
      authorInput.value = defaultAuthor;
    }
    authorInput.placeholder = data.authors.length > 1
      ? "Auteurs différents — saisir une valeur pour toutes les photos"
      : "Nom de l'auteur";

    editorRatingMixed = data.ratings.length > 1;
    if (editorRatingMixed) {
      editorRating = null;
      editorRatingTouched = false;
    } else if (data.ratings.length === 1) {
      editorRating = data.ratings[0];
      editorRatingTouched = false;
    } else {
      editorRating = defaultRating;
      editorRatingTouched = true;
    }
    renderEditorRating();

    byId("keyword-input").value = "";
    byId("save-status").textContent = "";
    byId("save-status").className = "";
    byId("save-progress").hidden = true;
    byId("save-errors").replaceChildren();

    const overlay = byId("editor-overlay");
    overlay.classList.add("open");
    overlay.setAttribute("aria-hidden", "false");
    byId("keyword-input").focus();
  }

  function closeEditor() {
    const overlay = byId("editor-overlay");
    overlay.classList.remove("open");
    overlay.setAttribute("aria-hidden", "true");
  }

  function renderKeywordChips() {
    const container = byId("keyword-chips");
    container.replaceChildren();
    if (!editorKeywords.length) {
      const empty = document.createElement("span");
      empty.className = "keyword-empty";
      empty.textContent = "Aucun mot-clé pour ces photos";
      container.appendChild(empty);
      return;
    }
    for (const keyword of editorKeywords) {
      const chip = document.createElement("span");
      chip.className = `keyword-chip${keyword.common ? "" : " mixed"}`;
      chip.title = keyword.common
        ? "Présent dans toutes les photos sélectionnées"
        : "Présent dans seulement certaines photos sélectionnées";
      const label = document.createElement("span");
      label.textContent = keyword.value;
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "chip-remove";
      remove.textContent = "×";
      remove.setAttribute("aria-label", `Retirer ${keyword.value}`);
      remove.addEventListener("click", () => {
        editorKeywords = editorKeywords.filter(item => item !== keyword);
        editorKeywordsTouched = true;
        renderKeywordChips();
        byId("keyword-input").focus();
      });
      chip.append(label, remove);
      container.appendChild(chip);
    }
  }

  function addKeywordsFromInput() {
    const input = byId("keyword-input");
    const parts = input.value.split(",").map(part => part.trim()).filter(Boolean);
    let added = false;
    for (const value of parts) {
      const exists = editorKeywords.some(
        item => item.value.toLocaleLowerCase("fr") === value.toLocaleLowerCase("fr")
      );
      if (!exists) {
        editorKeywords.push({ value, common: true });
        added = true;
      }
    }
    input.value = "";
    if (added) editorKeywordsTouched = true;
    renderKeywordChips();
  }

  function renderEditorRating() {
    const container = byId("rating-stars");
    container.replaceChildren();
    for (let star = 1; star <= 5; star++) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "star";
      button.dataset.value = star;
      button.textContent = "★";
      button.setAttribute("aria-label", `${star} étoile${star > 1 ? "s" : ""}`);
      if (editorRatingMixed) {
        button.classList.add("mixed");
      } else if (editorRating != null && star <= editorRating) {
        button.classList.add("filled");
      }
      button.addEventListener("click", () => {
        editorRating = star;
        editorRatingMixed = false;
        editorRatingTouched = true;
        renderEditorRating();
      });
      container.appendChild(button);
    }
    const display = byId("rating-display");
    if (editorRatingMixed) {
      display.textContent = "notes différentes";
      display.className = "rating-display mixed-text";
    } else if (editorRating != null) {
      display.textContent = `${editorRating}/5`;
      display.className = "rating-display";
    } else {
      display.textContent = "aucune note";
      display.className = "rating-display";
    }
  }

  function getEditorData() {
    const authorInput = byId("author-input");
    const authorValue = authorInput.value.trim();
    const authorChanged = authorValue !== editorAuthorInitial;

    return {
      photoNames: [...selectedNames],
      keywords: editorKeywordsTouched
        ? editorKeywords.map(item => item.value)
        : null,
      author: authorChanged ? authorValue : null,
      rating: editorRatingTouched ? editorRating : null,
    };
  }

  // ------------------------------------------------------------------ //
  // Saving
  // ------------------------------------------------------------------ //
  async function saveKeywords() {
    if (!selectedNames.size || saving) return;
    const data = getEditorData();
    if (data.keywords === null && data.author === null && data.rating === null) {
      closeEditor();
      return;
    }

    const button = byId("save-keywords");
    const status = byId("save-status");
    button.disabled = true;
    byId("save-progress").hidden = false;
    byId("save-progress-bar").removeAttribute("value");
    byId("save-errors").replaceChildren();
    status.textContent = "Préparation de l’écriture dans les fichiers…";
    status.className = "";
    saving = true;
    updatePhotoCount();

    try {
      const started = await requestJson("/api/save-metadata", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(data),
      });
      let job;
      const startedAt = Date.now();
      do {
        await new Promise(resolve => setTimeout(resolve, 500));
        job = await requestJson(
          `/api/save-metadata?jobId=${encodeURIComponent(started.jobId)}`
        );
        const progress = byId("save-progress-bar");
        progress.max = job.total || 1;
        progress.value = job.done;
        status.textContent =
          `Écriture des métadonnées : ${job.done}/${job.total} photo(s) traitée(s), ` +
          `${job.updated.length} enregistrée(s), ${job.errors.length} erreur(s).`;
        if (!job.complete && Date.now() - startedAt > 15 * 60 * 1000) {
          throw new Error(
            "La sauvegarde ne répond plus (délai de 15 minutes dépassé). " +
            "Consultez le terminal pour plus de détails."
          );
        }
      } while (!job.complete);

      const successfulNames = new Set(job.updated);
      for (const photo of photos) {
        if (!successfulNames.has(photo.name)) continue;
        if (data.keywords !== null) photo.keywords = data.keywords;
        if (data.rating !== null) photo.rating = data.rating;
        if (data.author !== null) photo.author = data.author;
        photo.metadataRead = true;
        validatedNames.add(photo.name);
      }
      for (const name of successfulNames) {
        selectedNames.delete(name);
        refreshCard(name);
        updateCardSelection(name);
      }
      updatePhotoCount();

      for (const error of job.errors) {
        const item = document.createElement("li");
        item.textContent = `${error.filename || "Sauvegarde"} : ${error.message}`;
        byId("save-errors").appendChild(item);
      }

      if (!job.errors.length) {
        closeEditor();
      } else {
        status.textContent =
          `${job.updated.length} photo(s) enregistrée(s), ` +
          `${job.errors.length} en erreur. Les erreurs sont détaillées ci-dessous.`;
        status.className = "error";
      }
    } catch (error) {
      status.textContent = error.message;
      status.className = "error";
    } finally {
      button.disabled = false;
      saving = false;
      updatePhotoCount();
    }
  }

  // ------------------------------------------------------------------ //
  // Events
  // ------------------------------------------------------------------ //
  byId("thumbnail-size").addEventListener("input", event => {
    byId("photo-grid").style.setProperty("--tile-size", `${event.target.value}px`);
  });
  byId("select-all").addEventListener("click", () => {
    for (const photo of photos) {
      selectedNames.add(photo.name);
      updateCardSelection(photo.name);
    }
    lastClickedIndex = photos.length - 1;
    updatePhotoCount();
  });
  byId("deselect-photos").addEventListener("click", () => {
    selectedNames.clear();
    for (const photo of photos) updateCardSelection(photo.name);
    lastClickedIndex = -1;
    updatePhotoCount();
  });
  byId("change-directory").addEventListener("click", async () => {
    const button = byId("change-directory");
    try {
      button.disabled = true;
      button.textContent = "Chargement…";
      const data = await requestJson("/api/change-directory", { method: "POST" });
      if (data.directory) {
        byId("directory-path").textContent = data.directory;
        photos.length = 0;
        photosByName.clear();
        cardsByName.clear();
        totalPhotos = 0;
        loaded = false;
        selectedNames.clear();
        validatedNames.clear();
        lastClickedIndex = -1;
        await loadPhotos();
      }
    } catch (error) {
      showMessage(`Impossible de changer de dossier : ${error.message}`, true);
    } finally {
      button.disabled = false;
      button.textContent = "Changer de dossier";
    }
  });
  byId("edit-keywords").addEventListener("click", openEditor);
  byId("cancel").addEventListener("click", closeEditor);
  byId("save-keywords").addEventListener("click", saveKeywords);
  byId("keyword-add-button").addEventListener("click", addKeywordsFromInput);
  byId("keyword-input").addEventListener("keydown", event => {
    if (event.key === "Enter") {
      event.preventDefault();
      addKeywordsFromInput();
    }
  });
  byId("keyword-input").addEventListener("input", event => {
    if (event.target.value.includes(",")) addKeywordsFromInput();
  });
  byId("editor-overlay").addEventListener("click", event => {
    if (event.target.id === "editor-overlay") closeEditor();
  });
  byId("keywords-form").addEventListener("submit", event => event.preventDefault());
  document.addEventListener("keydown", event => {
    if (event.key === "Escape") closeEditor();
  });

  loadPhotos();
})();
