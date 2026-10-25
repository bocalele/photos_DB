let state = null;
let selectedKeywords = new Set();
let minRating = 0;
let yearMin = null;
let yearMax = null;
let keywordsModeInitialized = false;
let filtersInitialized = false;
let currentMode = 'graph'; // default mode
let currentAppMode = 'face';
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
    .on('zoom', e => gRoot.attr('transform', e.transform));
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
    .on('click', onNodeClick);
  enter.append('text')
    .attr('class', 'photo-count')
    .attr('dy', '0.3em')
    .text(d => state.photoCounts[d.id] || 0);
  enter.append('text')
    .attr('dy', cfg.nodeRadius + 14)
    .text(d => labelFor(state.nodesById[d.id]));
  nodeSel = enter.merge(nodeSel);

  nodeSel.classed('self', d => d.id === state.selfId);
  nodeSel.select('circle')
    .attr('fill', d => state.clusterColor[state.cluster[d.id]])
    .attr('stroke', d => d.id === state.selfId ? 'var(--self)' : '#1a1d2e')
    .attr('stroke-width', d => d.id === state.selfId ? 4 : 2.5);

  simulation.nodes(nodes);
  simulation.force('link').links(links);
  simulation.alpha(0.6).restart();
  if (currentMode === 'graph') renderQuery();
}

function taxonDisplayName(taxon) {
  const name = taxon?.name || '';
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
  if (taxon) return taxon.name || taxon.scientific;
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
  // Pour le thésaurus, garder les noms entiers ; pour la famille, juste le prénom
  if (state.cfg === datasets.family) return node.name.split(' ')[0];
  return displayNodeName(node);
}

function ticked() {
  linkSel
    .attr('x1', d => d.source.x).attr('y1', d => d.source.y)
    .attr('x2', d => d.target.x).attr('y2', d => d.target.y);
  nodeSel.attr('transform', d => `translate(${d.x},${d.y})`);
}

// =========================================================
//  TOOLTIP
// =========================================================
const tooltip = () => document.getElementById('tooltip');
function onHover(event, d) {
  const n = state.nodesById[d.id];
  const cid = state.cluster[d.id];
  const tip = tooltip();
  tip.innerHTML = `
    <div class="name">${n.name}</div>
    ${state.cfg.formatTooltip(n)}
    <div class="branch" style="background:${state.clusterColor[cid]}">${state.clusterLabel[cid]}</div>
  `;
  tip.style.opacity = 1;
  onHoverMove(event);
}
function onHoverMove(event) {
  const rect = document.getElementById('graph-container').getBoundingClientRect();
  tooltip().style.left = (event.clientX - rect.left + 14) + 'px';
  tooltip().style.top  = (event.clientY - rect.top + 14) + 'px';
}
function onHoverOut() { tooltip().style.opacity = 0; }

