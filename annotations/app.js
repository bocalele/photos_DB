(() => {
  "use strict";

  const ICONS = {
    pin: '<path d="M12 2a7 7 0 0 0-7 7c0 5.2 7 13 7 13s7-7.8 7-13a7 7 0 0 0-7-7zm0 9.6A2.6 2.6 0 1 1 12 6.4a2.6 2.6 0 0 1 0 5.2z"/>',
    paw: '<path d="M12 12.4c-3.7 0-6.8 3.1-6.8 6.8 0 2.4 1.7 3.8 3.6 3.8 1 0 2.1-.5 3.2-.5s2.2.5 3.2.5c1.9 0 3.6-1.4 3.6-3.8 0-3.7-3.1-6.8-6.8-6.8zM5.4 6.3c1 0 1.8 1 1.8 2.3S6.4 11 5.4 11 3.6 10 3.6 8.6s.8-2.3 1.8-2.3zm4.5-2.6c1 0 1.8 1.1 1.8 2.5S10.9 8.7 9.9 8.7 8.1 7.6 8.1 6.2s.8-2.5 1.8-2.5zm4.2 0c1 0 1.8 1.1 1.8 2.5s-.8 2.5-1.8 2.5-1.8-1.1-1.8-2.5.8-2.5 1.8-2.5zm4.5 2.6c1 0 1.8 1 1.8 2.3s-.8 2.3-1.8 2.3-1.8-1-1.8-2.3.8-2.3 1.8-2.3z"/>',
    user: '<path d="M12 12.2a4.6 4.6 0 1 0 0-9.2 4.6 4.6 0 0 0 0 9.2zm0 2.2c-4.4 0-8 2.3-8 5.1V22h16v-2.5c0-2.8-3.6-5.1-8-5.1z"/>',
    tag: '<path d="M20.6 12.3 12.3 4H4v8.3l8.3 8.3a1.7 1.7 0 0 0 2.4 0l5.9-5.9a1.7 1.7 0 0 0 0-2.4zM7.5 9.1a1.6 1.6 0 1 1 0-3.2 1.6 1.6 0 0 1 0 3.2z"/>',
    folder: '<path d="M3 5.5A2.5 2.5 0 0 1 5.5 3H10l2 2h6.5A2.5 2.5 0 0 1 21 7.5v9A2.5 2.5 0 0 1 18.5 19h-13A2.5 2.5 0 0 1 3 16.5v-11zm2 2v9c0 .3.2.5.5.5h13c.3 0 .5-.2.5-.5v-7H5z"/>',
  };

  const COLORS = {
    geotag: "var(--geotag)",
    faune: "var(--faune)",
    face: "var(--face)",
    motscles: "var(--motscles)",
    organize: "var(--organize)",
  };

  const state = {
    directory: null,
    apps: [],
    active: null,
    booted: false,
    revision: 0,
    databaseRevision: 0,
    databaseScope: null,
    databaseSyncedAt: null,
    databaseDirty: true,
    faunaPending: [],
  };

  const frames = new Map();
  let lastDirectory = null;
  let pollDelay = 1500;
  let pollTimer = null;
  let polling = false;
  let noticeTimer = null;
  let stateRequest = null;
  let exiting = false;

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

  function updateSyncButton() {
    const button = byId("sync-database");
    button.classList.toggle("dirty", state.databaseDirty);
    if (state.databaseDirty) {
      button.title =
        "photos.db doit être mise à jour depuis les métadonnées des fichiers.";
    } else {
      const when = state.databaseSyncedAt
        ? ` Dernière synchronisation : ${state.databaseSyncedAt}.`
        : "";
      button.title = `photos.db est à jour.${when}`;
    }
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
        frame.dataset.revision = String(state.revision);
        frame.dataset.stale = "";
        if (app.id === "faune") state.faunaPending = [];
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

  function markFramesStale() {
    for (const [id, frame] of frames) {
      if (id === state.active) continue;
      if (frame.dataset.url || frame.src) frame.dataset.stale = "1";
    }
  }

  function notifyDatabaseUpdated() {
    const frame = frames.get("organize");
    if (!frame || frame.dataset.loaded !== "1" || !frame.contentWindow) return;
    frame.contentWindow.postMessage(
      { type: "database-updated", source: "annotations" },
      "*"
    );
  }

  async function fetchState() {
    if (!stateRequest) {
      stateRequest = api("/api/state")
        .then(data => {
          applyState(data);
          return data;
        })
        .finally(() => { stateRequest = null; });
    }
    return stateRequest;
  }

  async function activate(appId, skipState = false) {
    const app = appById(appId);
    if (!app) return;
    // Une autre app a pu modifier les fichiers depuis le dernier poll : on
    // relit l'état avant de réafficher un cadre potentiellement obsolète.
    if (state.booted && !skipState) await fetchState().catch(() => {});
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
    const urlChanged = frame.dataset.url !== (app.url || "");
    const stale = frame.dataset.stale === "1"
      || (frame.dataset.loaded === "1"
          && frame.dataset.revision !== String(state.revision));
    if (!frame.src || stale || urlChanged) {
      showStatus(`Chargement de ${app.label}…`);
      frame.dataset.loaded = "";
      frame.dataset.stale = "";
      frame.dataset.url = app.url || "";
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

  function reloadFrame(appId) {
    const app = appById(appId);
    const frame = frames.get(appId);
    if (!app || !app.url || !frame) return;
    showStatus(`Mise à jour de ${app.label}…`);
    frame.dataset.loaded = "";
    frame.dataset.stale = "";
    frame.dataset.url = app.url;
    frame.src = frameUrl(app, true);
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
    const previousRevision = state.revision;
    state.apps = data.apps;
    state.directory = data.directory;
    state.revision = Number(data.revision) || 0;
    state.databaseRevision = Number(data.databaseRevision) || 0;
    state.databaseScope = data.databaseScope ?? null;
    state.databaseSyncedAt = data.databaseSyncedAt ?? null;
    state.databaseDirty = Boolean(data.databaseDirty);
    lastDirectory = data.directory;
    updateSyncButton();

    if (!state.booted) {
      state.active = data.active;
      renderTabs();
      updateDirectory();
      state.booted = true;
      activate(state.active, true);
      return;
    }

    if (state.revision !== previousRevision) markFramesStale();
    updateTabs();
    updateDirectory();
    if (directoryChanged) {
      notice("Dossier : " + data.directory);
      state.faunaPending = [];
      // L'app active a déjà changé de dossier : la recharger de force
      // provoquerait un rafraîchissement complet de la fenêtre et remettrait
      // l'arborescence à zéro. Les autres cadres seront rechargés à la demande
      // lors de leur prochaine activation.
      markFramesStale();
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
    const previousDirectory = lastDirectory;
    try {
      const data = await api("/api/choose-directory", { method: "POST" });
      applyState(data);
      if (data.directory !== previousDirectory) {
        const active = appById(state.active);
        if (active) reloadFrame(active.id);
      }
    } catch (error) {
      notice(error.message, true);
    } finally {
      button.disabled = false;
      button.textContent = label;
    }
  }

  async function restartApp(appId) {
    try {
      if (appId === "faune") state.faunaPending = [];
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
    if (event.key === "Escape" && !byId("exit-overlay").hidden) {
      hideExitDialog();
      return;
    }
    if (!(event.metaKey || event.altKey) || event.shiftKey) return;
    const position = Number(event.key);
    if (!Number.isInteger(position) || position < 1 || position > state.apps.length) return;
    event.preventDefault();
    activate(state.apps[position - 1].id);
  });

  function pendingFaunaMessage() {
    const count = state.faunaPending.length;
    const names = state.faunaPending.slice(0, 3).join(", ");
    const detail = names ? ` (${names}${count > 3 ? "…" : ""})` : "";
    return (
      `Faune : ${count} détermination(s) non validée(s)${detail}. ` +
      "Validez-les ou supprimez-les avant de mettre à jour photos.db."
    );
  }

  async function syncDatabase() {
    if (state.faunaPending.length) {
      notice(pendingFaunaMessage(), true);
      return false;
    }
    const button = byId("sync-database");
    const label = button.querySelector(".sync-label");
    button.disabled = true;
    button.classList.add("busy");
    label.textContent = "Mise à jour…";
    try {
      const data = await api("/api/sync-database", { method: "POST" });
      state.revision = Number(data.revision ?? state.revision) || state.revision;
      state.databaseRevision = Number(data.databaseRevision) || 0;
      state.databaseScope = data.databaseScope ?? null;
      state.databaseSyncedAt = data.databaseSyncedAt ?? null;
      state.databaseDirty = Boolean(data.databaseDirty);
      updateSyncButton();
      // La mise à jour peut changer les statuts de Classement (lignes
      // supprimées, photos ajoutées). On invalide les cadres inactifs et on
      // demande au cadre Classement actif de relire l'arbre sans recharger
      // toute la page.
      markFramesStale();
      if (state.active === "organize") notifyDatabaseUpdated();
      const parts = [
        `${data.photos} photo(s)`,
        `${data.keyword_links} mot(s)-clé(s)`,
        `${data.taxa_links} taxon(s)`,
      ];
      if (data.errors) parts.push(`${data.errors} erreur(s)`);
      if (data.pruned_photos) parts.push(`${data.pruned_photos} photo(s) retirée(s)`);
      const scope = data.scope ? ` · ${data.scope}` : "";
      const complete = data.databaseSyncComplete !== false
        && !data.databaseDirty;
      if (!complete) {
        const reason = data.errors
          ? "des fichiers n’ont pas pu être lus"
          : "une écriture est arrivée pendant la synchronisation";
        notice(
          `photos.db : ${parts.join(", ")}${scope} · ${reason}. ` +
          "La base reste à mettre à jour.",
          true
        );
        return false;
      }
      notice(`photos.db : ${parts.join(", ")}${scope} · ${data.total_photos} photo(s) en base.`);
      return true;
    } catch (error) {
      notice(`photos.db : ${error.message}`, true);
      return false;
    } finally {
      button.disabled = false;
      button.classList.remove("busy");
      label.textContent = "photos.db";
      updateSyncButton();
    }
  }

  function showExitDialog() {
    byId("exit-overlay").hidden = false;
    byId("exit-save").focus();
  }

  function hideExitDialog() {
    byId("exit-overlay").hidden = true;
  }

  async function performExit(updateDatabase) {
    if (exiting) return;
    exiting = true;
    if (updateDatabase && state.faunaPending.length) {
      exiting = false;
      hideExitDialog();
      notice(pendingFaunaMessage(), true);
      return;
    }
    if (updateDatabase) {
      const saveButton = byId("exit-save");
      const cancelButton = byId("exit-cancel");
      const withoutButton = byId("exit-without-save");
      const originalLabel = saveButton.textContent;
      saveButton.disabled = true;
      cancelButton.disabled = true;
      withoutButton.disabled = true;
      saveButton.textContent = "Mise à jour de photos.db…";
      const saved = await syncDatabase();
      saveButton.disabled = false;
      cancelButton.disabled = false;
      withoutButton.disabled = false;
      saveButton.textContent = originalLabel;
      if (!saved) {
        exiting = false;
        if (state.faunaPending.length) {
          hideExitDialog();
          notice(pendingFaunaMessage(), true);
        } else {
          showExitDialog();
        }
        return;
      }
    }
    hideExitDialog();
    try {
      await api("/api/shutdown", { method: "POST" });
    } catch (_) {
      // Le serveur peut déjà être arrêté.
    }
    window.open("", "_self");
    window.close();
    setTimeout(() => {
      document.body.innerHTML =
        '<main style="display:grid;place-items:center;height:100vh;font-family:sans-serif;background:#12141d;color:#edf0f7">' +
        '<div style="text-align:center"><h1>Serveur arrêté</h1><p>Vous pouvez fermer cette fenêtre.</p></div></main>';
    }, 250);
  }

  async function exitAnnotations() {
    if (exiting) return;
    // Relire l'état une dernière fois : une écriture peut être arrivée juste
    // après le dernier rafraîchissement automatique.
    await fetchState().catch(() => {});
    if (state.databaseDirty) {
      showExitDialog();
      return;
    }
    await performExit(false);
  }

  window.addEventListener("message", event => {
    if (event.data?.type === "fauna-pending") {
      state.faunaPending = Array.isArray(event.data.names)
        ? event.data.names
        : [];
      return;
    }
    if (event.data?.type === "photos-exit") exitAnnotations();
  });

  byId("exit-app").addEventListener("click", exitAnnotations);
  byId("sync-database").addEventListener("click", syncDatabase);
  byId("exit-save").addEventListener("click", () => performExit(true));
  byId("exit-without-save").addEventListener("click", () => performExit(false));
  byId("exit-cancel").addEventListener("click", hideExitDialog);
  byId("exit-overlay").addEventListener("click", event => {
    if (event.target.id === "exit-overlay") hideExitDialog();
  });
  window.addEventListener("focus", () => refresh());

  refresh();
})();
