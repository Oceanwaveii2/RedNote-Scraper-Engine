// Named visual arrangements. These snapshots never replace node content or connections.
(() => {
  const key = 'scatter-graph-layouts-v1';
  const wrap = el('div', 'graph-layout-control');
  const toggle = el('button', '', '▦ Layouts');
  toggle.type = 'button';
  toggle.setAttribute('aria-expanded', 'false');
  toggle.setAttribute('aria-controls', 'graphLayoutMenu');
  const menu = el('section', 'graph-layout-menu');
  menu.id = 'graphLayoutMenu';
  menu.hidden = true;
  menu.setAttribute('aria-label', 'Saved node layouts');
  const title = el('h3', '', 'Saved layouts');
  const note = el('p', '', 'Save node positions, sizes, wire shapes and the view. Profile data and connections stay as they are.');
  const name = el('input');
  name.type = 'text';
  name.maxLength = 70;
  name.placeholder = 'Layout name';
  name.setAttribute('aria-label', 'Name this node layout');
  const save = el('button', 'primary', 'Save current layout');
  save.type = 'button';
  const list = el('div', 'graph-layout-list');
  const message = el('output', 'graph-layout-message');
  message.setAttribute('role', 'status');
  const exportButton = el('button', '', 'Export layouts');
  exportButton.type = 'button';
  const importButton = el('button', '', 'Import layouts');
  importButton.type = 'button';
  const importFile = el('input');
  importFile.type = 'file';
  importFile.accept = 'application/json,.json';
  importFile.hidden = true;
  const actions = el('div', 'graph-layout-actions');
  actions.append(exportButton, importButton, importFile);
  menu.append(title, note, name, save, list, actions, message);
  wrap.append(toggle, menu);
  $('#reset').before(wrap);

  const close = () => { menu.hidden = true; toggle.setAttribute('aria-expanded', 'false'); };
  const read = () => {
    try {
      const saved = JSON.parse(localStorage.getItem(key) || '[]');
      return Array.isArray(saved) ? saved.filter(item => item?.version === 1 && typeof item.id === 'string' && Array.isArray(item.geometry)) : [];
    } catch { return []; }
  };
  const write = entries => { localStorage.setItem(key, JSON.stringify(entries)); };
  const finite = value => typeof value === 'number' && Number.isFinite(value);
  const capture = label => {
    if (scene) sceneMode(false);
    const center = { x: (viewport.clientWidth / 2 - pan.x) / scale, y: (viewport.clientHeight / 2 - pan.y) / scale };
    return {
      version: 1, id: crypto.randomUUID(), name: label, createdAt: new Date().toISOString(),
      geometry: nodes.map(n => ({ id: n.id, x: n.x, y: n.y, w: n.w, h: n.h })),
      groups: structuredClone(layout.groups || []),
      wireShapes: layout.edges.map(edge => ({ from: edge[0], to: edge[1], style: edge[3]?.style, bend: edge[3]?.bend, fromPort: edge[3]?.fromPort, toPort: edge[3]?.toPort })),
      camera: { center, scale }
    };
  };
  const render = () => {
    list.replaceChildren();
    const entries = read().sort((a, b) => String(b.createdAt || '').localeCompare(String(a.createdAt || '')));
    if (!entries.length) list.append(el('p', 'graph-layout-empty', 'No layouts saved yet. The current board remains your default.'));
    for (const item of entries) {
      const row = el('div', 'graph-layout-row');
      const info = el('div', 'graph-layout-info');
      info.append(el('b', '', item.name), el('small', '', `${item.geometry.length} nodes · ${item.createdAt ? new Date(item.createdAt).toLocaleString() : 'Imported layout'}`));
      const load = el('button', '', 'Restore');
      load.type = 'button';
      load.setAttribute('aria-label', `Restore layout ${item.name}`);
      load.onclick = () => restore(item);
      const remove = el('button', 'graph-layout-remove', '×');
      remove.type = 'button';
      remove.setAttribute('aria-label', `Delete saved layout ${item.name}`);
      remove.title = 'Delete this saved layout';
      remove.onclick = () => {
        try { write(read().filter(saved => saved.id !== item.id)); render(); message.textContent = `Deleted saved layout “${item.name}”. The board was not changed.`; }
        catch { message.textContent = 'Could not delete the saved layout from browser storage.'; }
      };
      row.append(info, load, remove);
      list.append(row);
    }
  };
  const restore = item => {
    if (!nodes?.length || !layout) return;
    if (scene) sceneMode(false);
    const positions = new Map(item.geometry.filter(v => typeof v?.id === 'string').map(v => [v.id, v]));
    const matched = nodes.filter(n => positions.has(n.id));
    if (!matched.length) { message.textContent = 'This layout has no nodes in common with the current board.'; return; }
    const before = boardSnapshot();
    for (const n of matched) {
      const saved = positions.get(n.id);
      if (finite(saved.x) && finite(saved.y)) { n.x = saved.x; n.y = saved.y; }
      if (finite(saved.w) && saved.w > 0 && finite(saved.h) && saved.h > 0) { n.w = saved.w; n.h = saved.h; }
    }
    if (Array.isArray(item.groups)) layout.groups = structuredClone(item.groups);
    if (Array.isArray(item.wireShapes)) {
      const shapes = new Map(item.wireShapes.map(shape => [`${shape.from}\u0000${shape.to}`, shape]));
      for (const edge of layout.edges) {
        const shape = shapes.get(`${edge[0]}\u0000${edge[1]}`);
        if (!shape) continue;
        edge[3] ||= {};
        for (const field of ['style', 'bend', 'fromPort', 'toPort']) {
          if (shape[field] === undefined) delete edge[3][field];
          else edge[3][field] = shape[field];
        }
      }
    }
    layout.width = Math.max(1, ...nodes.map(n => n.x + Math.max(n.w || 0, 1) + 200));
    layout.height = Math.max(1, ...nodes.map(n => n.y + Math.max(n.h || 0, 1) + 200));
    for (const n of nodes) sourcePositions[n.id] = [n.x, n.y, n.w, n.h];
    mount();
    persist();
    saveConnections();
    try { localStorage.setItem('rednote-pdf-groups-v1', JSON.stringify(layout.groups)); } catch {}
    recordBoardChange(before);
    const camera = item.camera;
    if (camera && finite(camera.scale) && finite(camera.center?.x) && finite(camera.center?.y)) {
      scale = Math.max(.12, Math.min(1.7, camera.scale));
      pan = { x: viewport.clientWidth / 2 - camera.center.x * scale, y: viewport.clientHeight / 2 - camera.center.y * scale };
      transform();
    } else fit();
    const unmatched = item.geometry.length - matched.length;
    message.textContent = `Restored “${item.name}” · ${matched.length} nodes${unmatched ? ` · ${unmatched} saved nodes absent` : ''}. Other node data and connections were preserved. Undo is available.`;
    status(message.textContent);
    close();
  };
  save.onclick = () => {
    if (!nodes?.length || !layout) { message.textContent = 'The board is still loading.'; return; }
    const label = name.value.trim();
    if (!label) { message.textContent = 'Name the layout before saving.'; name.focus(); return; }
    try { const entries = read(); entries.push(capture(label)); write(entries); name.value = ''; render(); message.textContent = `Saved “${label}” in this browser.`; status(message.textContent); }
    catch { message.textContent = 'Could not save the layout in browser storage.'; }
  };
  name.addEventListener('keydown', event => { if (event.key === 'Enter') { event.preventDefault(); save.click(); } });
  exportButton.onclick = () => {
    const url = URL.createObjectURL(new Blob([JSON.stringify({ format: 'scatter-graph-layouts', version: 1, layouts: read() }, null, 2)], { type: 'application/json' }));
    const link = document.createElement('a'); link.href = url; link.download = 'scatter-graph-layouts.json'; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  importButton.onclick = () => importFile.click();
  importFile.onchange = async () => {
    const file = importFile.files?.[0]; if (!file) return;
    try {
      const content = JSON.parse(await file.text());
      if (content?.format !== 'scatter-graph-layouts' || content.version !== 1 || !Array.isArray(content.layouts)) throw Error('Not a Scatter layout export.');
      const incoming = content.layouts.filter(item => item?.version === 1 && typeof item.name === 'string' && Array.isArray(item.geometry) && item.geometry.every(v => typeof v?.id === 'string' && finite(v.x) && finite(v.y) && finite(v.w) && finite(v.h)));
      if (incoming.length !== content.layouts.length) throw Error('One or more saved layouts are invalid.');
      const entries = read(), ids = new Set(entries.map(item => item.id));
      for (const item of incoming) { const copy = structuredClone(item); if (typeof copy.id !== 'string' || ids.has(copy.id)) copy.id = crypto.randomUUID(); ids.add(copy.id); entries.push(copy); }
      write(entries); render(); message.textContent = `Imported ${incoming.length} saved layout${incoming.length === 1 ? '' : 's'}. Select Restore to apply one.`;
    } catch (error) { message.textContent = error.message || 'Could not import layouts.'; }
    importFile.value = '';
  };
  toggle.onclick = () => { const open = menu.hidden; menu.hidden = !open; toggle.setAttribute('aria-expanded', String(open)); if (open) { render(); name.focus(); } };
  document.addEventListener('pointerdown', event => { if (!wrap.contains(event.target)) close(); });
  document.addEventListener('keydown', event => { if (event.key === 'Escape' && !menu.hidden) { close(); toggle.focus(); } });
})();
