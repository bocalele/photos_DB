let state = null;
let selectedKeywords = new Set();
let minRating = 0;
let yearMin = null;
let yearMax = null;
let keywordsModeInitialized = false;
let filtersInitialized = false;
let currentMode = 'graph'; // default mode
let currentAppMode = 'faune';
const APP_MODES = {
  geotag: { layout: 'map' },
  faune: { layout: 'graph', dataset: 'fauna' },
  face: { layout: 'graph', dataset: 'family' },
  motscles: { layout: 'keywords' },
};
let datasetLoadSeq = 0;
let populateKeywords = [];
let currentFilterResult = { photos: [], ids: [], total: 0 };
let photoModalShown = 0;
let photoModalPhotos = [];
let photoModalSourcePhotos = [];
let photoModalLastIndex = -1;
let photoModalSortSeq = 0;
const PHOTO_SORTS = new Set([
  'date_desc', 'date_asc', 'rating_desc', 'rating_asc',
  'filename_asc', 'filename_desc', 'random',
]);
let photoModalSort = (() => {
  try {
    const saved = localStorage.getItem('select.photoSort');
    return PHOTO_SORTS.has(saved) ? saved : 'date_desc';
  } catch (_) {
    return 'date_desc';
  }
})();

let selectedPhotoIds = new Set();
let selectedPhotoCache = new Map();
let albumOptions = null;
let albumConfig = null;
let albumReturnMode = 'graph';
let albumEventsBound = false;
let albumPreviewSeq = 0;
let albumPreviewTimer = null;

const keywordFrequencies = {};
let keywordsLoaded = false;

async function initializeKeywordFrequencies() {
  if (keywordsLoaded) return;
  keywordsLoaded = true;
  const response = await fetch('/api/keywords');
  const data = await response.json();
  populateKeywords = (data.keywords || []).slice(0, 30).map(item => item.label);
  for (const item of data.keywords || []) {
    keywordFrequencies[item.label.toLowerCase()] = item.count || 0;
  }
}

function getKeywordFrequency(keyword) {
  return keywordFrequencies[keyword.toLowerCase()] || 0;
}

// =========================================================
//  Visualiseur de graphe hiérarchique générique
//  Datasets supportés : famille (généalogie) + thésaurus (taxonomie)
// =========================================================

// ---------- Parser thésaurus (.txt indenté) ----------
function parseThesaurus(text) {
  const lines = text.replace(/^\uFEFF/, '').split(/\r?\n/);
  const nodes = [];
  const parentsOf = {};
  const stack = [];
  let nextId = 1;
  for (const raw of lines) {
    if (!raw.trim()) continue;
    let depth = 0;
    while (depth < raw.length && raw[depth] === '\t') depth++;
    const content = raw.slice(depth).trim();
    if (!content) continue;
    const brace = content.match(/^\{(.+)\}$/);
    if (brace) {
      const target = stack[depth - 1];
      if (!target) continue;
      if (!target.scientific) target.scientific = brace[1];
      else if (!target.english) target.english = brace[1];
      continue;
    }
    const id = 'n' + (nextId++);
    const node = { id, name: content, depth };
    nodes.push(node);
    if (depth > 0 && stack[depth - 1]) parentsOf[id] = [stack[depth - 1].id];
    stack[depth] = node;
    stack.length = depth + 1;
  }
  return { nodes, parentsOf };
}

const RANK_LABELS = ['Classe', 'Ordre', 'Famille', 'Espèce', 'Sous-espèce', 'Variante', 'Niveau 6'];
const rankFromDepth = d => RANK_LABELS[d] || ('Niveau ' + d);

// ---------- Palette (couleurs cluster) ----------
const PALETTE = [
  '#e88a4e', '#5dc9b0', '#b07ed8', '#7eb6f0', '#e5c97a', '#d97a9d',
  '#9ec96b', '#69b8e8', '#ed8a8a', '#a8a4dd', '#71d3a4', '#e0a566',
  '#c490d1', '#84ccc9', '#dba77a', '#8fbcdb',
];

// ---------- Configurations de jeux de données ----------
const datasets = {};
let thesaurusConfig = null;

function familyLabelFromName(name) {
  const cleaned = name.replace(/\([^)]*\)/g, ' ').replace(/\s+-\s+/g, ' ');
  const tokens = cleaned.split(/\s+/).filter(Boolean);
  const familyTokens = tokens.filter(token => {
    const letters = token.replace(/[^A-Za-zÀ-ÖØ-öø-ÿ0-9]/g, '');
    return letters.length > 1 && letters === letters.toLocaleUpperCase('fr-FR');
  });
  return familyTokens.at(-1) || tokens.at(-1) || name;
}

// Charger la configuration des thésaurus
async function initializeDatasets() {
  try {
    const config = await fetch('/api/datasets').then(r => r.json());
    thesaurusConfig = config;

    datasets.family = {
      id: 'family',
      title: "Visages",
      subtitle: "Personnes et familles",
      selfLabel: "Nœud sélectionné",
      groupLabels: { parents: 'Parents', children: 'Enfants', siblings: 'Fratrie', spouse: 'Conjoint·e' },
      expandAllEnabled: true,
      genSpacing: 170,
      nodeRadius: 22,
      labelGenerations: false,
      formatTooltip: n => {
        const details = [];
        if (n.nickname) details.push(`Surnom : ${n.nickname}`);
        if (n.birth) details.push(`Naissance : ${n.birth}`);
        if (n.death) details.push(`Décès : ${n.death}`);
        if (n.job) details.push(n.job);
        return (details.length ? `<div class="dates">${details.join(' · ')}</div>` : '') +
               (n.bio ? `<div class="bio">${n.bio}</div>` : '');
      },
      load: async () => {
        const data = await fetch('/api/family').then(r => r.json());
        return {
          nodes: data.people, parentsOf: data.parentsOf, couples: data.couples,
          defaultSelf: data.defaultSelf, photoCounts: data.photoCounts || {},
        };
      },
      computeClusters(nodes, parentsOf, couples, spouseOf) {
        const alias = {};
        for (const [a, b] of couples) {
          if (!(parentsOf[a] || []).length && !(parentsOf[b] || []).length) {
            alias[a] = a; alias[b] = a;
          }
        }
        const memo = {};
        const cof = (id, visiting = new Set()) => {
          if (memo[id]) return memo[id];
          if (visiting.has(id)) return id;
          visiting.add(id);
          let r;
          if (alias[id]) r = alias[id];
          else if ((parentsOf[id] || []).length) r = cof(parentsOf[id][0], visiting);
          else {
            let found = null;
            for (const s of (spouseOf[id] || [])) {
              if ((parentsOf[s] || []).length) { found = cof(s, visiting); break; }
            }
            r = found || id;
          }
          visiting.delete(id);
          memo[id] = r;
          return r;
        };
        return Object.fromEntries(nodes.map(n => [n.id, cof(n.id)]));
      },
      clusterLabel(cid, byId) {
        return familyLabelFromName(byId[cid].name.trim());
      },
    };

    // Créer dynamiquement un dataset pour chaque thésaurus
    for (const thesDesc of config.thesaurusList) {
      datasets[thesDesc.id] = createThesaurusDataset(thesDesc, config.thesaurusConfig);
    }
  } catch (err) {
    console.error('Erreur lors du chargement de la configuration des thésaurus:', err);
  }
}

// Fonction générique pour créer un dataset de thésaurus
function createThesaurusDataset(thesDesc, globalConfig) {
  return {
    id: thesDesc.id,
    title: thesDesc.name,
    subtitle: thesDesc.description,
    selfLabel: "Nœud sélectionné",
    groupLabels: { parents: 'Taxon parent', children: 'Sous-taxons', siblings: 'Même rang', spouse: null },
    expandAllEnabled: globalConfig.expandAllEnabled,
    genSpacing: globalConfig.genSpacing,
    nodeRadius: globalConfig.nodeRadius,
    labelGenerations: globalConfig.labelGenerations,
    clusterTitle: thesDesc.id === 'fauna' ? 'Ordres' : 'Groupes',
    formatTooltip: n => {
      let html = '';
      if (n.scientific) html += `<div class="dates"><i>${n.scientific}</i></div>`;
      if (n.english) html += `<div class="bio">${n.english}</div>`;
      if (n.rank) html += `<div class="bio" style="margin-top:4px;opacity:0.7">${n.rank}</div>`;
      return html;
    },
    load: async () => {
      const data = await fetch(`/api/thesaurus/${thesDesc.id}`).then(r => r.json());
      return {
        nodes: data.nodes || [],
        parentsOf: data.parentsOf || {},
        couples: data.couples || [],
        defaultSelf: data.defaultSelf,
        photoCounts: data.photoCounts || {},
        classes: data.classes || [],
      };
    },
    computeClusters(nodes, parentsOf) {
      const byId = Object.fromEntries(nodes.map(n => [n.id, n]));
      const cluster = {};
      for (const n of nodes) {
        if (n.groupId) {
          cluster[n.id] = n.groupId;
          continue;
        }
        let cur = n.id;
        while (byId[cur]?.depth > 1) {
          const next = (parentsOf[cur] || [])[0];
          if (!next) break;
          cur = next;
        }
        cluster[n.id] = cur;
      }
      return cluster;
    },
    clusterLabel(cid, byId) {
      return byId[cid]?.groupLabel || byId[cid]?.name || '';
    },
  };
}

let svg, gRoot, linkLayer, nodeLayer, simulation, zoom;
let nodeSel, linkSel;
let width = 0, height = 0;

const GRAPH_LABEL_MODES = new Set(['auto', 'all', 'hover', 'none']);
let graphLabelMode = (() => {
  try {
    const saved = localStorage.getItem('select.graphLabelMode');
    return GRAPH_LABEL_MODES.has(saved) ? saved : 'auto';
  } catch (_) {
    return 'auto';
  }
})();
let hoveredNodeId = null;
const HOVER_MAX_DEPTH = 4;
const nodePhotoCache = new Map();
let hoverPhotoToken = 0;
let labelLayoutFrame = 0;
let lastLabelLayout = 0;

function resize() {
  const graphEl = document.getElementById('graph-container');
  if (!graphEl) return;
  const rect = graphEl.getBoundingClientRect();
  width = rect.width; height = rect.height;
  if (svg) svg.attr('viewBox', `0 0 ${width} ${height}`);
}
window.addEventListener('resize', () => { resize(); if (simulation) simulation.alpha(0.3).restart(); });

// =========================================================
//  CHARGEMENT D'UN JEU DE DONNÉES
// =========================================================
async function loadDataset(key) {
  const loadSeq = ++datasetLoadSeq;
  const cfg = datasets[key];
  if (!cfg) return;
  const loaded = await cfg.load();
  if (loadSeq !== datasetLoadSeq) return;

  const spouseOf = {};
  for (const [a, b] of (loaded.couples || [])) {
    (spouseOf[a] ||= []).push(b);
    (spouseOf[b] ||= []).push(a);
  }
  const childrenOf = {};
  for (const [c, ps] of Object.entries(loaded.parentsOf)) {
    for (const p of ps) (childrenOf[p] ||= []).push(c);
  }
  const nodesById = Object.fromEntries(loaded.nodes.map(n => [n.id, n]));
  const generation = computeGeneration(loaded.nodes, loaded.parentsOf, spouseOf);

  const cluster = cfg.computeClusters(loaded.nodes, loaded.parentsOf, loaded.couples || [], spouseOf);
  const clusterIds = [...new Set(loaded.nodes.map(n => cluster[n.id]))];
  const clusterColor = {};
  clusterIds.forEach((cid, i) => { clusterColor[cid] = PALETTE[i % PALETTE.length]; });
  // Les classes (ou racines) restent neutres ; les couleurs marquent les ordres.
  for (const n of loaded.nodes) {
    if ((n.isClass || n.depth === 0) && cluster[n.id] === n.id) {
      clusterColor[n.id] = '#8b91b3';
    }
  }
  const clusterLabel = {};
  clusterIds.forEach(cid => { clusterLabel[cid] = cfg.clusterLabel(cid, nodesById); });

  const photoCounts = loaded.photoCounts || {};
  const defaultSelf = loaded.defaultSelf && nodesById[loaded.defaultSelf]
    ? loaded.defaultSelf
    : loaded.nodes[0]?.id;
  if (!defaultSelf) return;

  const classes = loaded.classes || [];
  const defaultNode = nodesById[defaultSelf];
  const activeClassId = cfg.id === 'fauna'
    ? (defaultNode?.classId || classes[0]?.id || null)
    : null;

  state = {
    cfg, nodes: loaded.nodes, nodesById,
    parentsOf: loaded.parentsOf, childrenOf, spouseOf,
    couples: loaded.couples || [],
    generation, cluster, clusterColor, clusterLabel, clusterIds,
    photoCounts, classes,
    activeClassId,
    selectedOrders: new Set(),
    selfId: defaultSelf,
    visible: new Set([defaultSelf]),
    nodeData: {},
  };
  for (const n of loaded.nodes) {
    state.nodeData[n.id] = {
      id: n.id, gen: generation[n.id],
      x: 0, y: (generation[n.id] ?? 0) * cfg.genSpacing,
      vx: 0, vy: 0,
    };
  }
  currentAppMode = cfg.id === 'fauna' ? 'faune' : 'face';
  if (cfg.id === 'fauna') {
    const allowed = faunaAllowedNodeIds();
    state.visible = allowed?.size ? allowed : new Set([defaultSelf]);
  } else {
    expandGroup(state.selfId, 'all');
  }

  document.getElementById('menu').style.display = 'none';
  document.getElementById('tooltip').style.opacity = 0;
  // ----- UI -----
  document.getElementById('app-title').textContent = 'Sélection';
  document.getElementById('app-sub').textContent = `${cfg.title} · ${cfg.subtitle}`;
  document.getElementById('self-label').textContent = cfg.selfLabel;
  document.getElementById('cluster-legend-title').textContent =
    cfg.clusterTitle || (cfg.id === 'family' ? 'Familles' : 'Groupes');

  const groupField = document.getElementById('group-field');
  const groupSelect = document.getElementById('group-select');
  groupSelect.replaceChildren();
  for (const item of classes) {
    const opt = document.createElement('option');
    opt.value = item.id;
    opt.textContent = item.name;
    groupSelect.appendChild(opt);
  }
  groupField.hidden = cfg.id !== 'fauna' || classes.length === 0;
  groupSelect.value = activeClassId || '';

  refreshFaunaSidebar();
  rebuildSelfSelect();
  renderClusterLegend();

  resetVisualization();
  update();
  updateModeTabs();
  setTimeout(centerOnSelf, 400);
}

