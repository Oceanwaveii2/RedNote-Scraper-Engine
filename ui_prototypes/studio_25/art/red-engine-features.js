/* Studio adapter: presets and source text come from the separate research service. */
(() => {
  const byId = id => document.getElementById(id);
  const tabs = [...document.querySelectorAll('[role="tab"][data-tool]')];
  const panels = [...document.querySelectorAll('[role="tabpanel"]')];
  let presets = [];
  let active = null;

  async function api(path, options = {}) {
    const response = await fetch('research/' + path, {cache:'no-store', ...options});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Research service error');
    return data;
  }
  function status(message, error = false) {
    byId('preset-status').textContent = message;
    byId('preset-status').classList.toggle('error', error);
  }
  function selectTool(tab, moveFocus = false) {
    tabs.forEach(item => {
      const selected = item === tab;
      item.setAttribute('aria-selected', String(selected));
      item.tabIndex = selected ? 0 : -1;
    });
    panels.forEach(panel => { panel.hidden = panel.id !== tab.getAttribute('aria-controls'); });
    if (moveFocus) tab.focus();
  }
  tabs.forEach((tab, index) => {
    tab.addEventListener('click', () => selectTool(tab));
    tab.addEventListener('keydown', event => {
      if (!['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) return;
      event.preventDefault();
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 :
        (index + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
      selectTool(tabs[next], true);
    });
  });
  if (tabs.length) selectTool(tabs[0]);

  function renderPreview() {
    const selected = [...document.querySelectorAll('input[name="source-type"]:checked')].map(input => input.value);
    byId('scope-question').textContent = byId('study-question').value.trim() || 'No research question yet';
    byId('scope-types').textContent = selected.join(' · ') || 'No source types selected';
    byId('scope-mode').textContent = document.querySelector('input[name="study-mode"]:checked')?.value || 'Locked';
    byId('scope-inclusion').textContent = byId('study-inclusion').value.trim() || 'To be defined';
    byId('scope-exclusion').textContent = byId('study-exclusion').value.trim() || 'To be defined';
    byId('scope-preview').textContent = 'Update scope preview →';
  }
  byId('scope-preview').addEventListener('click', renderPreview);

  function applyPreset(preset) {
    active = preset;
    byId('research-preset').value = preset.id;
    byId('study-question').value = preset.question;
    byId('study-inclusion').value = preset.inclusion_rule;
    byId('study-exclusion').value = preset.exclusion_rule;
    byId('preset-terms').value = preset.terms.map(item => item.term).join(' · ');
    document.querySelectorAll('input[name="study-mode"]').forEach(input => { input.checked = input.value === preset.mode; });
    document.querySelectorAll('input[name="source-type"]').forEach(input => { input.checked = preset.source_types.includes(input.value); });
    byId('active-preset-title').textContent = preset.title;
    byId('active-preset-question').textContent = preset.question;
    status(preset.terms.length + ' saved search seeds · ' + (preset.is_default ? 'Default preset' : 'Custom preset') + ' · Archive text only');
    renderPreview();
    runSearch();
  }
  function fillPicker() {
    const picker = byId('research-preset');
    picker.replaceChildren();
    presets.forEach(preset => {
      const option = document.createElement('option');
      option.value = preset.id;
      option.textContent = preset.title + (preset.is_default ? ' · default' : '');
      picker.append(option);
    });
  }
  async function loadPresets(selectId) {
    try {
      presets = (await api('api/presets')).items || [];
      fillPicker();
      const chosen = presets.find(item => item.id === selectId) || presets.find(item => item.is_default) || presets[0];
      if (!chosen) throw new Error('No research presets in the database');
      applyPreset(chosen);
    } catch (error) {
      status(error.message, true);
      byId('active-preset-title').textContent = 'Research service offline';
      byId('active-preset-question').textContent = 'The database-backed preset could not be loaded.';
      byId('search-results').textContent = 'Start the local Red Engine research service to search captured records.';
    }
  }
  byId('research-preset').addEventListener('change', event => {
    const preset = presets.find(item => item.id === event.target.value);
    if (preset) applyPreset(preset);
  });
  byId('preset-terms').addEventListener('input', () => {
    if (active) status('Search seeds edited. Save as a new preset to use them for ranking.');
  });
  function termList() {
    const raw = byId('preset-terms').value.split(/[,，·\n]/).map(term => term.trim()).filter(Boolean);
    const original = new Map((active?.terms || []).map(item => [item.term.toLocaleLowerCase(), item]));
    return [...new Set(raw)].map(term => original.get(term.toLocaleLowerCase()) || {term,concept:'custom',weight:3});
  }
  byId('save-preset').addEventListener('click', async () => {
    const title = byId('new-preset-name').value.trim();
    if (!title) return status('Enter a name before saving a new preset.', true);
    const payload = {
      title, summary:'User-authored local research preset',
      question:byId('study-question').value.trim(),
      inclusion_rule:byId('study-inclusion').value.trim(),
      exclusion_rule:byId('study-exclusion').value.trim(),
      mode:document.querySelector('input[name="study-mode"]:checked')?.value || 'Locked',
      source_types:[...document.querySelectorAll('input[name="source-type"]:checked')].map(input => input.value),
      terms:termList(),
    };
    try {
      const saved = await api('api/presets', {
        method:'POST', headers:{'Content-Type':'application/json','X-RedNote-Action':'explicit'},
        body:JSON.stringify(payload),
      });
      byId('new-preset-name').value = '';
      await loadPresets(saved.id);
      status('Saved “' + saved.title + '” in the separate Red Engine preset database.');
    } catch (error) { status(error.message, true); }
  });

  function safeSourceLink(url) {
    try {
      const parsed = new URL(url);
      return parsed.protocol === 'https:' && (parsed.hostname === 'rednote.com' || parsed.hostname.endsWith('.rednote.com')) ? parsed.href : null;
    } catch { return null; }
  }
  function renderMatches(data) {
    byId('search-count').textContent = data.total_matches.toLocaleString() + ' matching records · showing ' + data.items.length;
    byId('search-scope-note').textContent = data.scope_note + ' Current archive: ' + data.archive_counts.profiles +
      ' profile(s), ' + data.archive_counts.posts + ' posts, ' + data.archive_counts.comments.toLocaleString() + ' comments.';
    const list = byId('search-results');
    list.replaceChildren();
    if (!data.items.length) {
      const empty = document.createElement('p');
      empty.textContent = 'No captured text matched. Try another phrase or review the preset terms.';
      list.append(empty);
      return;
    }
    data.items.forEach(item => {
      const card = document.createElement('article');
      card.className = 're-search-hit';
      const meta = document.createElement('div');
      meta.className = 're-search-hit-meta';
      const type = document.createElement('span');
      type.textContent = item.record_type === 'comment' ? 'COMMENT' : 'POST';
      const terms = document.createElement('span');
      terms.textContent = item.matched_terms.length ? 'Matched: ' + item.matched_terms.join(' · ') : 'Exact phrase';
      meta.append(type, terms);
      const heading = document.createElement('strong');
      heading.textContent = item.post_title || 'Untitled source post';
      const excerpt = document.createElement('p');
      excerpt.textContent = item.excerpt || 'No captured text excerpt.';
      card.append(meta, heading, excerpt);
      const source = safeSourceLink(item.source_url);
      if (source) {
        const link = document.createElement('a');
        link.href = source;
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
        link.textContent = 'Open original RedNote post ↗';
        card.append(link);
      }
      list.append(card);
    });
  }
  async function runSearch() {
    if (!active) return;
    const query = byId('preset-search-query').value.trim();
    byId('search-results').textContent = 'Searching captured local text…';
    try {
      const url = 'api/search?preset=' + encodeURIComponent(active.id) + '&q=' + encodeURIComponent(query) + '&limit=12';
      renderMatches(await api(url));
    } catch (error) {
      byId('search-count').textContent = 'Search unavailable';
      byId('search-results').textContent = error.message;
    }
  }
  byId('preset-search-form').addEventListener('submit', event => { event.preventDefault(); runSearch(); });

  const nav = [...document.querySelectorAll('.re-nav a[href^="#"]')];
  if ('IntersectionObserver' in window) {
    const observer = new IntersectionObserver(entries => {
      const current = entries.filter(entry => entry.isIntersecting).sort((a,b) => b.intersectionRatio - a.intersectionRatio)[0];
      if (!current) return;
      nav.forEach(link => link.classList.toggle('active', link.hash === '#' + current.target.id));
    }, {rootMargin:'-15% 0px -70% 0px',threshold:0});
    document.querySelectorAll('main[id],section[id]').forEach(section => observer.observe(section));
  }
  loadPresets();
})();
