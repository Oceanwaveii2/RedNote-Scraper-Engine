// Optional navigation for the full board. It never filters, moves, or edits nodes.
(() => {
  const wrap=el('div','graph-jump');
  const toggle=el('button','','⌕ Jump');
  toggle.type='button';toggle.id='graphJump';toggle.setAttribute('aria-expanded','false');
  toggle.setAttribute('aria-controls','graphJumpMenu');
  const menu=el('div','graph-jump-menu');menu.id='graphJumpMenu';menu.hidden=true;
  const search=el('input');search.type='search';search.placeholder='Find a node…';search.setAttribute('aria-label','Find a node on the full board');
  const shortcuts=el('div','graph-jump-shortcuts');
  const results=el('div','graph-jump-results');
  const close=()=>{menu.hidden=true;toggle.setAttribute('aria-expanded','false')};
  const jump=id=>{if(scene)sceneMode(false);focusNode(id);close()};
  for(const [label,id] of [['Master profile','actor'],['Core','identity'],['Big Lips','alternative'],['Horse','horse-transform']]){
    const button=el('button','',label);button.type='button';button.onclick=()=>jump(id);shortcuts.append(button);
  }
  const all=el('button','','Whole board');all.type='button';all.onclick=()=>{if(scene)sceneMode(false);fit();close()};shortcuts.append(all);
  function render(){results.replaceChildren();if(!nodes?.length)return;const q=search.value.trim().toLocaleLowerCase();if(!q)return;const matches=nodes.filter(n=>!n.groupedIn&&(n.title||'').toLocaleLowerCase().includes(q)).slice(0,10);for(const n of matches){const button=el('button','',n.title);button.type='button';button.onclick=()=>jump(n.id);results.append(button)}if(!matches.length)results.append(el('span','','No matching node'))}
  search.addEventListener('input',render);
  toggle.onclick=()=>{const open=menu.hidden;menu.hidden=!open;toggle.setAttribute('aria-expanded',String(open));if(open){search.value='';render();search.focus()}};
  document.addEventListener('pointerdown',e=>{if(!wrap.contains(e.target))close()});
  document.addEventListener('keydown',e=>{if(e.key==='Escape'&&!menu.hidden){close();toggle.focus()}});
  menu.append(search,shortcuts,results);wrap.append(toggle,menu);$('#fit').before(wrap);
})();