function isFauna() {
  return Boolean(state?.cfg && state.cfg.id === 'fauna');
}

function faunaClassId() {
  if (!state) return null;
  const groupSelect = document.getElementById('group-select');
  return state.activeClassId || groupSelect?.value || state.classes[0]?.id || null;
}

function faunaOrdersForClass(classId = faunaClassId()) {
  if (!isFauna() || !classId) return [];
  const orders = new Map();
  for (const node of state.nodes) {
    if (node.classId !== classId || !node.groupId || node.groupId === classId) continue;
    if (orders.has(node.groupId)) continue;
    const orderNode = state.nodesById[node.groupId];
    if (!orderNode) continue;
    orders.set(node.groupId, {
      id: node.groupId,
      name: orderNode.name,
      scientific: orderNode.scientific || '',
      photoCount: state.photoCounts[node.groupId] || 0,
    });
  }
  return [...orders.values()].sort((a, b) => a.name.localeCompare(b.name, 'fr'));
}

function faunaAllowedNodeIds() {
  if (!isFauna()) return null;
  const classId = faunaClassId();
  const allowed = new Set();
  if (!classId) return allowed;
  const selected = state.selectedOrders || new Set();
  for (const node of state.nodes) {
    if (node.classId !== classId) continue;
    if (node.isClass || selected.size === 0 || selected.has(node.groupId)) {
      allowed.add(node.id);
    }
  }
  return allowed;
}

function rebuildSelfSelect() {
  if (!state) return;
  const select = document.getElementById('self-select');
  if (!select) return;
  const allowed = isFauna() ? faunaAllowedNodeIds() : null;
  const nodes = state.nodes.filter(node => !allowed || allowed.has(node.id));
  const sorted = nodes.slice().sort((a, b) => displayNodeName(a).localeCompare(displayNodeName(b), 'fr'));
  select.replaceChildren();

  if (isFauna()) {
    const classId = faunaClassId();
    const classNode = state.nodesById[classId];
    if (classNode && (!allowed || allowed.has(classNode.id))) {
      const opt = document.createElement('option');
      opt.value = classNode.id;
      opt.textContent = displayNodeName(classNode);
      select.appendChild(opt);
    }
    const groups = new Map();
    for (const node of sorted) {
      if (node.isClass) continue;
      const orderId = node.groupId || classId;
      if (!groups.has(orderId)) groups.set(orderId, []);
      groups.get(orderId).push(node);
    }
    for (const [orderId, list] of groups) {
      const optgroup = document.createElement('optgroup');
      optgroup.label = state.nodesById[orderId]?.name || 'Autres';
      for (const node of list) {
        const opt = document.createElement('option');
        opt.value = node.id;
        opt.textContent = displayNodeName(node);
        optgroup.appendChild(opt);
      }
      if (optgroup.children.length) select.appendChild(optgroup);
    }
  } else {
    for (const node of sorted) {
      const opt = document.createElement('option');
      opt.value = node.id;
      opt.textContent = displayNodeName(node);
      select.appendChild(opt);
    }
  }

  const hasCurrent = [...select.options].some(option => option.value === state.selfId);
  if (hasCurrent) {
    select.value = state.selfId;
  } else if (select.options.length) {
    state.selfId = select.options[0].value;
    select.value = state.selfId;
  }
}

function renderClusterLegend() {
  if (!state) return;
  const legendEl = document.getElementById('cluster-legend');
  if (!legendEl) return;
  legendEl.innerHTML = '';

  let ids;
  if (isFauna()) {
    const classId = faunaClassId();
    const selected = state.selectedOrders || new Set();
    ids = [...new Set(
      state.nodes
        .filter(node => node.classId === classId && node.groupId && node.groupId !== classId)
        .filter(node => selected.size === 0 || selected.has(node.groupId))
        .map(node => node.groupId)
    )];
    ids.sort((a, b) => (state.nodesById[a]?.name || '').localeCompare(state.nodesById[b]?.name || '', 'fr'));
  } else {
    ids = [...state.clusterIds];
  }

  if (!ids.length) {
    const empty = document.createElement('div');
    empty.style.opacity = '0.7';
    empty.textContent = isFauna() ? 'Aucun ordre.' : 'Aucun groupe.';
    legendEl.appendChild(empty);
    return;
  }

  const legendIds = ids.slice(0, 24);
  legendIds.forEach(cid => {
    const row = document.createElement('div');
    const label = isFauna()
      ? (state.nodesById[cid]?.name || state.clusterLabel[cid])
      : state.clusterLabel[cid];
    row.innerHTML = `<span class="dot" style="background:${state.clusterColor[cid]}"></span>${escapeHtml(label)}`;
    legendEl.appendChild(row);
  });
  if (ids.length > legendIds.length) {
    const more = document.createElement('div');
    more.style.opacity = '0.7';
    more.textContent = `… +${ids.length - legendIds.length} autres`;
    legendEl.appendChild(more);
  }
}

function refreshFaunaSidebar() {
  const orderField = document.getElementById('order-field');
  const orderList = document.getElementById('order-list');
  const summary = document.getElementById('order-summary');
  if (!orderField || !orderList) return;

  if (!isFauna()) {
    orderField.hidden = true;
    orderList.innerHTML = '';
    if (summary) summary.textContent = '';
    return;
  }

  const classId = faunaClassId();
  const orders = faunaOrdersForClass(classId);
  const valid = new Set(orders.map(order => order.id));
  for (const orderId of [...state.selectedOrders]) {
    if (!valid.has(orderId)) state.selectedOrders.delete(orderId);
  }

  orderField.hidden = false;
  if (summary) {
    summary.textContent = state.selectedOrders.size
      ? `(${state.selectedOrders.size} coché${state.selectedOrders.size > 1 ? 's' : ''})`
      : '(tous)';
  }
  if (!orders.length) {
    orderList.innerHTML = '<div class="order-empty">Aucun ordre avec photos.</div>';
    return;
  }
  orderList.innerHTML = orders.map(order => `
    <label class="check-row order-check">
      <input type="checkbox" data-order-id="${escapeHtml(order.id)}"${state.selectedOrders.has(order.id) ? ' checked' : ''}>
      <span class="order-name">${escapeHtml(order.name)}</span>
      <span class="order-count">${order.photoCount}</span>
    </label>
  `).join('');
}

function applyFaunaOrderFilter() {
  if (!isFauna()) return;
  const previousSelf = state.selfId;
  const allowed = faunaAllowedNodeIds();
  if (!allowed || !allowed.size) return;

  if (!allowed.has(state.selfId)) {
    const classId = faunaClassId();
    state.selfId = classId && allowed.has(classId) ? classId : [...allowed][0];
  }
  state.visible = new Set(allowed);
  refreshFaunaSidebar();
  rebuildSelfSelect();
  renderClusterLegend();
  update();
  if (state.selfId !== previousSelf) centerOnSelf();
}

function computeGeneration(nodes, parentsOf, spouseOf) {
  const gen = {};
  for (const p of nodes) {
    if ((parentsOf[p.id] || []).length) continue;
    const anySpouseHasParents = (spouseOf[p.id] || []).some(s => (parentsOf[s] || []).length);
    if (!anySpouseHasParents) gen[p.id] = 0;
  }
  let changed = true, safety = 0;
  while (changed && safety++ < 200) {
    changed = false;
    for (const p of nodes) {
      if (gen[p.id] !== undefined) continue;
      const ps = parentsOf[p.id] || [];
      if (ps.length) {
        const gs = ps.map(pid => gen[pid]).filter(x => x !== undefined);
        if (gs.length === ps.length) { gen[p.id] = Math.max(...gs) + 1; changed = true; continue; }
      } else {
        const sg = (spouseOf[p.id] || []).map(s => gen[s]).filter(x => x !== undefined);
        if (sg.length) { gen[p.id] = sg[0]; changed = true; }
      }
    }
  }
  for (const p of nodes) if (gen[p.id] === undefined) gen[p.id] = 0;
  return gen;
}

// =========================================================
//  D3 / SIMULATION
// =========================================================
function resetVisualization() {
  if (!svg) svg = d3.select('#svg');
  svg.selectAll('*').remove();
  resize();

  gRoot = svg.append('g');
  linkLayer = gRoot.append('g').attr('class', 'links');
  nodeLayer = gRoot.append('g').attr('class', 'nodes');

  zoom = d3.zoom().scaleExtent([0.1, 3])
    .on('zoom', e => {
      gRoot.attr('transform', e.transform);
      scheduleLabelLayout();
    });
  svg.call(zoom);

  if (simulation) simulation.stop();

  const cfg = state.cfg;
  simulation = d3.forceSimulation()
    .force('link', d3.forceLink().id(d => d.id)
      .distance(d => d.type === 'spouse' ? 70 : Math.max(cfg.genSpacing - 60, 50))
      .strength(d => d.type === 'spouse' ? 0.9 : 0.05))
    .force('charge', d3.forceManyBody().strength(-700))
    .force('collide', d3.forceCollide().radius(cfg.nodeRadius + 18))
    .force('y', d3.forceY(d => (d.gen ?? 0) * cfg.genSpacing).strength(1.4))
    .force('childrenCenter', alpha => {
      for (const node of simulation.nodes()) {
        const ps = (state.parentsOf[node.id] || []).filter(p => state.visible.has(p));
        if (!ps.length) continue;
        let sx = 0, c = 0;
        for (const pid of ps) {
          const pn = state.nodeData[pid];
          if (pn && pn.x !== undefined) { sx += pn.x; c++; }
        }
        if (!c) continue;
        node.vx += (sx / c - node.x) * 0.25 * alpha;
      }
    })
    .force('x', d3.forceX(0).strength(0.02))
    .on('tick', ticked);

  nodeSel = nodeLayer.selectAll('.node');
  linkSel = linkLayer.selectAll('.link');
}

function buildGraph() {
  const visibleFiltered = new Set(state.visible);
  if (isFauna()) {
    const allowed = faunaAllowedNodeIds();
    if (allowed) {
      for (const id of [...visibleFiltered]) {
        if (!allowed.has(id)) visibleFiltered.delete(id);
      }
    }
  }
  const nodes = [...visibleFiltered].map(id => state.nodeData[id]);
  const links = [];
  const seen = new Set();
  const add = (a, b, type) => {
    if (!visibleFiltered.has(a) || !visibleFiltered.has(b)) return;
    const key = a < b ? `${type}:${a}:${b}` : `${type}:${b}:${a}`;
    if (seen.has(key)) return;
    seen.add(key);
    links.push({ source: a, target: b, type });
  };
  for (const [c, ps] of Object.entries(state.parentsOf)) {
    for (const p of ps) add(p, c, 'parent');
  }
  for (const [a, b] of state.couples) add(a, b, 'spouse');
  return { nodes, links };
}

