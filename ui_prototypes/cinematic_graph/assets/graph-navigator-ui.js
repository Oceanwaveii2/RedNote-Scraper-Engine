// Optional overview of the original board. Navigation changes only the camera.
(() => {
  const toggle = el('button', '', '◇ Navigator');
  toggle.type = 'button';
  toggle.id = 'graphNavigatorToggle';
  toggle.setAttribute('aria-expanded', 'false');
  toggle.setAttribute('aria-controls', 'graphNavigator');
  $('#reset').before(toggle);
  const panel = el('section', 'graph-navigator');
  panel.id = 'graphNavigator';
  panel.hidden = true;
  panel.setAttribute('aria-label', 'Optional board navigator');
  const heading = el('div', 'graph-navigator-heading');
  heading.append(el('b', '', 'Board navigator'));
  const close = el('button', '', '×');
  close.type = 'button';
  close.setAttribute('aria-label', 'Close board navigator');
  heading.append(close);
  const description = el('p', '', 'The full board stays visible. Choose a route or click the map to move the view.');
  const routeBar = el('div', 'graph-navigator-routes');
  const sectionBar = el('div', 'graph-navigator-sections');
  sectionBar.hidden = true;
  const map = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  map.id = 'graphNavigatorMap';
  map.setAttribute('role', 'img');
  map.setAttribute('aria-label', 'Full node board overview');
  const search = el('input');
  search.type = 'search';
  search.placeholder = 'Find a node in any route…';
  search.setAttribute('aria-label', 'Find a node on the full board');
  const results = el('div', 'graph-navigator-results');
  panel.append(heading, description, routeBar, sectionBar, map, search, results);
  viewport.append(panel);
  panel.addEventListener('pointerdown', event => event.stopPropagation());
  panel.addEventListener('wheel', event => event.stopPropagation());

  const roots = [['Master', 'actor'], ['Core', 'identity'], ['Big Lips', 'alternative'], ['Horse', 'horse-transform']];
  const routeFor = n => {
    if (n.id === 'actor') return 'master';
    if (ownedBranch('identity').some(member => member.id === n.id)) return 'core';
    if (ownedBranch('alternative').some(member => member.id === n.id)) return 'lips';
    if (ownedBranch('horse-transform').some(member => member.id === n.id)) return 'horse';
    return 'other';
  };
  let selectedRoute = null;
  const sectionFor = n => {
    if (n.kind === 'output' || n.outputProfile) return 'Outputs';
    if (n.kind === 'junction' || n.kind === 'audio_fx' || n.story?.music_processing) return 'Processing';
    if (['cinematic', 'video', 'image', 'audio'].includes(n.kind)) return 'Media';
    if (n.kind === 'profile' || n.postGroup || n.story?.protagonist_profile) return 'Profiles';
    return 'Character';
  };
  const focus = id => { if (scene) sceneMode(false); focusNode(id); renderMap(); };
  const fitMembers = members => {
    if (scene) sceneMode(false);
    if (!members.length) return;
    const left = Math.min(...members.map(n => n.x)) - 70;
    const top = Math.min(...members.map(n => n.y)) - 70;
    const right = Math.max(...members.map(n => n.x + Math.max(n.w, 1))) + 70;
    const bottom = Math.max(...members.map(n => n.y + Math.max(n.h, 1))) + 70;
    fitBounds({ x: left, y: top, w: right - left, h: bottom - top });
    renderMap();
  };
  const renderSections = () => {
    sectionBar.replaceChildren();
    sectionBar.hidden = !selectedRoute;
    if (!selectedRoute) return;
    const members = ownedBranch(selectedRoute);
    for (const label of ['Character', 'Profiles', 'Media', 'Processing', 'Outputs']) {
      const button = el('button', '', label);
      button.type = 'button';
      const section = members.filter(n => sectionFor(n) === label);
      button.disabled = !section.length;
      button.title = section.length ? `Fit ${section.length} ${label.toLowerCase()} nodes` : `No ${label.toLowerCase()} nodes in this route`;
      button.onclick = () => fitMembers(section);
      sectionBar.append(button);
    }
  };
  for (const [label, root] of roots) {
    const button = el('button', '', label);
    button.type = 'button';
    button.title = `Fit the ${label} route without changing nodes`;
    button.onclick = () => {
      selectedRoute = root === 'actor' ? null : root;
      for (const peer of routeBar.children) peer.setAttribute('aria-pressed', String(peer === button));
      renderSections();
      root === 'actor' ? focus(root) : fitMembers(ownedBranch(root));
    };
    routeBar.append(button);
  }
  const whole = el('button', '', 'All');
  whole.type = 'button';
  whole.onclick = () => { selectedRoute = null; for (const peer of routeBar.children) peer.setAttribute('aria-pressed', String(peer === whole)); renderSections(); if (scene) sceneMode(false); fit(); renderMap(); };
  routeBar.append(whole);
  const svgNode = (tag, attrs) => {
    const node = document.createElementNS('http://www.w3.org/2000/svg', tag);
    for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, String(value));
    return node;
  };
  function renderMap() {
    if (panel.hidden || !nodes?.length) return;
    const left = Math.min(...nodes.map(n => n.x)) - 100;
    const top = Math.min(...nodes.map(n => n.y)) - 100;
    const right = Math.max(...nodes.map(n => n.x + Math.max(n.w, 1))) + 100;
    const bottom = Math.max(...nodes.map(n => n.y + Math.max(n.h, 1))) + 100;
    map.setAttribute('viewBox', `${left} ${top} ${right - left} ${bottom - top}`);
    map.replaceChildren();
    for (const n of nodes) {
      const rect = svgNode('rect', {
        x: n.x, y: n.y, width: Math.max(n.w, 28), height: Math.max(n.h, 28), rx: 28,
        class: `graph-nav-node graph-nav-${routeFor(n)}`
      });
      rect.append(svgNode('title', {}));
      rect.firstChild.textContent = n.title;
      rect.addEventListener('click', event => { event.stopPropagation(); focus(n.id); });
      map.append(rect);
    }
    const view = svgNode('rect', { x: 0, y: 0, width: 0, height: 0, class: 'graph-nav-viewport' });
    view.id = 'graphNavigatorViewport';
    map.append(view);
    updateViewport();
  }
  function updateViewport() {
    const rect = document.getElementById('graphNavigatorViewport');
    if (!rect || !scale || panel.hidden) return;
    rect.setAttribute('x', -pan.x / scale);
    rect.setAttribute('y', -pan.y / scale);
    rect.setAttribute('width', viewport.clientWidth / scale);
    rect.setAttribute('height', viewport.clientHeight / scale);
  }
  map.addEventListener('click', event => {
    if (!nodes?.length) return;
    const point = map.createSVGPoint(); point.x = event.clientX; point.y = event.clientY;
    const coordinate = point.matrixTransform(map.getScreenCTM().inverse());
    pan = { x: viewport.clientWidth / 2 - coordinate.x * scale, y: viewport.clientHeight / 2 - coordinate.y * scale };
    transform(); updateViewport();
  });
  function renderResults() {
    results.replaceChildren();
    if (!nodes?.length) return;
    const query = search.value.trim().toLocaleLowerCase();
    if (!query) { results.append(el('small', '', `${nodes.length} nodes on the full board`)); return; }
    const matches = nodes.filter(n => !n.groupedIn && `${n.title} ${n.kind}`.toLocaleLowerCase().includes(query)).slice(0, 30);
    for (const n of matches) {
      const button = el('button', '', n.title);
      button.type = 'button';
      button.onclick = () => focus(n.id);
      results.append(button);
    }
    if (!matches.length) results.append(el('small', '', 'No matching node'));
  }
  const hide = () => { panel.hidden = true; toggle.setAttribute('aria-expanded', 'false'); };
  const show = () => { panel.hidden = false; toggle.setAttribute('aria-expanded', 'true'); renderMap(); renderResults(); };
  toggle.onclick = () => panel.hidden ? show() : hide();
  close.onclick = hide;
  search.addEventListener('input', renderResults);
  document.addEventListener('keydown', event => { if (event.key === 'Escape' && !panel.hidden && !document.querySelector('dialog[open]')) { hide(); toggle.focus(); } });
  // Only the small navigator updates while open; the graph itself is untouched.
  let lastGeometry = '';
  setInterval(() => {
    if (panel.hidden || !nodes?.length) return;
    const geometry = nodes.map(n => `${n.id}:${Math.round(n.x)},${Math.round(n.y)},${Math.round(n.w)},${Math.round(n.h)}`).join('|');
    if (geometry !== lastGeometry) { lastGeometry = geometry; renderMap(); renderResults(); }
    else updateViewport();
  }, 400);
})();
