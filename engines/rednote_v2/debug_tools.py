"""Read-only development evidence. Fixed code allowlist, never arbitrary files."""
import hashlib
import json
import sys
from pathlib import Path

CODE_FILES = ('desktop_bridge.py','profile_clone.py','rednote_v2/api.py',
    'rednote_v2/store.py','rednote_v2/derived.py','rednote_v2/host.py',
    'rednote_v2/codex_engine.py','rednote_v2/debug_tools.py',
    'rednote_v2/browser_chrome.py','rednote_v2/page_translation.py',
    'codex_bridge_kit/client.py','ui_v2/src/main.tsx','ui_v2/src/CodexPanel.tsx',
    'ui_v2/src/Documents.tsx','ui_v2/src/api.ts','ui_v2/src/style.css')

def code_root():
    return Path(sys._MEIPASS)/'debug_source' if getattr(sys,'frozen',False) else Path(__file__).resolve().parents[1]

def code_read(args):
    if set(args)-{'path','startLine','lineCount'}:raise ValueError('Unsupported code arguments')
    root=code_root()
    if not args.get('path'):
        return {'files':[{'path':name,'available':(root/name).is_file()} for name in CODE_FILES],
                'access':'read-only packaged source; no secrets or archive bodies'}
    name=args['path']
    if name not in CODE_FILES:raise ValueError('File is outside the application-code allowlist')
    start=args.get('startLine',1);count=args.get('lineCount',80)
    if type(start) is not int or type(count) is not int or start<1 or not 1<=count<=120:raise ValueError('Invalid code range')
    path=root/name
    if not path.is_file():raise ValueError('Code snapshot unavailable')
    raw=path.read_bytes();lines=raw.decode('utf-8-sig').splitlines();selected=[];budget=0
    for i,line in enumerate(lines[start-1:start-1+count],start):
        if budget+len(line)>20000:break
        selected.append({'line':i,'text':line});budget+=len(line)
    return {'path':name,'sha256':hashlib.sha256(raw).hexdigest(),'totalLines':len(lines),
            'lines':selected,'nextLine':start+len(selected) if start+len(selected)<=len(lines) else None}

def runtime_snapshot(store,client):
    import app
    w=getattr(app.app,'browser_workspace',None)
    try:release=json.loads((store.base/'release.json').read_text(encoding='utf-8'))
    except (OSError,ValueError):release={}
    with store.connection() as db:
        counts={t:db.execute('SELECT COUNT(*) FROM '+t).fetchone()[0] for t in ('profiles','posts','comments','assets','files','quarantine')}
        recent=[dict(r) for r in db.execute('SELECT seq,at,operation,result,duration_ms,counts FROM telemetry ORDER BY seq DESC LIMIT 30')]
    jobs=[]
    try:
        rows=json.loads((store.base/'clone_queue.json').read_text(encoding='utf-8')).get('jobs',[])
        for row in rows.values() if isinstance(rows,dict) else rows:
            jobs.append({k:row.get(k) for k in ('jobId','status','phase','reason','resumeOnStartup','updatedAt')})
    except (OSError,ValueError):pass
    windows=w._window_health() if w else {}
    for name,state in windows.items():
        state['nativeChromeRequired']=name!='studio'
        state['uiTechnology']='React local Studio' if name=='studio' else 'WinForms chrome + WebView2 page'
        state['uiReady']=bool(state.get('present') and state.get('loaded') and (name=='studio' or state.get('nativeChromeReady')))
    return {'release':{k:release.get(k) for k in ('version','buildId','createdAt')},
        'windows':windows,'archiveCounts':counts,'captureJobs':jobs,
        'recentTelemetry':recent,'gpt':{'connected':bool(client.process and client.process.poll() is None),
        'busy':client.turn_lock.locked()},'sourceCode':'packaged read-only snapshot',
        'privacy':'No source text, cookies, signed URLs, prompts or credentials in runtime snapshot'}