function update() {
  const { nodes, links } = buildGraph();
  const cfg = state.cfg;

  linkSel = linkLayer.selectAll('.link').data(links,
    d => `${d.type}:${d.source.id || d.source}:${d.target.id || d.target}`);
  linkSel.exit().remove();
  linkSel = linkSel.enter().append('line')
    .attr('class', d => 'link' + (d.type === 'spouse' ? ' spouse' : ''))
    .merge(linkSel);

  nodeSel = nodeLayer.selectAll('.node').data(nodes, d => d.id);
  nodeSel.exit().remove();
  const enter = nodeSel.enter().append('g')
    .attr('class', 'node')
    .call(d3.drag()
      .on('start', (e, d) => { if (!e.active) simulation.alphaTarget(0.3).restart(); d.fx = d.x; d.fy = d.y; })
      .on('drag',  (e, d) => { d.fx = e.x; d.fy = e.y; })
      .on('end',   (e, d) => { if (!e.active) simulation.alphaTarget(0); d.fx = null; d.fy = null; }));
  enter.append('circle')
    .attr('r', cfg.nodeRadius)
    .on('mouseover', onHover).on('mousemove', onHoverMove).on('mouseout', onHoverOut)
    .on('click', onNodeClick)
    .on('dblclick', onNodeDoubleClick);
  enter.append('text')
    .attr('class', 'photo-count')
    .attr('dy', '0.3em')
    .text(d => state.photoCounts[d.id] || 0);
  enter.append('text')
    .attr('class', 'node-label')
    .attr('dy', cfg.nodeRadius + 14)
    .text(d => {
      d.labelText = labelFor(state.nodesById[d.id]);
      return d.labelText;
    });
  nodeSel = enter.merge(nodeSel);

  nodeSel.classed('self', d => d.id === state.selfId);
  nodeSel.select('circle')
    .attr('fill', d => state.clusterColor[state.cluster[d.id]])
    .attr('stroke', d => d.id === state.selfId ? 'var(--self)' : '#1a1d2e')
    .attr('stroke-width', d => d.id === state.selfId ? 4 : 2.5);

  // Mesurer une fois le libellé à sa taille réelle : le calcul de
  // non-chevauchement n'a plus qu'à comparer des rectangles.
  nodeSel.select('text.node-label').attr('display', null);
  nodeSel.each(function (d) {
    const label = this.querySelector('text.node-label');
    try {
      d.labelWidth = label ? label.getComputedTextLength() : 0;
    } catch (_) {
      d.labelWidth = 0;
    }
  });

  if (hoveredNodeId && !nodes.some(node => node.id === hoveredNodeId)) {
    hoveredNodeId = null;
  }
  applyHoverHighlight();
  syncLabelModeControl();
  scheduleLabelLayout();

  simulation.nodes(nodes);
  simulation.force('link').links(links);
  simulation.alpha(0.6).restart();
  if (currentMode === 'graph') renderQuery();
}

function primaryVernacular(name) {
  const value = String(name || '').trim();
  if (!value) return '';
  return value.split(',')[0].trim() || value;
}

function taxonDisplayName(taxon) {
  const name = primaryVernacular(taxon?.name);
  const scientific = taxon?.scientific || '';
  if (name && scientific && name !== scientific) return `${name} (${scientific})`;
  return name || scientific;
}

function mostSpecificTaxon(taxa) {
  return (taxa || []).reduce((best, taxon) => {
    if (!taxon || !(taxon.name || taxon.scientific)) return best;
    const depth = String(taxon.path || '').split('/').filter(Boolean).length;
    if (!best || depth > best.depth) return { taxon, depth };
    return best;
  }, null)?.taxon || null;
}

function photoDisplayTitle(photo) {
  const taxon = mostSpecificTaxon(photo?.taxa);
  if (taxon) return primaryVernacular(taxon.name) || taxon.scientific;
  const people = (photo?.people || []).filter(Boolean);
  if (people.length) return people.slice(0, 2).join(', ') + (people.length > 2 ? '…' : '');
  const keyword = (photo?.keywords || []).find(Boolean);
  if (keyword) return keyword;
  const city = photo?.location?.city;
  if (city) return city;
  return photo?.filename || `Photo ${photo?.id ?? ''}`.trim();
}

function photoDisplayTooltip(photo, title) {
  const taxon = mostSpecificTaxon(photo?.taxa);
  const scientific = taxon && taxon.name && taxon.scientific && taxon.name !== taxon.scientific
    ? taxon.scientific
    : '';
  return [title, scientific, photo?.filename].filter((value, index, values) => value && values.indexOf(value) === index).join(' · ');
}

function displayNodeName(node) {
  if (state?.cfg?.id === 'fauna') return taxonDisplayName(node);
  return node.name;
}

function labelFor(node) {
  if (!node) return '';
  const cfg = state?.cfg;
  // Libellés courts dans le graphe : le nom scientifique complet reste dans
  // l'infobulle et le menu. Cela réduit fortement les chevauchements en faune.
  if (cfg === datasets.family) return (node.name || '').split(' ')[0];
  if (cfg?.id === 'fauna') return node.name || node.scientific || '';
  return displayNodeName(node);
}

function ticked() {
  linkSel
    .attr('x1', d => d.source.x).attr('y1', d => d.source.y)
    .attr('x2', d => d.target.x).attr('y2', d => d.target.y);
  nodeSel.attr('transform', d => `translate(${d.x},${d.y})`);
  scheduleLabelLayout();
}

// =========================================================
//  LISIBILITÉ DU GRAPHE
// =========================================================
function syncLabelModeControl() {
  const select = document.getElementById('label-mode');
  if (select && select.value !== graphLabelMode) select.value = graphLabelMode;
}

function setGraphLabelMode(mode) {
  if (!GRAPH_LABEL_MODES.has(mode)) return;
  graphLabelMode = mode;
  try {
    localStorage.setItem('select.graphLabelMode', mode);
  } catch (_) {
    // Mode privé ou stockage indisponible : le choix reste valable pour la session.
  }
  scheduleLabelLayout();
}

function scheduleLabelLayout() {
  if (labelLayoutFrame) return;
  labelLayoutFrame = requestAnimationFrame(timestamp => {
    labelLayoutFrame = 0;
    // Le calcul est volontairement limité à ~25 images/s : pendant que la
    // simulation bouge, une étiquette peut suivre son nœud avec un léger retard
    // sans refaire tout le calcul de collisions à chaque tick.
    if (timestamp - lastLabelLayout < 40) {
      scheduleLabelLayout();
      return;
    }
    lastLabelLayout = timestamp;
    layoutLabels();
  });
}

function labelPriority(d, near) {
  const node = state.nodesById[d.id] || {};
  let priority = 0;
  if (d.id === state.selfId) priority += 1_000_000_000;
  if (d.id === hoveredNodeId) priority += 500_000_000;
  if (near.has(d.id)) priority += 100_000_000;
  if (node.isClass || node.depth === 0) priority += 10_000_000;
  if (node.depth === 1) priority += 5_000_000;
  const count = Number(state.photoCounts[d.id]) || 0;
  priority += Math.min(count, 999) * 1000;
  priority += Math.max(0, 100 - (node.depth ?? 100));
  return priority;
}

function labelRect(d) {
  const text = d.labelText ?? labelFor(state.nodesById[d.id]) ?? '';
  const estimated = Math.max(16, text.length * 7);
  const width = Math.max(16, d.labelWidth || estimated) + 8;
  const y = (d.y || 0) + state.cfg.nodeRadius + 14;
  return {
    x1: (d.x || 0) - width / 2,
    x2: (d.x || 0) + width / 2,
    y1: y - 12,
    y2: y + 4,
  };
}

function rectsOverlap(a, b) {
  return a.x1 < b.x2 && a.x2 > b.x1 && a.y1 < b.y2 && a.y2 > b.y1;
}

const LABEL_GRID_CELL = 80;

function gridCells(rect) {
  return {
    x0: Math.floor(rect.x1 / LABEL_GRID_CELL),
    x1: Math.floor(rect.x2 / LABEL_GRID_CELL),
    y0: Math.floor(rect.y1 / LABEL_GRID_CELL),
    y1: Math.floor(rect.y2 / LABEL_GRID_CELL),
  };
}

function addToGrid(grid, rect, value = null) {
  const cells = gridCells(rect);
  for (let x = cells.x0; x <= cells.x1; x += 1) {
    for (let y = cells.y0; y <= cells.y1; y += 1) {
      const key = `${x},${y}`;
      const bucket = grid.get(key);
      if (bucket) bucket.push({ rect, value });
      else grid.set(key, [{ rect, value }]);
    }
  }
}

function collidesWithGrid(grid, rect, ignoreValue = null) {
  const cells = gridCells(rect);
  for (let x = cells.x0; x <= cells.x1; x += 1) {
    for (let y = cells.y0; y <= cells.y1; y += 1) {
      const bucket = grid.get(`${x},${y}`);
      if (!bucket) continue;
      for (const item of bucket) {
        if (ignoreValue !== null && item.value === ignoreValue) continue;
        if (rectsOverlap(rect, item.rect)) return true;
      }
    }
  }
  return false;
}

function hoverDepths(id) {
  const depths = new Map();
  if (!id || !state) return depths;
  depths.set(id, 0);
  let frontier = [id];
  for (let depth = 1; depth <= HOVER_MAX_DEPTH; depth += 1) {
    const next = [];
    for (const current of frontier) {
      const neighbors = new Set([
        ...getParents(current),
        ...getChildren(current),
        ...getSpouses(current),
        ...getSiblings(current),
      ]);
      for (const neighbor of neighbors) {
        if (!state.visible.has(neighbor) || depths.has(neighbor)) continue;
        depths.set(neighbor, depth);
        next.push(neighbor);
      }
    }
    if (!next.length) break;
    frontier = next;
  }
  return depths;
}

function applyHoverHighlight() {
  if (!nodeSel || !linkSel || !state) return;
  const active = hoveredNodeId && state.nodesById[hoveredNodeId]
    ? hoveredNodeId
    : null;
  if (!active) {
    nodeSel
      .classed('hovered', false)
      .classed('neighbor', false)
      .classed('faded', false)
      .style('opacity', null);
    linkSel
      .classed('highlight', false)
      .classed('faded', false)
      .style('stroke', null)
      .style('stroke-width', null)
      .style('stroke-opacity', null);
    return;
  }
  const depths = hoverDepths(active);
  nodeSel
    .classed('hovered', d => d.id === active)
    .classed('neighbor', d => d.id !== active && depths.has(d.id))
    .classed('faded', d => !depths.has(d.id))
    .style('opacity', d => depths.has(d.id)
      ? String(Math.max(0.2, 1 - depths.get(d.id) * 0.2))
      : null);
  const endpoint = value => (value && typeof value === 'object' ? value.id : value);
  const edgeDepth = d => {
    const source = endpoint(d.source);
    const target = endpoint(d.target);
    if (!depths.has(source) || !depths.has(target)) return null;
    return Math.max(depths.get(source), depths.get(target));
  };
  linkSel
    .classed('highlight', d => edgeDepth(d) !== null)
    .classed('faded', d => {
      const source = endpoint(d.source);
      const target = endpoint(d.target);
      return !depths.has(source) && !depths.has(target);
    })
    .style('stroke', d => edgeDepth(d) !== null ? '#ff6b6b' : null)
    .style('stroke-width', d => {
      const depth = edgeDepth(d);
      return depth === null ? null : `${Math.max(0.9, 2.4 - depth * 0.25)}px`;
    })
    .style('stroke-opacity', d => {
      const depth = edgeDepth(d);
      if (depth !== null) return String(Math.max(0.18, 1 - depth * 0.18));
      const source = endpoint(d.source);
      const target = endpoint(d.target);
      return depths.has(source) || depths.has(target) ? '0.16' : null;
    });
}

function updateLabelSummary(shown, total) {
  const el = document.getElementById('label-summary');
  if (!el) return;
  let text;
  if (graphLabelMode === 'auto') {
    text = `${shown} / ${total} étiquettes visibles (anti-chevauchement)`;
  } else if (graphLabelMode === 'all') {
    text = `${total} étiquettes affichées`;
  } else if (graphLabelMode === 'hover') {
    text = hoveredNodeId
      ? `${shown} étiquette(s) autour du nœud survolé`
      : 'Survolez un nœud pour afficher son nom';
  } else {
    text = 'Étiquettes masquées';
  }
  if (el.textContent !== text) el.textContent = text;
}

