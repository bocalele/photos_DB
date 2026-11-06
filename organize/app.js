(() => {
  "use strict";

  const byId = id => document.getElementById(id);
  const state = {
    root: "",
    rootName: "PHOTOS",
    directory: "",
    path: "",
    total: 0,
    photos: [],
    folders: [],
    mode: "photos",
    viewMode: "icons",
    selected: new Set(),
    selectedFolders: new Set(),
    lastIndex: -1,
    lastFolderIndex: -1,
    slideshowIndex: 0,
    pendingPath: null,
    treeSelection: null,
    treeNodes: new Map(),
    openPaths: new Set([""]),
    busy: false,
    dragging: null,
    renameTarget: null,
    moveSources: [],
    pendingRatings: new Set(),
  };

  let thumbnailObserver;
  let photoToken = 0;
  let folderToken = 0;
  let navigationToken = 0;
  let statusTimer;
  let refreshing = false;

  // --- utilitaires -------------------------------------------------------

  async function api(url, options) {
    const response = await fetch(url, options);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `Erreur HTTP ${response.status}`);
    return data;
  }

  function jsonPost(url, payload) {
    return api(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  }

  function message(text, isError = false) {
    const element = document.createElement("div");
    element.className = `message${isError ? " error" : ""}`;
    element.textContent = text;
    return element;
  }

  function showStatus(text, kind = "success") {
    const element = byId("status");
    element.textContent = text;
    element.className = `status ${kind}`;
    element.hidden = false;
    clearTimeout(statusTimer);
    if (kind === "success") {
      statusTimer = setTimeout(() => { element.hidden = true; }, 6000);
    }
  }

  function hideStatus() {
    byId("status").hidden = true;
  }

  function setDropHint(text) {
    const element = byId("drop-hint");
    element.textContent = text || "";
    element.hidden = !text;
  }

  function folderName(path) {
    if (!path) return state.rootName;
    const parts = path.split("/");
    return parts[parts.length - 1];
  }

  function pathDepth(path) {
    return path ? path.split("/").length : 0;
  }

  function pathIsInside(path, parent) {
    return path === parent || path.startsWith(`${parent}/`);
  }

  function formatSize(bytes) {
    if (!Number.isFinite(bytes)) return "";
    if (bytes < 1024) return `${bytes} o`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} Ko`;
    if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} Mo`;
    return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} Go`;
  }

  function formatDate(iso) {
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return "";
    return date.toLocaleDateString("fr-FR", { day: "2-digit", month: "2-digit", year: "2-digit" });
  }

  function folderIcon() {
    return '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 6.5A2.5 2.5 0 0 1 5.5 4H10l2 2h6.5A2.5 2.5 0 0 1 21 8.5v9A2.5 2.5 0 0 1 18.5 20h-13A2.5 2.5 0 0 1 3 17.5v-11z"/></svg>';
  }

  function chevronIcon() {
    return '<svg viewBox="0 0 12 12" aria-hidden="true"><path d="M4 2l4 4-4 4z"/></svg>';
  }

  function trashIcon() {
    return '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 3h6l1 2h4v2H4V5h4l1-2zm-3 6h12l-1 12H7L6 9zm3 2v8h2v-8H9zm4 0v8h2v-8h-2z"/></svg>';
  }

  function applyStatusDot(dot, status) {
    dot.className = "tree-status-dot";
    if (!status) {
      dot.hidden = true;
      dot.title = "";
      dot.removeAttribute("aria-label");
      return;
    }
    const value = typeof status === "string" ? status : status.status;
    if (!value) {
      dot.hidden = true;
      dot.title = "";
      return;
    }
    dot.hidden = false;
    dot.classList.add(`status-${value}`);
    dot.title = statusTitle(status);
    dot.setAttribute("aria-label", dot.title);
  }

  function statusTitle(status) {
    if (!status) return "Dossier hors base de données";
    const value = typeof status === "string" ? status : status.status;
    if (typeof status === "object") {
      const parts = [];
      if (status.missing) parts.push(`${status.missing} photo(s) manquante(s)`);
      if (status.stale) parts.push(`${status.stale} photo(s) non à jour`);
      if (status.unindexed) parts.push(`${status.unindexed} photo(s) non indexée(s)`);
      if (parts.length) {
        if (value === "unindexed") parts.push("au moins un dossier sans photo en base");
        return parts.join(" · ");
      }
    }
    if (value === "missing") return "Au moins une photo est manquante";
    if (value === "unindexed") return "Au moins un dossier n’a aucune photo en base";
    if (value === "stale") return "Au moins une photo n’est pas à jour";
    return "Toutes les photos sont présentes et à jour";
  }

  function makeStatusDot(status) {
    const dot = document.createElement("span");
    dot.className = "tree-status-dot";
    applyStatusDot(dot, status);
    return dot;
  }

  function applyFolderStatus(element, status) {
    const value = status
      ? (typeof status === "string" ? status : status.status)
      : null;
    element.classList.remove(
      "folder-status-ok",
      "folder-status-stale",
      "folder-status-unindexed",
      "folder-status-missing"
    );
    if (value) element.classList.add(`folder-status-${value}`);
  }

  function renderStars(container, rating, onRate) {
    container.replaceChildren();
    const value = Number(rating) || 0;
    for (let index = 1; index <= 5; index += 1) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = `star${index <= value ? " active" : ""}`;
      button.textContent = "★";
      button.title = index === value
        ? "Cliquer pour retirer la note"
        : `Donner ${index} étoile${index > 1 ? "s" : ""}`;
      button.setAttribute("aria-label", button.title);
      button.addEventListener("click", event => {
        event.preventDefault();
        event.stopPropagation();
        onRate(index);
      });
      button.addEventListener("mousedown", event => event.stopPropagation());
      button.addEventListener("dragstart", event => event.preventDefault());
      container.appendChild(button);
    }
  }

  function updateRatingUI(name, rating) {
    document.querySelectorAll(`.photo-rating[data-photo="${CSS.escape(name)}"]`).forEach(element => {
      renderStars(element, rating, value => ratePhoto(name, value));
    });
  }

  function applyState(data) {
    state.root = data.root;
    state.rootName = data.rootName || "PHOTOS";
    state.directory = data.directory;
    state.path = data.path || "";
    if (data.photoCount !== undefined) state.total = data.photoCount;
    byId("tree-root").textContent = state.root;
    byId("tree-root").title = state.root;
  }

  // --- chargement --------------------------------------------------------

  async function init() {
    bindEvents();
    updateViewUI();
    updateModeUI();
    try {
      const data = await api("/api/state?refresh=1");
      applyState(data);
      await renderTree();
      await refreshContent();
    } catch (error) {
      byId("photo-grid").replaceChildren(message(`Impossible de charger : ${error.message}`, true));
    }
  }

  async function refreshAll() {
    if (state.busy || refreshing) return;
    refreshing = true;
    try {
      const data = await api("/api/state?refresh=1");
      applyState(data);
      await renderTree();
      await refreshContent();
      showStatus("Vue actualisée.");
    } catch (error) {
      showStatus(error.message, "error");
    } finally {
      refreshing = false;
    }
  }

  async function refreshContent() {
    if (state.viewMode === "icons" && state.mode === "folders") {
      await loadFolders();
    } else {
      await loadPhotos();
    }
  }

  async function loadPhotos() {
    const token = ++photoToken;
    if (state.viewMode === "icons") {
      byId("photo-grid").replaceChildren(message("Chargement des photos…"));
    }
    byId("photo-grid").classList.toggle("folders-mode", false);
    try {
      const data = await api(
        `/api/photos?path=${encodeURIComponent(state.path)}&limit=1000`
      );
      if (token !== photoToken) return;
      state.total = data.total;
      state.photos = data.photos;
      state.selected.clear();
      state.lastIndex = -1;
      state.slideshowIndex = Math.min(state.slideshowIndex, Math.max(0, state.photos.length - 1));
      if (state.viewMode === "icons") renderPhotos();
      renderSlideshow();
      updatePhotoHeader();
      updateSelectionUI();
    } catch (error) {
      if (token !== photoToken) return;
      state.photos = [];
      state.total = 0;
      byId("photo-grid").replaceChildren(message(error.message, true));
      renderSlideshow();
      updatePhotoHeader();
      updateSelectionUI();
    }
  }

  async function loadFolders() {
    const token = ++folderToken;
    const grid = byId("photo-grid");
    grid.classList.toggle("folders-mode", true);
    thumbnailObserver?.disconnect();
    grid.replaceChildren(message("Chargement des dossiers…"));
    try {
      const data = await api(`/api/tree?path=${encodeURIComponent(state.path)}`);
      if (token !== folderToken) return;
      state.folders = data.children || [];
      state.selectedFolders.clear();
      state.lastFolderIndex = -1;
      setNodeStatus(state.path, data.status);
      renderFolders();
      updatePhotoHeader();
      updateSelectionUI();
    } catch (error) {
      if (token !== folderToken) return;
      state.folders = [];
      grid.replaceChildren(message(error.message, true));
      updatePhotoHeader();
      updateSelectionUI();
    }
  }

  // --- cartes photos -----------------------------------------------------

  function makePhotoCard(photo) {
    const card = document.createElement("figure");
    card.className = "photo-card";
    card.dataset.name = photo.name;
    card.draggable = true;
    card.title = `${photo.displayName}\n${formatSize(photo.size)} · ${formatDate(photo.modified)}`;
    const selected = state.selected.has(photo.name);
    card.classList.toggle("selected", selected);

    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.className = "photo-check";
    checkbox.checked = selected;
    checkbox.setAttribute("aria-label", `Sélectionner ${photo.displayName}`);
    checkbox.addEventListener("change", () => {
      togglePhotoSelection(photo.name, state.photos.indexOf(photo));
    });

    const image = document.createElement("img");
    image.alt = photo.displayName;
    image.loading = "lazy";
    image.decoding = "async";
    image.addEventListener("error", () => {
      image.removeAttribute("src");
      image.classList.add("thumbnail-error");
      image.alt = "Aperçu indisponible";
    });
    if (thumbnailObserver) {
      image.dataset.src = photo.thumbnail;
      thumbnailObserver.observe(image);
    } else {
      image.src = photo.thumbnail;
    }

    const caption = document.createElement("figcaption");
    const name = document.createElement("div");
    name.className = "photo-name";
    name.textContent = photo.displayName;
    const meta = document.createElement("div");
    meta.className = "photo-meta";
    const size = document.createElement("span");
    size.textContent = formatSize(photo.size);
    const modified = document.createElement("span");
    modified.textContent = formatDate(photo.modified);
    meta.append(size, modified);

    const actions = document.createElement("div");
    actions.className = "photo-actions";
    const rating = document.createElement("div");
    rating.className = "photo-rating";
    rating.dataset.photo = photo.name;
    renderStars(rating, photo.rating, value => ratePhoto(photo.name, value));
    const trash = document.createElement("button");
    trash.type = "button";
    trash.className = "icon-button trash-button";
    trash.title = "Mettre à la corbeille";
    trash.setAttribute("aria-label", `Mettre ${photo.displayName} à la corbeille`);
    trash.innerHTML = trashIcon();
    trash.addEventListener("click", event => {
      event.preventDefault();
      event.stopPropagation();
      trashPhoto(photo.name);
    });
    actions.append(rating, trash);
    caption.append(name, meta, actions);

    const statusValue = photo.status === "unindexed" ? "stale" : photo.status;
    const status = makeStatusDot({ status: statusValue });
    status.classList.add("photo-status");
    if (photo.status === "unindexed") {
      status.title = "Photo absente de photos.db";
      status.setAttribute("aria-label", status.title);
    }

    card.append(checkbox, image, status, caption);
    card.addEventListener("click", event => {
      if (event.target.closest(".photo-check, .photo-rating, .trash-button")) return;
      handlePhotoClick(photo.name, state.photos.indexOf(photo), event);
    });
    card.addEventListener("dragstart", event => {
      if (!state.selected.has(photo.name)) selectOnly(photo.name, state.photos.indexOf(photo));
      startDrag(event, [...state.selected], "photos");
    });
    card.addEventListener("dragend", endDrag);
    return card;
  }

  function renderPhotos() {
    const grid = byId("photo-grid");
    thumbnailObserver?.disconnect();
    thumbnailObserver = "IntersectionObserver" in window
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
    if (!state.photos.length) {
      grid.appendChild(message("Aucune photo dans ce dossier."));
      return;
    }
    const fragment = document.createDocumentFragment();
    for (const photo of state.photos) fragment.appendChild(makePhotoCard(photo));
    grid.appendChild(fragment);
  }

  // --- cartes dossiers ---------------------------------------------------

  function makeFolderCard(folder, index) {
    const card = document.createElement("figure");
    card.className = "folder-card";
    card.dataset.path = folder.path;
    card.draggable = true;
    card.title = folder.path;
    const selected = state.selectedFolders.has(folder.path);
    card.classList.toggle("selected", selected);

    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.className = "photo-check";
    checkbox.checked = selected;
    checkbox.setAttribute("aria-label", `Sélectionner ${folder.name}`);
    checkbox.addEventListener("change", () => {
      toggleFolderSelection(folder.path, index);
    });

    const icon = document.createElement("span");
    icon.className = "folder-card-icon";
    icon.innerHTML = folderIcon();

    const name = document.createElement("div");
    name.className = "folder-card-name";
    name.textContent = folder.name;

    const meta = document.createElement("div");
    meta.className = "folder-card-meta";
    meta.textContent = folder.status ? statusTitle(folder.status) : "Hors base";

    applyFolderStatus(card, folder.status);
    if (folder.status) card.title = `${folder.path}\n${statusTitle(folder.status)}`;
    card.append(checkbox, icon, name, meta);
    card.addEventListener("click", event => {
      if (event.target.closest(".photo-check")) return;
      handleFolderCardClick(folder.path, index, event);
    });
    card.addEventListener("dblclick", event => {
      if (event.target.closest(".photo-check")) return;
      selectFolder(folder.path);
    });
    card.addEventListener("dragstart", event => {
      if (!state.selectedFolders.has(folder.path)) selectOnlyFolder(folder.path, index);
      startDrag(event, [...state.selectedFolders], "folder");
    });
    card.addEventListener("dragend", endDrag);
    card.addEventListener("dragover", event => handleFolderCardDragOver(event, folder.path, card));
    card.addEventListener("dragleave", event => {
      if (!card.contains(event.relatedTarget)) card.classList.remove("drop-target");
    });
    card.addEventListener("drop", event => handleFolderCardDrop(event, folder.path, card));
    return card;
  }

  function renderFolders() {
    const grid = byId("photo-grid");
    thumbnailObserver?.disconnect();
    grid.classList.add("folders-mode");
    grid.replaceChildren();
    if (!state.folders.length) {
      grid.appendChild(message("Aucun sous-dossier dans ce dossier."));
      return;
    }
    const fragment = document.createDocumentFragment();
    state.folders.forEach((folder, index) => {
      fragment.appendChild(makeFolderCard(folder, index));
    });
    grid.appendChild(fragment);
  }

  function updatePhotoHeader() {
    byId("folder-title").textContent = folderName(state.path);
    byId("folder-path").textContent = state.directory;
    byId("folder-path").title = state.directory;
    const selected = state.mode === "folders" ? state.selectedFolders.size : state.selected.size;
    const count = state.mode === "folders" ? state.folders.length : state.total;
    const unit = state.mode === "folders" ? "dossier(s)" : "photo(s)";
    byId("photo-count").textContent =
      `${count} ${unit}` + (selected ? ` · ${selected} sélectionné(s)` : "");
    updateBreadcrumb();
  }

  function updateBreadcrumb() {
    const breadcrumb = byId("breadcrumb");
    breadcrumb.replaceChildren();
    const rootButton = document.createElement("button");
    rootButton.type = "button";
    rootButton.textContent = state.rootName;
    rootButton.className = state.path ? "" : "current";
    rootButton.addEventListener("click", () => selectFolder(""));
    breadcrumb.appendChild(rootButton);

    let current = "";
    for (const part of state.path.split("/").filter(Boolean)) {
      current = current ? `${current}/${part}` : part;
      const separator = document.createElement("span");
      separator.className = "separator";
      separator.textContent = "›";
      breadcrumb.appendChild(separator);
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = part;
      if (current === state.path) button.className = "current";
      const target = current;
      button.addEventListener("click", () => selectFolder(target));
      breadcrumb.appendChild(button);
    }
  }

  // --- sélection ---------------------------------------------------------

  function updateCardsSelection() {
    const grid = byId("photo-grid");
    for (const card of grid.querySelectorAll(".photo-card")) {
      const selected = state.selected.has(card.dataset.name);
      card.classList.toggle("selected", selected);
      const checkbox = card.querySelector(".photo-check");
      if (checkbox) checkbox.checked = selected;
    }
    for (const card of grid.querySelectorAll(".folder-card")) {
      const selected = state.selectedFolders.has(card.dataset.path);
      card.classList.toggle("selected", selected);
      const checkbox = card.querySelector(".photo-check");
      if (checkbox) checkbox.checked = selected;
    }
  }

  function updateSelectionUI() {
    updateCardsSelection();
    const count = state.mode === "folders" ? state.selectedFolders.size : state.selected.size;
    byId("deselect").disabled = count === 0;
    const available = state.mode === "folders" ? state.folders.length : state.photos.length;
    byId("select-all").disabled = available === 0 || state.busy;
    byId("rename").disabled = state.busy || count !== 1;
    byId("move-to").disabled = state.busy || count === 0;
    updatePhotoHeader();
  }

  function selectOnly(name, index) {
    state.selected.clear();
    state.selected.add(name);
    state.selectedFolders.clear();
    state.lastIndex = index;
    updateSelectionUI();
  }

  function togglePhotoSelection(name, index) {
    if (state.selected.has(name)) state.selected.delete(name);
    else state.selected.add(name);
    state.selectedFolders.clear();
    state.lastIndex = index;
    updateSelectionUI();
  }

  function handlePhotoClick(name, index, event) {
    if (event.shiftKey && state.lastIndex >= 0) {
      const start = Math.min(state.lastIndex, index);
      const end = Math.max(state.lastIndex, index);
      for (let i = start; i <= end; i++) state.selected.add(state.photos[i].name);
      state.selectedFolders.clear();
    } else if (event.metaKey || event.ctrlKey) {
      togglePhotoSelection(name, index);
      return;
    } else {
      state.selected.clear();
      state.selected.add(name);
      state.selectedFolders.clear();
    }
    state.lastIndex = index;
    updateSelectionUI();
  }

  function selectOnlyFolder(path, index) {
    state.selectedFolders.clear();
    state.selectedFolders.add(path);
    state.selected.clear();
    state.lastFolderIndex = index;
    updateSelectionUI();
  }

  function toggleFolderSelection(path, index) {
    if (state.selectedFolders.has(path)) state.selectedFolders.delete(path);
    else state.selectedFolders.add(path);
    state.selected.clear();
    state.lastFolderIndex = index;
    updateSelectionUI();
  }

  function handleFolderCardClick(path, index, event) {
    if (event.shiftKey && state.lastFolderIndex >= 0) {
      const start = Math.min(state.lastFolderIndex, index);
      const end = Math.max(state.lastFolderIndex, index);
      for (let i = start; i <= end; i++) state.selectedFolders.add(state.folders[i].path);
      state.selected.clear();
    } else if (event.metaKey || event.ctrlKey) {
      toggleFolderSelection(path, index);
      return;
    } else {
      state.selectedFolders.clear();
      state.selectedFolders.add(path);
      state.selected.clear();
    }
    state.lastFolderIndex = index;
    updateSelectionUI();
  }

  function selectAll() {
    if (state.mode === "folders") {
      state.selectedFolders.clear();
      for (const folder of state.folders) state.selectedFolders.add(folder.path);
      state.selected.clear();
      state.lastFolderIndex = state.folders.length - 1;
    } else {
      state.selected.clear();
      for (const photo of state.photos) state.selected.add(photo.name);
      state.selectedFolders.clear();
      state.lastIndex = state.photos.length - 1;
    }
    updateSelectionUI();
  }

  function deselectAll() {
    state.selected.clear();
    state.selectedFolders.clear();
    state.lastIndex = -1;
    state.lastFolderIndex = -1;
    updateSelectionUI();
  }

  // --- arborescence ------------------------------------------------------

  function makeTreeNode(folder, depth) {
    const entry = {
      folder,
      loaded: false,
      depth,
    };

    const node = document.createElement("div");
    node.className = "tree-node";
    node.dataset.path = folder.path;

    const row = document.createElement("div");
    row.className = "tree-row";
    row.dataset.path = folder.path;
    row.style.setProperty("--depth", String(depth));
    row.draggable = Boolean(folder.path);

    const toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "tree-toggle";
    if (!folder.hasChildren) toggle.classList.add("empty");
    toggle.innerHTML = chevronIcon();
    toggle.setAttribute("aria-label", folder.hasChildren ? "Afficher les sous-dossiers" : "Dossier vide");
    toggle.addEventListener("click", event => {
      event.stopPropagation();
      toggleTreeNode(folder.path);
    });

    const label = document.createElement("span");
    label.className = "tree-label";
    label.textContent = folder.name;
    label.title = folder.path || state.rootName;

    applyFolderStatus(row, folder.status);
    row.append(toggle, label);
    const children = document.createElement("div");
    children.className = "tree-children";
    children.hidden = true;
    node.append(row, children);

    entry.node = node;
    entry.row = row;
    entry.children = children;
    state.treeNodes.set(folder.path, entry);

    row.addEventListener("click", event => {
      if (event.target.closest(".tree-toggle")) return;
      selectFolder(folder.path);
    });
    row.addEventListener("dblclick", event => {
      if (event.target.closest(".tree-toggle")) return;
      toggleTreeNode(folder.path);
    });
    row.addEventListener("dragstart", event => {
      if (!folder.path) {
        event.preventDefault();
        return;
      }
      const sources = state.mode === "folders" && state.selectedFolders.has(folder.path)
        ? [...state.selectedFolders]
        : [folder.path];
      startDrag(event, sources, "folder");
    });
    row.addEventListener("dragend", endDrag);
    row.addEventListener("dragover", event => handleTreeDragOver(event, folder.path, row));
    row.addEventListener("dragleave", event => {
      if (!row.contains(event.relatedTarget)) row.classList.remove("drop-target");
    });
    row.addEventListener("drop", event => handleTreeDrop(event, folder.path, row));

    return entry;
  }

  function setNodeStatus(path, status) {
    const entry = state.treeNodes.get(path);
    if (!entry) return;
    applyFolderStatus(entry.row, status);
    const label = entry.row.querySelector(".tree-label");
    if (label) {
      const text = folderName(path) || state.rootName;
      label.title = status ? `${text}\n${statusTitle(status)}` : text;
    }
  }

  async function loadTreeChildren(path) {
    const entry = state.treeNodes.get(path);
    if (!entry || entry.loaded) return;
    entry.loaded = true;
    entry.children.replaceChildren(message("Chargement…"));
    try {
      const data = await api(`/api/tree?path=${encodeURIComponent(path)}`);
      entry.children.replaceChildren();
      setNodeStatus(path, data.status);
      if (!data.children.length) {
        entry.row.querySelector(".tree-toggle")?.classList.add("empty");
        return;
      }
      for (const child of data.children) {
        const childEntry = makeTreeNode(child, entry.depth + 1);
        entry.children.appendChild(childEntry.node);
      }
    } catch (error) {
      entry.children.replaceChildren(message(error.message, true));
      entry.loaded = false;
    }
  }

  async function expandTreeNode(path) {
    const entry = state.treeNodes.get(path);
    if (!entry) return false;
    state.openPaths.add(path);
    entry.node.classList.add("open");
    entry.children.hidden = false;
    await loadTreeChildren(path);
    return true;
  }

  async function collapseTreeNode(path) {
    const entry = state.treeNodes.get(path);
    if (!entry) return;
    state.openPaths.delete(path);
    entry.node.classList.remove("open");
    entry.children.hidden = true;
  }

  async function toggleTreeNode(path) {
    const entry = state.treeNodes.get(path);
    if (!entry) return;
    if (entry.node.classList.contains("open")) await collapseTreeNode(path);
    else await expandTreeNode(path);
  }

  async function revealPath(path) {
    await expandTreeNode("");
    let current = "";
    for (const part of path.split("/").filter(Boolean)) {
      const next = current ? `${current}/${part}` : part;
      if (!state.treeNodes.has(next)) break;
      await expandTreeNode(next);
      current = next;
    }
  }

  async function renderTree() {
    const tree = byId("tree");
    const previousScrollTop = tree.scrollTop;
    tree.replaceChildren();
    state.treeNodes.clear();
    if (!state.openPaths.has("")) state.openPaths.add("");
    const root = makeTreeNode(
      { name: state.rootName, path: "", hasChildren: true, status: null },
      0
    );
    tree.appendChild(root.node);
    const requested = [...state.openPaths]
      .filter(path => path !== "")
      .sort((first, second) => pathDepth(first) - pathDepth(second));
    await expandTreeNode("");
    for (const path of requested) {
      if (state.treeNodes.has(path)) await expandTreeNode(path);
    }
    await revealPath(state.path);
    tree.scrollTop = previousScrollTop;
    // Au premier affichage (ou après un rechargement complet), centrer le
    // dossier courant évite qu'il apparaisse collé en bas de l'arborescence.
    selectTreeNode(state.path, { scroll: true, center: previousScrollTop === 0 });
  }

  function scrollTreeRowIntoView(row, center = false) {
    const tree = byId("tree");
    const treeRect = tree.getBoundingClientRect();
    const rowRect = row.getBoundingClientRect();
    if (center) {
      tree.scrollTop +=
        (rowRect.top - treeRect.top) -
        (tree.clientHeight - rowRect.height) / 2;
      return;
    }
    if (rowRect.top < treeRect.top + 4) {
      tree.scrollTop -= treeRect.top - rowRect.top + 4;
    } else if (rowRect.bottom > treeRect.bottom - 4) {
      tree.scrollTop += rowRect.bottom - treeRect.bottom + 4;
    }
  }

  function selectTreeNode(path, { scroll = false, center = false } = {}) {
    for (const entry of state.treeNodes.values()) {
      entry.row.classList.toggle("current", entry.folder.path === path);
    }
    const entry = state.treeNodes.get(path);
    if (entry && scroll) scrollTreeRowIntoView(entry.row, center);
  }

  async function selectFolder(path) {
    if (state.busy) return;
    if (path === state.path) {
      state.treeSelection = path;
      selectTreeNode(path);
      return;
    }
    if (state.pendingPath === path) return;
    state.pendingPath = path;
    const token = ++navigationToken;
    try {
      const data = await jsonPost("/api/select-directory", { directory: absolutePath(path) });
      if (token !== navigationToken) return;
      applyState(data);
      state.treeSelection = path;
      state.selected.clear();
      state.selectedFolders.clear();
      state.lastIndex = -1;
      state.lastFolderIndex = -1;
      state.slideshowIndex = 0;
      selectTreeNode(path);
      updateSelectionUI();
      await refreshContent();
    } catch (error) {
      if (token === navigationToken) showStatus(error.message, "error");
    } finally {
      if (token === navigationToken) state.pendingPath = null;
    }
  }

  function absolutePath(path) {
    if (!path) return state.root;
    return `${state.root.replace(/\/+$/, "")}/${path}`;
  }

  // --- glisser-déposer ---------------------------------------------------

  function hasExternalFiles(event) {
    return Array.from(event.dataTransfer?.types || []).includes("Files");
  }

  function startDrag(event, sources, kind) {
    if (state.busy || !sources.length) {
      event.preventDefault();
      return;
    }
    state.dragging = { sources, kind };
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData(
      "application/x-photos-items",
      JSON.stringify({ sources, kind })
    );
    event.dataTransfer.setData("text/plain", sources.join("\n"));
    if (kind === "photos") {
      for (const name of sources) {
        byId("photo-grid")
          .querySelector(`.photo-card[data-name="${CSS.escape(name)}"]`)
          ?.classList.add("dragging");
      }
    } else {
      for (const path of sources) {
        state.treeNodes.get(path)?.node.classList.add("dragging");
      }
    }
  }

  function endDrag() {
    state.dragging = null;
    setDropHint("");
    document.querySelectorAll(".dragging").forEach(element => {
      element.classList.remove("dragging");
    });
    document.querySelectorAll(".drop-target").forEach(element => {
      element.classList.remove("drop-target");
    });
  }

  function canDrop(destination) {
    if (!state.dragging?.sources?.length) return false;
    return !state.dragging.sources.some(source => pathIsInside(destination, source));
  }

  function parseDrag(event) {
    try {
      const payload = JSON.parse(event.dataTransfer.getData("application/x-photos-items"));
      if (Array.isArray(payload.sources) && payload.sources.length) return payload.sources;
    } catch (_) {
      // Le navigateur peut ne pas exposer les données pendant dragover.
    }
    return state.dragging?.sources || [];
  }

  function handleTreeDragOver(event, destination, row) {
    if (hasExternalFiles(event)) {
      event.preventDefault();
      event.dataTransfer.dropEffect = "copy";
      row.classList.add("drop-target");
      setDropHint(`Importer dans « ${destination ? folderName(destination) : state.rootName} »`);
      return;
    }
    if (!canDrop(destination)) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
    row.classList.add("drop-target");
    setDropHint(`Déplacer vers « ${destination ? folderName(destination) : state.rootName} »`);
  }

  function handleTreeDrop(event, destination, row) {
    event.preventDefault();
    row.classList.remove("drop-target");
    if (hasExternalFiles(event)) {
      importFromFinder(event, destination);
      return;
    }
    const sources = parseDrag(event);
    endDrag();
    if (sources.length) moveItems(sources, destination);
  }

  function handleFolderCardDragOver(event, destination, card) {
    event.stopPropagation();
    if (hasExternalFiles(event)) {
      event.preventDefault();
      event.dataTransfer.dropEffect = "copy";
      card.classList.add("drop-target");
      setDropHint(`Importer dans « ${folderName(destination)} »`);
      return;
    }
    if (!canDrop(destination)) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
    card.classList.add("drop-target");
    setDropHint(`Déplacer vers « ${folderName(destination)} »`);
  }

  function handleFolderCardDrop(event, destination, card) {
    event.preventDefault();
    event.stopPropagation();
    card.classList.remove("drop-target");
    if (hasExternalFiles(event)) {
      importFromFinder(event, destination);
      return;
    }
    const sources = parseDrag(event);
    endDrag();
    if (sources.length) moveItems(sources, destination);
  }

  // --- import depuis le Finder ------------------------------------------

  function readAllEntries(reader) {
    return new Promise((resolve, reject) => {
      const entries = [];
      const read = () => {
        reader.readEntries(batch => {
          if (!batch.length) {
            resolve(entries);
            return;
          }
          entries.push(...batch);
          read();
        }, reject);
      };
      read();
    });
  }

  async function collectEntry(entry, prefix, items) {
    if (entry.isFile) {
      const file = await new Promise((resolve, reject) => entry.file(resolve, reject));
      items.push({ file, relativePath: `${prefix}${entry.name}` });
      return;
    }
    if (entry.isDirectory) {
      const reader = entry.createReader();
      const children = await readAllEntries(reader);
      for (const child of children) {
        await collectEntry(child, `${prefix}${entry.name}/`, items);
      }
    }
  }

  async function itemsFromDrop(event) {
    const transfer = event.dataTransfer;
    const entries = [];
    for (const item of Array.from(transfer.items || [])) {
      if (item.kind !== "file") continue;
      const entry = item.webkitGetAsEntry?.();
      if (entry) entries.push(entry);
    }
    if (entries.length) {
      const items = [];
      for (const entry of entries) await collectEntry(entry, "", items);
      return items;
    }
    return Array.from(transfer.files || []).map(file => ({
      file,
      relativePath: file.name,
    }));
  }

  async function importFromFinder(event, destination) {
    setDropHint("");
    let items = [];
    try {
      items = await itemsFromDrop(event);
    } catch (error) {
      showStatus(`Import impossible : ${error.message}`, "error");
      return;
    }
    if (!items.length) {
      showStatus("Aucun fichier à importer.", "warning");
      return;
    }
    const form = new FormData();
    form.append("destination", destination);
    form.append("paths", JSON.stringify(items.map(item => item.relativePath)));
    form.append("mtimes", JSON.stringify(items.map(item => item.file.lastModified || null)));
    for (const item of items) form.append("files", item.file, item.file.name);
    showStatus(`Import de ${items.length} fichier(s) vers « ${destination ? folderName(destination) : state.rootName} »…`);
    try {
      const response = await fetch("/api/import", { method: "POST", body: form });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.error || `Erreur HTTP ${response.status}`);
      if (data.state) applyState(data.state);
      await renderTree();
      await refreshContent();
      const parts = [];
      if (data.imported?.length) parts.push(`${data.imported.length} fichier(s) importé(s)`);
      if (data.skipped?.length) parts.push(`${data.skipped.length} ignoré(s)`);
      if (data.errors?.length) parts.push(`${data.errors.length} erreur(s)`);
      showStatus(
        parts.join(" · ") || "Aucun fichier importé.",
        data.skipped?.length || data.errors?.length ? "warning" : "success"
      );
    } catch (error) {
      showStatus(error.message, "error");
    }
  }

  // --- notation et corbeille --------------------------------------------

  async function ratePhoto(name, rating) {
    if (state.pendingRatings.has(name)) return;
    const photo = state.photos.find(item => item.name === name);
    const next = photo && Number(photo.rating) === Number(rating) ? 0 : rating;
    state.pendingRatings.add(name);
    try {
      const data = await jsonPost("/api/rating", { path: name, rating: next });
      for (const item of state.photos) {
        if (item.name === name) item.rating = data.rating;
      }
      updateRatingUI(name, data.rating);
      showStatus(data.rating ? `Note : ${data.rating} étoile(s).` : "Note retirée.");
    } catch (error) {
      showStatus(error.message, "error");
    } finally {
      state.pendingRatings.delete(name);
    }
  }

  async function trashPhoto(name) {
    const photo = state.photos.find(item => item.name === name);
    const label = photo?.displayName || name;
    if (!window.confirm(`Mettre « ${label} » à la corbeille ?`)) return;
    try {
      const data = await jsonPost("/api/trash", { path: name });
      state.photos = state.photos.filter(item => item.name !== name);
      state.total = Math.max(0, state.total - 1);
      state.selected.delete(name);
      state.slideshowIndex = Math.min(
        state.slideshowIndex,
        Math.max(0, state.photos.length - 1)
      );
      renderPhotos();
      renderSlideshow();
      updatePhotoHeader();
      updateSelectionUI();
      if (data.state) applyState(data.state);
      await renderTree();
      const warning = data.databaseError ? ` · base : ${data.databaseError}` : "";
      showStatus(`« ${label} » mise à la corbeille.${warning}`, data.databaseError ? "warning" : "success");
    } catch (error) {
      showStatus(error.message, "error");
    }
  }

  // --- diaporama ---------------------------------------------------------

  function currentSlideshowPhoto() {
    return state.photos[state.slideshowIndex] || null;
  }

  function renderSlideshow() {
    const photo = currentSlideshowPhoto();
    const image = byId("slideshow-image");
    const empty = byId("slideshow-empty");
    const rating = byId("slideshow-rating");
    const trash = byId("slideshow-trash");
    const previous = byId("slideshow-prev");
    const next = byId("slideshow-next");
    if (!photo) {
      image.hidden = true;
      image.removeAttribute("src");
      empty.hidden = false;
      empty.textContent = "Aucune photo dans ce dossier.";
      byId("slideshow-name").textContent = "";
      byId("slideshow-meta").textContent = "";
      byId("slideshow-counter").textContent = "0 / 0";
      rating.replaceChildren();
      delete rating.dataset.photo;
      trash.disabled = true;
      previous.disabled = true;
      next.disabled = true;
      return;
    }
    image.hidden = false;
    image.src = photo.preview || photo.thumbnail;
    image.alt = photo.displayName;
    empty.hidden = true;
    byId("slideshow-name").textContent = photo.displayName;
    byId("slideshow-meta").textContent =
      `${formatSize(photo.size)} · ${formatDate(photo.modified)}`;
    byId("slideshow-counter").textContent =
      `${state.slideshowIndex + 1} / ${state.photos.length}`;
    rating.dataset.photo = photo.name;
    renderStars(rating, photo.rating, value => ratePhoto(photo.name, value));
    trash.disabled = false;
    previous.disabled = state.slideshowIndex <= 0;
    next.disabled = state.slideshowIndex >= state.photos.length - 1;
  }

  function stepSlideshow(delta) {
    if (state.viewMode !== "slideshow" || !state.photos.length) return;
    state.slideshowIndex = Math.max(
      0,
      Math.min(state.photos.length - 1, state.slideshowIndex + delta)
    );
    renderSlideshow();
  }

  function updateViewUI() {
    const icons = state.viewMode === "icons";
    byId("photo-grid").hidden = !icons;
    byId("slideshow").hidden = icons;
    byId("view-icons").classList.toggle("active", icons);
    byId("view-slideshow").classList.toggle("active", !icons);
    document.body.classList.toggle("view-slideshow", !icons);
    if (!icons) renderSlideshow();
    updateModeHelp();
  }

  function updateModeUI() {
    for (const mode of ["photos", "folders"]) {
      byId(`mode-${mode}`).classList.toggle("active", state.mode === mode);
    }
    byId("photo-grid").classList.toggle("folders-mode", state.mode === "folders");
    updateModeHelp();
  }

  function updateModeHelp() {
    const help = byId("mode-help");
    if (state.viewMode === "slideshow") {
      help.innerHTML =
        "Diaporama : <strong>←</strong>/<strong>→</strong> pour naviguer, " +
        "les étoiles pour noter, la corbeille pour supprimer la photo affichée.";
      return;
    }
    if (state.mode === "folders") {
      help.innerHTML =
        "Mode Dossiers : sélectionnez un ou plusieurs dossiers, puis glissez-les " +
        "vers un dossier de l’arborescence. <kbd>Maj</kbd> + clic sélectionne une plage.";
      return;
    }
    help.innerHTML =
      "Mode Photos : un clic sélectionne, <kbd>Maj</kbd> + clic sélectionne une plage. " +
      "Glissez les fichiers vers un dossier ou déposez des fichiers du Finder.";
  }

  function setViewMode(mode) {
    if (mode === state.viewMode) return;
    state.viewMode = mode;
    if (mode === "slideshow") {
      if (state.mode === "photos" && state.lastIndex >= 0 && state.lastIndex < state.photos.length) {
        state.slideshowIndex = state.lastIndex;
      }
      state.mode = "photos";
      state.selected.clear();
      state.selectedFolders.clear();
      updateModeUI();
    }
    updateViewUI();
    refreshContent();
  }

  function setMode(mode) {
    if (state.viewMode === "slideshow") {
      state.viewMode = "icons";
      updateViewUI();
    }
    if (state.mode === mode) return;
    state.mode = mode;
    state.selected.clear();
    state.selectedFolders.clear();
    state.lastIndex = -1;
    state.lastFolderIndex = -1;
    updateModeUI();
    updateSelectionUI();
    refreshContent();
  }

  // --- opérations --------------------------------------------------------

  async function moveItems(sources, destination) {
    if (state.busy || !sources.length) return;
    if (sources.some(source => pathIsInside(destination, source))) {
      showStatus("Un dossier ne peut pas être déplacé dans lui-même.", "warning");
      return;
    }
    const label = destination ? folderName(destination) : state.rootName;
    state.busy = true;
    updateSelectionUI();
    showStatus(`Déplacement de ${sources.length} élément(s) vers « ${label} »…`);
    try {
      const data = await jsonPost("/api/move", { sources, destination });
      if (data.state) applyState(data.state);
      state.selected.clear();
      state.selectedFolders.clear();
      state.lastIndex = -1;
      state.lastFolderIndex = -1;
      state.treeSelection = null;
      await renderTree();
      await refreshContent();
      const errors = data.results
        .filter(result => result.status === "error")
        .map(result => `${folderName(result.source)} : ${result.error}`);
      const skipped = data.results
        .filter(result => result.status === "skipped")
        .map(result => result.error || `${folderName(result.source)} ignoré`);
      const parts = [];
      if (data.moved) parts.push(`${data.moved} élément(s) déplacé(s)`);
      if (data.databases?.photos) parts.push(`photos.db : ${data.databases.photos} chemin(s)`);
      const faceCount =
        (data.databases?.face_photos || 0) +
        (data.databases?.faces || 0) +
        (data.databases?.reference_faces || 0);
      if (faceCount) parts.push(`face.db : ${faceCount} chemin(s)`);
      if (!parts.length) parts.push("Aucun changement.");
      if (errors.length) parts.push(...errors);
      if (skipped.length) parts.push(...skipped);
      showStatus(parts.join("\n"), data.errors ? "warning" : "success");
    } catch (error) {
      showStatus(error.message, "error");
    } finally {
      state.busy = false;
      updateSelectionUI();
    }
  }

  function openFolderDialog() {
    byId("folder-parent").textContent = folderName(state.path);
    byId("folder-name").value = "";
    byId("folder-error").textContent = "";
    byId("folder-dialog").showModal();
    setTimeout(() => byId("folder-name").focus(), 0);
  }

  async function createFolder(event) {
    event.preventDefault();
    const name = byId("folder-name").value.trim();
    if (!name) return;
    try {
      const data = await jsonPost("/api/create-folder", { parent: state.path, name });
      byId("folder-dialog").close();
      state.openPaths.add(state.path);
      await renderTree();
      await refreshContent();
      showStatus(`Dossier créé : ${data.name}`);
    } catch (error) {
      byId("folder-error").textContent = error.message;
    }
  }

  function openRenameDialog() {
    if (state.mode === "folders" && state.selectedFolders.size === 1) {
      const source = [...state.selectedFolders][0];
      state.renameTarget = {
        kind: "folder",
        source,
        name: folderName(source),
      };
    } else if (state.mode === "photos" && state.selected.size === 1) {
      const photo = state.photos.find(item => state.selected.has(item.name));
      if (!photo) return;
      state.renameTarget = { kind: "photo", source: photo.name, name: photo.displayName };
    } else {
      return;
    }
    byId("rename-source").textContent =
      state.renameTarget.kind === "photo" ? "Photo" : "Dossier";
    byId("rename-name").value = state.renameTarget.name;
    byId("rename-error").textContent = "";
    byId("rename-dialog").showModal();
    setTimeout(() => {
      byId("rename-name").focus();
      byId("rename-name").select();
    }, 0);
  }

  async function renameItem(event) {
    event.preventDefault();
    const target = state.renameTarget;
    const name = byId("rename-name").value.trim();
    if (!target || !name) return;
    try {
      const data = await jsonPost("/api/rename", { source: target.source, name });
      byId("rename-dialog").close();
      if (data.state) applyState(data.state);
      state.selected.clear();
      state.selectedFolders.clear();
      state.treeSelection = null;
      state.lastIndex = -1;
      state.lastFolderIndex = -1;
      await renderTree();
      await refreshContent();
      showStatus(
        `${target.kind === "photo" ? "Photo" : "Dossier"} renommé : ${folderName(data.destination)}` +
        (data.databases?.photos ? ` · photos.db : ${data.databases.photos}` : "")
      );
    } catch (error) {
      byId("rename-error").textContent = error.message;
    }
  }

  async function openMoveDialog() {
    const sources = state.mode === "folders"
      ? [...state.selectedFolders]
      : [...state.selected];
    if (!sources.length) return;
    try {
      const data = await api("/api/directories");
      const select = byId("move-destination");
      select.replaceChildren();
      for (const directory of data.directories) {
        if (sources.some(source => pathIsInside(directory.path, source))) continue;
        const option = document.createElement("option");
        option.value = directory.path;
        option.textContent =
          `${"  ".repeat(directory.depth)}${directory.path ? directory.name : state.rootName}`;
        select.appendChild(option);
      }
      if (!select.options.length) {
        showStatus("Aucune destination disponible.", "warning");
        return;
      }
      const preferred = [...select.options].find(option => option.value === state.path);
      if (preferred) select.value = preferred.value;
      state.moveSources = sources;
      byId("move-summary").textContent =
        `${sources.length} élément(s) à déplacer`;
      byId("move-error").textContent = "";
      byId("move-dialog").showModal();
    } catch (error) {
      showStatus(error.message, "error");
    }
  }

  async function submitMove(event) {
    event.preventDefault();
    const destination = byId("move-destination").value;
    const sources = state.moveSources;
    byId("move-dialog").close();
    await moveItems(sources, destination);
  }

  async function changeDirectory() {
    if (state.busy) return;
    const token = ++navigationToken;
    state.pendingPath = null;
    try {
      const data = await jsonPost("/api/change-directory", {});
      if (token !== navigationToken) return;
      applyState(data);
      state.treeSelection = state.path;
      state.selected.clear();
      state.selectedFolders.clear();
      state.lastIndex = -1;
      state.lastFolderIndex = -1;
      state.slideshowIndex = 0;
      updateSelectionUI();
      const reveal = revealPath(state.path).then(() => {
        if (token === navigationToken) selectTreeNode(state.path, { scroll: true });
      });
      await Promise.all([reveal, refreshContent()]);
    } catch (error) {
      if (token === navigationToken) showStatus(error.message, "error");
    }
  }

  async function exitApp() {
    try {
      await jsonPost("/api/shutdown", {});
    } catch (_) {
      // Le serveur peut couper la réponse en s'arrêtant.
    }
    window.close();
  }

  // --- événements --------------------------------------------------------

  function bindEvents() {
    byId("new-folder").addEventListener("click", openFolderDialog);
    byId("folder-form").addEventListener("submit", createFolder);
    byId("folder-cancel").addEventListener("click", () => byId("folder-dialog").close());
    byId("rename-form").addEventListener("submit", renameItem);
    byId("rename-cancel").addEventListener("click", () => byId("rename-dialog").close());
    byId("move-form").addEventListener("submit", submitMove);
    byId("move-cancel").addEventListener("click", () => byId("move-dialog").close());
    byId("select-all").addEventListener("click", selectAll);
    byId("deselect").addEventListener("click", deselectAll);
    byId("rename").addEventListener("click", openRenameDialog);
    byId("move-to").addEventListener("click", openMoveDialog);
    byId("refresh").addEventListener("click", refreshAll);
    byId("change-directory").addEventListener("click", changeDirectory);
    byId("exit-app").addEventListener("click", exitApp);
    byId("mode-photos").addEventListener("click", () => setMode("photos"));
    byId("mode-folders").addEventListener("click", () => setMode("folders"));
    byId("view-icons").addEventListener("click", () => setViewMode("icons"));
    byId("view-slideshow").addEventListener("click", () => setViewMode("slideshow"));
    byId("slideshow-prev").addEventListener("click", () => stepSlideshow(-1));
    byId("slideshow-next").addEventListener("click", () => stepSlideshow(1));
    byId("slideshow-trash").addEventListener("click", () => {
      const photo = currentSlideshowPhoto();
      if (photo) trashPhoto(photo.name);
    });

    const grid = byId("photo-grid");
    grid.addEventListener("dragover", event => {
      if (hasExternalFiles(event)) {
        event.preventDefault();
        event.dataTransfer.dropEffect = "copy";
        grid.classList.add("drop-target");
        setDropHint(`Importer dans « ${folderName(state.path)} »`);
        return;
      }
      if (!canDrop(state.path)) return;
      event.preventDefault();
      event.dataTransfer.dropEffect = "move";
      grid.classList.add("drop-target");
      setDropHint(`Déplacer vers « ${folderName(state.path)} »`);
    });
    grid.addEventListener("dragleave", event => {
      if (!grid.contains(event.relatedTarget)) {
        grid.classList.remove("drop-target");
        setDropHint("");
      }
    });
    grid.addEventListener("drop", event => {
      event.preventDefault();
      grid.classList.remove("drop-target");
      if (hasExternalFiles(event)) {
        importFromFinder(event, state.path);
        return;
      }
      const sources = parseDrag(event);
      endDrag();
      if (sources.length) moveItems(sources, state.path);
    });

    window.addEventListener("message", event => {
      if (event.data?.type === "database-updated") refreshAll();
    });
    window.addEventListener("dragover", event => event.preventDefault());
    window.addEventListener("drop", event => event.preventDefault());
    document.addEventListener("keydown", event => {
      const target = event.target;
      if (
        target instanceof HTMLInputElement ||
        target instanceof HTMLSelectElement ||
        target instanceof HTMLTextAreaElement
      ) {
        return;
      }
      if (byId("folder-dialog").open || byId("rename-dialog").open || byId("move-dialog").open) return;
      if (state.viewMode === "slideshow") {
        if (event.key === "ArrowLeft") {
          event.preventDefault();
          stepSlideshow(-1);
        } else if (event.key === "ArrowRight") {
          event.preventDefault();
          stepSlideshow(1);
        } else if (event.key === "Escape") {
          event.preventDefault();
          setViewMode("icons");
        }
        return;
      }
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "a") {
        event.preventDefault();
        selectAll();
      } else if (event.key === "F2" || (event.metaKey && event.key === "Enter")) {
        event.preventDefault();
        openRenameDialog();
      } else if (event.key === "Escape") {
        deselectAll();
      }
    });
  }

  init();
})();
