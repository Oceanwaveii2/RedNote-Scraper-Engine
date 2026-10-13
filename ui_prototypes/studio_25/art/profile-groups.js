// Studio-only profile folders. These never write to the mirror archive or engine.
const STORAGE_KEY = 'rn25-profile-groups-v1';
const STARTER_GROUPS = [
  { id: 'starter-core', name: 'Rainee Core', members: [
    { id: 'fixture-lingzi', name: 'Ling_ZU', kind: 'source_fixture' },
    { id: 'fixture-animation', name: 'Animation creator', kind: 'source_fixture' },
    { id: 'fixture-example', name: 'Example creator', kind: 'source_fixture' },
  ] },
  { id: 'starter-dudu', name: '嘟嘟唇 · Rainee / Dudu Lips', members: [] },
  { id: 'starter-horse', name: 'Rainee Year of the Horse', members: [] },
];

function readGroups() {
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || 'null');
    if (!Array.isArray(saved)) return structuredClone(STARTER_GROUPS);
    return saved.filter(group => group && typeof group.id === 'string' && typeof group.name === 'string')
      .map(group => ({
        id: group.id,
        name: group.name.slice(0, 80),
        members: Array.isArray(group.members) ? group.members.filter(member => member && typeof member.id === 'string')
          .map(member => ({ id: member.id, name: String(member.name || member.id), kind: member.kind || 'source_archive' })) : [],
      }));
  } catch {
    return structuredClone(STARTER_GROUPS);
  }
}

function profileKind(profile) {
  const declared = String(profile.identity_kind || profile.identityKind || profile.profile_type || profile.origin || '').toLowerCase();
  if (['authored', 'derived', 'fictional', 'character'].includes(declared)) return 'authored_identity';
  return profile.fixture ? 'source_fixture' : 'source_archive';
}

function kindLabel(kind) {
  return kind === 'authored_identity' ? 'Authored identity' : kind === 'source_fixture' ? 'Source fixture' : 'Archive source';
}

function profileName(profile) {
  return profile.source_id === 'ling_zi' ? 'Ling_ZU' : profile.name || profile.source_id || profile.id;
}

function previewFor(profile, id) {
  const avatar = profile?.assets?.find(asset => asset.role === 'avatar' && asset.url && asset.id);
  if (avatar) return { src: `/engine/api/v2/assets/${encodeURIComponent(avatar.id)}/preview`, label: 'Saved profile avatar' };
  for (const post of profile?.previews || []) {
    const image = post.assets?.find(asset => ['cover', 'image'].includes(asset.role) && asset.url && asset.id);
    if (image) return { src: `/engine/api/v2/assets/${encodeURIComponent(image.id)}/preview`, label: 'Saved post image · representative' };
  }
  if (id === 'fixture-lingzi') return { src: '/art/group-previews/lingzi-post-reference.jpg', label: 'Imported local post image · reference' };
  return null;
}