function layoutLabels() {
  if (!nodeSel || !state) return;
  const labelText = nodeSel.select('text.node-label');
  const nodes = nodeSel.data();
  const total = nodes.length;
  if (!total) {
    updateLabelSummary(0, 0);
    return;
  }
  if (graphLabelMode === 'none') {
    labelText.attr('display', 'none');
    updateLabelSummary(0, total);
    return;
  }
  if (graphLabelMode === 'all') {
    labelText.attr('display', null);
    updateLabelSummary(total, total);
    return;
  }

  const near = hoveredNodeId ? hoverDepths(hoveredNodeId) : new Map();
  const nearIds = new Set(near.keys());
  let candidates = nodes.map(d => ({
    d,
    priority: labelPriority(d, nearIds),
    rect: labelRect(d),
  }));
  if (graphLabelMode === 'hover') {
    const visibleAroundHover = hoveredNodeId
      ? new Set([hoveredNodeId, ...nearIds, state.selfId])
      : new Set([state.selfId]);
    candidates = candidates.filter(item => visibleAroundHover.has(item.d.id));
  }
  candidates.sort((a, b) => b.priority - a.priority || a.d.id.localeCompare(b.d.id));

  const radius = state.cfg.nodeRadius + 2;
  const nodeGrid = new Map();
  for (const d of nodes) {
    addToGrid(nodeGrid, {
      x1: (d.x || 0) - radius,
      x2: (d.x || 0) + radius,
      y1: (d.y || 0) - radius,
      y2: (d.y || 0) + radius,
    }, d.id);
  }
  const labelGrid = new Map();
  const visible = new Set();
  for (const item of candidates) {
    const force = item.d.id === state.selfId || item.d.id === hoveredNodeId;
    if (!force) {
      const hitNode = collidesWithGrid(nodeGrid, item.rect, item.d.id);
      if (hitNode || collidesWithGrid(labelGrid, item.rect)) continue;
    }
    visible.add(item.d.id);
    addToGrid(labelGrid, item.rect);
  }
  labelText.each(function (d) {
    const hidden = !visible.has(d.id);
    if (hidden) {
      if (this.getAttribute('display') !== 'none') {
        this.setAttribute('display', 'none');
      }
    } else if (this.hasAttribute('display')) {
      this.removeAttribute('display');
    }
  });
  updateLabelSummary(visible.size, total);
}

function fitGraph() {
  if (!state || !simulation || !width || !height) return;
  const nodes = simulation.nodes().filter(node =>
    state.visible.has(node.id)
    && Number.isFinite(node.x)
    && Number.isFinite(node.y)
  );
  if (!nodes.length) return;
  let minX = Infinity, maxX = -Infinity;
  let minY = Infinity, maxY = -Infinity;
  for (const node of nodes) {
    minX = Math.min(minX, node.x);
    maxX = Math.max(maxX, node.x);
    minY = Math.min(minY, node.y);
    maxY = Math.max(maxY, node.y);
  }
  const pad = Math.max(60, state.cfg.nodeRadius * 4);
  const spanX = Math.max(1, maxX - minX + pad * 2);
  const spanY = Math.max(1, maxY - minY + pad * 2);
  const scale = Math.max(0.1, Math.min(2, Math.min(width / spanX, height / spanY)));
  const cx = (minX + maxX) / 2;
  const cy = (minY + maxY) / 2;
  const transform = d3.zoomIdentity
    .translate(width / 2 - cx * scale, height / 2 - cy * scale)
    .scale(scale);
  svg.transition().duration(450).call(zoom.transform, transform);
}

// =========================================================
//  TOOLTIP
// =========================================================
const tooltip = () => document.getElementById('tooltip');
function onHover(event, d) {
  hoveredNodeId = d.id;
  applyHoverHighlight();
  scheduleLabelLayout();
  const n = state.nodesById[d.id];
  const cid = state.cluster[d.id];
  const tip = tooltip();
  tip.innerHTML = `
    <div class="tooltip-top">
      <div id="tooltip-photo" class="tooltip-photo"><span class="tooltip-photo-empty">…</span></div>
      <div class="tooltip-main">
        <div class="name">${escapeHtml(n.name)}</div>
        ${state.cfg.formatTooltip(n)}
      </div>
    </div>
    <div class="branch" style="background:${state.clusterColor[cid]}">${escapeHtml(state.clusterLabel[cid])}</div>
  `;
  tip.style.opacity = 1;
  const token = ++hoverPhotoToken;
  loadTooltipPhoto(d.id, token);
  onHoverMove(event);
}

function renderTooltipPhoto(photo) {
  const holder = document.getElementById('tooltip-photo');
  if (!holder) return;
  holder.replaceChildren();
  if (photo?.thumbUrl) {
    const image = document.createElement('img');
    image.src = photo.thumbUrl;
    image.alt = photo.filename || '';
    image.loading = 'eager';
    image.addEventListener('error', () => {
      if (!image.isConnected) return;
      holder.replaceChildren();
      const empty = document.createElement('span');
      empty.className = 'tooltip-photo-empty';
      empty.textContent = '—';
      holder.appendChild(empty);
    });
    holder.appendChild(image);
    holder.title = photo.filename || '';
  } else {
    const empty = document.createElement('span');
    empty.className = 'tooltip-photo-empty';
    empty.textContent = '—';
    holder.appendChild(empty);
  }
}

function loadTooltipPhoto(nodeId, token) {
  if (!state) return;
  const cacheKey = `${state.cfg.id}:${nodeId}`;
  if (nodePhotoCache.has(cacheKey)) {
    renderTooltipPhoto(nodePhotoCache.get(cacheKey));
    return;
  }
  if (!Number(state.photoCounts[nodeId] || 0)) {
    nodePhotoCache.set(cacheKey, null);
    renderTooltipPhoto(null);
    return;
  }
  const url = `/api/node-photo?dataset=${encodeURIComponent(state.cfg.id)}&node=${encodeURIComponent(nodeId)}`;
  fetch(url)
    .then(response => response.ok ? response.json() : Promise.reject(new Error('Photo indisponible')))
    .then(data => {
      const photo = data.photo || null;
      nodePhotoCache.set(cacheKey, photo);
      if (token === hoverPhotoToken && hoveredNodeId === nodeId) renderTooltipPhoto(photo);
    })
    .catch(() => {
      nodePhotoCache.set(cacheKey, null);
      if (token === hoverPhotoToken && hoveredNodeId === nodeId) renderTooltipPhoto(null);
    });
}
function onHoverMove(event) {
  const rect = document.getElementById('graph-container').getBoundingClientRect();
  tooltip().style.left = (event.clientX - rect.left + 14) + 'px';
  tooltip().style.top  = (event.clientY - rect.top + 14) + 'px';
}
function onHoverOut() {
  tooltip().style.opacity = 0;
  hoverPhotoToken += 1;
  hoveredNodeId = null;
  applyHoverHighlight();
  scheduleLabelLayout();
}

// =========================================================
//  MENU CONTEXTUEL
// =========================================================
const menuEl = () => document.getElementById('menu');

function allGraphNodeIds() {
  if (!state) return new Set();
  const allowed = isFauna() ? faunaAllowedNodeIds() : null;
  return new Set(
    state.nodes
      .map(node => node.id)
      .filter(id => !allowed || allowed.has(id))
  );
}

function showAllNodes() {
  if (!state) return;
  state.visible = allGraphNodeIds();
  state.visible.add(state.selfId);
  update();
}

function selectNode(id, { isolate = false } = {}) {
  if (!state || !state.nodesById[id]) return;
  state.selfId = id;
  if (isolate) state.visible = new Set([id]);
  else state.visible.add(id);
  const select = document.getElementById('self-select');
  if (select && [...select.options].some(option => option.value === id)) {
    select.value = id;
  }
  const node = state.nodesById[id];
  if (node?.classId) {
    const groupSelect = document.getElementById('group-select');
    if (groupSelect && [...groupSelect.options].some(option => option.value === node.classId)) {
      groupSelect.value = node.classId;
    }
  }
  update();
}

function positionMenu(event) {
  const menu = menuEl();
  let x = event.clientX + 12;
  let y = event.clientY + 12;
  menu.style.position = 'fixed';
  menu.style.display = 'block';
  menu.style.left = x + 'px';
  menu.style.top = y + 'px';
  requestAnimationFrame(() => {
    const rect = menu.getBoundingClientRect();
    const menuWidth = rect.width || 240;
    const menuHeight = rect.height || 260;
    if (x + menuWidth > window.innerWidth) x = Math.max(10, window.innerWidth - menuWidth - 10);
    if (y + menuHeight > window.innerHeight) y = Math.max(10, window.innerHeight - menuHeight - 10);
    menu.style.left = x + 'px';
    menu.style.top = y + 'px';
  });
}

function showBackgroundMenu(event) {
  const menu = menuEl();
  const allCount = allGraphNodeIds().size;
  const visibleCount = state?.visible?.size || 0;
  menu.innerHTML = `
    <div class="menu-title">Graphe</div>
    <button data-action="all">
      <span><span class="icon">◎</span>Afficher tous les nœuds</span>
      <span class="badge">${allCount}</span>
    </button>
    <button data-action="only-self">
      <span><span class="icon">◉</span>Garder uniquement le nœud sélectionné</span>
      <span class="badge">${visibleCount > 1 ? 'isoler' : ''}</span>
    </button>
    <hr/>
    <button data-action="fit"><span><span class="icon">⤢</span>Ajuster la vue</span></button>
    <button data-action="center"><span><span class="icon">⌖</span>Recentrer sur le nœud sélectionné</span></button>
  `;
  menu.querySelectorAll('button[data-action]').forEach(button => {
    button.addEventListener('click', event => {
      event.stopPropagation();
      const action = button.dataset.action;
      if (action === 'all') showAllNodes();
      else if (action === 'only-self') {
        if (state?.selfId) selectNode(state.selfId, { isolate: true });
      } else if (action === 'fit') {
        fitGraph();
      } else if (action === 'center') {
        centerOnSelf();
      }
      hideMenu();
    });
  });
  positionMenu(event);
}

function onNodeClick(event, d) {
  event.stopPropagation();
  selectNode(d.id);
  showMenuFor(d, event);
}

function onNodeDoubleClick(event, d) {
  event.preventDefault();
  event.stopPropagation();
  hideMenu();
  keepDescendants(d.id);
}
document.addEventListener('click', hideMenu);

function showMenuFor(d, event) {
  const id = d.id;
  const labels = state.cfg.groupLabels;
  const groups = {
    parents:  getParents(id),
    children: getChildren(id),
    siblings: getSiblings(id),
    spouse:   getSpouses(id),
  };
  const expanded = {};
  for (const k of Object.keys(groups)) {
    expanded[k] = groups[k].length > 0 && groups[k].every(x => state.visible.has(x));
  }
  const allList = [...groups.parents, ...groups.children, ...groups.siblings, ...groups.spouse];
  const allTotal = allList.length;
  const allExp = allTotal > 0 && allList.every(x => state.visible.has(x));

  const btn = (group, label, exp, total) => {
    if (label === null) return '';
    if (total === 0) return `<button disabled style="opacity:0.4"><span><span class="icon">·</span>${label}</span><span class="badge">aucun</span></button>`;
    return `<button data-group="${group}" class="${exp?'expanded':''}">
      <span><span class="icon">${exp?'⊖':'⊕'}</span>${label}</span>
      <span class="badge">${exp?'replier':'déplier'} (${total})</span>
    </button>`;
  };

  const menu = menuEl();
  menu.innerHTML = `
    <div class="menu-title">${escapeHtml(displayNodeName(state.nodesById[id]))}</div>
    ${btn('parents',  labels.parents,  expanded.parents,  groups.parents.length)}
    ${btn('children', labels.children, expanded.children, groups.children.length)}
    ${btn('siblings', labels.siblings, expanded.siblings, groups.siblings.length)}
    ${btn('spouse',   labels.spouse,   expanded.spouse,   groups.spouse.length)}
    <hr/>
    ${btn('all', 'Tout', allExp, allTotal)}
    <hr/>
    <button data-group="show-all"><span><span class="icon">◎</span>Afficher tous les nœuds du graphe</span></button>
    <button data-group="only"><span><span class="icon">◉</span>Garder uniquement ce nœud</span></button>
    <button class="set-self" data-group="self"><span><span class="icon">★</span>Définir comme ${state.cfg.selfLabel.toLowerCase()}</span></button>
  `;
  menu.querySelectorAll('button[data-group]').forEach(b => {
    b.addEventListener('click', e => {
      e.stopPropagation();
      const group = b.dataset.group;
      if (group === 'self') {
        selectNode(id, { isolate: false });
        setTimeout(centerOnSelf, 180);
      } else if (group === 'show-all') {
        showAllNodes();
      } else if (group === 'only') {
        selectNode(id, { isolate: true });
      } else {
        toggleGroup(id, group);
      }
      hideMenu();
    });
  });
  positionMenu(event);
}
function hideMenu() { menuEl().style.display = 'none'; }

