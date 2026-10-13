/* Studio navigation links to two independent graph UI prototypes.
   The Studio app redraws its sidebar during routing, so install links after each redraw. */
(() => {
  const app = document.getElementById('app');
  if (!app) return;

  function addGraphLinks() {
    const sidebar = app.querySelector('.sidebar');
    const mainNavigation = sidebar?.querySelector('.nav');
    if (!mainNavigation || sidebar.querySelector('.graph-links')) return;

    const graphNavigation = document.createElement('nav');
    graphNavigation.className = 'graph-links';
    graphNavigation.setAttribute('aria-label', 'Graph and research prototypes');
    graphNavigation.innerHTML = `
      <span class="graph-links-heading">Graph &amp; research</span>
      <a class="graph-link graph-link-scatter" href="http://127.0.0.1:5186/" target="_blank" rel="noopener noreferrer" title="Open Scatter Graph UI prototype in a new tab">
        <svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M4 5h4v4H4z M16 3h4v4h-4z M15 16h5v5h-5z M7 7l9-2 M8 9l8 9"/></svg>
        <span>Scatter Graph<small>UI prototype</small></span>
      </a>
      <a class="graph-link graph-link-red" href="http://127.0.0.1:5187/" target="_blank" rel="noopener noreferrer" title="Open the read-only Red Engine mirror graph prototype in a new tab">
        <svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><circle cx="5" cy="5" r="2"/><circle cx="19" cy="5" r="2"/><circle cx="12" cy="19" r="2"/><path d="M7 5h10 M6 7l5 10 M18 7l-5 10"/></svg>
        <span>Red Engine<small>Mirror graph · read only</small></span>
      </a>
      <a class="graph-link graph-link-feature" href="red-engine-features.html" title="Open the Red Engine analytical feature system UI prototype">
        <svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><rect x="2.5" y="3" width="19" height="18" rx="2"/><path d="M7 15l3-3 3 2 4-6 M6 18h12"/></svg>
        <span>Red Engine Features<small>Research console · UI concept</small></span>
      </a>`;
    mainNavigation.insertAdjacentElement('afterend', graphNavigation);
  }

  new MutationObserver(addGraphLinks).observe(app, {childList: true, subtree: true});
  addGraphLinks();
})();
