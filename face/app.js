(() => {
  const byId = id => document.getElementById(id);
  const normalize = value =>
    value.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

  const state = {
    photos: [],
    total: 0,
    filter: "all",
    mode: "photos",
    pending: [],
    people: [],
    photo: null,
    selectedFace: null,
    selected: new Set(),
    lastSelectedIndex: -1,
    saving: false,
    loaded: false,
  };
  const cardByName = new Map();
  const pendingByKey = new Map();
  let thumbnailObserver;
  let pendingObserver;
  let openToken = 0;
  let pendingToken = 0;
  let saveTimer;
  let saveQueued = false;
  let pendingReload = false;

  async function api(url, options) {
    const response = await fetch(url, options);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `Erreur HTTP ${response.status}`);
    return data;
  }

  // --- chargement ---------------------------------------------------------

  async function loadState() {
    try {
      const data = await api("/api/state");
      byId("directory-path").textContent = data.directory;
      byId("directory-path").title = data.directory;
      const gallery = data.gallery || {};
      byId("photo-count").title =
        `${gallery.people} personne(s) de référence, ${gallery.exemplars} visage(s) étalon, ` +
        `seuil ${Number(gallery.threshold || 0).toFixed(2)}`;
    } catch (error) {
      showMessage(error.message, true);
    }
  }

  async function loadPeople() {
    try {
      const data = await api("/api/people");
      state.people = data.people;
    } catch (error) {
      showMessage(error.message, true);
    }
  }

  async function loadPhotos() {
    const token = ++openToken;
    state.loaded = false;
    state.photos = [];
    state.total = 0;
    clearSelection();
    byId("photo-grid").replaceChildren(message("Chargement des photos…"));
    try {
      let offset = 0;
      do {
        const data = await api(
          `/api/photos?offset=${offset}&limit=500&filter=${encodeURIComponent(state.filter)}`
        );
        if (token !== openToken) return;
        state.photos.push(...data.photos);
        state.total = data.total;
        offset = state.photos.length;
        byId("photo-count").textContent =
          `${state.photos.length} / ${state.total} photo(s)` +
          (state.filter === "all" ? "" : " (filtrées)");
        byId("directory-path").textContent = data.directory;
        byId("directory-path").title = data.directory;
      } while (state.photos.length < state.total);
      state.loaded = true;
      renderPhotos();
    } catch (error) {
      byId("photo-grid").replaceChildren(message(error.message, true));
    }
  }

  function message(text, isError = false) {
    const element = document.createElement("div");
    element.className = "message";
    element.textContent = text;
    if (isError) element.style.color = "var(--danger)";
    return element;
  }

  function showMessage(text, isError = false) {
    byId("photo-count").textContent = text;
    byId("photo-count").style.color = isError ? "var(--danger)" : "";
  }

  function updateSelectionControls() {
    byId("select-all").disabled = state.mode !== "photos" || !state.photos.length;
    byId("deselect-photos").disabled = state.selected.size === 0;
    byId("analyze-selection").disabled = state.selected.size === 0;
  }

  function clearSelection() {
    state.selected.clear();
    state.lastSelectedIndex = -1;
    updateSelectionControls();
  }

  function updateCardSelection(name) {
    const card = cardByName.get(name);
    if (!card) return;
    const selected = state.selected.has(name);
    card.classList.toggle("selected", selected);
    const checkbox = card.querySelector(".photo-checkbox");
    if (checkbox) checkbox.checked = selected;
  }

  function toggleSelection(name, index) {
    if (state.selected.has(name)) state.selected.delete(name);
    else state.selected.add(name);
    state.lastSelectedIndex = index;
    updateCardSelection(name);
    updateSelectionControls();
  }

  function selectPhotoRange(index) {
    if (state.lastSelectedIndex < 0) {
      const photo = state.photos[index];
      if (photo) state.selected.add(photo.name);
    } else {
      const start = Math.min(state.lastSelectedIndex, index);
      const end = Math.max(state.lastSelectedIndex, index);
      for (let position = start; position <= end; position++) {
        const photo = state.photos[position];
        if (photo) state.selected.add(photo.name);
      }
    }
    state.lastSelectedIndex = index;
    for (const photo of state.photos) updateCardSelection(photo.name);
    updateSelectionControls();
  }

  function handlePhotoClick(name, index, event) {
    if (event.shiftKey) {
      selectPhotoRange(index);
      return;
    }
    if (event.metaKey || event.ctrlKey) {
      toggleSelection(name, index);
      return;
    }
    openPhoto(name);
  }

  function selectAllPhotos() {
    for (const photo of state.photos) state.selected.add(photo.name);
    state.lastSelectedIndex = state.photos.length - 1;
    for (const photo of state.photos) updateCardSelection(photo.name);
    updateSelectionControls();
  }

  // --- galerie ------------------------------------------------------------

  function badgeFor(photo) {
    if (photo.error) return { text: "erreur", className: "error", title: photo.error };
    if (!photo.indexed) return { text: "non analysée", className: "missing", title: "Aucune analyse de visages pour cette photo." };
    const invalid = photo.nInvalid || 0;
    const invalidNote = invalid
      ? ` ${invalid} détection(s) invalidée(s).`
      : "";
    if (!photo.nFaces) {
      return {
        text: "0 visage",
        className: "missing",
        title: invalid
          ? `Aucun visage valide.${invalidNote}`
          : "Aucun visage détecté.",
      };
    }
    const ignored = photo.nIgnored || 0;
    const resolved = photo.nResolved ?? (photo.nNamed + ignored);
    if (resolved >= photo.nFaces) {
      if (!photo.nNamed) {
        return {
          text: "0 nom ✓",
          className: "present",
          title: `Tous les visages sont validés sans nom.${invalidNote}`,
        };
      }
      return {
        text: ignored ? `${photo.nNamed} nom(s) ✓` : `${photo.nFaces} ✓`,
        className: "present",
        title: (ignored
          ? `${photo.nNamed} visage(s) nommé(s), ${ignored} validé(s) sans nom.`
          : "Tous les visages sont nommés.") + invalidNote,
      };
    }
    const missing = photo.nFaces - resolved;
    return {
      text: `${resolved}/${photo.nFaces}`,
      className: "unknown",
      title: `${photo.nNamed} nommé(s), ${ignored} sans nom, ${missing} à décider.${invalidNote}`,
    };
  }

  function setBadge(card, photo) {
    const badge = card.querySelector(".photo-badge");
    if (!badge) return;
    const info = badgeFor(photo);
    badge.textContent = info.text;
    badge.title = info.title;
    badge.className = `photo-badge ${info.className}`;
  }

  function renderPhotos() {
    const grid = byId("photo-grid");
    cardByName.clear();
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

    const named = state.photos.filter(
      photo => photo.indexed && photo.nFaces > 0 && photo.nNamed >= photo.nFaces
    ).length;
    if (state.loaded) {
      byId("photo-count").textContent =
        `${state.photos.length} photo(s) · ${named} entièrement nommée(s)` +
        (state.selected.size ? ` · ${state.selected.size} sélectionnée(s)` : "") +
        (state.filter === "all" ? "" : " (filtrées)");
      byId("photo-count").style.color = "";
    }
    updateSelectionControls();

    grid.replaceChildren();
    if (!state.photos.length) {
      grid.appendChild(
        message(state.loaded ? "Aucune photo dans ce dossier ou ce filtre." : "Chargement des photos…")
      );
      return;
    }

    for (const [index, photo] of state.photos.entries()) {
      const card = document.createElement("figure");
      const selected = state.selected.has(photo.name);
      card.className = `photo-card${selected ? " selected" : ""}`;
      card.dataset.name = photo.name;
      card.title = `${photo.name}\nMaj + clic : sélectionner une plage`;

      const info = badgeFor(photo);
      const badge = document.createElement("span");
      badge.className = `photo-badge ${info.className}`;
      badge.textContent = info.text;
      badge.title = info.title;

      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.className = "photo-checkbox";
      checkbox.checked = selected;
      checkbox.setAttribute("aria-label", `Sélectionner ${photo.displayName}`);
      checkbox.addEventListener("change", () => toggleSelection(photo.name, index));

      const image = document.createElement("img");
      image.dataset.src = photo.thumbnail;
      image.alt = photo.displayName;
      image.loading = "lazy";
      image.decoding = "async";
      image.addEventListener("error", () => {
        image.removeAttribute("src");
        image.alt = "Aperçu indisponible";
      });

      const caption = document.createElement("figcaption");
      caption.textContent = photo.displayName;

      const relative = document.createElement("div");
      relative.className = "photo-relative";
      relative.textContent = photo.relative || "";

      card.append(checkbox, badge, image, caption, relative);
      card.addEventListener("click", event => {
        if (event.target === checkbox) return;
        handlePhotoClick(photo.name, index, event);
      });
      grid.appendChild(card);
      cardByName.set(photo.name, card);
      if (thumbnailObserver) thumbnailObserver.observe(image);
      else image.src = image.dataset.src;
    }
  }

  // --- visages à vérifier -------------------------------------------------

  async function loadPending() {
    const token = ++pendingToken;
    clearSelection();
    const grid = byId("photo-grid");
    grid.replaceChildren(message("Recherche des visages à décider…"));
    try {
      const data = await api("/api/pending?limit=600");
      if (token !== pendingToken) return;
      state.pending = data.faces;
      byId("pending-count").textContent = String(data.total);
      byId("photo-count").textContent = data.total
        ? `${data.total} visage(s) à décider, du plus incertain au plus sûr`
        : "Aucun visage à décider dans ce dossier.";
      byId("photo-count").style.color = "";
      renderPending();
    } catch (error) {
      grid.replaceChildren(message(error.message, true));
    }
  }

  function renderPending() {
    const grid = byId("photo-grid");
    pendingByKey.clear();
    pendingObserver?.disconnect();
    pendingObserver = "IntersectionObserver" in window
      ? new IntersectionObserver((entries, observer) => {
          for (const entry of entries) {
            if (!entry.isIntersecting) continue;
            const image = entry.target;
            if (image.dataset.src) {
              image.src = image.dataset.src;
              image.removeAttribute("data-src");
            }
            observer.unobserve(image);
          }
        }, { root: grid, rootMargin: "300px 0px" })
      : null;

    grid.replaceChildren();
    if (!state.pending.length) {
      grid.appendChild(
        message("Aucun visage à décider dans ce dossier : tout est nommé ou validé sans nom.")
      );
      return;
    }

    for (const item of state.pending) {
      const key = `${item.name}#${item.faceIndex}`;
      const card = document.createElement("figure");
      card.className = "face-card";
      card.title = item.hasProposal
        ? `${item.name} · proposition ${item.person} · confiance ${(item.score * 100).toFixed(0)} %`
        : `${item.name} · aucune proposition fiable`;

      const image = document.createElement("img");
      image.alt = item.person || "Visage sans proposition";
      image.decoding = "async";
      if (pendingObserver) {
        image.dataset.src = item.crop;
        pendingObserver.observe(image);
      } else {
        image.src = item.crop;
      }

      const body = document.createElement("div");
      body.className = "face-card-body";
      const name = document.createElement("div");
      name.className = "face-card-name";
      name.textContent = item.hasProposal ? item.person : "Aucune proposition";
      if (!item.hasProposal) name.classList.add("no-proposal");
      const meta = document.createElement("div");
      meta.className = "face-card-meta";
      const file = document.createElement("span");
      file.textContent = item.name.split("/").pop();
      const score = document.createElement("span");
      score.className = "face-card-score";
      score.textContent = item.hasProposal
        ? item.score.toFixed(2).replace(".", ",")
        : "—";
      meta.append(file, score);
      body.append(name, meta);
      card.append(image, body);
      card.addEventListener("click", () => openPhoto(item.name, item.faceIndex));
      grid.appendChild(card);
      pendingByKey.set(key, card);
    }
  }

  function removePendingCard(name, faceIndex) {
    const key = `${name}#${faceIndex}`;
    const card = pendingByKey.get(key);
    if (!card) return;
    pendingByKey.delete(key);
    card.remove();
    state.pending = state.pending.filter(
      item => !(item.name === name && item.faceIndex === faceIndex)
    );
    byId("pending-count").textContent = String(state.pending.length);
    byId("photo-count").textContent = state.pending.length
      ? `${state.pending.length} visage(s) à décider, du plus incertain au plus sûr`
      : "Aucun visage à décider dans ce dossier.";
  }

  function setMode(mode) {
    state.mode = mode;
    clearSelection();
    document.querySelectorAll(".mode").forEach(button =>
      button.classList.toggle("active", button.dataset.mode === mode)
    );
    byId("photo-filters").hidden = mode !== "photos";
    byId("photo-grid").classList.toggle("faces-mode", mode === "faces");
    updateSelectionControls();
    if (mode === "faces") loadPending();
    else loadPhotos();
  }

  // --- photo --------------------------------------------------------------

  // Les détecteurs peuvent renvoyer une boîte qui dépasse l'image. On la ramène
  // légèrement à l'intérieur pour qu'elle reste cliquable et validable, même
  // avec un serveur qui n'aurait pas encore été redémarré.
  function clampFaceBox(bbox) {
    if (!Array.isArray(bbox) || bbox.length !== 4) return bbox;
    const values = bbox.map(Number);
    if (!values.every(Number.isFinite)) return bbox;
    let [x1, y1, x2, y2] = values;
    [x1, x2] = [Math.min(x1, x2), Math.max(x1, x2)];
    [y1, y2] = [Math.min(y1, y2), Math.max(y1, y2)];
    if (x1 >= 0 && y1 >= 0 && x2 <= 1 && y2 <= 1) return [x1, y1, x2, y2];
    const inset = 0.002;
    const minimum = 0.004;
    const low = inset;
    const high = 1 - inset;
    x1 = Math.min(Math.max(x1, low), high);
    y1 = Math.min(Math.max(y1, low), high);
    x2 = Math.min(Math.max(x2, low), high);
    y2 = Math.min(Math.max(y2, low), high);
    if (x2 - x1 < minimum) {
      const center = Math.min(Math.max((x1 + x2) / 2, low + minimum / 2), high - minimum / 2);
      x1 = center - minimum / 2;
      x2 = center + minimum / 2;
    }
    if (y2 - y1 < minimum) {
      const center = Math.min(Math.max((y1 + y2) / 2, low + minimum / 2), high - minimum / 2);
      y1 = center - minimum / 2;
      y2 = center + minimum / 2;
    }
    return [x1, y1, x2, y2];
  }

  function hydratePhoto(data) {
    for (const face of data.faces) {
      face.bbox = clampFaceBox(face.bbox);
      face.current = face.existing
        ? { id_people: face.existing.id_people, name: face.existing.name }
        : null;
      face.ignored = Boolean(face.ignored) && !face.current;
      face.invalid = Boolean(face.invalid);
      if (face.invalid) {
        face.current = null;
        face.ignored = false;
      }
      face.dirty = false;
    }
    state.photo = data;
  }

  async function openPhoto(name, faceIndex) {
    const token = ++openToken;
    byId("viewer-empty").hidden = true;
    byId("viewer-body").hidden = false;
    byId("viewer-title").textContent = name.split("/").pop();
    byId("viewer-subtitle").textContent = "Analyse de la photo…";
    byId("face-layer").replaceChildren();
    for (const card of cardByName.values()) card.classList.remove("active");
    cardByName.get(name)?.classList.add("active");

    try {
      const data = await api(`/api/photo?name=${encodeURIComponent(name)}`);
      if (token !== openToken) return;
      hydratePhoto(data);
      const preferred = Number.isInteger(faceIndex)
        ? data.faces.find(face => face.index === faceIndex)
        : null;
      const unnamed = data.faces.find(
        face => !face.invalid && !face.current && !face.ignored
      );
      state.selectedFace = preferred
        ? preferred.index
        : unnamed
          ? unnamed.index
          : (data.faces[0]?.index ?? null);
      renderViewer();
    } catch (error) {
      byId("viewer-subtitle").textContent = error.message;
    }
  }

  function renderViewer() {
    const data = state.photo;
    if (!data) return;
    const validFaces = data.faces.filter(face => !face.invalid);
    const invalid = data.faces.length - validFaces.length;
    const named = validFaces.filter(face => face.current).length;
    const ignored = validFaces.filter(face => face.ignored).length;
    const pending = validFaces.length - named - ignored;
    const orphanNote = data.orphans?.length
      ? ` · ${data.orphans.length} nom(s) déjà présent(s) hors détection`
      : "";
    const invalidNote = invalid
      ? ` · ${invalid} détection(s) invalidée(s)`
      : "";
    byId("viewer-title").textContent = data.displayName;
    byId("viewer-subtitle").textContent =
      `${validFaces.length} visage(s) · ${named} nommé(s) · ` +
      `${ignored} sans nom · ${pending} à décider` +
      `${invalidNote}${orphanNote}`;
    byId("validate-all").title =
      `Accepte les propositions avec un score ≥ ${Number(data.threshold).toFixed(2).replace(".", ",")}`;

    const image = byId("viewer-image");
    if (image.dataset.name !== data.name || image.dataset.src !== data.image) {
      image.dataset.name = data.name;
      image.dataset.src = data.image;
      image.src = data.image;
      image.alt = data.displayName;
    }
    if (image.complete && image.naturalWidth) fitStage();
    renderFaces();
    renderEditor();
  }

  function fitStage() {
    const image = byId("viewer-image");
    const stage = byId("stage");
    const wrap = stage.parentElement;
    if (!image.naturalWidth || !wrap) return;
    const availableWidth = Math.max(60, wrap.clientWidth - 28);
    const availableHeight = Math.max(60, wrap.clientHeight - 28);
    // Une toute petite image (aperçu de 160 px d'un PSD, par exemple) est
    // agrandie pour rester lisible ; les photos normales ne sont jamais agrandies.
    const limit = Math.max(image.naturalWidth, image.naturalHeight) < 700 ? 4 : 1;
    const scale = Math.min(
      availableWidth / image.naturalWidth,
      availableHeight / image.naturalHeight,
      limit
    );
    stage.style.width = `${Math.max(1, Math.round(image.naturalWidth * scale))}px`;
    stage.style.height = `${Math.max(1, Math.round(image.naturalHeight * scale))}px`;
  }

  function faceLabel(face) {
    if (face.invalid) return { text: "pas un visage", score: "" };
    if (face.current) return { text: face.current.name, score: "" };
    if (face.ignored) return { text: "sans nom", score: "" };
    if (face.proposal) {
      return {
        text: face.proposal.name,
        score: face.proposal.score.toFixed(2).replace(".", ","),
      };
    }
    return { text: "?", score: "" };
  }

  function renderFaces() {
    const layer = byId("face-layer");
    layer.replaceChildren();
    if (!state.photo) return;
    for (const face of state.photo.faces) {
      const box = document.createElement("div");
      box.className = "face-box";
      if (face.invalid) box.classList.add("invalid");
      else if (face.current) box.classList.add("named");
      else if (face.ignored) box.classList.add("ignored");
      else if (face.proposal) box.classList.add("proposal");
      else box.classList.add("unknown");
      if (face.index === state.selectedFace) box.classList.add("selected");
      box.style.left = `${face.bbox[0] * 100}%`;
      box.style.top = `${face.bbox[1] * 100}%`;
      box.style.width = `${(face.bbox[2] - face.bbox[0]) * 100}%`;
      box.style.height = `${(face.bbox[3] - face.bbox[1]) * 100}%`;
      box.title = face.invalid
        ? "Détection invalidée : ce n'est pas un visage"
        : face.current
          ? `Nommé : ${face.current.name}`
          : face.ignored
            ? "Validé sans nom"
            : face.proposal
              ? `Proposition : ${face.proposal.name} (${face.proposal.score.toFixed(2)})`
              : "Visage sans proposition";

      const label = faceLabel(face);
      const chip = document.createElement("span");
      chip.className = "face-chip";
      chip.textContent = label.text;
      if (label.score) {
        const score = document.createElement("span");
        score.className = "score";
        score.textContent = ` ${label.score}`;
        chip.append(score);
      }
      box.appendChild(chip);
      box.addEventListener("click", event => {
        event.stopPropagation();
        state.selectedFace = face.index;
        renderFaces();
        renderEditor();
      });
      layer.appendChild(box);
    }
  }

  function selectedFace() {
    if (!state.photo || state.selectedFace === null) return null;
    return state.photo.faces.find(face => face.index === state.selectedFace) || null;
  }

  function photoNavigationOrder() {
    const items = state.mode === "faces" && state.pending.length
      ? state.pending.map(item => item.name)
      : state.photos.map(photo => photo.name);
    const names = [];
    const seen = new Set();
    for (const name of items) {
      if (seen.has(name)) continue;
      seen.add(name);
      names.push(name);
    }
    return names;
  }

  async function navigatePhoto(step) {
    const names = photoNavigationOrder();
    if (!names.length) return;
    const current = state.photo?.name;
    const position = names.indexOf(current);
    let next;
    if (position === -1) {
      next = step > 0 ? names[0] : names[names.length - 1];
    } else {
      next = names[(position + step + names.length) % names.length];
    }
    if (next && next !== current) await openPhoto(next);
  }

  function renderEditor() {
    const face = selectedFace();
    const empty = byId("editor-empty");
    const body = byId("editor-body");
    if (!state.photo || !state.photo.faces.length) {
      empty.hidden = false;
      empty.textContent = "Aucun visage détecté sur cette photo.";
      body.hidden = true;
      byId("validate-without-names").disabled = true;
      return;
    }
    empty.hidden = true;
    body.hidden = false;

    const position = state.photo.faces.findIndex(item => item.index === state.selectedFace);
    byId("editor-position").textContent =
      `Visage ${Math.max(1, position + 1)} / ${state.photo.faces.length}` +
      (face?.detScore ? ` · détection ${face.detScore.toFixed(2)}` : "");

    const status = byId("editor-status");
    if (!face) {
      status.textContent = "Sélectionnez un visage sur la photo.";
      status.className = "editor-status none";
    } else if (face.invalid) {
      status.textContent = "Détection invalidée : ce n'est pas un visage.";
      status.className = "editor-status invalid";
    } else if (face.current) {
      status.textContent = `Nommé : ${face.current.name}`;
      status.className = "editor-status named";
    } else if (face.ignored) {
      status.textContent = "Validé sans nom";
      status.className = "editor-status ignored";
    } else if (face.proposal) {
      status.textContent = `Proposition : ${face.proposal.name} (${face.proposal.score.toFixed(2).replace(".", ",")})`;
      status.className = "editor-status proposed";
    } else {
      status.textContent = "Aucune proposition fiable.";
      status.className = "editor-status none";
    }

    const alternatives = byId("editor-alternatives");
    alternatives.replaceChildren();
    for (const alternative of face?.alternatives || []) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "alternative";
      if (face.current?.id_people === alternative.id_people) button.classList.add("active");
      const name = document.createElement("span");
      name.textContent = alternative.name;
      const score = document.createElement("span");
      score.className = "score";
      score.textContent = alternative.score.toFixed(2).replace(".", ",");
      button.append(name, score);
      button.addEventListener("click", () => assign(face, alternative));
      alternatives.appendChild(button);
    }

    const ignoreButton = byId("ignore-face");
    ignoreButton.disabled = !face;
    if (face?.ignored) {
      ignoreButton.textContent = "Sans nom ✓";
      ignoreButton.title = "Cliquer pour remettre ce visage à décider";
    } else {
      ignoreButton.textContent = "Sans nom";
      ignoreButton.title = face?.current
        ? "Retirer le nom et valider ce visage sans nom"
        : "Valider ce visage sans nom";
    }
    const invalidateButton = byId("invalidate-face");
    invalidateButton.disabled = !face;
    if (face?.invalid) {
      invalidateButton.textContent = "Pas un visage ✓";
      invalidateButton.title = "Réactiver cette détection comme visage";
    } else {
      invalidateButton.textContent = "Pas un visage";
      invalidateButton.title = "Ignorer définitivement cette fausse détection";
    }
    byId("validate-without-names").disabled = !state.photo.faces.some(
      item => !item.invalid && !item.current && !item.ignored
    );
    byId("save-status").classList.remove("error");
  }

  // --- actions ------------------------------------------------------------

  function assign(face, person) {
    if (!face || !person) return;
    face.current = { id_people: person.id_people, name: person.name };
    face.ignored = false;
    face.invalid = false;
    face.dirty = true;
    const faces = state.photo.faces;
    const position = faces.findIndex(item => item.index === face.index);
    const next = [...faces.slice(position + 1), ...faces.slice(0, position)].find(
      item => !item.invalid && !item.current && !item.ignored
    );
    if (next) state.selectedFace = next.index;
    byId("person-search").value = "";
    byId("person-results").replaceChildren();
    searchItems = [];
    renderFaces();
    renderEditor();
    scheduleSave();
  }

  function clearFaceName() {
    const face = selectedFace();
    if (!face?.current) return;
    face.current = null;
    face.ignored = false;
    face.dirty = true;
    pendingReload = true;
    renderFaces();
    renderEditor();
    scheduleSave();
  }

  function ignoreFace() {
    const face = selectedFace();
    if (!face) return;
    face.invalid = false;
    if (face.ignored) {
      // Annule la validation « sans nom » : le visage redevient à décider.
      face.ignored = false;
      face.current = null;
      pendingReload = true;
    } else {
      face.current = null;
      face.ignored = true;
    }
    face.dirty = true;
    renderFaces();
    renderEditor();
    scheduleSave();
  }

  function invalidateFace() {
    const face = selectedFace();
    if (!face) return;
    face.invalid = !face.invalid;
    face.current = null;
    face.ignored = false;
    face.dirty = true;
    pendingReload = true;
    renderFaces();
    renderEditor();
    scheduleSave();
  }

  function validateWithoutNames() {
    if (!state.photo) return;
    let count = 0;
    for (const face of state.photo.faces) {
      if (face.invalid || face.current || face.ignored) continue;
      face.ignored = true;
      face.dirty = true;
      count += 1;
    }
    if (!count) {
      byId("save-status").textContent = "Tous les visages ont déjà une décision.";
      return;
    }
    renderFaces();
    renderEditor();
    scheduleSave();
  }

  function validateAll() {
    if (!state.photo) return;
    const threshold = Number(state.photo.threshold) || 0.43;
    let count = 0;
    for (const face of state.photo.faces) {
      // On ne valide en série que les propositions au-dessus du seuil calibré :
      // les scores plus bas, et les visages « sans nom », restent inchangés.
      if (
        !face.invalid
        && !face.current
        && !face.ignored
        && face.proposal
        && face.proposal.score >= threshold
      ) {
        face.current = { id_people: face.proposal.id_people, name: face.proposal.name };
        face.ignored = false;
        face.dirty = true;
        count += 1;
      }
    }
    if (!count) {
      byId("save-status").textContent =
        `Aucune proposition sûre (seuil ${threshold.toFixed(2).replace(".", ",")}).`;
      return;
    }
    renderFaces();
    renderEditor();
    scheduleSave();
  }

  function scheduleSave() {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(savePhoto, 120);
  }

  async function savePhoto() {
    if (!state.photo) return;
    if (state.saving) {
      // Une écriture est déjà en cours : on la rejouera avec l'état à jour.
      saveQueued = true;
      return;
    }
    if (!state.photo.faces.length) return;
    state.saving = true;
    const status = byId("save-status");
    status.classList.remove("error");
    status.textContent = "Écriture dans la photo…";
    try {
      const payload = {
        name: state.photo.name,
        faces: state.photo.faces.map(face => ({
          index: face.index,
          bbox: face.bbox,
          id_people: face.current?.id_people ?? null,
          ignored: Boolean(face.ignored),
          invalid: Boolean(face.invalid),
          apply: Boolean(face.dirty),
        })),
      };
      const data = await api("/api/save", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const selected = state.selectedFace;
      hydratePhoto(data);
      state.selectedFace = selected;
      renderViewer();
      updateCard(data);
      if (state.mode === "faces") {
        for (const face of data.faces) {
          if (face.existing || face.ignored || face.invalid) {
            removePendingCard(data.name, face.index);
          }
        }
        if (pendingReload) await loadPending();
      }
      pendingReload = false;
      status.textContent = "Enregistré.";
      setTimeout(() => {
        if (status.textContent === "Enregistré.") {
          status.textContent = "";
        }
      }, 3000);
    } catch (error) {
      status.textContent = error.message;
      status.classList.add("error");
    } finally {
      state.saving = false;
      if (saveQueued) {
        saveQueued = false;
        savePhoto();
      }
    }
  }

  function updateCard(data) {
    const entry = state.photos.find(photo => photo.name === data.name);
    if (!entry) return;
    entry.indexed = true;
    const validFaces = data.faces.filter(face => !face.invalid);
    entry.nFaces = validFaces.length;
    entry.nNamed = validFaces.filter(face => face.existing).length;
    entry.nIgnored = validFaces.filter(face => face.ignored).length;
    entry.nInvalid = data.faces.length - validFaces.length;
    entry.nResolved = validFaces.filter(
      face => face.existing || face.ignored
    ).length;
    const card = cardByName.get(data.name);
    if (card) setBadge(card, entry);
  }

  // --- recherche de personne ---------------------------------------------

  let searchItems = [];
  let searchActive = 0;

  function highlightSearch() {
    searchItems.forEach((item, index) => {
      item.button.classList.toggle("active", index === searchActive);
    });
  }

  function searchPeople() {
    const results = byId("person-results");
    const raw = byId("person-search").value.trim();
    const query = normalize(raw);
    results.replaceChildren();
    searchItems = [];
    searchActive = 0;
    if (query.length < 1) return;
    const matches = state.people
      .filter(person => normalize(person.name).includes(query))
      .slice(0, 8);
    const exact = state.people.some(person => normalize(person.name) === query);

    for (const person of matches) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "person-result";
      button.textContent = person.name;
      button.addEventListener("click", () => assign(selectedFace(), person));
      results.appendChild(button);
      searchItems.push({ button, person });
    }

    if (!exact && raw.length >= 2) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "person-result create";
      button.textContent = `Créer « ${raw} »`;
      button.title = "Ajoute ce nom à la liste des personnes du catalogue";
      button.addEventListener("click", () => createPerson(raw));
      results.appendChild(button);
      searchItems.push({ button, createName: raw });
    }
    highlightSearch();
  }

  function activateSearchItem(item) {
    if (!item) return;
    if (item.person) assign(selectedFace(), item.person);
    else createPerson(item.createName);
  }

  async function createPerson(name) {
    const status = byId("save-status");
    status.classList.remove("error");
    status.textContent = "Création…";
    try {
      const person = await api("/api/people", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }),
      });
      if (!state.people.some(item => item.id_people === person.id_people)) {
        state.people.push({
          id_people: person.id_people,
          name: person.label,
          prenom: person.prenom,
          nom: person.nom,
        });
      }
      const face = selectedFace();
      if (face) assign(face, { id_people: person.id_people, name: person.label });
      else {
        byId("person-search").value = "";
        byId("person-results").replaceChildren();
      }
      status.textContent = `${person.label} ajouté au catalogue.`;
    } catch (error) {
      status.textContent = error.message;
      status.classList.add("error");
    }
  }

  // --- analyse ------------------------------------------------------------

  async function analyzePhotos(selectionOnly = false) {
    const button = selectionOnly ? byId("analyze-selection") : byId("analyze");
    const status = byId("analyze-status");
    const bar = byId("analyze-bar");
    const text = byId("analyze-text");
    const names = selectionOnly ? [...state.selected] : [];
    button.disabled = true;
    byId("analyze").disabled = true;
    byId("analyze-selection").disabled = true;
    status.hidden = false;
    bar.removeAttribute("value");
    text.textContent = selectionOnly
      ? `Préparation de l’analyse de ${names.length} photo(s)…`
      : "Préparation de l’analyse…";
    try {
      const { jobId } = await api("/api/analyze", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(selectionOnly ? { names } : {}),
      });
      let job;
      do {
        await sleep(600);
        job = await api(`/api/analyze?jobId=${encodeURIComponent(jobId)}`);
        if (job.total) {
          bar.max = job.total;
          bar.value = job.done;
          text.textContent =
            `${job.done}/${job.total} photo(s) · ${job.faces} visage(s) · ` +
            `${job.labels} nom(s) trouvé(s)` +
            (job.errors ? ` · ${job.errors} erreur(s)` : "");
        } else {
          text.textContent = "Analyse en cours…";
        }
      } while (!job.complete);
      text.textContent =
        `Analyse terminée : ${job.total} photo(s), ${job.faces} visage(s), ` +
        `${job.labels} nom(s).` + (job.message && job.message !== "Terminé." ? ` ${job.message}` : "");
      if (state.mode === "faces") await loadPending();
      else await loadPhotos();
      if (state.photo) await openPhoto(state.photo.name);
    } catch (error) {
      text.textContent = error.message;
    } finally {
      byId("analyze").disabled = false;
      updateSelectionControls();
    }
  }

  // --- événements ---------------------------------------------------------

  byId("thumbnail-size").addEventListener("input", event => {
    byId("photo-grid").style.setProperty("--tile-size", `${event.target.value}px`);
  });

  byId("change-directory").addEventListener("click", async () => {
    const button = byId("change-directory");
    button.disabled = true;
    const label = button.textContent;
    button.textContent = "Chargement…";
    try {
      await api("/api/change-directory", { method: "POST" });
      state.photo = null;
      state.selectedFace = null;
      byId("viewer-body").hidden = true;
      byId("viewer-empty").hidden = false;
      if (state.mode === "faces") await loadPending();
      else await loadPhotos();
    } catch (error) {
      showMessage(error.message, true);
    } finally {
      button.disabled = false;
      button.textContent = label;
    }
  });

  document.querySelectorAll(".mode").forEach(button => {
    button.addEventListener("click", () => setMode(button.dataset.mode));
  });

  document.querySelectorAll(".filter").forEach(button => {
    button.addEventListener("click", () => {
      document.querySelectorAll(".filter").forEach(item => item.classList.toggle("active", item === button));
      state.filter = button.dataset.filter;
      loadPhotos();
    });
  });

  byId("select-all").addEventListener("click", selectAllPhotos);
  byId("deselect-photos").addEventListener("click", clearSelection);
  byId("analyze").addEventListener("click", () => analyzePhotos(false));
  byId("analyze-selection").addEventListener("click", () => analyzePhotos(true));
  byId("validate-all").addEventListener("click", validateAll);
  byId("validate-without-names").addEventListener("click", validateWithoutNames);
  byId("ignore-face").addEventListener("click", ignoreFace);
  byId("invalidate-face").addEventListener("click", invalidateFace);
  byId("person-search").addEventListener("input", searchPeople);
  byId("person-search").addEventListener("keydown", event => {
    if (!searchItems.length) return;
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const step = event.key === "ArrowDown" ? 1 : -1;
      searchActive = (searchActive + step + searchItems.length) % searchItems.length;
      highlightSearch();
      searchItems[searchActive].button.scrollIntoView({ block: "nearest" });
    } else if (event.key === "Enter") {
      event.preventDefault();
      activateSearchItem(searchItems[Math.min(searchActive, searchItems.length - 1)]);
    } else if (event.key === "Escape") {
      byId("person-results").replaceChildren();
      searchItems = [];
    }
  });
  byId("reload-photo").addEventListener("click", () => {
    if (state.photo) {
      byId("viewer-image").dataset.name = "";
      openPhoto(state.photo.name);
    }
  });
  byId("viewer-image").addEventListener("load", fitStage);

  document.addEventListener("keydown", event => {
    if (!state.photo || byId("viewer-body").hidden) return;
    const target = event.target;
    if (
      target instanceof HTMLInputElement
      || target instanceof HTMLTextAreaElement
      || target instanceof HTMLSelectElement
    ) return;

    if (
      (event.ctrlKey || event.metaKey)
      && (event.key === "ArrowRight" || event.key === "ArrowLeft")
    ) {
      event.preventDefault();
      navigatePhoto(event.key === "ArrowRight" ? 1 : -1);
      return;
    }

    const face = selectedFace();
    const faces = state.photo.faces;
    if (!faces.length) return;

    if (event.key === "ArrowRight" || event.key === "ArrowLeft") {
      event.preventDefault();
      const position = faces.findIndex(item => item.index === state.selectedFace);
      const step = event.key === "ArrowRight" ? 1 : -1;
      const next = faces[(position + step + faces.length) % faces.length];
      state.selectedFace = next.index;
      renderFaces();
      renderEditor();
      return;
    }
    if (!face) return;
    if (event.key === "Enter") {
      event.preventDefault();
      if (face.proposal) assign(face, face.proposal);
      return;
    }
    if (/^[1-9]$/.test(event.key)) {
      const alternative = face.alternatives?.[Number(event.key) - 1];
      if (alternative) {
        event.preventDefault();
        assign(face, alternative);
      }
      return;
    }
    if (event.key === "Backspace" || event.key === "Delete") {
      if (face.current) {
        event.preventDefault();
        clearFaceName();
      }
    }
  });

  new ResizeObserver(fitStage).observe(byId("stage").parentElement);

  // --- séparateur redimensionnable ------------------------------------------
  (() => {
    const workspace = document.querySelector(".workspace");
    const splitter = byId("splitter");
    if (!workspace || !splitter) return;
    const STORAGE_KEY = "face.split";
    const DEFAULT_RATIO = 0.40;
    const MIN_LEFT_PX = 340;
    const MIN_RIGHT_PX = 440;
    const clamp = value => {
      const width = workspace.getBoundingClientRect().width;
      if (!width) return value;
      const minimum = MIN_LEFT_PX / width;
      const maximum = Math.max(minimum, (width - 7 - MIN_RIGHT_PX) / width);
      return Math.min(maximum, Math.max(minimum, value));
    };
    let ratio = Number(localStorage.getItem(STORAGE_KEY));
    if (!Number.isFinite(ratio) || ratio <= 0) ratio = DEFAULT_RATIO;
    ratio = clamp(ratio);
    const apply = () => workspace.style.setProperty("--split", `${(ratio * 100).toFixed(2)}%`);
    apply();
    window.addEventListener("resize", () => {
      ratio = clamp(ratio);
      apply();
    });

    const move = event => {
      const rect = workspace.getBoundingClientRect();
      if (!rect.width) return;
      ratio = clamp((event.clientX - rect.left) / rect.width);
      apply();
    };
    const stop = event => {
      splitter.classList.remove("dragging");
      splitter.removeEventListener("pointermove", move);
      splitter.removeEventListener("pointerup", stop);
      splitter.removeEventListener("pointercancel", stop);
      try { splitter.releasePointerCapture(event.pointerId); } catch (_) { /* déjà relâché */ }
      localStorage.setItem(STORAGE_KEY, String(ratio));
    };
    splitter.addEventListener("pointerdown", event => {
      if (event.button !== 0) return;
      event.preventDefault();
      splitter.setPointerCapture(event.pointerId);
      splitter.classList.add("dragging");
      splitter.addEventListener("pointermove", move);
      splitter.addEventListener("pointerup", stop);
      splitter.addEventListener("pointercancel", stop);
    });
    splitter.addEventListener("dblclick", () => {
      ratio = DEFAULT_RATIO;
      apply();
      localStorage.removeItem(STORAGE_KEY);
    });
    splitter.addEventListener("keydown", event => {
      if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
      event.preventDefault();
      ratio = clamp(ratio + (event.key === "ArrowLeft" ? -0.02 : 0.02));
      apply();
      localStorage.setItem(STORAGE_KEY, String(ratio));
    });
  })();

  async function exitApplication() {
    if (window.parent && window.parent !== window) {
      window.parent.postMessage({ type: "photos-exit" }, "*");
      return;
    }
    try {
      await fetch("/api/shutdown", { method: "POST", keepalive: true });
    } catch (_) {
      // Le serveur peut déjà être arrêté.
    }
    window.open("", "_self");
    window.close();
    setTimeout(() => {
      document.body.innerHTML =
        '<main style="display:grid;place-items:center;height:100vh;font-family:sans-serif;background:#171a27;color:#edf0f7">' +
        '<div style="text-align:center"><h1>Serveur arrêté</h1><p>Vous pouvez fermer cette fenêtre.</p></div></main>';
    }, 250);
  }

  byId("exit-app")?.addEventListener("click", exitApplication);

  updateSelectionControls();
  loadState();
  loadPeople();
  loadPhotos();
})();