let backgroundPointerStart = null;
const graphContainerEl = document.getElementById('graph-container');
graphContainerEl?.addEventListener('mousedown', event => {
  if (event.button === 0) {
    backgroundPointerStart = { x: event.clientX, y: event.clientY };
  }
});
graphContainerEl?.addEventListener('click', event => {
  if (event.target.closest?.('.node')) return;
  if (backgroundPointerStart) {
    const dx = event.clientX - backgroundPointerStart.x;
    const dy = event.clientY - backgroundPointerStart.y;
    if (Math.hypot(dx, dy) > 5) return;
  }
  event.stopPropagation();
  showBackgroundMenu(event);
});

// =========================================================
//  ACCESSEURS RELATIONS
// =========================================================
const getParents  = id => state.parentsOf[id]  || [];
const getChildren = id => state.childrenOf[id] || [];
const getSpouses  = id => state.spouseOf[id]   || [];
function getSiblings(id) {
  const sibs = new Set();
  for (const p of getParents(id)) for (const c of (state.childrenOf[p] || [])) if (c !== id) sibs.add(c);
  return [...sibs];
}

function descendantIds(id, { includeSpouses = false } = {}) {
  const descendants = new Set();
  const pending = [id];
  while (pending.length) {
    const current = pending.pop();
    if (descendants.has(current)) continue;
    descendants.add(current);
    for (const child of getChildren(current)) pending.push(child);
  }
  if (includeSpouses) {
    for (const current of [...descendants]) {
      for (const spouse of getSpouses(current)) descendants.add(spouse);
    }
  }
  return descendants;
}

function keepDescendants(id) {
  if (!state || !state.nodesById[id]) return;
  const descendants = descendantIds(id, { includeSpouses: state.cfg.id === 'family' });
  const allowed = isFauna() ? faunaAllowedNodeIds() : null;
  if (allowed) {
    for (const nodeId of [...descendants]) {
      if (!allowed.has(nodeId)) descendants.delete(nodeId);
    }
  }
  // Le nœud double-cliqué reste visible même si un filtre d'ordre l'exclut
  // temporairement : c'est le point d'entrée de la descendance demandée.
  descendants.add(id);
  state.selfId = id;
  state.visible = descendants;
  const select = document.getElementById('self-select');
  if (select && [...select.options].some(option => option.value === id)) {
    select.value = id;
  }
  const node = state.nodesById[id];
  if (node?.classId) {
    const groupSelect = document.getElementById('group-select');
    if (groupSelect && [...groupSelect.options].some(option => option.value === node.classId)) {
      groupSelect.value = node.classId;
    }
  }
  update();
  setTimeout(fitGraph, 450);
}

// =========================================================
//  EXPAND / COLLAPSE
// =========================================================
function listFor(id, group) {
  const m = { parents: getParents(id), children: getChildren(id), siblings: getSiblings(id), spouse: getSpouses(id) };
  if (group === 'all') return [...m.parents, ...m.children, ...m.siblings, ...m.spouse];
  return m[group];
}
function expandGroup(id, group) {
  const allowed = isFauna() ? faunaAllowedNodeIds() : null;
  for (const x of listFor(id, group)) {
    if (!allowed || allowed.has(x)) state.visible.add(x);
  }
}
function collapseGroup(id, group) {
  for (const x of listFor(id, group)) {
    if (x === state.selfId) continue;
    state.visible.delete(x);
  }
  pruneOrphans();
}
function toggleGroup(id, group) {
  const list = listFor(id, group);
  if (!list.length) return;
  if (list.every(x => state.visible.has(x))) collapseGroup(id, group);
  else expandGroup(id, group);
  update();
}
function pruneOrphans() {
  const reachable = new Set([state.selfId]);
  const queue = [state.selfId];
  while (queue.length) {
    const cur = queue.shift();
    for (const n of [...getParents(cur), ...getChildren(cur), ...getSpouses(cur)]) {
      if (state.visible.has(n) && !reachable.has(n)) { reachable.add(n); queue.push(n); }
    }
  }
  state.visible = reachable;
}

// =========================================================
//  CENTRAGE / NIVEAUX
// =========================================================
function centerOnSelf() {
  if (!state) return;
  const n = state.nodeData[state.selfId];
  if (!n) return;
  const t = d3.zoomIdentity.translate(width / 2 - n.x, height / 2 - n.y).scale(1);
  svg.transition().duration(600).call(zoom.transform, t);
}

// =========================================================
//  ÉVÉNEMENTS UI
// =========================================================
document.getElementById('self-select').addEventListener('change', e => {
  state.selfId = e.target.value;
  if (isFauna()) {
    const allowed = faunaAllowedNodeIds();
    state.visible = allowed?.size ? new Set(allowed) : new Set([state.selfId]);
  } else {
    state.visible = new Set([state.selfId]);
    expandGroup(state.selfId, 'all');
  }
  const node = state.nodesById[state.selfId];
  if (node?.classId) {
    state.activeClassId = node.classId;
    const groupSelect = document.getElementById('group-select');
    if ([...groupSelect.options].some(option => option.value === node.classId)) {
      groupSelect.value = node.classId;
    }
  }
  hideMenu(); update();
  centerOnSelf();
});
document.getElementById('label-mode')?.addEventListener('change', e => {
  setGraphLabelMode(e.target.value);
});
document.getElementById('fit-graph')?.addEventListener('click', fitGraph);
document.getElementById('center-graph')?.addEventListener('click', centerOnSelf);
syncLabelModeControl();

document.getElementById('group-select').addEventListener('change', e => {
  if (!state) return;
  state.activeClassId = e.target.value;
  state.selectedOrders.clear();
  state.selfId = state.activeClassId;
  refreshFaunaSidebar();
  rebuildSelfSelect();
  const allowed = faunaAllowedNodeIds();
  state.visible = allowed?.size ? new Set(allowed) : new Set([state.selfId]);
  hideMenu(); update();
  centerOnSelf();
});
document.getElementById('order-list')?.addEventListener('change', event => {
  const input = event.target.closest('input[data-order-id]');
  if (!input || !isFauna()) return;
  if (input.checked) state.selectedOrders.add(input.dataset.orderId);
  else state.selectedOrders.delete(input.dataset.orderId);
  applyFaunaOrderFilter();
});
document.querySelectorAll('.mode-tab').forEach(button => {
  button.addEventListener('click', () => activateAppMode(button.dataset.mode));
});

// =========================================================
//  DÉMARRAGE
// =========================================================
// Les datasets sont maintenant initialisés depuis index.html
// qui appelle initializeDatasets() au chargement

// =========================================================
//  MODE GÉOGRAPHIQUE
// =========================================================
let mapInstance = null;
let markerClusterGroup = null;
let locationHierarchy = null;
let selectedLocations = new Set();
let locationMarkers = {};

async function loadLocationHierarchy() {
  try {
    const response = await fetch('/api/locations');
    locationHierarchy = await response.json();
    initializeGeographicMode();
  } catch (err) {
    console.error('Erreur lors du chargement des lieux:', err);
  }
}

function flattenLocationHierarchy(items = null, parent = null) {
  if (!locationHierarchy) return [];
  if (!items) items = locationHierarchy.hierarchy;
  const flat = [];
  for (const item of items) {
    flat.push({ ...item, parent });
    if (item.children && item.children.length) {
      flat.push(...flattenLocationHierarchy(item.children, item));
    }
  }
  return flat;
}

function initializeGeographicMode() {
  if (!mapInstance) {
    mapInstance = L.map('map').setView([20, 0], 2);
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      attribution: '© OpenStreetMap',
      maxZoom: 19,
      // className: 'leaflet-tile'
    }).addTo(mapInstance);
    markerClusterGroup = L.markerClusterGroup({
      maxClusterRadius: 80,
      iconCreateFunction: createClusterIcon
    });
    mapInstance.addLayer(markerClusterGroup);
  }
  addLocationMarkersToMap();
  setupLocationSearch();
}

function createClusterIcon(cluster) {
  const count = cluster.getChildCount();
  const size = count < 10 ? 'small' : count < 50 ? 'medium' : 'large';
  return L.divIcon({
    html: `<div class="marker-cluster marker-cluster-${size}"><span>${count}</span></div>`,
    className: 'marker-cluster-icon',
    iconSize: [40, 40]
  });
}

function addLocationMarkersToMap() {
  markerClusterGroup.clearLayers();
  locationMarkers = {};
  const allLocations = flattenLocationHierarchy();
  const showOnlyWithPhotos = document.getElementById('map-filter-photos')?.checked || false;
  
  for (const loc of allLocations) {
    if (!loc.lat || !loc.lng || loc.children?.length) continue; // Ignorer les nœuds intermédiaires
    
    const hasPhotos = (loc.photoCount || 0) > 0;
    if (showOnlyWithPhotos && !hasPhotos) continue;
    
    const markerClass = hasPhotos ? 'location-marker' : 'location-marker no-photos';
    const icon = L.divIcon({
      html: `<div class="${markerClass}" style="width: 32px; height: 32px;">${loc.photoCount || 0}</div>`,
      className: 'location-icon',
      iconSize: [32, 32],
      iconAnchor: [16, 16]
    });
    
    const marker = L.marker([loc.lat, loc.lng], { icon })
      .bindPopup(`<strong>${loc.name}</strong><br/>Type: ${loc.type}<br/>Photos: ${loc.photoCount || 0}`)
      .on('click', () => selectLocation(loc));
    
    markerClusterGroup.addLayer(marker);
    locationMarkers[loc.id] = { marker, location: loc };
  }
}

function selectLocation(location) {
  if (selectedLocations.has(location.id)) {
    selectedLocations.delete(location.id);
  } else {
    selectedLocations.add(location.id);
  }
  updateSelectedLocationsDisplay();
}

function updateSelectedLocationsDisplay() {
  const container = document.getElementById('selected-locations');
  container.innerHTML = '';
  
  if (selectedLocations.size === 0) {
    container.innerHTML = '<div style="color: var(--muted); font-size: 11px;">Aucun lieu sélectionné</div>';
    renderQuery();
    return;
  }
  
  const allLocations = flattenLocationHierarchy();
  const selected = allLocations.filter(l => selectedLocations.has(l.id));
  
  for (const loc of selected) {
    const tag = document.createElement('div');
    tag.className = 'location-tag';
    tag.innerHTML = `
      <span class="location-tag-name">${loc.name}</span>
      <span class="location-tag-count">${loc.photoCount || 0}</span>
      <span class="location-tag-remove">✕</span>
    `;
    tag.querySelector('.location-tag-remove').addEventListener('click', e => {
      e.stopPropagation();
      selectLocation(loc);
    });
    tag.addEventListener('click', () => {
      if (loc.lat && loc.lng) {
        mapInstance.setView([loc.lat, loc.lng], loc.zoom || 10);
      }
    });
    container.appendChild(tag);
  }
  renderQuery();
}

function setupLocationSearch() {
  const searchInput = document.getElementById('location-search');
  const suggestionsContainer = document.getElementById('location-suggestions');
  
  searchInput.addEventListener('input', e => {
    const query = e.target.value.toLowerCase();
    suggestionsContainer.innerHTML = '';
    
    if (!query) return;
    
    const allLocations = flattenLocationHierarchy();
    const matches = allLocations
      .filter(l => l.name.toLowerCase().includes(query) && (!l.children || l.children.length === 0))
      .slice(0, 10);
    
    for (const loc of matches) {
      const div = document.createElement('div');
      div.className = 'suggestion-item';
      const breadcrumb = getBreadcrumb(loc);
      div.innerHTML = `
        <span>${loc.name}</span>
        <span class="suggestion-type">${loc.type}</span>
      `;
      div.title = breadcrumb;
      div.addEventListener('click', () => {
        selectLocation(loc);
        if (loc.lat && loc.lng) {
          mapInstance.setView([loc.lat, loc.lng], loc.zoom || 10);
        }
        searchInput.value = '';
        suggestionsContainer.innerHTML = '';
      });
      suggestionsContainer.appendChild(div);
    }
  });
}

function getBreadcrumb(location, allLocs = null) {
  if (!allLocs) allLocs = flattenLocationHierarchy();
  const path = [location.name];
  let current = location;
  while (current.parent) {
    path.unshift(current.parent.name);
    current = current.parent;
  }
  return path.join(' > ');
}

function resetMapView() {
  if (mapInstance) {
    mapInstance.setView([20, 0], 2);
  }
}

// Mode switcher implémenté avec support keywords à la fin du fichier

document.getElementById('map-reset-btn').addEventListener('click', resetMapView);
document.getElementById('map-filter-photos').addEventListener('change', () => {
  addLocationMarkersToMap();
});