export function setupProfileGroups({ state, views, render, esc, icon, toast }) {
  const stylesheet = document.createElement('link');
  stylesheet.rel = 'stylesheet';
  stylesheet.href = 'art/profile-groups.css?v=2';
  document.head.append(stylesheet);

  let groups = readGroups();
  const selected = new Set();
  const SCATTER_ORIGIN = 'http://127.0.0.1:5186';
  const publishGroups = () => {
    if (window.parent !== window && document.referrer.startsWith(SCATTER_ORIGIN + '/')) {
      window.parent.postMessage({ type: 'rednote-studio-profile-groups', groups: structuredClone(groups) }, SCATTER_ORIGIN);
    }
  };
  window.addEventListener('message', event => {
    if (event.origin === SCATTER_ORIGIN && event.data?.type === 'scatter-request-profile-groups') publishGroups();
  });
  window.addEventListener('storage', event => {
    if(event.key===STORAGE_KEY){groups=readGroups();publishGroups();render();}
  });
  const save = () => { localStorage.setItem(STORAGE_KEY, JSON.stringify(groups)); publishGroups(); };
  const profileById = id => state.profiles.find(profile => profile.id === id);
  const visibleProfiles = () => state.profiles.filter(profile =>
    `${profile.name || ''} ${profile.source_id || ''}`.toLowerCase().includes(state.query.toLowerCase()));
  const currentSelection = () => state.profiles.filter(profile => selected.has(profile.id));

  function previewMarkup(profile, id, name, size = 'row') {
    const preview = previewFor(profile, id);
    const label = preview?.label || 'No saved image · designed fallback';
    return `<span class="pg-preview pg-preview-${size} ${preview ? 'has-image' : 'is-fallback'}" role="img" aria-label="${esc(name)}: ${esc(label)}" data-pg-name="${esc(name)}" title="${esc(label)}">
      <svg class="pg-preview-design" viewBox="0 0 64 64" aria-hidden="true"><rect x="1" y="1" width="62" height="62" rx="12"/><circle cx="32" cy="25" r="9"/><path d="M15 53c2-11 9-17 17-17s15 6 17 17"/><path d="M7 45l8-7 M49 15l8-7"/></svg>
      ${preview ? `<img loading="lazy" decoding="async" src="${esc(preview.src)}" alt="">` : ''}
    </span>`;
  }

  document.addEventListener('error', event => {
    const image = event.target;
    if (!(image instanceof HTMLImageElement) || !image.closest('.pg-preview')) return;
    image.hidden = true;
    const frame = image.parentElement;
    frame.classList.add('pg-preview-failed');
    frame.title = 'Image unavailable · designed fallback';
    frame.setAttribute('aria-label', `${frame.dataset.pgName}: Image unavailable · designed fallback`);
    const sourceLabel = frame.closest('.pg-profile, .pg-member')?.querySelector('.pg-source-label');
    if (sourceLabel) sourceLabel.textContent = 'Image unavailable · designed fallback';
  }, true);

  function memberMarkup(member, groupId) {
    const live = profileById(member.id);
    const name = live ? profileName(live) : member.name;
    const kind = live ? profileKind(live) : member.kind;
    const preview = previewFor(live, member.id);
    return `<li class="pg-member">${previewMarkup(live, member.id, name, 'member')}<div><strong>${esc(name)}</strong><small>${esc(kindLabel(kind))}${live ? '' : ' · not loaded in this view'}</small><small class="pg-source-label">${esc(preview?.label || 'No saved image')}</small></div><button type="button" data-pg-action="remove-member" data-pg-group="${esc(groupId)}" data-pg-profile="${esc(member.id)}" aria-label="Remove ${esc(name)} from ${esc(groups.find(group => group.id === groupId)?.name || 'group')}">×</button></li>`;
  }

  function groupMarkup(group) {
    const selectedInside = group.members.some(member => selected.has(member.id));
    return `<article class="pg-folder" id="pg-${esc(group.id)}">
      <div class="pg-folder-top">${icon('folder')}<div><h3>${esc(group.name)}</h3><small>${group.members.length} ${group.members.length === 1 ? 'profile' : 'profiles'}</small></div>${group.members.length ? `<div class="pg-cover-stack" aria-label="Member previews">${group.members.slice(0, 3).map(member => previewMarkup(profileById(member.id), member.id, member.name, 'stack')).join('')}</div>` : ''}</div>
      <div class="pg-path" aria-label="Future graph path">Alternative Path <span>›</span> Character Profile <span>›</span> <strong>${esc(group.name)}</strong> <span>›</span> Nodes</div>
      <ul class="pg-members">${group.members.length ? group.members.map(member => memberMarkup(member, group.id)).join('') : '<li class="pg-empty">No profiles in this group yet.</li>'}</ul>
      <div class="pg-folder-actions"><button type="button" class="primary" data-pg-action="add" data-pg-group="${esc(group.id)}" ${currentSelection().length ? '' : 'disabled'}>Add selected</button><button type="button" data-pg-action="remove-selected" data-pg-group="${esc(group.id)}" ${selectedInside ? '' : 'disabled'}>Remove selected</button></div>
    </article>`;
  }

  function groupPanel() {
    // Selection is temporary and cannot target a profile absent from the current source mode.
    for (const id of selected) if (!profileById(id)) selected.delete(id);
    const matching = visibleProfiles();
    return `<section class="profile-groups panel" aria-labelledby="pg-title">
      <div class="pg-heading"><div class="pg-heading-icon">${icon('folder')}</div><div><h2 id="pg-title">Profile groups</h2><p>Arrange source profiles for future character paths. A profile can belong to more than one group.</p></div><span class="badge neutral">Local UI prototype</span></div>
      <p class="pg-boundary">Groups organize references only. They do not merge identities, create graph nodes, or edit mirror data.</p>
      <div class="pg-layout"><div class="pg-picker"><div class="pg-subhead"><div><h3>Select profiles</h3><small>${selected.size} selected · ${matching.length} shown</small></div><div class="pg-select-actions"><button type="button" data-pg-action="select-all" ${matching.length ? '' : 'disabled'}>Select shown</button><button type="button" data-pg-action="clear" ${selected.size ? '' : 'disabled'}>Clear</button></div></div>
        <div class="pg-profile-list">${matching.length ? matching.map(profile => { const preview = previewFor(profile, profile.id); return `<label class="pg-profile ${selected.has(profile.id) ? 'selected' : ''}"><input type="checkbox" data-pg-select="${esc(profile.id)}" ${selected.has(profile.id) ? 'checked' : ''}>${previewMarkup(profile, profile.id, profileName(profile))}<span class="pg-profile-text"><strong>${esc(profileName(profile))}</strong><small>${esc(kindLabel(profileKind(profile)))} · ${esc(profile.source_id || profile.id)}</small><small class="pg-source-label">${esc(preview?.label || 'No saved image')}</small></span></label>`; }).join('') : '<p class="pg-empty">No profiles match this search.</p>'}</div>
      </div><div class="pg-folder-area"><div class="pg-subhead"><div><h3>Folders</h3><small>${groups.length} groups · local arrangement</small></div></div>
        <form id="pg-create-form" class="pg-create"><input name="groupName" aria-label="New profile group name" maxlength="80" placeholder="Name a new group" required><button type="submit">Create group</button></form>
        <div class="pg-folders">${groups.map(groupMarkup).join('')}</div>
      </div></div>
    </section>`;
  }

  const baseStorage = views.storage;
  const groupedStorage = () => {
    const html = baseStorage();
    if (!['all', 'profiles'].includes(state.tab)) return html;
    const tabsStart = html.indexOf('<div class="tabs">');
    const tabsEnd = tabsStart < 0 ? -1 : html.indexOf('</div>', tabsStart);
    return tabsEnd < 0 ? groupPanel() + html : html.slice(0, tabsEnd + 6) + groupPanel() + html.slice(tabsEnd + 6);
  };
  views.storage = groupedStorage;
  views.profiles = groupedStorage;

  document.addEventListener('change', event => {
    const input = event.target.closest('input[data-pg-select]');
    if (!input) return;
    input.checked ? selected.add(input.dataset.pgSelect) : selected.delete(input.dataset.pgSelect);
    render();
    document.querySelectorAll('input[data-pg-select]').forEach(box => {
      if (box.dataset.pgSelect === input.dataset.pgSelect) box.focus({ preventScroll: true });
    });
  });

  document.addEventListener('click', event => {
    const button = event.target.closest('[data-pg-action]');
    if (!button) return;
    const action = button.dataset.pgAction;
    const group = groups.find(item => item.id === button.dataset.pgGroup);
    if (action === 'select-all') visibleProfiles().forEach(profile => selected.add(profile.id));
    else if (action === 'clear') selected.clear();
    else if (action === 'add' && group) {
      const existing = new Set(group.members.map(member => member.id));
      for (const profile of currentSelection()) if (!existing.has(profile.id)) {
        group.members.push({ id: profile.id, name: profileName(profile), kind: profileKind(profile) });
        existing.add(profile.id);
      }
      save();
      toast(`Selected profiles added to ${group.name}. Archive unchanged.`);
    } else if (action === 'remove-selected' && group) {
      group.members = group.members.filter(member => !selected.has(member.id));
      save();
      toast(`Selected profiles removed from ${group.name}. Archive unchanged.`);
    } else if (action === 'remove-member' && group) {
      group.members = group.members.filter(member => member.id !== button.dataset.pgProfile);
      save();
      toast(`Profile removed from ${group.name}. Archive unchanged.`);
    }
    render();
  });

  document.addEventListener('submit', event => {
    if (event.target.id !== 'pg-create-form') return;
    event.preventDefault();
    const name = String(new FormData(event.target).get('groupName') || '').trim().slice(0, 80);
    if (!name) return;
    if (groups.some(group => group.name.toLocaleLowerCase() === name.toLocaleLowerCase())) {
      toast('That group name already exists.');
      return;
    }
    const id = `group-${typeof crypto.randomUUID === 'function' ? crypto.randomUUID() : Date.now().toString(36)}`;
    groups.push({ id, name, members: [] });
    save();
    render();
    document.getElementById(`pg-${id}`)?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    toast(`${name} created as a local prototype folder.`);
  });

  setTimeout(publishGroups, 0);
  return { snapshot: () => groups.map(group => ({ id: group.id, name: group.name, members: group.members.map(member => ({ ...member })) })) };
}
