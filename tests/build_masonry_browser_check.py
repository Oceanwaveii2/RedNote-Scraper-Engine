"""Generate a CLI-run local fixture using the current layout and observer code.

The fixture never contacts the source website or writes sample cards to archives.
"""
import ast
import json
import re
import sys
from pathlib import Path

base = Path(__file__).resolve().parents[1]
master = (base / 'templates' / 'master_shell.html').read_text(encoding='utf-8-sig')
master_style = re.search(r'<style>(.*?)</style>', master, re.S).group(1)
master_layout = re.search(r'// Ordered masonry layout\.(.*?)// End ordered masonry layout\.', master, re.S).group(1)
master_layout = "(() => { const $ = id => document.getElementById(id);\n" + master_layout + '\nobserveNoteCards(); })();'
tree = ast.parse((base / 'profile_archive.py').read_text(encoding='utf-8-sig'))
values = {}
for node in ast.walk(tree):
    if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id in {'style', 'masonry_script'}:
                values[target.id] = node.value.value
heights = [180, 260, 210, 280, 220, 170, 140, 230, 200, 300, 190, 220]
master_cards = ''.join(f'<article class="note" data-index="{i}"><div style="height:{height}px;background:#345;color:white">Local fixture {i}</div></article>' for i, height in enumerate(heights))
offline_cards = ''.join(f'<a class="card" data-index="{i}"><div style="height:{height}px;background:#345;color:white">Local fixture {i}</div></a>' for i, height in enumerate(heights))
fixtures = [
    {'name': 'master', 'selector': '#note-feed > .note', 'html': f'<style>{master_style}</style><main class="library"><div id="note-feed" class="feed">{master_cards}</div></main><script>{master_layout}</script>'},
    {'name': 'offline', 'selector': '.grid > .card', 'html': f'<style>{values["style"]}</style><main><div class="grid">{offline_cards}</div></main>{values["masonry_script"]}'},
]
code = '''async page => {
  await page.route('**/*', route => route.abort());
  const fixtures = FIXTURES;
  const results = [];
  for (const fixture of fixtures) {
    for (const width of [1280, 700, 420]) {
      await page.setViewportSize({width, height:1000});
      await page.goto('about:blank');
      await page.setContent(fixture.html);
      await page.waitForTimeout(120);
      const initial = await page.locator(fixture.selector).evaluateAll(nodes => nodes.map(node => {const r=node.getBoundingClientRect();return {index:Number(node.dataset.index),x:r.x,y:r.y,width:r.width,height:r.height,right:r.right,bottom:r.bottom};}));
      const columns = width === 1280 ? 3 : width === 700 ? 2 : 1;
      for(let i=1;i<columns;i++) {
        if(initial[i].y !== initial[0].y || initial[i].x <= initial[i-1].x) throw Error(fixture.name+' first row order failed at '+width);
      }
      function checkOverlap(items) {
        for(let i=0;i<items.length;i++)for(let j=i+1;j<items.length;j++) {
          const a=items[i],b=items[j];
          if(a.x < b.right-1 && b.x < a.right-1 && a.y < b.bottom-1 && b.y < a.bottom-1) throw Error(fixture.name+' cards overlap: '+i+' and '+j+' at '+width);
        }
      }
      checkOverlap(initial);
      await page.locator(fixture.selector).first().evaluate(node => {node.firstElementChild.style.height='420px';node.firstElementChild.dispatchEvent(new Event('load'));});
      await page.waitForTimeout(120);
      const resized = await page.locator(fixture.selector).evaluateAll(nodes => nodes.map(node => {const r=node.getBoundingClientRect();return {x:r.x,y:r.y,right:r.right,bottom:r.bottom};}));
      checkOverlap(resized);
      results.push({layout:fixture.name,viewport:width,columns,firstThree:initial.slice(0,3),overlaps:0,resizeOverlap:0});
    }
  }
  return results;
}'''.replace('FIXTURES', json.dumps(fixtures, ensure_ascii=False))
Path(sys.argv[1]).write_text(code, encoding='utf-8')