// =========================================================
//  MODE MOTS-CLÉS & FILTRES
// =========================================================

async function initializeKeywordsMode() {
  if (Object.keys(keywordFrequencies).length === 0) {
    await initializeKeywordFrequencies();
  }
  
  // Remplir les mots-clés populaires
  const popularContainer = document.getElementById('popular-keywords');
  popularContainer.innerHTML = '';
  for (const kw of populateKeywords) {
    const tag = document.createElement('span');
    tag.className = 'popular-keyword';
    tag.textContent = `${kw} (${getKeywordFrequency(kw)})`;
    tag.addEventListener('click', () => addKeyword(kw));
    popularContainer.appendChild(tag);
  }

  if (!keywordsModeInitialized) {
    document.getElementById('keyword-add-btn').addEventListener('click', addKeywordFromInput);
    document.getElementById('keyword-input').addEventListener('keypress', e => {
      if (e.key === 'Enter') addKeywordFromInput();
    });
    keywordsModeInitialized = true;
  }
  updateKeywordsDisplay();
}

function addKeywordFromInput() {
  const input = document.getElementById('keyword-input');
  const keyword = input.value.trim().toLowerCase();
  if (keyword && keyword.length > 0) {
    addKeyword(keyword);
    input.value = '';
  }
}

function addKeyword(keyword) {
  keyword = keyword.trim().toLowerCase();
  if (!selectedKeywords.has(keyword)) {
    selectedKeywords.add(keyword);
    updateKeywordsDisplay();
    updateKeywordsCloud();
    updateFilterSummary();
  }
}

function removeKeyword(keyword) {
  selectedKeywords.delete(keyword);
  updateKeywordsDisplay();
  updateKeywordsCloud();
  updateFilterSummary();
}

function renderQuery() {
  if (currentMode === 'graph' && !state) return;
  filterPhotos(currentMode);
}

function numberOrNull(id) {
  const element = document.getElementById(id);
  if (!element || !element.value) return null;
  const value = Number(element.value);
  return Number.isFinite(value) ? value : null;
}

function getTechnicalFilters() {
  return {
    model: document.getElementById('model-select')?.value || null,
    lens: document.getElementById('lens-select')?.value || null,
    iso_min: numberOrNull('iso-min'),
    iso_max: numberOrNull('iso-max'),
    focal_min: numberOrNull('focal-min'),
    focal_max: numberOrNull('focal-max'),
  };
}

async function filterPhotos(mode, { sort = null } = {}) {
  const selections = getSelectionsForMode(mode);
  const data = {
    mode: mode,
    dataset: state ? state.cfg.id : 'family',
    selections: selections,
    min_rating: minRating,
    year_min: yearMin,
    year_max: yearMax,
    ...getTechnicalFilters(),
    limit: 1000,
    sort: sort || 'date_desc',
  };

  try {
    const response = await fetch('/api/filter', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(data),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Erreur de filtrage.');
    displayFilteredPhotos(result);
    return result;
  } catch (error) {
    console.error('Erreur lors du filtrage:', error);
    const empty = { photos: [], ids: [], total: 0 };
    displayFilteredPhotos(empty);
    return empty;
  }
}

function getSelectionsForMode(mode) {
  if (mode === 'keywords') {
    return Array.from(selectedKeywords);
  } else if (mode === 'graph') {
    if (!state) return [];
    const visible = Array.from(state.visible);
    if (isFauna()) {
      const allowed = faunaAllowedNodeIds();
      return allowed ? visible.filter(id => allowed.has(id)) : visible;
    }
    return visible;
  } else if (mode === 'map') {
    return Array.from(selectedLocations);
  }
  return [];
}

function displayFilteredPhotos(result) {
  const photos = Array.isArray(result) ? result : (result.photos || []);
  const total = Array.isArray(result) ? photos.length : (result.total ?? photos.length);
  const ids = Array.isArray(result) ? photos.map(photo => photo.id) : (result.ids || photos.map(photo => photo.id));
  currentFilterResult = { photos, ids, total };
  const summaryEl = document.getElementById('filter-summary');
  if (summaryEl) {
    summaryEl.textContent = `${total} photo(s)` + (photos.length < total ? ` · ${photos.length} affichées` : '');
  }
  const countEl = document.getElementById('selection-count');
  if (countEl) countEl.textContent = total > 999 ? '999+' : String(total);
}

async function openPhotosForSelection() {
  const button = document.getElementById('graph-photos-btn');
  const previous = button?.innerHTML;
  if (button) {
    button.disabled = true;
    button.textContent = 'Chargement…';
  }
  try {
    const result = await filterPhotos(currentMode, { sort: photoModalSort });
    openPhotoModal(result);
  } finally {
    if (button) {
      button.disabled = false;
      button.innerHTML = previous;
      const countEl = document.getElementById('selection-count');
      if (countEl) countEl.textContent = currentFilterResult.total > 999 ? '999+' : String(currentFilterResult.total || 0);
    }
  }
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, character => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[character]));
}

function updatePhotoModalCount() {
  const count = document.getElementById('photo-modal-count');
  if (!count) return;
  const total = currentFilterResult.total || 0;
  const shown = photoModalShown;
  const parts = [`${total} photo${total > 1 ? 's' : ''}`];
  if (shown && shown < total) parts.push(`${shown} premières affichées`);
  if (selectedPhotoIds.size) parts.push(`${selectedPhotoIds.size} sélectionnée${selectedPhotoIds.size > 1 ? 's' : ''}`);
  count.textContent = parts.join(' · ');
}

function updateAlbumSelectionUI() {
  const count = selectedPhotoIds.size;
  const albumCount = document.getElementById('album-count');
  const photoCount = document.getElementById('photo-album-count');
  if (albumCount) albumCount.textContent = String(count);
  if (photoCount) photoCount.textContent = String(count);
  const albumButton = document.getElementById('album-btn');
  if (albumButton) albumButton.disabled = count === 0;
  const createButton = document.getElementById('photo-create-album');
  if (createButton) createButton.disabled = count === 0;

  document.querySelectorAll('.photo-select').forEach(button => {
    const photoId = Number(button.dataset.photoId);
    const selected = selectedPhotoIds.has(photoId);
    button.classList.toggle('selected', selected);
    button.textContent = selected ? '✓' : '+';
    button.setAttribute('aria-pressed', selected ? 'true' : 'false');
    button.title = selected ? 'Retirer de la sélection' : 'Sélectionner pour l’album';
    button.closest('.photo-card')?.classList.toggle('selected', selected);
  });
  updatePhotoModalCount();
  if (currentMode === 'album') updateAlbumPhotoCount();
}

function togglePhotoSelection(photo) {
  if (!photo) return;
  const photoId = Number(photo.id);
  if (selectedPhotoIds.has(photoId)) {
    selectedPhotoIds.delete(photoId);
    selectedPhotoCache.delete(photoId);
  } else {
    selectedPhotoIds.add(photoId);
    selectedPhotoCache.set(photoId, photo);
  }
  updateAlbumSelectionUI();
}

function selectPhotoRange(index) {
  if (!photoModalPhotos.length) return;
  if (photoModalLastIndex < 0) {
    const photo = photoModalPhotos[index];
    if (photo) {
      selectedPhotoIds.add(Number(photo.id));
      selectedPhotoCache.set(Number(photo.id), photo);
    }
  } else {
    const start = Math.min(photoModalLastIndex, index);
    const end = Math.max(photoModalLastIndex, index);
    for (let position = start; position <= end; position++) {
      const photo = photoModalPhotos[position];
      if (!photo) continue;
      selectedPhotoIds.add(Number(photo.id));
      selectedPhotoCache.set(Number(photo.id), photo);
    }
  }
  photoModalLastIndex = index;
  updateAlbumSelectionUI();
}

function selectPhotoFromModal(photo, index, event) {
  if (event?.shiftKey) {
    selectPhotoRange(index);
    return;
  }
  togglePhotoSelection(photo);
  photoModalLastIndex = index;
}

function selectAllCurrentPhotos() {
  const photos = (currentFilterResult.photos || []).slice(0, 1000);
  for (const photo of photos) {
    const photoId = Number(photo.id);
    selectedPhotoIds.add(photoId);
    selectedPhotoCache.set(photoId, photo);
  }
  photoModalLastIndex = photos.length - 1;
  updateAlbumSelectionUI();
}

function clearPhotoSelection() {
  selectedPhotoIds.clear();
  selectedPhotoCache.clear();
  photoModalLastIndex = -1;
  updateAlbumSelectionUI();
}

function enterAlbumMode() {
  if (!selectedPhotoIds.size) return;
  if (currentMode !== 'album') {
    albumReturnMode = currentMode;
    albumConfig = null;
  }
  closePhotoModal();
  setFiltersOpen(false);
  currentMode = 'album';
  const app = document.getElementById('app');
  app.className = 'mode-album';
  updateModeTitles('album');
  state?.simulation?.stop?.();
  document.getElementById('menu').style.display = 'none';
  document.getElementById('tooltip').style.opacity = 0;
  initializeAlbumMode();
}

function openPhotoModal(result) {
  const modal = document.getElementById('photo-modal');
  const grid = document.getElementById('photo-grid');
  const count = document.getElementById('photo-modal-count');
  if (!modal || !grid || !count) return;

  const photos = Array.isArray(result) ? result : (result.photos || []);
  const total = Array.isArray(result) ? photos.length : (result.total ?? photos.length);
  const ids = Array.isArray(result) ? photos.map(photo => photo.id) : (result.ids || photos.map(photo => photo.id));
  currentFilterResult = { photos, ids, total };
  photoModalSourcePhotos = photos.slice(0, 1000);

  const sortSelect = document.getElementById('photo-sort');
  if (sortSelect) sortSelect.value = photoModalSort;

  renderPhotoModalGrid();
  modal.classList.add('open');
  modal.setAttribute('aria-hidden', 'false');
}

function renderPhotoModalGrid() {
  const grid = document.getElementById('photo-grid');
  if (!grid) return;
  const photos = photoModalSourcePhotos;
  photoModalShown = photos.length;
  photoModalPhotos = photos;
  photoModalLastIndex = -1;
  grid.title = 'Cliquer pour sélectionner · Maj + clic pour sélectionner une plage';
  grid.innerHTML = '';

  if (!photos.length) {
    grid.innerHTML = '<div class="photo-empty">Aucune photo ne correspond à cette sélection.</div>';
    updateAlbumSelectionUI();
    return;
  }

  const fragment = document.createDocumentFragment();
  for (const [index, photo] of photos.entries()) {
    const card = document.createElement('article');
    const selected = selectedPhotoIds.has(Number(photo.id));
    card.className = `photo-card${selected ? ' selected' : ''}`;
    const people = (photo.people || []).slice(0, 4).join(', ');
    const keywords = (photo.keywords || []).slice(0, 5).join(', ');
    const taxa = (photo.taxa || []).map(taxonDisplayName).filter(Boolean).slice(0, 4).join(', ');
    const title = photoDisplayTitle(photo);
    card.innerHTML = `
      <button class="photo-select${selected ? ' selected' : ''}" type="button"
        data-photo-id="${Number(photo.id)}" aria-pressed="${selected ? 'true' : 'false'}"
        title="${selected ? 'Retirer de la sélection' : 'Sélectionner pour l’album'}">${selected ? '✓' : '+'}</button>
      <img src="${photo.thumbUrl}" alt="${escapeHtml(title)}" loading="lazy">
      <div class="photo-card-body">
        <div class="photo-title" title="${escapeHtml(photoDisplayTooltip(photo, title))}">${escapeHtml(title)}</div>
        <div class="photo-meta">${escapeHtml(photo.date || 'Date inconnue')}${photo.rating ? ' · ' + Number(photo.rating) + '★' : ''}</div>
        <div class="photo-meta">${escapeHtml(photo.model || '')}${photo.lens ? ' · ' + escapeHtml(photo.lens) : ''}</div>
        ${people ? `<div class="photo-tags">${escapeHtml(people)}</div>` : ''}
        ${taxa ? `<div class="photo-tags">${escapeHtml(taxa)}</div>` : ''}
        ${keywords ? `<div class="photo-tags muted">${escapeHtml(keywords)}</div>` : ''}
      </div>
    `;
    card.querySelector('.photo-select').addEventListener('click', event => {
      event.stopPropagation();
      selectPhotoFromModal(photo, index, event);
    });
    card.addEventListener('click', event => {
      if (event.target.closest('button')) return;
      selectPhotoFromModal(photo, index, event);
    });
    card.querySelector('img').addEventListener('error', event => {
      event.currentTarget.replaceWith(Object.assign(document.createElement('div'), {
        className: 'photo-missing',
        textContent: 'Miniature indisponible',
      }));
    });
    fragment.appendChild(card);
  }
  grid.appendChild(fragment);
  updateAlbumSelectionUI();
}

