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

  // --- galerie ------------------------------------------------------------

  function badgeFor(photo) {
    if (photo.error) return { text: "erreur", className: "error", title: photo.error };
    if (!photo.indexed) return { text: "non analysée", className: "missing", title: "Aucune analyse de visages pour cette photo." };
    if (!photo.nFaces) return { text: "0 visage", className: "missing", title: "Aucun visage détecté." };
    if (photo.nNamed >= photo.nFaces) {
      return { text: `${photo.nFaces} ✓`, className: "present", title: "Tous les visages sont nommés." };
    }
    const missing = photo.nFaces - photo.nNamed;
    return {
      text: `${photo.nNamed}/${photo.nFaces}`,
      className: "unknown",
      title: `${missing} visage(s) sans nom.`,
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
        (state.filter === "all" ? "" : " (filtrées)");
      byId("photo-count").style.color = "";
    }

    grid.replaceChildren();
    if (!state.photos.length) {
      grid.appendChild(
        message(state.loaded ? "Aucune photo dans ce dossier ou ce filtre." : "Chargement des photos…")
      );
      return;
    }

    for (const photo of state.photos) {
      const card = document.createElement("figure");
      card.className = "photo-card";
      card.dataset.name = photo.name;
      card.title = photo.name;

      const info = badgeFor(photo);
      const badge = document.createElement("span");
      badge.className = `photo-badge ${info.className}`;
      badge.textContent = info.text;
      badge.title = info.title;

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

      card.append(badge, image, caption, relative);
      card.addEventListener("click", () => openPhoto(photo.name));
      grid.appendChild(card);
      cardByName.set(photo.name, card);
      if (thumbnailObserver) thumbnailObserver.observe(image);
      else image.src = image.dataset.src;
    }
  }

  // --- visages à vérifier -------------------------------------------------

  async function loadPending() {
    const token = ++pendingToken;
    const grid = byId("photo-grid");
    grid.replaceChildren(message("Recherche des visages à vérifier…"));
    try {
      const data = await api("/api/pending?limit=600");
      if (token !== pendingToken) return;
      state.pending = data.faces;
      byId("pending-count").textContent = String(data.total);
      byId("photo-count").textContent = `${data.total} visage(s) à vérifier, du plus douteux au plus sûr`;
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
        message("Aucun visage à vérifier dans ce dossier : tout est nommé.")
      );
      return;
    }

    for (const item of state.pending) {
      const key = `${item.name}#${item.faceIndex}`;
      const card = document.createElement("figure");
      card.className = "face-card";
      card.title = `${item.name} · confiance ${(item.score * 100).toFixed(0)} %`;

      const image = document.createElement("img");
      image.alt = item.person;
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
      name.textContent = item.person;
      const meta = document.createElement("div");
      meta.className = "face-card-meta";
      const file = document.createElement("span");
      file.textContent = item.name.split("/").pop();
      const score = document.createElement("span");
      score.className = "face-card-score";
      score.textContent = item.score.toFixed(2).replace(".", ",");
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
    byId("photo-count").textContent = `${state.pending.length} visage(s) à vérifier, du plus douteux au plus sûr`;
  }

  function setMode(mode) {
    state.mode = mode;
    document.querySelectorAll(".mode").forEach(button =>
      button.classList.toggle("active", button.dataset.mode === mode)
    );
    byId("photo-filters").hidden = mode !== "photos";
    byId("photo-grid").classList.toggle("faces-mode", mode === "faces");
    if (mode === "faces") loadPending();
    else loadPhotos();
  }

  // --- photo --------------------------------------------------------------

  function hydratePhoto(data) {
    for (const face of data.faces) {
      face.current = face.existing
        ? { id_people: face.existing.id_people, name: face.existing.name }
        : null;
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
      const unnamed = data.faces.find(face => !face.current);
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
    const named = data.faces.filter(face => face.current).length;
    const orphanNote = data.orphans?.length
      ? ` · ${data.orphans.length} nom(s) déjà présent(s) hors détection`
      : "";
    byId("viewer-title").textContent = data.displayName;
    byId("viewer-subtitle").textContent =
      `${data.faces.length} visage(s) · ${named} nommé(s)${orphanNote}`;
    byId("validate-all").title =
      `Nomme les visages reconnus avec un score ≥ ${Number(data.threshold).toFixed(2).replace(".", ",")}`;

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
    if (face.current) return { text: face.current.name, score: "" };
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
      if (face.current) box.classList.add("named");
      else if (face.proposal) box.classList.add("proposal");
      else box.classList.add("unknown");
      if (face.index === state.selectedFace) box.classList.add("selected");
      box.style.left = `${face.bbox[0] * 100}%`;
      box.style.top = `${face.bbox[1] * 100}%`;
      box.style.width = `${(face.bbox[2] - face.bbox[0]) * 100}%`;
      box.style.height = `${(face.bbox[3] - face.bbox[1]) * 100}%`;
      box.title = face.current
        ? `Nommé : ${face.current.name}`
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

  function renderEditor() {
    const face = selectedFace();
    const empty = byId("editor-empty");
    const body = byId("editor-body");
    if (!state.photo || !state.photo.faces.length) {
      empty.hidden = false;
      empty.textContent = "Aucun visage détecté sur cette photo.";
      body.hidden = true;
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
    } else if (face.current) {
      status.textContent = `Nommé : ${face.current.name}`;
      status.className = "editor-status named";
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

    byId("ignore-face").disabled = !face?.current;
    byId("save-status").classList.remove("error");
  }

  // --- actions ------------------------------------------------------------

  function assign(face, person) {
    if (!face || !person) return;
    face.current = { id_people: person.id_people, name: person.name };
    face.dirty = true;
    const faces = state.photo.faces;
    const position = faces.findIndex(item => item.index === face.index);
    const next = [...faces.slice(position + 1), ...faces.slice(0, position)].find(
      item => !item.current
    );
    if (next) state.selectedFace = next.index;
    byId("person-search").value = "";
    byId("person-results").replaceChildren();
    searchItems = [];
    renderFaces();
    renderEditor();
    scheduleSave();
  }

  function ignoreFace() {
    const face = selectedFace();
    if (!face) return;
    face.current = null;
    face.dirty = true;
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
      // les scores plus bas restent à confirmer un par un.
      if (!face.current && face.proposal && face.proposal.score >= threshold) {
        face.current = { id_people: face.proposal.id_people, name: face.proposal.name };
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
          bbox: face.bbox,
          id_people: face.current?.id_people ?? null,
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
          if (face.existing) removePendingCard(data.name, face.index);
        }
      }
      status.textContent = "Enregistré dans les métadonnées de la photo.";
      setTimeout(() => {
        if (status.textContent === "Enregistré dans les métadonnées de la photo.") {
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
    entry.nFaces = data.faces.length;
    entry.nNamed = data.faces.filter(face => face.existing).length;
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

  async function analyzeDirectory() {
    const button = byId("analyze");
    const status = byId("analyze-status");
    const bar = byId("analyze-bar");
    const text = byId("analyze-text");
    button.disabled = true;
    status.hidden = false;
    bar.removeAttribute("value");
    text.textContent = "Préparation de l’analyse…";
    try {
      const { jobId } = await api("/api/analyze", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
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
      button.disabled = false;
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

  byId("analyze").addEventListener("click", analyzeDirectory);
  byId("validate-all").addEventListener("click", validateAll);
  byId("ignore-face").addEventListener("click", ignoreFace);
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
    if (target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement) return;
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
        ignoreFace();
      }
    }
  });

  new ResizeObserver(fitStage).observe(byId("stage").parentElement);

  loadState();
  loadPeople();
  loadPhotos();
})();
