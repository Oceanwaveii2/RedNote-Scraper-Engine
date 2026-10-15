/* Explicit, revision-checked projection of authored board wires into the graph. */
(() => {
  'use strict';
  const sidebar = document.querySelector('#boardSidebar');
  if (!sidebar) return;
  const endpoint = 'http://127.0.0.1:5189/api/scatter/v1';
  const section = document.createElement('section');
  section.style.cssText = 'margin:12px 0;padding:12px;border:1px solid #66404b;border-radius:10px;background:#16151b;color:#f5edf0';
  const title = document.createElement('b');
  title.textContent = 'TYPED WIRE SYNC';
  const description = document.createElement('p');
  description.textContent = 'Map bound board wires to engine ports. Review the graph changes before applying.';
  const prepare = document.createElement('button');
  prepare.textContent = 'Prepare wire mapping';
  const status = document.createElement('p');
  status.setAttribute('role', 'status');
  const rows = document.createElement('div');
  const preview = document.createElement('div');
  section.append(title, description, prepare, status, rows, preview);
  sidebar.append(section);

  let graph = null;
  let bindings = null;
  let mappedRows = [];
  let pending = null;
  let pendingEdges = null;
  let projectId = '';

  async function api(path, data) {
    const response = await fetch(endpoint + path, data === undefined ? {} : {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data)
    });
    const value = await response.json();
    if (!response.ok) throw Error(value.error || 'Engine request failed');
    return value;
  }

  function button(label, handler) {
    const element = document.createElement('button');
    element.textContent = label;
    element.addEventListener('click', async () => {
      element.disabled = true;
      try { await handler(); }
      catch (error) { status.textContent = error.message; }
      finally { element.disabled = false; }
    });
    return element;
  }

  function portOptions(select, node, direction, preferredType = null) {
    select.replaceChildren();
    for (const [id, port] of Object.entries(node.ports || {})) {
      if (port.direction !== direction) continue;
      const option = document.createElement('option');
      option.value = id;
      option.textContent = `${id} · ${port.type}`;
      select.append(option);
      if (preferredType && port.type === preferredType) select.value = id;
    }
  }

  async function stableEdgeId(edge) {
    const bytes = new TextEncoder().encode(`${edge[0]}\u0000${edge[1]}`);
    const digest = await crypto.subtle.digest('SHA-256', bytes);
    return 'visual-' + Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('');
  }

  async function selectedMappings() {
    const result = [];
    for (const item of mappedRows) {
      if (!item.from.value || !item.to.value) throw Error('Choose both typed ports for every mapped wire');
      result.push({
        ui_edge_id: await stableEdgeId(item.edge),
        from_ui_node_id: item.edge[0], from_port: item.from.value,
        to_ui_node_id: item.edge[1], to_port: item.to.value
      });
    }
    return result;
  }

  function clearPreview() {
    pending = null;
    pendingEdges = null;
    preview.replaceChildren();
  }

  prepare.addEventListener('click', async () => {
    prepare.disabled = true;
    try {
      clearPreview();
      rows.replaceChildren();
      mappedRows = [];
      projectId = localStorage.getItem('scatter-engine-project-id') || '';
      if (!projectId) throw Error('Load an engine project in the Scatter Engine panel first');
      if (typeof layout === 'undefined' || !layout?.edges || typeof nodes === 'undefined')
        throw Error('Open a board before mapping its wires');
      graph = await api('/graph?project_id=' + encodeURIComponent(projectId));
      bindings = await api('/bindings?project_id=' + encodeURIComponent(projectId));
      const bound = new Map(bindings.bindings.filter(item => item.semantic_node_exists)
        .map(item => [item.ui_node_id, item.semantic_node_id]));
      let skipped = 0;
      for (const edge of layout.edges) {
        if (edge[3]?.active === false) continue;
        if ((edge[3]?.direction || 'forward') !== 'forward') { skipped++; continue; }
        const fromNode = graph.snapshot.nodes[bound.get(edge[0])];
        const toNode = graph.snapshot.nodes[bound.get(edge[1])];
        if (!fromNode || !toNode) { skipped++; continue; }
        const row = document.createElement('div');
        row.style.cssText = 'padding:8px;margin:6px 0;border:1px solid #51424a;border-radius:8px';
        const label = document.createElement('div');
        label.textContent = `${edge[0]} → ${edge[1]} · ${edge[2] || 'connection'}`;
        const from = document.createElement('select');
        const to = document.createElement('select');
        from.setAttribute('aria-label', `Output port for ${edge[0]} to ${edge[1]}`);
        to.setAttribute('aria-label', `Input port for ${edge[0]} to ${edge[1]}`);
        portOptions(from, fromNode, 'out');
        const selectedType = fromNode.ports?.[from.value]?.type;
        portOptions(to, toNode, 'in', selectedType);
        from.addEventListener('change', () => {
          const type = fromNode.ports?.[from.value]?.type;
          const matching = Object.entries(toNode.ports || {}).find(([, port]) =>
            port.direction === 'in' && port.type === type);
          if (matching) to.value = matching[0];
          clearPreview();
        });
        to.addEventListener('change', clearPreview);
        row.append(label, from, to);
        rows.append(row);
        mappedRows.push({ edge, from, to });
      }
      status.textContent = `${mappedRows.length} bound forward wires · ${skipped} skipped (unbound or reverse) · graph revision ${graph.revision}`;
      if (!mappedRows.length) return;
      rows.append(button('Preview graph changes', async () => {
        clearPreview();
        pendingEdges = await selectedMappings();
        pending = await api('/edge-plan', {
          project_id: projectId, expected_graph_revision: graph.revision,
          visual_edges: pendingEdges
        });
        const summary = document.createElement('p');
        summary.textContent = `${pending.add.length} connections to add · ${pending.remove.length} board-owned connections to remove`;
        preview.append(summary);
        for (const edge of pending.add) {
          const line = document.createElement('p');
          line.textContent = `+ ${edge.from_node}.${edge.from_port} → ${edge.to_node}.${edge.to_port}`;
          preview.append(line);
        }
        for (const edgeId of pending.remove) {
          const line = document.createElement('p');
          line.textContent = `− ${edgeId}`;
          preview.append(line);
        }
        if (!pending.changed) return;
        preview.append(button('Apply reviewed changes', async () => {
          const result = await api('/edge-apply', {
            project_id: projectId, expected_graph_revision: pending.graph_revision,
            expected_bindings_hash: pending.bindings_hash,
            command_id: crypto.randomUUID(), visual_edges: pendingEdges
          });
          status.textContent = `Engine graph revision ${result.revision} · ${result.added} added · ${result.removed} removed`;
          clearPreview();
          graph = await api('/graph?project_id=' + encodeURIComponent(projectId));
        }));
      }));
    } catch (error) { status.textContent = error.message; }
    finally { prepare.disabled = false; }
  });
})();