async function reloadPhotoModalWithSort() {
  const sequence = ++photoModalSortSeq;
  const select = document.getElementById('photo-sort');
  if (select) select.disabled = true;
  try {
    const result = await filterPhotos(currentMode, { sort: photoModalSort });
    if (sequence !== photoModalSortSeq) return;
    const modal = document.getElementById('photo-modal');
    if (!modal?.classList.contains('open')) return;
    openPhotoModal(result);
  } finally {
    if (sequence === photoModalSortSeq && select) select.disabled = false;
  }
}

function setPhotoModalSort(value) {
  photoModalSort = PHOTO_SORTS.has(value) ? value : 'date_desc';
  const select = document.getElementById('photo-sort');
  if (select) select.value = photoModalSort;
  try {
    localStorage.setItem('select.photoSort', photoModalSort);
  } catch (_) {
    // Le tri reste actif pour la session.
  }
  reloadPhotoModalWithSort();
}

function closePhotoModal() {
  const modal = document.getElementById('photo-modal');
  if (!modal) return;
  modal.classList.remove('open');
  modal.setAttribute('aria-hidden', 'true');
}

// =========================================================
//  MODE ALBUM
// =========================================================
function albumSlug(value) {
  return String(value || 'album')
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .replace(/[^a-zA-Z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .toLowerCase() || 'album';
}

function formatFileSize(bytes) {
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} Mo`;
  return `${Math.max(1, Math.round(bytes / 1024))} Ko`;
}

function defaultAlbumConfig() {
  return {
    title: 'Mon album',
    subtitle: '',
    intro: '',
    author: '',
    template: 'grid',
    theme: 'dark',
    columns: 3,
    show_captions: true,
    show_metadata: true,
    cover_id: selectedPhotoIds.values().next().value || 0,
    photo_ids: [...selectedPhotoIds],
  };
}

function updateAlbumPhotoCount() {
  const label = document.getElementById('album-photo-count');
  if (label) label.textContent = `${selectedPhotoIds.size} photo${selectedPhotoIds.size > 1 ? 's' : ''}`;
  const exportButton = document.getElementById('album-export');
  if (exportButton) exportButton.disabled = selectedPhotoIds.size === 0;
}

function renderAlbumOptions() {
  if (!albumOptions || !albumConfig) return;
  const templates = document.getElementById('album-templates');
  if (templates) {
    templates.innerHTML = albumOptions.templates.map(template => `
      <button type="button" class="album-template-card${albumConfig.template === template.id ? ' active' : ''}" data-template="${escapeHtml(template.id)}">
        <span class="template-thumb ${escapeHtml(template.id)}"><i></i><i></i><i></i><i></i></span>
        <strong>${escapeHtml(template.name)}</strong>
        <small>${escapeHtml(template.description)}</small>
      </button>
    `).join('');
  }
  const themes = document.getElementById('album-themes');
  if (themes) {
    themes.innerHTML = albumOptions.themes.map(theme => `
      <button type="button" class="album-theme${albumConfig.theme === theme.id ? ' active' : ''}" data-theme="${escapeHtml(theme.id)}">
        <span class="theme-swatch" style="background:${escapeHtml(theme.swatch)}"></span>${escapeHtml(theme.name)}
      </button>
    `).join('');
  }
}

function renderAlbumSelected() {
  const container = document.getElementById('album-selected');
  if (!container) return;
  if (!selectedPhotoIds.size) {
    container.innerHTML = '<div class="album-empty-selection">Aucune photo sélectionnée.</div>';
    return;
  }
  container.innerHTML = [...selectedPhotoIds].map(photoId => {
    const photo = selectedPhotoCache.get(photoId);
    const source = photo?.thumbUrl || `/api/photos/${photoId}/thumbnail`;
    const title = photoDisplayTitle(photo || { id: photoId });
    const tooltip = photoDisplayTooltip(photo || { id: photoId }, title);
    const cover = Number(albumConfig?.cover_id) === photoId;
    return `<div class="album-selected-item${cover ? ' cover' : ''}" data-photo-id="${photoId}" title="${escapeHtml(tooltip)} · cliquer pour la couverture">
      <img src="${escapeHtml(source)}" alt="${escapeHtml(title)}" loading="lazy">
      ${cover ? '<span class="album-cover-badge">Couverture</span>' : ''}
      <button type="button" class="album-remove" data-remove-photo="${photoId}" aria-label="Retirer cette photo">×</button>
    </div>`;
  }).join('');
}

function syncAlbumControls() {
  if (!albumConfig) return;
  const values = {
    'album-title': albumConfig.title,
    'album-subtitle': albumConfig.subtitle,
    'album-intro': albumConfig.intro,
    'album-author': albumConfig.author,
  };
  for (const [id, value] of Object.entries(values)) {
    const input = document.getElementById(id);
    if (input) input.value = value || '';
  }
  const columns = document.getElementById('album-columns');
  const output = document.getElementById('album-columns-value');
  if (columns) columns.value = String(albumConfig.columns);
  if (output) output.textContent = String(albumConfig.columns);
  const captions = document.getElementById('album-captions');
  const metadata = document.getElementById('album-metadata');
  if (captions) captions.checked = Boolean(albumConfig.show_captions);
  if (metadata) metadata.checked = Boolean(albumConfig.show_metadata);
}

function scheduleAlbumPreview(delay = 260) {
  clearTimeout(albumPreviewTimer);
  albumPreviewTimer = setTimeout(() => requestAlbumPreview(), delay);
}

async function requestAlbumPreview() {
  if (!albumConfig) return;
  albumConfig.photo_ids = [...selectedPhotoIds];
  const sequence = ++albumPreviewSeq;
  const iframe = document.getElementById('album-preview');
  const refresh = document.getElementById('album-preview-refresh');
  if (refresh) {
    refresh.disabled = true;
    refresh.textContent = 'Mise à jour…';
  }
  try {
    const response = await fetch('/api/album/preview', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(albumConfig),
    });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(data.error || `Erreur ${response.status}`);
    }
    const data = await response.json();
    if (sequence !== albumPreviewSeq || !iframe) return;
    iframe.srcdoc = data.html;
  } catch (error) {
    if (sequence !== albumPreviewSeq || !iframe) return;
    iframe.srcdoc = `<!doctype html><html lang="fr"><body style="margin:0;padding:32px;background:#171a27;color:#ff8787;font:13px -apple-system,sans-serif">${escapeHtml(error.message)}</body></html>`;
  } finally {
    if (sequence === albumPreviewSeq && refresh) {
      refresh.disabled = false;
      refresh.textContent = 'Synchroniser';
    }
  }
}

function bindAlbumEvents() {
  if (albumEventsBound) return;
  albumEventsBound = true;

  document.getElementById('album-back')?.addEventListener('click', leaveAlbumMode);
  document.getElementById('album-preview-refresh')?.addEventListener('click', () => requestAlbumPreview());
  document.getElementById('album-export')?.addEventListener('click', exportAlbum);

  const fieldMap = {
    'album-title': 'title',
    'album-subtitle': 'subtitle',
    'album-intro': 'intro',
    'album-author': 'author',
  };
  for (const [id, key] of Object.entries(fieldMap)) {
    document.getElementById(id)?.addEventListener('input', event => {
      if (!albumConfig) return;
      albumConfig[key] = event.target.value;
      scheduleAlbumPreview();
    });
  }

  document.getElementById('album-columns')?.addEventListener('input', event => {
    if (!albumConfig) return;
    albumConfig.columns = Number(event.target.value);
    const output = document.getElementById('album-columns-value');
    if (output) output.textContent = String(albumConfig.columns);
    scheduleAlbumPreview();
  });
  document.getElementById('album-captions')?.addEventListener('change', event => {
    if (!albumConfig) return;
    albumConfig.show_captions = event.target.checked;
    scheduleAlbumPreview();
  });
  document.getElementById('album-metadata')?.addEventListener('change', event => {
    if (!albumConfig) return;
    albumConfig.show_metadata = event.target.checked;
    scheduleAlbumPreview();
  });

  document.getElementById('album-templates')?.addEventListener('click', event => {
    const card = event.target.closest('.album-template-card');
    if (!card || !albumConfig) return;
    albumConfig.template = card.dataset.template;
    renderAlbumOptions();
    scheduleAlbumPreview(0);
  });
  document.getElementById('album-themes')?.addEventListener('click', event => {
    const button = event.target.closest('.album-theme');
    if (!button || !albumConfig) return;
    albumConfig.theme = button.dataset.theme;
    renderAlbumOptions();
    scheduleAlbumPreview(0);
  });

  document.getElementById('album-selected')?.addEventListener('click', event => {
    const removeButton = event.target.closest('[data-remove-photo]');
    if (removeButton) {
      event.stopPropagation();
      const photoId = Number(removeButton.dataset.removePhoto);
      selectedPhotoIds.delete(photoId);
      selectedPhotoCache.delete(photoId);
      if (Number(albumConfig?.cover_id) === photoId) {
        albumConfig.cover_id = selectedPhotoIds.values().next().value || 0;
      }
      renderAlbumSelected();
      updateAlbumSelectionUI();
      scheduleAlbumPreview(0);
      return;
    }
    const item = event.target.closest('.album-selected-item');
    if (!item || !albumConfig) return;
    albumConfig.cover_id = Number(item.dataset.photoId);
    renderAlbumSelected();
    scheduleAlbumPreview(0);
  });
}

async function initializeAlbumMode() {
  if (!albumConfig) albumConfig = defaultAlbumConfig();
  albumConfig.photo_ids = [...selectedPhotoIds];
  if (!selectedPhotoIds.has(Number(albumConfig.cover_id))) {
    albumConfig.cover_id = selectedPhotoIds.values().next().value || 0;
  }

  bindAlbumEvents();

  if (!albumOptions) {
    try {
      const response = await fetch('/api/album/options');
      if (!response.ok) throw new Error(`Erreur ${response.status}`);
      albumOptions = await response.json();
      const status = document.getElementById('album-export-status');
      if (status) {
        status.classList.remove('error');
        status.textContent = '';
      }
    } catch (error) {
      const status = document.getElementById('album-export-status');
      if (status) {
        status.classList.add('error');
        status.textContent = `Options indisponibles : ${error.message}`;
      }
      return;
    }
  }

  syncAlbumControls();
  renderAlbumOptions();
  renderAlbumSelected();
  updateAlbumPhotoCount();
  requestAlbumPreview();
}

async function exportAlbum() {
  if (!albumConfig || !selectedPhotoIds.size) return;
  albumConfig.photo_ids = [...selectedPhotoIds];
  const button = document.getElementById('album-export');
  const status = document.getElementById('album-export-status');
  if (button) {
    button.disabled = true;
    button.textContent = 'Préparation de l’album…';
  }
  if (status) {
    status.classList.remove('error');
    status.textContent = 'Conversion des photos et création du fichier ZIP…';
  }
  try {
    const response = await fetch('/api/album/export', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(albumConfig),
    });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(data.error || `Erreur ${response.status}`);
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `${albumSlug(albumConfig.title)}.zip`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 60000);
    if (status) status.textContent = `Album exporté · ${formatFileSize(blob.size)}`;
  } catch (error) {
    if (status) {
      status.classList.add('error');
      status.textContent = error.message;
    }
  } finally {
    if (button) {
      button.disabled = selectedPhotoIds.size === 0;
      button.textContent = "Exporter l'album (.zip)";
    }
  }
}

function leaveAlbumMode() {
  clearTimeout(albumPreviewTimer);
  albumPreviewSeq += 1;
  albumConfig = null;
  const status = document.getElementById('album-export-status');
  if (status) {
    status.classList.remove('error');
    status.textContent = '';
  }
  const preview = document.getElementById('album-preview');
  if (preview) preview.srcdoc = '';
  const target = albumReturnMode && albumReturnMode !== 'album' ? albumReturnMode : 'graph';
  currentMode = target;
  currentAppMode = target === 'map'
    ? 'geotag'
    : target === 'keywords'
      ? 'motscles'
      : (state?.cfg?.id === 'fauna' ? 'faune' : 'face');
  const app = document.getElementById('app');
  app.className = `mode-${target}`;
  updateModeTitles(target);
  updateModeTabs();
  document.getElementById('menu').style.display = 'none';
  document.getElementById('tooltip').style.opacity = 0;
  if (target !== 'graph') state?.simulation?.stop?.();
  if (target === 'map') {
    setTimeout(() => mapInstance?.invalidateSize?.(), 280);
  } else if (target === 'keywords') {
    initializeKeywordsMode();
    initializeFilters();
  } else if (target === 'graph') {
    requestAnimationFrame(() => {
      resize();
      state?.simulation?.alpha(0.15).restart?.();
    });
  }
  renderQuery();
}

function updateKeywordsDisplay() {
  const container = document.getElementById('keywords-selected-list');
  if (!container) return;
  container.innerHTML = '';
  
  if (selectedKeywords.size === 0) {
    renderQuery();
    return;
  }
  
  for (const kw of selectedKeywords) {
    const tag = document.createElement('div');
    tag.className = 'keyword-tag';
    tag.innerHTML = `<span>${escapeHtml(kw)}</span><span class="keyword-tag-remove">×</span>`;
    tag.querySelector('.keyword-tag-remove').addEventListener('click', () => removeKeyword(kw));
    container.appendChild(tag);
  }
  renderQuery();
}

function updateKeywordsCloud() {
  const cloud = document.getElementById('keywords-cloud');
  const empty = document.getElementById('keywords-empty');
  if (empty) empty.hidden = selectedKeywords.size > 0;
  if (cloud) cloud.hidden = selectedKeywords.size === 0;

  if (selectedKeywords.size === 0) {
    cloud.innerHTML = '';
    return;
  }
  
  cloud.innerHTML = '';
  
  // Créer des bulles avec des tailles basées sur la fréquence
  const keywords = Array.from(selectedKeywords);
  keywords.forEach((kw) => {
    const freq = getKeywordFrequency(kw);
    // Mapper la fréquence à une taille (1-5)
    const size = Math.min(5, Math.max(1, Math.floor(freq / 200) + 1));
    
    const bubble = document.createElement('div');
    bubble.className = `keyword-bubble size-${size}`;
    bubble.innerHTML = `
      <div class="keyword-name">${escapeHtml(kw)}</div>
      <div class="keyword-count">${freq} photos</div>
    `;
    bubble.addEventListener('click', () => removeKeyword(kw));
    bubble.title = `Cliquer pour retirer - ${freq} photos`;
    cloud.appendChild(bubble);
  });
}

async function loadTechnicalFacets() {
  try {
    const response = await fetch('/api/technical');
    const data = await response.json();
    const fill = (id, values) => {
      const select = document.getElementById(id);
      if (!select) return;
      select.innerHTML = '<option value="">Tous</option>';
      for (const value of values || []) {
        const option = document.createElement('option');
        option.value = value;
        option.textContent = value;
        select.appendChild(option);
      }
    };
    fill('model-select', data.models);
    fill('lens-select', data.lenses);
    for (const [id, value] of [['iso-min', data.iso_min], ['iso-max', data.iso_max], ['focal-min', data.focal_min], ['focal-max', data.focal_max]]) {
      const input = document.getElementById(id);
      if (input && value !== null && value !== undefined) input.placeholder = String(value);
    }
  } catch (error) {
    console.error('Facettes techniques indisponibles:', error);
  }
}

// Filtres
function initializeFilters() {
  if (filtersInitialized) return;
  filtersInitialized = true;

  // Étoiles de notation
  const starsContainer = document.getElementById('rating-stars');
  for (const star of starsContainer.querySelectorAll('.star')) {
    star.addEventListener('click', () => {
      const rating = parseInt(star.dataset.rating);
      setMinRating(rating);
      updateStarsDisplay();
    });
  }
  updateStarsDisplay();

  // Filtres d'années
  document.getElementById('year-min').addEventListener('change', () => {
    yearMin = document.getElementById('year-min').value ? parseInt(document.getElementById('year-min').value) : null;
    updateFilterSummary();
  });
  
  document.getElementById('year-max').addEventListener('change', () => {
    yearMax = document.getElementById('year-max').value ? parseInt(document.getElementById('year-max').value) : null;
    updateFilterSummary();
  });

  for (const id of ['model-select', 'lens-select', 'iso-min', 'iso-max', 'focal-min', 'focal-max']) {
    document.getElementById(id)?.addEventListener('change', updateFilterSummary);
  }

  document.getElementById('filters-toggle').addEventListener('click', () => {
    setFiltersOpen(!document.getElementById('filter-panel').classList.contains('open'));
  });
  document.getElementById('filter-close').addEventListener('click', () => setFiltersOpen(false));
  document.getElementById('filter-backdrop').addEventListener('click', () => setFiltersOpen(false));
  loadTechnicalFacets();
}

function setFiltersOpen(open) {
  const panel = document.getElementById('filter-panel');
  const toggle = document.getElementById('filters-toggle');
  const backdrop = document.getElementById('filter-backdrop');
  panel.classList.toggle('open', open);
  panel.setAttribute('aria-hidden', open ? 'false' : 'true');
  toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
  backdrop.hidden = !open;
}

function setMinRating(rating) {
  minRating = rating;
  const labels = ['Aucune', '★☆☆☆☆', '★★☆☆☆', '★★★☆☆', '★★★★☆', '★★★★★'];
  document.getElementById('rating-value').textContent = labels[rating] || 'Aucune';
  updateFilterSummary();
}

function updateStarsDisplay() {
  const stars = document.querySelectorAll('.star');
  for (let i = 0; i < stars.length; i++) {
    if (i < minRating) {
      stars[i].classList.add('active');
      stars[i].textContent = '★';
    } else {
      stars[i].classList.remove('active');
      stars[i].textContent = '☆';
    }
  }
}

function updateFilterSummary() {
  const active = [
    selectedKeywords.size > 0,
    minRating > 0,
    Boolean(yearMin || yearMax),
    Boolean(document.getElementById('model-select')?.value),
    Boolean(document.getElementById('lens-select')?.value),
    Boolean(document.getElementById('iso-min')?.value || document.getElementById('iso-max')?.value),
    Boolean(document.getElementById('focal-min')?.value || document.getElementById('focal-max')?.value),
  ].filter(Boolean).length;
  const badge = document.getElementById('filters-count');
  if (badge) {
    badge.textContent = String(active);
    badge.hidden = active === 0;
  }
  renderQuery();
}

// =========================================================
//  MODE SWITCHER
// =========================================================
function appModeFromState() {
  if (currentMode === 'map') return 'geotag';
  if (currentMode === 'keywords') return 'motscles';
  if (currentMode === 'graph') return state?.cfg?.id === 'fauna' ? 'faune' : 'face';
  return currentAppMode;
}

function updateModeTabs() {
  for (const button of document.querySelectorAll('.mode-tab')) {
    const active = button.dataset.mode === currentAppMode;
    button.classList.toggle('active', active);
    button.setAttribute('aria-pressed', active ? 'true' : 'false');
  }
}

function updateModeTitles(mode) {
  const titleEl = document.getElementById('app-title');
  const subEl = document.getElementById('app-sub');
  
  switch(mode) {
    case 'graph':
      titleEl.textContent = 'Sélection';
      subEl.textContent = state?.cfg
        ? `${state.cfg.title} · ${state.cfg.subtitle}`
        : 'Parcourir la hiérarchie';
      break;
    case 'map':
      titleEl.textContent = 'Sélection';
      subEl.textContent = 'Géotag · sélection par lieu';
      break;
    case 'keywords':
      titleEl.textContent = 'Sélection';
      subEl.textContent = 'Mots-clés';
      break;
    case 'album':
      titleEl.textContent = 'Album';
      subEl.textContent = 'Composition et export HTML';
      break;
    default:
      titleEl.textContent = 'Sélection';
      subEl.textContent = 'Parcourir et filtrer les photos';
  }
}

function setLayoutMode(mode, { query = true } = {}) {
  if (mode === 'map') currentAppMode = 'geotag';
  else if (mode === 'keywords') currentAppMode = 'motscles';
  else if (mode === 'graph' && currentAppMode !== 'faune' && currentAppMode !== 'face') {
    currentAppMode = state?.cfg?.id === 'fauna' ? 'faune' : 'face';
  }

  currentMode = mode;
  const app = document.getElementById('app');
  app.className = `mode-${mode}`;
  updateModeTitles(mode);
  updateModeTabs();

  document.getElementById('menu').style.display = 'none';
  document.getElementById('tooltip').style.opacity = 0;

  if (mode !== 'graph') state?.simulation?.stop?.();
  if (mode === 'map') {
    setTimeout(() => mapInstance?.invalidateSize?.(), 300);
  } else if (mode === 'keywords') {
    initializeKeywordsMode();
    initializeFilters();
  }
  if (query) updateFilterSummary();
}

async function activateAppMode(appMode) {
  const config = APP_MODES[appMode];
  if (!config) return;
  if (!Object.keys(datasets).length) await initializeDatasets();

  currentAppMode = appMode;
  updateModeTabs();

  if (config.layout !== 'graph') {
    setLayoutMode(config.layout);
    return;
  }

  setLayoutMode('graph', { query: false });
  const datasetKey = config.dataset;
  if (!datasets[datasetKey]) {
    updateModeTabs();
    return;
  }
  if (!state || state.cfg.id !== datasetKey) {
    await loadDataset(datasetKey);
  } else {
    updateModeTitles('graph');
    updateModeTabs();
    updateFilterSummary();
  }
}

document.getElementById('graph-photos-btn')?.addEventListener('click', openPhotosForSelection);
document.getElementById('album-btn')?.addEventListener('click', enterAlbumMode);
document.getElementById('photo-create-album')?.addEventListener('click', enterAlbumMode);
document.getElementById('photo-select-all')?.addEventListener('click', selectAllCurrentPhotos);
document.getElementById('photo-clear-selection')?.addEventListener('click', clearPhotoSelection);
document.getElementById('photo-modal-close')?.addEventListener('click', closePhotoModal);
document.getElementById('photo-modal')?.addEventListener('click', event => {
  if (event.target.id === 'photo-modal') closePhotoModal();
});
document.getElementById('photo-sort')?.addEventListener('change', event => {
  setPhotoModalSort(event.target.value);
});
document.getElementById('photo-shuffle')?.addEventListener('click', () => {
  setPhotoModalSort('random');
});
document.addEventListener('keydown', event => {
  if (event.key === 'Escape') {
    closePhotoModal();
    setFiltersOpen(false);
  }
});

document.getElementById('exit-app')?.addEventListener('click', async () => {
  try {
    await fetch('/api/shutdown', { method: 'POST', keepalive: true });
  } catch (_) {
    // Le serveur peut déjà être arrêté.
  }
  window.open('', '_self');
  window.close();
  setTimeout(() => {
    document.body.innerHTML =
      '<main style="display:grid;place-items:center;height:100vh;font-family:sans-serif;background:#171a27;color:#edf0f7">' +
      '<div style="text-align:center"><h1>Serveur arrêté</h1><p>Vous pouvez fermer cette fenêtre.</p></div></main>';
  }, 250);
});

let databaseTotalFetchedAt = 0;
async function loadDatabaseTotal({ force = false } = {}) {
  const element = document.getElementById('database-count');
  if (!element) return;
  if (!force && Date.now() - databaseTotalFetchedAt < 15000) return;
  databaseTotalFetchedAt = Date.now();
  try {
    const response = await fetch('/api/health', { cache: 'no-store' });
    if (!response.ok) throw new Error('Base indisponible');
    const data = await response.json();
    const total = Number(data.tables?.photos ?? 0);
    element.innerHTML = `<strong>${total.toLocaleString('fr-FR')}</strong> photo${total > 1 ? 's' : ''} dans la base`;
    element.hidden = false;
  } catch (_) {
    element.textContent = 'Base indisponible';
    element.hidden = false;
  }
}
window.addEventListener('focus', () => loadDatabaseTotal());

// Initialiser le mode par défaut au chargement
async function preferredSelectApp() {
  try {
    const data = await fetch('/api/preferences').then(r => r.json());
    return typeof data.defaultApp === 'string' ? data.defaultApp : null;
  } catch (_) {
    return null;
  }
}

async function initializeDefaultMode() {
  if (!Object.keys(datasets).length) await initializeDatasets();
  const defaultDataset = thesaurusConfig?.defaultThesaurus || 'family';
  const fallback = defaultDataset === 'fauna' && datasets.fauna ? 'faune' : 'face';
  const preferred = await preferredSelectApp();
  let initialApp = APP_MODES[preferred] ? preferred : fallback;
  const dataset = APP_MODES[initialApp]?.dataset;
  if (dataset && !datasets[dataset]) initialApp = fallback;
  await activateAppMode(initialApp);
}

// Charger les lieux au démarrage
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', () => {
    loadDatabaseTotal({ force: true });
    loadLocationHierarchy();
    initializeFilters();
    initializeDefaultMode();
  });
} else {
  loadDatabaseTotal({ force: true });
  loadLocationHierarchy();
  initializeFilters();
  initializeDefaultMode();
}
