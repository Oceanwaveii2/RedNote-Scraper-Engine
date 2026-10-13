(()=>{
  const narrow=window.matchMedia('(max-width:900px)');
  const toggle=document.getElementById('toggleTools');
  const viewport=document.getElementById('viewport');
  const precision=document.querySelector('.graph-precision-hud');
  if(precision){
    const more=document.createElement('button');
    more.type='button';more.className='precision-more';more.textContent='⌖ Position';
    more.setAttribute('aria-expanded','false');
    more.onclick=()=>{const open=precision.classList.toggle('precision-compact-open');more.setAttribute('aria-expanded',String(open));};
    precision.append(more);
  }
  function closeTools(){
    if(!narrow.matches)return;
    document.body.classList.add('tools-closed');
    toggle?.setAttribute('aria-expanded','false');
  }
  closeTools();
  narrow.addEventListener('change',event=>{if(event.matches)closeTools();});
  viewport?.addEventListener('pointerdown',closeTools);
  document.addEventListener('keydown',event=>{if(event.key==='Escape')closeTools();});
})();
