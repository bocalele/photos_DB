(() => {
  "use strict";

  const ICONS = {
    pin: '<path d="M12 2a7 7 0 0 0-7 7c0 5.2 7 13 7 13s7-7.8 7-13a7 7 0 0 0-7-7zm0 9.6A2.6 2.6 0 1 1 12 6.4a2.6 2.6 0 0 1 0 5.2z"/>',
    paw: '<path d="M12 12.4c-3.7 0-6.8 3.1-6.8 6.8 0 2.4 1.7 3.8 3.6 3.8 1 0 2.1-.5 3.2-.5s2.2.5 3.2.5c1.9 0 3.6-1.4 3.6-3.8 0-3.7-3.1-6.8-6.8-6.8zM5.4 6.3c1 0 1.8 1 1.8 2.3S6.4 11 5.4 11 3.6 10 3.6 8.6s.8-2.3 1.8-2.3zm4.5-2.6c1 0 1.8 1.1 1.8 2.5S10.9 8.7 9.9 8.7 8.1 7.6 8.1 6.2s.8-2.5 1.8-2.5zm4.2 0c1 0 1.8 1.1 1.8 2.5s-.8 2.5-1.8 2.5-1.8-1.1-1.8-2.5.8-2.5 1.8-2.5zm4.5 2.6c1 0 1.8 1 1.8 2.3s-.8 2.3-1.8 2.3-1.8-1-1.8-2.3.8-2.3 1.8-2.3z"/>',
    user: '<path d="M12 12.2a4.6 4.6 0 1 0 0-9.2 4.6 4.6 0 0 0 0 9.2zm0 2.2c-4.4 0-8 2.3-8 5.1V22h16v-2.5c0-2.8-3.6-5.1-8-5.1z"/>',
    tag: '<path d="M20.6 12.3 12.3 4H4v8.3l8.3 8.3a1.7 1.7 0 0 0 2.4 0l5.9-5.9a1.7 1.7 0 0 0 0-2.4zM7.5 9.1a1.6 1.6 0 1 1 0-3.2 1.6 1.6 0 0 1 0 3.2z"/>',
  };

  const COLORS = {
    geotag: "var(--geotag)",
    faune: "var(--faune)",
    face: "var(--face)",
    motscles: "var(--motscles)",
  };

  const state = {
    directory: null,
    apps: [],
    active: null,
    booted: false,
  };

  const frames = new Map();
  let lastDirectory = null;
  let pollDelay = 1500;
  let pollTimer = null;
  let polling = false;
  let noticeTimer = null;

  const byId = id => document.getElementById(id);

  async function api(url, options) {
    const response = await fetch(url, options);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `Erreur HTTP ${response.status}`);
    return data;
  }

  function appById(id) {
    return state.apps.find(app => app.id === id) || null;
  }

  function notice(message, isError = false) {
    const element = byId("notice");
    element.textContent = message;
    element.classList.toggle("error", isError);
    element.hidden = false;
    clearTimeout(noticeTimer);
    noticeTimer = setTimeout(() => { element.hidden = true; }, 5000);
  }

  // --- barre ---------------------------------------------------------------

  function renderTabs() {
    const container = byId("app-tabs");
    container.replaceChildren();
    for (const app of state.apps) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "app-tab";
      button.dataset.app = app.id;
      button.dataset.status = app.status;
      button.style.setProperty("--tab-color", COLORS[app.id] || "var(--accent)");
      button.title = app.description || app.label;

      const icon = document.createElement("span");
      icon.className = "tab-icon";
      icon.innerHTML = `<svg viewBox="0 0 24 24" aria-hidden="true">${ICONS[app.icon] || ""}</svg>`;

      const label = document.createElement("span");
      label.className = "tab-label";
      label.textContent = app.label;

      const dot = document.createElement("span");
      dot.className = "tab-dot";

      button.append(icon, label, dot);
      button.addEventListener("click", () => activate(app.id));
      container.appendChild(button);
    }
  }

  function updateTabs() {
    for (const button of byId("app-tabs").querySelectorAll(".app-tab")) {
      const app = appById(button.dataset.app);
      if (!app) continue;
      button.dataset.status = app.status;
      button.classList.toggle("active", app.id === state.active);
    }
  }

  function updateDirectory() {
    const element = byId("directory-path");
    element.textContent = state.directory || "";
    element.title = state.directory || "";
  }

  // --- cadres --------------------------------------------------------------

  function ensureFrame(app) {
    let frame = frames.get(app.id);
    if (!frame) {
      frame = document.createElement("iframe");
      frame.className = "app-frame";
      frame.dataset.app = app.id;
      frame.title = app.label;
      frame.hidden = true;
      frame.addEventListener("load", () => {
        frame.dataset.loaded = "1";
        if (state.active === app.id) hideStatus();
      });
      byId("frames-host").appendChild(frame);
      frames.set(app.id, frame);
    }
    return frame;
  }

  function frameUrl(app, fresh = false) {
    if (!app.url) return null;
    if (!fresh) return `${app.url}?embed=1`;
    const separator = app.url.includes("?") ? "&" : "?";
    return `${app.url}${separator}v=${Date.now()}&embed=1`;
  }

  function showFrame(appId) {
    for (const [id, frame] of frames) {
      frame.hidden = id !== appId;
    }
  }

  function activate(appId) {
    const app = appById(appId);
    if (!app) return;
    state.active = appId;
    updateTabs();

    if (app.status !== "ready") {
      showStatus(
        app.status === "error" || app.status === "stopped"
          ? "L’application ne répond pas."
          : `Démarrage de ${app.label}…`,
        app
      );
      showFrame(appId);
      api("/api/active", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ app: appId }),
      }).catch(() => {});
      return;
    }

    const frame = ensureFrame(app);
    if (!frame.src) {
      showStatus(`Chargement de ${app.label}…`);
      frame.dataset.loaded = "";
      frame.src = frameUrl(app, true);
    } else if (frame.dataset.loaded === "1") {
      hideStatus();
    } else {
      showStatus(`Chargement de ${app.label}…`);
    }
    showFrame(appId);
    api("/api/active", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ app: appId }),
    }).catch(() => {});
  }

  function reloadFrames() {
    for (const [id, frame] of frames) {
      const app = appById(id);
      if (!app || !app.url) continue;
      const url = frameUrl(app, true);
      if (!url) continue;
      frame.dataset.loaded = "";
      frame.src = url;
    }
    const active = appById(state.active);
    if (active) showStatus(`Mise à jour de ${active.label}…`);
  }

  // --- statut --------------------------------------------------------------

  function showStatus(text, app = null) {
    const box = byId("frame-status");
    box.hidden = false;
    byId("status-text").textContent = text;
    const failed = app && (app.status === "error" || app.status === "stopped");
    byId("status-spinner").hidden = Boolean(failed);
    byId("status-error").hidden = !failed;
    byId("status-log").hidden = true;
    if (failed) {
      byId("status-error-message").textContent =
        app.error || "Le serveur de cette application ne répond pas.";
      byId("restart-app").dataset.app = app.id;
      byId("show-log").dataset.app = app.id;
    }
  }

  function hideStatus() {
    byId("frame-status").hidden = true;
  }

  function applyState(data) {
    const directoryChanged = lastDirectory !== null && data.directory !== lastDirectory;
    state.apps = data.apps;
    state.directory = data.directory;
    lastDirectory = data.directory;

    if (!state.booted) {
      state.active = data.active;
      renderTabs();
      updateDirectory();
      state.booted = true;
      activate(state.active);
      return;
    }

    updateTabs();
    updateDirectory();
    if (directoryChanged) {
      notice("Dossier : " + data.directory);
      reloadFrames();
      // Certaines apps peuvent encore démarrer : le prochain poll les chargera.
    }

    const active = appById(state.active);
    if (!active) return;
    if (active.status !== "ready") {
      showStatus(`Démarrage de ${active.label}…`, active);
      return;
    }

    const frame = frames.get(active.id);
    if (!frame || !frame.src) {
      const created = ensureFrame(active);
      showStatus(`Chargement de ${active.label}…`);
      created.dataset.loaded = "";
      created.src = frameUrl(active, true);
      showFrame(active.id);
      return;
    }
    if (frame.dataset.loaded === "1") hideStatus();
  }

  async function refresh() {
    if (polling) return;
    polling = true;
    clearTimeout(pollTimer);
    try {
      const data = await api("/api/state");
      pollDelay = data.apps.some(app => app.status === "starting") ? 1200 : 4000;
      applyState(data);
    } catch (error) {
      pollDelay = 5000;
      notice("Serveur d’annotations injoignable : " + error.message, true);
    } finally {
      polling = false;
      pollTimer = setTimeout(refresh, pollDelay);
    }
  }

  // --- actions -------------------------------------------------------------

  async function chooseDirectory() {
    const button = byId("choose-directory");
    button.disabled = true;
    const label = button.textContent;
    button.textContent = "Sélection…";
    try {
      const data = await api("/api/choose-directory", { method: "POST" });
      applyState(data);
    } catch (error) {
      notice(error.message, true);
    } finally {
      button.disabled = false;
      button.textContent = label;
    }
  }

  async function restartApp(appId) {
    try {
      showStatus("Redémarrage…");
      await api("/api/restart", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ app: appId }),
      });
      const frame = frames.get(appId);
      if (frame) {
        frame.removeAttribute("src");
        frame.dataset.loaded = "";
      }
      refresh();
    } catch (error) {
      notice(error.message, true);
    }
  }

  async function showLog(appId) {
    const pre = byId("status-log");
    if (!pre.hidden) {
      pre.hidden = true;
      return;
    }
    try {
      const data = await api(`/api/logs?app=${encodeURIComponent(appId)}&lines=80`);
      pre.textContent = data.log || "(journal vide)";
      pre.hidden = false;
      pre.scrollTop = pre.scrollHeight;
    } catch (error) {
      notice(error.message, true);
    }
  }

  // --- événements ----------------------------------------------------------

  byId("choose-directory").addEventListener("click", chooseDirectory);

  byId("open-external").addEventListener("click", () => {
    const app = appById(state.active);
    if (app?.url) window.open(app.url, "_blank", "noopener");
  });

  byId("restart-app").addEventListener("click", event => {
    restartApp(event.currentTarget.dataset.app || state.active);
  });

  byId("show-log").addEventListener("click", event => {
    showLog(event.currentTarget.dataset.app || state.active);
  });

  document.addEventListener("keydown", event => {
    if (!(event.metaKey || event.altKey) || event.shiftKey) return;
    const position = Number(event.key);
    if (!Number.isInteger(position) || position < 1 || position > state.apps.length) return;
    event.preventDefault();
    activate(state.apps[position - 1].id);
  });

  async function syncDatabase() {
    const button = byId("sync-database");
    const label = button.querySelector("span");
    button.disabled = true;
    button.classList.add("busy");
    label.textContent = "Mise à jour…";
    try {
      const data = await api("/api/sync-database", { method: "POST" });
      const parts = [
        `${data.photos} photo(s)`,
        `${data.keyword_links} mot(s)-clé(s)`,
        `${data.taxa_links} taxon(s)`,
      ];
      if (data.errors) parts.push(`${data.errors} erreur(s)`);
      if (data.pruned_photos) parts.push(`${data.pruned_photos} photo(s) retirée(s)`);
      const scope = data.scope ? ` · ${data.scope}` : "";
      notice(`photos.db : ${parts.join(", ")}${scope} · ${data.total_photos} photo(s) en base.`);
    } catch (error) {
      notice(`photos.db : ${error.message}`, true);
    } finally {
      button.disabled = false;
      button.classList.remove("busy");
      label.textContent = "photos.db";
    }
  }

  byId("sync-database").addEventListener("click", syncDatabase);
  window.addEventListener("focus", () => refresh());

  refresh();
})();
