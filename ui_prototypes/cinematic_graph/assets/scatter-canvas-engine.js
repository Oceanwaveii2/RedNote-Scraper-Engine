/* Retained board renderer. Keeps authored HTML cards and SVG connection controls. */
(() => {
  'use strict';
  const viewport = document.getElementById('viewport');
  const world = document.getElementById('world');
  const wires = document.getElementById('wires');
  if (!viewport || !world || !wires || !window.ScatterSpatialCore) return;

  const canvas = document.createElement('canvas');
  canvas.id = 'scatter-wire-canvas';
  canvas.setAttribute('aria-hidden', 'true');
  canvas.style.cssText = 'position:absolute;inset:0;width:100%;height:100%;pointer-events:none;z-index:0';
  world.style.zIndex = '1';
  for (const selector of ['.zoom', '#connectHint', '#drop']) {
    const overlay = viewport.querySelector(selector);
    if (overlay) overlay.style.zIndex = '2';
  }
  viewport.insertBefore(canvas, world);
  const context = canvas.getContext('2d', { alpha: true, desynchronized: true });
  if (!context) { canvas.remove(); return; }

  const spatial = new window.ScatterSpatialCore();
  const nodeById = new Map();
  const hitPaths = new Map();
  const materialized = new Set();
  let desiredCards = new Set();
  let virtualizeCards = false;
  const domCardBudget = 32;
  let edgeAdjacency = new Map();
  let indexedEdges = null;
  let renderedEdges = [];
  let frame = 0;
  let lastVisible = 0;
  let lastEdges = 0;
  let lastPaintMs = 0;
  let needsSpatialSync = false;
  let overviewClusters = [];
  wires.replaceChildren();

  function rebuildEdges() {
    if (!layout?.edges) return;
    indexedEdges = layout.edges;
    edgeAdjacency = new Map();
    const source = scene ? [
      ['actor', 'main', 'actor / alternative form'],
      ['tori', 'main', 'video reference'],
      ['main', 'main-output', 'text output'],
      ['main-output', 'junction', 'story fork'],
      ['junction', 'adela', 'video branch'],
      ['main', 'lip1', 'via photo profile'],
      ['junction', 'lip2', 'via animation profile'],
      ['junction', 'lip3', 'via animation profile'],
      ['lip3', 'vanity', 'after object 03'],
      ['vanity', 'junction', 'reference contribution']
    ] : layout.edges;
    const seen = new Set();
    renderedEdges = [];
    for (const original of source) {
      const endpoint = id => {
        const node = nodeById.get(id);
        if (!node?.groupedIn) return id;
        const owner = nodeById.get(node.groupedIn);
        if (!owner) return id;
        return node.kind === 'output' ? (groupedOutput(owner)?.id || owner.id) : owner.id;
      };
      const from = endpoint(original[0]);
      const to = endpoint(original[1]);
      if (from === to || !nodeById.has(from) || !nodeById.has(to)) continue;
      if (scene && (!sceneLayout[from] || !sceneLayout[to])) continue;
      const key = `${from}>${to}`;
      if (seen.has(key)) continue;
      seen.add(key);
      renderedEdges.push({ edge: [from, to, original[2], original[3]], original });
    }
    renderedEdges.forEach(({ edge }, index) => {
      for (const id of [edge[0], edge[1]]) {
        let adjacency = edgeAdjacency.get(id);
        if (!adjacency) edgeAdjacency.set(id, adjacency = new Set());
        adjacency.add(index);
      }
    });
  }

  function syncMovingNodes() {
    if (!nodes) return;
    if (drag?.id && typeof ownedBranch === 'function') {
      for (const node of ownedBranch(drag.id)) spatial.upsert(node.id, node.x, node.y, node.w, node.h);
    } else if (drag?.id) {
      const node = nodeById.get(drag.id);
      if (node) spatial.upsert(node.id, node.x, node.y, node.w, node.h);
    } else if (active && typeof ownedBranch === 'function') {
      for (const node of ownedBranch(active)) spatial.upsert(node.id, node.x, node.y, node.w, node.h);
    } else if (active) {
      const node = nodeById.get(active);
      if (node) spatial.upsert(node.id, node.x, node.y, node.w, node.h);
    }
  }

  function resize() {
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const width = viewport.clientWidth;
    const height = viewport.clientHeight;
    const pixelWidth = Math.max(1, Math.round(width * dpr));
    const pixelHeight = Math.max(1, Math.round(height * dpr));
    if (canvas.width !== pixelWidth || canvas.height !== pixelHeight) {
      canvas.width = pixelWidth;
      canvas.height = pixelHeight;
    }
    return { dpr, width, height };
  }

  function chooseCards(width, height) {
    if (!virtualizeCards) return new Set(nodeById.keys());
    const wanted = new Set();
    if (scale < 0.25) {
      if (active && nodeById.has(active) && !nodeById.get(active).groupedIn) wanted.add(active);
      return wanted;
    }
    const centerX = (width / 2 - pan.x) / scale;
    const centerY = (height / 2 - pan.y) / scale;
    const candidates = spatial.query(-pan.x / scale, -pan.y / scale,
      width / scale, height / scale, 180 / scale)
      .filter(id => !nodeById.get(id)?.groupedIn && (!scene || sceneLayout[id]));
    candidates.sort((leftId, rightId) => {
      const a = nodeById.get(leftId), b = nodeById.get(rightId);
      const ad = Math.hypot(a.x + a.w / 2 - centerX, a.y + a.h / 2 - centerY);
      const bd = Math.hypot(b.x + b.w / 2 - centerX, b.y + b.h / 2 - centerY);
      return ad - bd;
    });
    for (const id of candidates.slice(0, domCardBudget)) wanted.add(id);
    if (active && nodeById.has(active) && !nodeById.get(active).groupedIn) wanted.add(active);
    return wanted;
  }

  function applyCardVisibility() {
    if (!virtualizeCards) return;
    for (const id of materialized) {
      if (desiredCards.has(id)) continue;
      document.getElementById(id)?.remove();
      materialized.delete(id);
    }
    for (const id of desiredCards) {
      if (materialized.has(id)) continue;
      const node = nodeById.get(id);
      if (!node) continue;
      priorCard(node);
      const element = document.getElementById(id);
      if (!element) continue;
      element.style.contentVisibility = 'auto';
      element.style.containIntrinsicSize = `${Math.max(1, node.w)}px ${Math.max(1, node.h)}px`;
      element.classList.toggle('selected', id === active);
      materialized.add(id);
    }
  }

  function paintOverview(visibleIds) {
    overviewClusters = [];
    if (!virtualizeCards) return;
    const proxyIds = visibleIds.filter(id => !materialized.has(id) &&
      !nodeById.get(id)?.groupedIn && (!scene || sceneLayout[id]));
    if (proxyIds.length > 2000 || scale < 0.25) {
      const screenCell = 130 / scale;
      const clusters = new Map();
      for (const id of proxyIds) {
        const node = nodeById.get(id);
        if (!node) continue;
        const cx = Math.floor((node.x + node.w / 2) / screenCell);
        const cy = Math.floor((node.y + node.h / 2) / screenCell);
        const key = `${cx},${cy}`;
        const cluster = clusters.get(key);
        if (cluster) cluster.count++;
        else clusters.set(key, { x: cx * screenCell, y: cy * screenCell, count: 1 });
      }
      context.font = `${13 / scale}px Segoe UI, sans-serif`;
      for (const cluster of clusters.values()) {
        overviewClusters.push({ x: cluster.x + 8 / scale, y: cluster.y + 8 / scale,
          w: 108 / scale, h: 88 / scale });
        context.fillStyle = '#202737';
        context.strokeStyle = '#ff496a';
        context.lineWidth = 1.5 / scale;
        context.fillRect(cluster.x + 8 / scale, cluster.y + 8 / scale, 108 / scale, 88 / scale);
        context.strokeRect(cluster.x + 8 / scale, cluster.y + 8 / scale, 108 / scale, 88 / scale);
        context.fillStyle = '#f6e6eb';
        context.fillText(`${cluster.count} nodes`, cluster.x + 18 / scale, cluster.y + 56 / scale);
      }
      return;
    }
    for (const id of proxyIds) {
      const node = nodeById.get(id);
      if (!node) continue;
      context.fillStyle = '#19202e';
      context.strokeStyle = node.kind === 'identity' ? '#28b6ff' : '#ff496a';
      context.lineWidth = 1.5 / scale;
      context.fillRect(node.x, node.y, node.w, node.h);
      context.strokeRect(node.x, node.y, node.w, node.h);
      if (scale >= 0.45) {
        context.fillStyle = '#f3e8ed';
        context.font = '17px Segoe UI, sans-serif';
        context.fillText(String(node.title || id).slice(0, 35), node.x + 12, node.y + 28);
      }
    }
  }

  function pathFor(edge, fromNode, toNode) {
    const meta = edge[3] || {};
    const backward = toNode.x + toNode.w < fromNode.x;
    const down = toNode.y > fromNode.y + fromNode.h + 60;
    const fromSide = meta.fromPort || (down ? 'bottom' : backward ? 'left' : 'right');
    const toSide = meta.toPort || (down ? 'top' : backward ? 'right' : 'left');
    const from = graphPortPoint(fromNode, fromSide);
    const to = graphPortPoint(toNode, toSide);
    return { d: wireGeometry(from.x, from.y, to.x, to.y, down, backward, meta), from, to, down };
  }

  function arrow(point, other, color) {
    const angle = Math.atan2(point.y - other.y, point.x - other.x);
    context.save();
    context.translate(point.x, point.y);
    context.rotate(angle);
    context.fillStyle = color;
    context.beginPath();
    context.moveTo(0, 0);
    context.lineTo(-12 / Math.max(scale, 0.15), -5 / Math.max(scale, 0.15));
    context.lineTo(-12 / Math.max(scale, 0.15), 5 / Math.max(scale, 0.15));
    context.closePath();
    context.fill();
    context.restore();
  }

  function updateHit(index, edge, d) {
    let hit = hitPaths.get(index);
    if (!hit) {
      hit = document.createElementNS('http://www.w3.org/2000/svg', 'path');
      hit.setAttribute('class', 'wire-hit');
      hit.setAttribute('tabindex', '0');
      hit.setAttribute('role', 'button');
      hit.addEventListener('click', () => openConnection(renderedEdges[index]?.original));
      hit.addEventListener('keydown', event => {
        if (event.key === 'Enter') { event.preventDefault(); openConnection(renderedEdges[index]?.original); }
      });
      wires.append(hit);
      hitPaths.set(index, hit);
    }
    if (hit.getAttribute('d') !== d) hit.setAttribute('d', d);
    hit.setAttribute('aria-label', `Edit connection ${edge[0]} to ${edge[1]}`);
    return hit;
  }

  function paint() {
    frame = 0;
    if (!layout || !nodes || !Number.isFinite(scale) || scale <= 0) return;
    const started = performance.now();
    if (needsSpatialSync) {
      syncMovingNodes();
      needsSpatialSync = false;
    }
    if (indexedEdges !== layout.edges) rebuildEdges();
    const { dpr, width, height } = resize();
    const x = -pan.x / scale;
    const y = -pan.y / scale;
    const worldWidth = width / scale;
    const worldHeight = height / scale;
    const visibleIds = spatial.query(x, y, worldWidth, worldHeight, 450 / scale)
      .filter(id => !nodeById.get(id)?.groupedIn && (!scene || sceneLayout[id]));
    const visibleEdges = new Set();
    if (scale >= 0.25) {
      for (const id of visibleIds) {
        for (const edgeIndex of edgeAdjacency.get(id) || []) visibleEdges.add(edgeIndex);
      }
    }

    context.setTransform(1, 0, 0, 1, 0, 0);
    context.clearRect(0, 0, canvas.width, canvas.height);
    context.setTransform(dpr * scale, 0, 0, dpr * scale, dpr * pan.x, dpr * pan.y);
    desiredCards = chooseCards(width, height);
    applyCardVisibility();
    paintOverview(visibleIds);
    context.lineWidth = 3 / Math.max(scale, 0.15);
    context.lineCap = 'round';
    context.lineJoin = 'round';
    const usedHits = new Set();
    const editBends = document.body.classList.contains('edit-wire-bends');
    if (editBends) wires.querySelectorAll('.wire-bend-handle').forEach(handle => handle.remove());

    for (const index of visibleEdges) {
      const projection = renderedEdges[index];
      if (!projection) continue;
      const { edge, original } = projection;
      const fromNode = nodeById.get(edge[0]);
      const toNode = nodeById.get(edge[1]);
      if (!fromNode || !toNode) continue;
      const geometry = pathFor(edge, fromNode, toNode);
      const music = String(edge[3]?.signal || '').startsWith('music');
      const duduProfile = [fromNode, toNode].some(node =>
        node.story?.ui_template?.kind?.startsWith('dudu-'));
      const color = music ? '#26b4ff' : duduProfile ? '#3bbcff' : '#ff496a';
      const path = new Path2D(geometry.d);
      context.strokeStyle = color;
      context.globalAlpha = edge[3]?.active === false ? 0.3 : 0.85;
      context.setLineDash(flowPreview && edge[3]?.active !== false ? [10 / scale, 25 / scale] : []);
      context.lineDashOffset = flowPreview ? -(performance.now() / 35) / scale : 0;
      context.stroke(path);
      context.setLineDash([]);
      context.globalAlpha = 1;
      const direction = edge[3]?.direction || 'forward';
      if (direction !== 'reverse') arrow(geometry.to, geometry.from, color);
      if (direction !== 'forward') arrow(geometry.from, geometry.to, color);
      const hit = updateHit(index, edge, geometry.d);
      usedHits.add(index);
      if (editBends) addWireController(wires, hit, original, geometry.down);
    }
    for (const [index, hit] of hitPaths) {
      if (usedHits.has(index)) continue;
      hit.remove();
      hitPaths.delete(index);
    }
    lastVisible = visibleIds.length;
    lastEdges = visibleEdges.size;
    lastPaintMs = performance.now() - started;
    if (flowPreview && scale >= 0.25 && !document.hidden) requestPaint(false);
  }

  function requestPaint(sync = true) {
    if (sync) needsSpatialSync = true;
    if (!frame) frame = requestAnimationFrame(paint);
  }

  const priorMount = mount;
  mount = function scatterMount() {
    if (nodes) {
      spatial.replace(nodes);
      nodeById.clear();
      for (const node of nodes) nodeById.set(node.id, node);
    }
    virtualizeCards = Boolean(nodes && nodes.length > 12);
    desiredCards = chooseCards(viewport.clientWidth, viewport.clientHeight);
    materialized.clear();
    priorMount();
    rebuildEdges();
    requestPaint();
  };
  drawWires = requestPaint;
  const priorSaveConnections = saveConnections;
  saveConnections = function scatterSaveConnections() {
    priorSaveConnections();
    rebuildEdges();
    requestPaint();
  };
  const priorCard = card;
  card = function scatterCard(node) {
    spatial.upsert(node.id, node.x, node.y, node.w, node.h);
    nodeById.set(node.id, node);
    if (virtualizeCards && !desiredCards.has(node.id)) return;
    priorCard(node);
    const element = document.getElementById(node.id);
    if (element) {
      element.style.contentVisibility = 'auto';
      element.style.containIntrinsicSize = `${Math.max(1, node.w)}px ${Math.max(1, node.h)}px`;
      materialized.add(node.id);
    }
  };
  const resizeObserver = new ResizeObserver(requestPaint);
  resizeObserver.observe(viewport);
  viewport.addEventListener('pointerdown', event => {
    if (!virtualizeCards || event.button !== 0 || event.target.closest('.node,.zoom,.wire-hit,button,input,textarea,select')) return;
    const bounds = viewport.getBoundingClientRect();
    const px = event.clientX - bounds.left;
    const py = event.clientY - bounds.top;
    const wx = (px - pan.x) / scale;
    const wy = (py - pan.y) / scale;
    if (scale < 0.25) {
      const cluster = overviewClusters.find(box => wx >= box.x && wy >= box.y && wx <= box.x + box.w && wy <= box.y + box.h);
      if (!cluster) return;
      event.preventDefault();
      event.stopImmediatePropagation();
      scale = 0.45;
      pan.x = viewport.clientWidth / 2 - (cluster.x + cluster.w / 2) * scale;
      pan.y = viewport.clientHeight / 2 - (cluster.y + cluster.h / 2) * scale;
      transform();
      requestPaint();
      return;
    }
    const candidates = spatial.query(wx - 5 / scale, wy - 5 / scale, 10 / scale, 10 / scale)
      .filter(id => !nodeById.get(id)?.groupedIn && (!scene || sceneLayout[id]));
    if (!candidates.length) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    focusNode(candidates[0]);
    requestPaint();
  }, true);
  viewport.addEventListener('pointerup', () => { if (nodes) spatial.replace(nodes); requestPaint(); });
  viewport.addEventListener('pointercancel', () => { if (nodes) spatial.replace(nodes); requestPaint(); });
  document.addEventListener('visibilitychange', () => { if (!document.hidden) requestPaint(); });
  window.scatterCanvasEngine = {
    spatial,
    invalidate() {
      spatial.replace(nodes || []);
      nodeById.clear();
      for (const node of nodes || []) nodeById.set(node.id, node);
      rebuildEdges();
      requestPaint();
    },
    metrics() { return { visibleNodes: lastVisible, visibleEdges: lastEdges,
      mountedCards: materialized.size, paintMs: lastPaintMs }; }
  };
  if (typeof nodes !== 'undefined' && nodes) window.scatterCanvasEngine.invalidate();
})();
