/* Narrow prototype bridge. Board layout remains local until semantic nodes are mapped. */
(() => {
  const endpoint = 'http://127.0.0.1:5189/api/scatter/v1';
  const sidebar = document.querySelector('#boardSidebar');
  if (!sidebar) return;
  const section = document.createElement('section');
  section.style.cssText = 'margin:12px 0;padding:12px;border:1px solid #4a6474;border-radius:10px;background:#101821;color:#eaf3f6';
  const title = document.createElement('b'); title.textContent = 'SCATTER ENGINE';
  const note = document.createElement('p');
  note.textContent = 'Attach a semantic project. The visual board is still a separate prototype.';
  const project = document.createElement('input');
  project.placeholder = 'Semantic project ID';
  project.setAttribute('aria-label', 'Scatter semantic project ID');
  project.value = localStorage.getItem('scatter-engine-project-id') ||
    'scatter-50d74973438347d6ac316028292574b3';
  const load = document.createElement('button'); load.textContent = 'Load engine project';
  const status = document.createElement('p'); status.setAttribute('role', 'status');
  const outputs = document.createElement('div');
  const runPanel = document.createElement('div');
  runPanel.style.cssText = 'margin-top:10px;border-top:1px solid #38505e;padding-top:8px';
  section.append(title, note, project, load, status, outputs, runPanel);
  sidebar.append(section);
  const api = async (path, data) => {
    const response = await fetch(endpoint + path, data === undefined ? {} : {
      method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(data)
    });
    const value = await response.json();
    if (!response.ok) throw Error(value.error || 'Engine request failed');
    return value;
  };
  const button = (label, handler) => {
    const element = document.createElement('button'); element.textContent = label;
    element.addEventListener('click', () => handler().catch(error => {status.textContent = error.message;}));
    return element;
  };
  const showRun = async runId => {
    const run = await api('/run?run_id=' + encodeURIComponent(runId));
    status.textContent = `Run ${runId}: ${run.state}`;
    runPanel.replaceChildren();
    const runTitle = document.createElement('b');
    runTitle.textContent = `${run.output_id} · ${run.provider} · ${run.state}`;
    runPanel.append(runTitle);
    const events = await api('/events?run_id=' + encodeURIComponent(runId));
    const trail = document.createElement('p');
    trail.textContent = events.map(item => item.stage).join(' → ');
    runPanel.append(trail);
    if (run.state === 'outcome_unknown' && run.provider === 'gemini') {
      const remote = await api('/gemini-status?run_id=' + encodeURIComponent(runId));
      const message = document.createElement('p');
      message.textContent = `Antigravity transcript: ${remote.state}. No automatic retry.`;
      runPanel.append(message);
    }
    if (run.state === 'failed') {
      const message = document.createElement('p');
      message.textContent = `Engine rejected this run: ${run.error || 'unknown error'}`;
      runPanel.append(message);
    }
    if (run.state === 'waiting_for_selection') {
      const artifact = await api('/artifact?run_id=' + encodeURIComponent(runId) + '&stage=interpretations');
      for (const candidate of artifact.content.candidates) {
        const row = document.createElement('div');
        row.style.cssText = 'padding:8px;margin:8px 0;border:1px solid #38505e;border-radius:8px';
        const description = document.createElement('p'); description.textContent = candidate.logline;
        const bridge = document.createElement('p');
        bridge.textContent = candidate.causal_bridge;
        const roles = document.createElement('small');
        roles.textContent = (candidate.card_roles || []).map(role => `${role.node_id}: ${role.role}`).join(' · ');
        row.append(description, bridge, roles, button('Use ' + candidate.id + ' and queue plan', async () => {
          const child = await api('/select', {run_id: runId, candidate_id: candidate.id,
            expected_revision: run.revision});
          status.textContent = 'Plan queued: ' + child.run_id;
          row.parentElement.querySelectorAll('button').forEach(element => {element.disabled = true;});
          runPanel.append(button('Open plan run', () => showRun(child.run_id)));
        }));
        runPanel.append(row);
      }
    }
  };
  load.addEventListener('click', async () => {
    try {
      const id = project.value.trim();
      const graph = await api('/graph?project_id=' + encodeURIComponent(id));
      localStorage.setItem('scatter-engine-project-id', id);
      status.textContent = `Engine project ${id}, revision ${graph.revision}`;
      const privacy = sidebar.querySelector('.privacy');
      if (privacy) privacy.textContent = 'Scatter engine connected · visual board mapping pending';
      outputs.replaceChildren();
      runPanel.replaceChildren();
      let bindings = await api('/bindings?project_id=' + encodeURIComponent(id));
      const mapping = document.createElement('div');
      mapping.style.cssText = 'margin:10px 0;padding:8px;border:1px solid #38505e;border-radius:8px';
      const mappingTitle = document.createElement('p');
      mappingTitle.textContent = 'Link selected visual card to a semantic node';
      const semanticChoice = document.createElement('select');
      semanticChoice.setAttribute('aria-label', 'Semantic graph node');
      const empty = document.createElement('option'); empty.value = ''; empty.textContent = 'Choose semantic node';
      semanticChoice.append(empty);
      for (const [nodeId, node] of Object.entries(graph.snapshot.nodes)) {
        const option = document.createElement('option');
        option.value = nodeId; option.textContent = `${node.title} · ${node.kind}`;
        semanticChoice.append(option);
      }
      const boundList = document.createElement('div');
      const renderBindings = () => {
        boundList.replaceChildren();
        for (const item of bindings.bindings) {
          const label = document.createElement('p');
          label.textContent = `${item.ui_node_id} → ${item.semantic_node_id}` +
            (item.semantic_node_exists ? '' : ' · missing in current graph');
          boundList.append(label);
        }
      };
      mapping.append(mappingTitle, semanticChoice,
        button('Bind selected card', async () => {
          if (typeof active === 'undefined' || !active || !nodes.some(node => node.id === active))
            throw Error('Select a visual card on the board first');
          if (!semanticChoice.value) throw Error('Choose a semantic node');
          bindings = await api('/binding', {project_id:id, ui_node_id:active,
            semantic_node_id:semanticChoice.value, expected_graph_revision:graph.revision});
          status.textContent = `Linked ${active} to ${semanticChoice.value}; source pins remain in the engine`;
          renderBindings();
        }), button('Unbind selected card', async () => {
          if (typeof active === 'undefined' || !active) throw Error('Select a visual card first');
          bindings = await api('/binding', {project_id:id, ui_node_id:active,
            semantic_node_id:null, expected_graph_revision:graph.revision});
          status.textContent = `Removed engine link for ${active}`;
          renderBindings();
        }), button('Queue selected output', async () => {
          if (typeof active === 'undefined' || !active) throw Error('Select a visual card first');
          const bound = bindings.bindings.find(item => item.ui_node_id === active && item.semantic_node_exists);
          if (!bound || graph.snapshot.nodes[bound.semantic_node_id].kind !== 'output')
            throw Error('Selected visual card is not linked to an engine output');
          const run = await api('/queue-output', {project_id:id, output_id:bound.semantic_node_id});
          status.textContent = `${run.run_id} queued from selected card`;
          outputs.append(button('Open new run', () => showRun(run.run_id)));
        }), boundList);
      renderBindings();
      outputs.append(mapping);
      const branches = await api('/branches?project_id=' + encodeURIComponent(id));
      const recent = await api('/runs?project_id=' + encodeURIComponent(id));
      if (recent.length) {
        const heading = document.createElement('p'); heading.textContent = 'Recent engine runs';
        outputs.append(heading);
        for (const run of recent) outputs.append(button(`${run.output_id} · ${run.state}`, () => showRun(run.id)));
      }
      for (const [branchId, items] of Object.entries(branches.branches)) {
        const heading = document.createElement('p'); heading.textContent = 'Branch: ' + branchId;
        outputs.append(heading);
        outputs.append(button('Queue branch', async () => {
          const runs = await api('/queue-branch', {project_id: id, branch_id: branchId});
          status.textContent = runs.map(run => run.run_id).join(', ') + ' queued';
          runs.forEach(run => outputs.append(button('Open ' + run.output_id, () => showRun(run.run_id))));
        }));
        for (const output of items) outputs.append(button('Queue ' + output.title, async () => {
          const run = await api('/queue-output', {project_id: id, output_id: output.output_id});
          status.textContent = run.run_id + ' queued';
          outputs.append(button('Open run', () => showRun(run.run_id)));
        }));
      }
    } catch (error) { status.textContent = error.message + ' · engine API may be offline'; }
  });
})();