// =========================================================
//  MENU CONTEXTUEL
// =========================================================
const menuEl = () => document.getElementById('menu');
function onNodeClick(event, d) { event.stopPropagation(); showMenuFor(d, event); }
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
    <div class="menu-title">${displayNodeName(state.nodesById[id])}</div>
    ${btn('parents',  labels.parents,  expanded.parents,  groups.parents.length)}
    ${btn('children', labels.children, expanded.children, groups.children.length)}
    ${btn('siblings', labels.siblings, expanded.siblings, groups.siblings.length)}
    ${btn('spouse',   labels.spouse,   expanded.spouse,   groups.spouse.length)}
    <hr/>
    ${btn('all', 'Tout', allExp, allTotal)}
    <hr/>
    <button data-group="hide"><span><span class="icon">✖</span>Cacher ce nœud</span></button>
    <button class="set-self" data-group="self"><span><span class="icon">★</span>Définir comme ${state.cfg.selfLabel.toLowerCase()}</span></button>
  `;
  menu.querySelectorAll('button[data-group]').forEach(b => {
    b.addEventListener('click', e => {
      e.stopPropagation();
      const group = b.dataset.group;
      if (group === 'self') {
        state.selfId = id;
        document.getElementById('self-select').value = id;
        const node = state.nodesById[id];
        if (node?.classId) {
          const groupSelect = document.getElementById('group-select');
          if ([...groupSelect.options].some(option => option.value === node.classId)) {
            groupSelect.value = node.classId;
          }
        }
        state.visible.add(id);
        update();
        setTimeout(centerOnSelf, 180);
      } else if (group === 'hide') {
        hideNode(id);
      } else {
        toggleGroup(id, group);
      }
      hideMenu();
    });
  });

  let x = event.clientX + 12;
  let y = event.clientY + 12;
  menu.style.position = 'fixed';
  menu.style.display = 'block';
  console.log('Menu displayed at', x, y);
  menu.style.left = x + 'px';
  menu.style.top = y + 'px';
  
  // Use requestAnimationFrame to allow DOM to render before measuring
  requestAnimationFrame(() => {
    const menuRect = menu.getBoundingClientRect();
    const defaultWidth = 240;
    const defaultHeight = 320;
    const menuWidth = menuRect.width || defaultWidth;
    const menuHeight = menuRect.height || defaultHeight;
    
    if (x + menuWidth > window.innerWidth) x = Math.max(10, window.innerWidth - menuWidth - 10);
    if (y + menuHeight > window.innerHeight) y = Math.max(10, window.innerHeight - menuHeight - 10);
    
    menu.style.left = x + 'px';
    menu.style.top = y + 'px';
  });
}
function hideMenu() { menuEl().style.display = 'none'; }

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
function hideNode(id) {
  if (id === state.selfId) return;
  state.visible.delete(id);
  pruneOrphans(); 
  update();
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

async function filterPhotos(mode) {
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
    const result = await filterPhotos(currentMode);
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

function selectAllCurrentPhotos() {
  for (const photo of (currentFilterResult.photos || []).slice(0, 1000)) {
    const photoId = Number(photo.id);
    selectedPhotoIds.add(photoId);
    selectedPhotoCache.set(photoId, photo);
  }
  updateAlbumSelectionUI();
}

function clearPhotoSelection() {
  selectedPhotoIds.clear();
  selectedPhotoCache.clear();
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

  const shown = photos.slice(0, 1000);
  photoModalShown = shown.length;
  updatePhotoModalCount();
  grid.innerHTML = '';

  if (!photos.length && !total) {
    grid.innerHTML = '<div class="photo-empty">Aucune photo ne correspond à cette sélection.</div>';
  } else {
    for (const photo of shown) {
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
        togglePhotoSelection(photo);
      });
      card.addEventListener('click', event => {
        if (event.target.closest('button')) return;
        togglePhotoSelection(photo);
      });
      card.querySelector('img').addEventListener('error', event => {
        event.currentTarget.replaceWith(Object.assign(document.createElement('div'), {
          className: 'photo-missing',
          textContent: 'Miniature indisponible',
        }));
      });
      grid.appendChild(card);
    }
  }

  updateAlbumSelectionUI();
  modal.classList.add('open');
  modal.setAttribute('aria-hidden', 'false');
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
document.addEventListener('keydown', event => {
  if (event.key === 'Escape') {
    closePhotoModal();
    setFiltersOpen(false);
  }
});

// Initialiser le mode par défaut au chargement
async function initializeDefaultMode() {
  if (!Object.keys(datasets).length) await initializeDatasets();
  const defaultDataset = thesaurusConfig?.defaultThesaurus || 'family';
  const initialApp = defaultDataset === 'fauna' && datasets.fauna ? 'faune' : 'face';
  await activateAppMode(initialApp);
}

// Charger les lieux au démarrage
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', () => {
    loadLocationHierarchy();
    initializeFilters();
    initializeDefaultMode();
  });
} else {
  loadLocationHierarchy();
  initializeFilters();
  initializeDefaultMode();
}
