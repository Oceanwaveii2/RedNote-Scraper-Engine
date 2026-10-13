// Context actions and minimum readable sizes for the existing full board.
(() => {
  const minimum = n => {
    const template = n.story?.ui_template?.kind;
    if (template === 'envelope') return { w: 420, h: 747, defaultW: 728, ratio: 728 / 1294 };
    if (template === 'dudu-dossier') return { w: 620, h: 900, defaultW: 850, ratio: 850 / 1355 };
    if (template === 'dudu-cabinet') return { w: 1000, h: 580, defaultW: 1425, ratio: 1425 / 830 };
    if (n.id === 'identity' && template === 'core_route') return { w: 320, h: 568, ratio: typeof CORE_ART_RATIO === 'number' ? CORE_ART_RATIO : 941 / 1672 };
    if (n.kind === 'junction') return { w: 64, h: 64 };
    if (n.kind === 'identity') return { w: 250, h: 250 };
    if (n.kind === 'cinematic') return { w: 600, h: 460 };
    if (n.kind === 'profile') return { w: 650, h: 520 };
    if (n.kind === 'actor') return { w: 600, h: 480 };
    if (n.kind === 'instruction') return { w: 520, h: 500 };
    if (n.kind === 'audio_fx') return { w: 420, h: 420 };
    if (n.kind === 'output') return { w: 330, h: 370 };
    if (n.kind === 'image') return { w: 340, h: 400 };
    return { w: 320, h: 340 };
  };
  const applySize = (n, width, height) => {
    const min = minimum(n);
    n.w = Math.max(min.w, Math.min(2200, Number.isFinite(width) ? width : min.defaultW || min.w));
    n.h = min.ratio ? Math.ceil(n.w / min.ratio) : Math.max(min.h, Math.min(2200, Number.isFinite(height) ? height : min.h));
    const card = document.getElementById(n.id);
    if (card) { card.style.width = n.w + 'px'; card.style.height = n.h + 'px'; }
    return min;
  };
  let repairQueued = false;
  const previousCard = card;
  card = function(n) {
    if (!Number.isFinite(n.w) || n.w < minimum(n).w || !Number.isFinite(n.h) || n.h < minimum(n).h) {
      const min = minimum(n);
      applySize(n, n.w > 0 ? n.w : min.defaultW || min.w, n.h);
      if (!repairQueued) {
        repairQueued = true;
        requestAnimationFrame(() => {
          repairQueued = false;
          if (!nodes?.length || !layout || scene) return;
          for (const item of nodes) sourcePositions[item.id] = [item.x, item.y, item.w, item.h];
          layout.width = Math.max(layout.width, ...nodes.map(item => item.x + item.w + 180));
          layout.height = Math.max(layout.height, ...nodes.map(item => item.y + item.h + 180));
          world.style.width = layout.width + 'px'; world.style.height = layout.height + 'px';
          $('#wires').setAttribute('viewBox', `0 0 ${layout.width} ${layout.height}`);
          $('#wires').style.width = layout.width + 'px'; $('#wires').style.height = layout.height + 'px';
          drawWires();
          persist();
        });
      }
    }
    previousCard(n);
    const element = document.getElementById(n.id);
    if (element) {
      const min = minimum(n);
      element.style.minWidth = min.w + 'px';
      if (!min.ratio) element.style.minHeight = min.h + 'px';
      element.querySelector('.resize-node')?.setAttribute('title', `Drag to resize · minimum ${min.w} × ${min.h}`);
    }
  };

  // Handle resizing before the older generic pointer handler can shrink a card.
  viewport.addEventListener('pointermove', event => {
    if (drag?.mode !== 'resize') return;
    const n = nodes?.find(item => item.id === drag.id);
    if (!n) return;
    const min = minimum(n);
    if (n.id === 'identity' && min.ratio && typeof coreResize !== 'undefined' && coreResize) {
      // The Core card's existing aspect-locked handler has already run in capture.
      event.stopImmediatePropagation();
      return;
    }
    const dx = (event.clientX - drag.x) / scale, dy = (event.clientY - drag.y) / scale;
    if (min.ratio) {
      const width = Math.abs(dx / Math.max(drag.nw, 1)) >= Math.abs(dy / Math.max(drag.nh, 1)) ? drag.nw + dx : (drag.nh + dy) * min.ratio;
      applySize(n, width, n.h);
    } else applySize(n, drag.nw + dx, drag.nh + dy);
    drawWires();
    event.stopImmediatePropagation();
  }, true);
  document.addEventListener('keydown', event => {
    const handle = event.target.closest?.('.resize-node');
    if (!handle || !['ArrowRight', 'ArrowLeft', 'ArrowDown', 'ArrowUp'].includes(event.key)) return;
    const n = nodes?.find(item => item.id === handle.closest('.node')?.id);
    if (!n) return;
    event.preventDefault(); event.stopImmediatePropagation();
    const before = boardSnapshot(), step = event.shiftKey ? 100 : 20;
    const dw = event.key === 'ArrowRight' ? step : event.key === 'ArrowLeft' ? -step : 0;
    const dh = event.key === 'ArrowDown' ? step : event.key === 'ArrowUp' ? -step : 0;
    const min = minimum(n);
    applySize(n, n.w + dw + (min.ratio ? dh * min.ratio : 0), n.h + dh);
    sourcePositions[n.id] = [n.x, n.y, n.w, n.h];
    drawWires(); persist(); recordBoardChange(before);
  }, true);

  // The existing numeric controller is another resize path; give it the same floor.
  const apply = controller.querySelector('.controller-apply');
  apply.onclick = () => {
    const n = nodes?.find(item => item.id === active);
    if (!n) return;
    const before = boardSnapshot(), oldX = n.x, oldY = n.y;
    const values = Object.fromEntries([...controller.querySelectorAll('input')].map(input => [input.dataset.key, Number(input.value)]));
    if (Number.isFinite(values.x)) n.x = Math.max(0, values.x);
    if (Number.isFinite(values.y)) n.y = Math.max(0, values.y);
    applySize(n, values.w, values.h);
    for (const child of ownedBranch(n.id)) if (child.id !== n.id) { child.x += n.x - oldX; child.y += n.y - oldY; }
    for (const item of nodes) sourcePositions[item.id] = [item.x, item.y, item.w, item.h];
    layout.width = Math.max(layout.width, ...nodes.map(item => item.x + item.w + 180));
    layout.height = Math.max(layout.height, ...nodes.map(item => item.y + item.h + 180));
    mount(); persist(); recordBoardChange(before); controller.close();
    status('Node placement saved · minimum size enforced · Undo available');
  };

  let copied = null, menuNode = null, pastePoint = null;
  const menu = el('div', 'graph-node-menu');
  menu.hidden = true;
  menu.setAttribute('role', 'menu');
  menu.setAttribute('aria-label', 'Node actions');
  document.body.append(menu);
  const hide = () => { menu.hidden = true; menuNode = null; };
  const copy = n => {
    copied = structuredClone(n);
    status(`Copied “${n.title}” · paste creates a separate node without wires`);
  };
  const paste = point => {
    if (!copied || !nodes?.length) return;
    if (scene) sceneMode(false);
    const before = boardSnapshot(), n = structuredClone(copied);
    n.id = 'copy-' + crypto.randomUUID();
    n.uiCreated = true;
    delete n.groupedIn;
    const min = minimum(n);
    applySize(n, n.w > 0 ? n.w : min.defaultW || min.w, n.h);
    const x = point?.x ?? (viewport.clientWidth / 2 - pan.x) / scale;
    const y = point?.y ?? (viewport.clientHeight / 2 - pan.y) / scale;
    n.x = Math.max(0, Math.round(x)); n.y = Math.max(0, Math.round(y));
    nodes.push(n);
    sourcePositions[n.id] = [n.x, n.y, n.w, n.h];
    layout.width = Math.max(layout.width, n.x + n.w + 180);
    layout.height = Math.max(layout.height, n.y + n.h + 180);
    mount(); persist(); recordBoardChange(before); select(n.id); renderObjects(); inspectSelection();
    status(`Pasted “${n.title}” · source data kept · no wires copied · Undo available`);
  };
  const addAction = (label, handler, disabled = false) => {
    const button = el('button', '', label);
    button.type = 'button'; button.setAttribute('role', 'menuitem'); button.disabled = disabled;
    button.onclick = () => { hide(); handler(); };
    menu.append(button);
  };
  const toWorld = (x, y) => {
    const box = viewport.getBoundingClientRect();
    return { x: (x - box.left - pan.x) / scale, y: (y - box.top - pan.y) / scale };
  };
  viewport.addEventListener('contextmenu', event => {
    if (event.target.closest('input,textarea,[contenteditable],video,audio,.zoom,.graph-navigator')) return;
    const element = event.target.closest('.node');
    event.preventDefault(); event.stopImmediatePropagation();
    menuNode = element ? nodes.find(n => n.id === element.id) : null;
    if (menuNode) select(menuNode.id);
    pastePoint = toWorld(event.clientX, event.clientY);
    menu.replaceChildren();
    if (menuNode) {
      const selected = menuNode;
      addAction('Copy node   Ctrl+C', () => copy(selected));
    }
    addAction('Paste node   Ctrl+V', () => paste(pastePoint), !copied);
    if (menuNode) {
      addAction('Node controller…', () => { select(menuNode?.id || element.id); openNodeController(); });
      const selected = menuNode;
      addAction('Delete node…', () => { select(selected.id); $('#deleteNode').click(); });
    }
    menu.hidden = false;
    menu.style.left = Math.min(event.clientX, window.innerWidth - menu.offsetWidth - 8) + 'px';
    menu.style.top = Math.min(event.clientY, window.innerHeight - menu.offsetHeight - 8) + 'px';
    menu.querySelector('button:not(:disabled)')?.focus();
  }, true);
  document.addEventListener('pointerdown', event => { if (!menu.hidden && !menu.contains(event.target)) hide(); });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && !menu.hidden) { hide(); return; }
    if (!(event.ctrlKey || event.metaKey) || event.altKey || event.target.closest('input,textarea,[contenteditable]') || document.querySelector('dialog[open]')) return;
    const key = event.key.toLowerCase();
    if (key === 'c') {
      const n = nodes?.find(item => item.id === active);
      if (!n) return;
      event.preventDefault(); event.stopImmediatePropagation(); copy(n);
    } else if (key === 'v' && copied) {
      event.preventDefault(); event.stopImmediatePropagation(); hide(); paste();
    }
  }, true);
})();
