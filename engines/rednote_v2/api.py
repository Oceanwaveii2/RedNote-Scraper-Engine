"""Bounded local API. All derived actions are explicit; never starts acquisition."""
import hashlib
import json
import mimetypes
import threading
import time
import uuid
import os
from werkzeug.utils import secure_filename
from pathlib import Path
from urllib.parse import urlsplit

from flask import Blueprint, abort, jsonify, request, send_file, current_app, Response
from .store import Store, now


def create_blueprint(base, store_override=None, enable_assistant=True):
    store=store_override if store_override is not None else Store(base)
    bp=Blueprint('rednote_v2',__name__)
    bp.store=store
    if enable_assistant:
        from .codex_engine import register
        register(bp,store)
    from .derived import DerivedWorker
    bp.worker=None
    preview_slots=threading.BoundedSemaphore(2)

    def refresh():
        while not store.stop.is_set():
            try:store.sync()
            except Exception:store.event('archive.sync','failed')
            store.stop.wait(30)
    def start_owned_workers():
        if bp.worker is not None:return
        store.recover_derived()
        bp.worker=DerivedWorker(store)
        threading.Thread(target=refresh,name='rednote-v2-import',daemon=True).start()
    bp.start_owned_workers=start_owned_workers

    @bp.before_request
    def guard():
        if request.host.split(':')[0]!='127.0.0.1':abort(403)
        if request.headers.get('Sec-Fetch-Site')=='cross-site':abort(403)
        if request.method not in {'GET','HEAD','OPTIONS'}:
            origin=request.headers.get('Origin')
            if origin!=request.host_url.rstrip('/') or request.headers.get('X-RedNote-Action')!='explicit':abort(403)
        budget=100*1024*1024 if request.path=='/api/v2/documents' else 2*1024*1024
        request.max_content_length=budget
        if request.content_length and request.content_length>budget:abort(413)

    @bp.after_request
    def headers(response):
        response.headers['X-Content-Type-Options']='nosniff'
        if not request.path.startswith('/api/v2/assets/'):
            response.headers['Cache-Control']='no-store'
        response.headers['Cross-Origin-Resource-Policy']='same-origin'
        return response

    @bp.errorhandler(ValueError)
    def invalid(exc):return jsonify(error=str(exc)),400

    @bp.get('/api/v2/profiles')
    def profiles():return jsonify(items=store.profile_list())

    @bp.get('/api/v2/profiles/<eid>/posts')
    def posts(eid):
        started=time.monotonic()
        result=store.posts(eid,int(request.args.get('after',-1)),request.args.get('q','')[:200],int(request.args.get('limit',36)))
        store.event('query.posts','ok',eid,{'rows':len(result['items'])},round((time.monotonic()-started)*1000))
        return jsonify(result)

    @bp.get('/api/v2/profiles/<eid>/summary')
    def summary(eid):return jsonify(store.summary(eid))

    @bp.get('/api/v2/posts/<eid>')
    def post(eid):
        data=store.post(eid)
        return jsonify(data) if data else abort(404)

    @bp.get('/api/v2/posts/<eid>/comments')
    def comments(eid):
        started=time.monotonic()
        result=store.comments(eid,request.args.get('parent') or None,int(request.args.get('after',-1)),int(request.args.get('limit',40)),request.args.get('q','')[:200])
        store.event('query.comments','ok',eid,{'rows':len(result['items'])},round((time.monotonic()-started)*1000))
        return jsonify(result)

    @bp.get('/api/v2/assets/<eid>')
    def asset(eid):
        with store.connection() as db:
            row=db.execute('SELECT f.* FROM assets a JOIN files f ON f.id=a.file_id WHERE a.id=?',(eid,)).fetchone()
        if not row or row['verification'] not in {'verified','unverified'}:abort(404)
        path=store.local(row['path'])
        if not path:abort(404)
        mime=mimetypes.guess_type(path)[0] or 'application/octet-stream'
        if mime in {'text/html','image/svg+xml','application/javascript'}:abort(415)
        return send_file(path,mimetype=mime,conditional=True,as_attachment=False)

    @bp.get('/api/v2/assets/<eid>/preview')
    def preview(eid):
        from PIL import Image, ImageOps
        with store.connection() as db:row=db.execute('SELECT f.* FROM assets a JOIN files f ON f.id=a.file_id WHERE a.id=?',(eid,)).fetchone()
        path=store.local(row['path']) if row else None
        if not path or row['verification'] not in {'verified','unverified'}:abort(404)
        stat=path.stat();key=hashlib.sha256(f'{row["sha256"]}:{stat.st_size}:{stat.st_mtime_ns}:preview480-v1'.encode()).hexdigest()
        root=store.root/'derived'/'thumbnails';root.mkdir(parents=True,exist_ok=True);output=root/(key+'.webp')
        if not output.exists():
            with preview_slots:
                if not output.exists():
                    try:
                        with Image.open(path) as original:
                            if original.width*original.height>50_000_000:abort(413)
                            image=ImageOps.exif_transpose(original);image.thumbnail((480,640));image=image.convert('RGB')
                            temporary=root/(key+'-'+uuid.uuid4().hex+'.partial')
                            image.save(temporary,format='WEBP',quality=80);os.replace(temporary,output)
                    except (OSError,ValueError):abort(415)
        response=send_file(output,mimetype='image/webp',conditional=True)
        response.headers['Cache-Control']='private, max-age=86400'
        return response

    @bp.post('/api/v2/reconcile')
    def reconcile():
        # File facts must be refreshed even when source JSON has not changed.
        with store.connection() as db:db.execute('DELETE FROM imports')
        return jsonify(store.sync())

    @bp.post('/api/v2/backup')
    def backup():return jsonify(store.backup())

    @bp.get('/api/v2/profiles/<eid>/export')
    def export(eid):
        data=json.dumps(store.export(eid),ensure_ascii=False)
        return Response(data,mimetype='application/json',headers={'Content-Disposition':'attachment; filename="rednote-2.5-foundation.json"'})

    @bp.get('/api/v2/health')
    def health():
        with store.connection() as db:
            counts={name:db.execute('SELECT COUNT(*) FROM '+name).fetchone()[0] for name in ['profiles','posts','comments','assets','files','quarantine']}
            seq=db.execute('SELECT MAX(seq) FROM telemetry').fetchone()[0]
        return jsonify(schema=1,counts=counts,eventSequence=seq,
                       acquisition='legacy adapter; explicit resume only',
                       derivedWorkerAttached=bp.worker is not None,
                       graph='2.5 contract only')

    @bp.get('/api/v2/jobs')
    def jobs():
        with store.connection() as db:derived=[{k:r[k] for k in ['id','kind','entity_id','state','progress','reason','updated_at']} for r in db.execute('SELECT * FROM jobs ORDER BY created_at DESC LIMIT 100')]
        path=Path(base)/'clone_queue.json';acquisition=[]
        if path.exists():
            try:
                q=json.loads(path.read_text(encoding='utf-8')).get('jobs',[])
                for j in q.values() if isinstance(q,dict) else q:
                    item={k:j.get(k) for k in ['jobId','profileId','status','reason','phase','resumeOnStartup','updatedAt']}
                    activity=j.get('downloadActivity') or {}
                    item['currentRun']={k:activity.get(k) for k in ['runId','startedAt','networkDownloads','reusedFiles','duplicateContent','failedTransfers','downloadedBytes','skippedPosts']}
                    acquisition.append(item)
            except (ValueError,OSError):pass
        return jsonify(derived=derived,acquisition=acquisition)

    @bp.get('/api/v2/capture-progress')
    def capture_progress():
        workspace=getattr(current_app,'browser_workspace',None)
        if workspace is None:
            return jsonify(status='unavailable',busy=False,progress={},message='Native capture is unavailable in this read-only view.')
        return jsonify(workspace._source_control_state())

    @bp.route('/api/v2/profiles/<eid>/selection',methods=['GET','POST'])
    def selection(eid):
        with store.connection() as db:
            if request.method=='POST':
                data=request.get_json(silent=True) or {};postid=data.get('postId');state=data.get('state')
                if state not in {'keep','ignore','clear'}:raise ValueError('Choose keep, ignore or clear.')
                if not db.execute('SELECT 1 FROM posts WHERE id=? AND profile_id=?',(postid,eid)).fetchone():abort(404)
                if state=='clear':db.execute('DELETE FROM selections WHERE profile_id=? AND post_id=?',(eid,postid))
                else:db.execute('INSERT OR REPLACE INTO selections VALUES(?,?,?,?)',(eid,postid,state,now()))
            return jsonify(items=[dict(r) for r in db.execute('SELECT * FROM selections WHERE profile_id=?',(eid,))])

    @bp.post('/api/v2/translation')
    def translation():
        data=request.get_json(silent=True) or {};ids=data.get('ids') or [];target=data.get('target','en');provider=data.get('provider','gpt')
        if not isinstance(ids,list) or not 1<=len(ids)<=12 or any(not isinstance(eid,str) or len(eid)>100 for eid in ids):raise ValueError('Select up to 12 visible text entries.')
        if target not in {'en','zh'}:raise ValueError('Choose English or Chinese.')
        if provider!='gpt':raise ValueError('Only GPT translation is available.')
        rows=[]
        with store.connection() as db:
            for eid in ids:
                row=db.execute('SELECT title || char(10) || body AS body FROM posts WHERE id=?',(eid,)).fetchone() or db.execute('SELECT body FROM comments WHERE id=?',(eid,)).fetchone()
                if not row:abort(404)
                rows.append((eid,row['body'],hashlib.sha256(row['body'].encode('utf-8')).hexdigest()))
        result=bp.codex_engine.translate([r[1] for r in rows],target)
        revision=result['revision']
        with store.connection() as db:
            for (eid,text,sha),translated in zip(rows,result['translations']):
                current=db.execute('SELECT title || char(10) || body AS body FROM posts WHERE id=?',(eid,)).fetchone() or db.execute('SELECT body FROM comments WHERE id=?',(eid,)).fetchone()
                if not current or hashlib.sha256(current['body'].encode('utf-8')).hexdigest()!=sha:
                    raise ValueError('Source text changed during translation; retry from the current Chinese original.')
                db.execute('INSERT OR REPLACE INTO derivatives VALUES(?,?,?,?,?,?,?,?,?)',(str(uuid.uuid4()),eid,'translation',target,sha,result['engine'],revision,translated,now()))
        store.event('translation.visible','ok',counts={'segments':len(rows)})
        return jsonify(result)

    @bp.route('/api/v2/glossary',methods=['GET','POST'])
    def glossary():
        with store.connection() as db:
            if request.method=='POST':
                d=request.get_json(silent=True) or {}
                if not all(isinstance(d.get(k),str) and len(d[k])<=300 for k in ['source','target','note']):raise ValueError('Provide short reviewed glossary fields.')
                db.execute('INSERT OR REPLACE INTO glossary VALUES(?,?,?,?,?,?)',(d.get('id') or str(uuid.uuid4()),d['source'],d['target'],d['note'],1,now()))
            return jsonify(items=[dict(r) for r in db.execute('SELECT * FROM glossary ORDER BY updated_at DESC')])

    @bp.get('/api/v2/telemetry')
    def telemetry():
        with store.connection() as db:return jsonify(items=[dict(r) for r in db.execute('SELECT * FROM telemetry ORDER BY seq DESC LIMIT 100')])

    @bp.route('/api/v2/relationships',methods=['GET','POST'])
    def relationships():
        with store.connection() as db:
            if request.method=='POST':
                d=request.get_json(silent=True) or {};source=d.get('fromId');target=d.get('toId');kind=d.get('kind');selector=d.get('selector') or {}
                if kind not in {'references','influenced_by','scene_reference','audio_reference'}:raise ValueError('Choose a reference relationship.')
                if not isinstance(selector,dict) or set(selector)-{'start','end','page','rect','startMs','endMs','note'}:raise ValueError('Unsupported selector.')
                for key in ['start','end','page','startMs','endMs']:
                    if selector.get(key) is not None and (not isinstance(selector[key],int) or isinstance(selector[key],bool) or selector[key]<0):raise ValueError('Ranges must be nonnegative integers.')
                for left,right in [('start','end'),('startMs','endMs')]:
                    if selector.get(left) is not None and selector.get(right) is not None and selector[left]>selector[right]:raise ValueError('Range end must follow start.')
                if not all(db.execute('SELECT 1 FROM entities WHERE id=?',(eid,)).fetchone() for eid in [source,target]):abort(404)
                if selector.get('note') is not None and (not isinstance(selector['note'],str) or len(selector['note'])>1000):raise ValueError('Reference note exceeds its limit.')
                if 'rect' in selector and (not isinstance(selector['rect'],list) or len(selector['rect'])!=4 or any(not isinstance(v,(int,float)) or v<0 or v>1 for v in selector['rect'])):raise ValueError('PDF rect uses four normalized coordinates.')
                db.execute('INSERT OR IGNORE INTO relationships VALUES(?,?,?,?,?,?,?)',(str(uuid.uuid4()),source,target,kind,'user',json.dumps(selector,ensure_ascii=False),now()))
            return jsonify(items=[dict(r) for r in db.execute('SELECT * FROM relationships WHERE from_id=?',(request.args.get('fromId'),))])

    @bp.post('/api/v2/derived')
    def derived():
        d=request.get_json(silent=True) or {}
        if bp.worker is None:abort(503)
        return jsonify(bp.worker.queue(d.get('entityId'),d.get('kind')))

    @bp.post('/api/v2/jobs/<jid>/cancel')
    def cancel(jid):
        if bp.worker is None:abort(503)
        bp.worker.cancel(jid);return jsonify(cancelled=True)

    @bp.post('/api/v2/jobs/<jid>/retry')
    def retry(jid):
        with store.connection() as db:job=db.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone()
        if not job:abort(404)
        if job['state'] not in {'interrupted','failed','cancelled'}:raise ValueError('Only interrupted, failed or cancelled work can be retried.')
        if bp.worker is None:abort(503)
        return jsonify(bp.worker.queue(job['entity_id'],job['kind']))

    @bp.get('/api/v2/posts/<eid>/subtitles/<language>')
    def subtitles(eid,language):
        if language not in {'en','zh','both'}:abort(404)
        if language=='both':
            import html
            with store.connection() as db:
                zh=db.execute("SELECT content,source_hash FROM derivatives WHERE entity_id=? AND kind='transcript' AND language='zh' ORDER BY created_at DESC LIMIT 1",(eid,)).fetchone()
                en=db.execute("SELECT content,source_hash FROM derivatives WHERE entity_id=? AND kind='transcript' AND language='en' ORDER BY created_at DESC LIMIT 1",(eid,)).fetchone()
            if not zh or not en or hashlib.sha256(zh['content'].encode('utf-8')).hexdigest()!=en['source_hash']:abort(404)
            def stamp(ms):
                h,v=divmod(ms,3600000);m,v=divmod(v,60000);s,v=divmod(v,1000)
                return f'{h:02}:{m:02}:{s:02}.{v:03}'
            source=json.loads(zh['content']);translated=json.loads(en['content']);cues=[]
            for index,row in enumerate(source):
                target=translated[index]['text'] if index<len(translated) else ''
                body=html.escape(row['text'].replace('-->','→'))+'\n'+html.escape(target.replace('-->','→'))
                cues.append(f'{index+1}\n{stamp(row["startMs"])} --> {stamp(row["endMs"])}\n{body}')
            return Response('WEBVTT\n\n'+'\n\n'.join(cues),mimetype='text/vtt')
        with store.connection() as db:
            row=db.execute("SELECT content,source_hash FROM derivatives WHERE entity_id=? AND kind='subtitles' AND language=? ORDER BY created_at DESC LIMIT 1",(eid,language)).fetchone()
            if language=='en':
                zh=db.execute("SELECT content FROM derivatives WHERE entity_id=? AND kind='transcript' AND language='zh' ORDER BY created_at DESC LIMIT 1",(eid,)).fetchone()
                if not zh or not row or row['source_hash']!=hashlib.sha256(zh['content'].encode('utf-8')).hexdigest():abort(404)
        path=store.local(row[0]) if row else None
        if not path:abort(404)
        return send_file(path,mimetype='text/vtt',conditional=True)

    @bp.route('/api/v2/documents',methods=['GET','POST'])
    def documents():
        if request.method=='POST':
            upload=request.files.get('file')
            if not upload:raise ValueError('Choose a local file.')
            ext=Path(upload.filename or '').suffix.lower()
            if ext not in {'.pdf','.txt','.md','.png','.jpg','.jpeg','.gif','.webp','.mp4','.webm','.mp3','.wav','.m4a'}:raise ValueError('Choose PDF, text, image, video or audio.')
            root=Path(base)/'documents_v2';root.mkdir(exist_ok=True)
            temporary=root/(uuid.uuid4().hex+'.partial');upload.save(temporary)
            try:
                if temporary.stat().st_size>100*1024*1024:abort(413)
                with temporary.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
                final=root/(digest+ext)
                if final.exists():temporary.unlink()
                else:os.replace(temporary,final)
                role='document' if ext in {'.txt','.md','.pdf'} else ('video' if ext in {'.mp4','.webm'} else ('audio' if ext in {'.mp3','.wav','.m4a'} else 'image'))
                with store.connection() as db:
                    eid=store.entity(db,'document',digest)
                    aid=store.asset(db,eid,role,0,{'file':final.relative_to(store.base).as_posix(),'sha256':digest,'mime':mimetypes.guess_type(final)[0]})
                    db.execute('INSERT OR IGNORE INTO documents VALUES(?,?,?,?,?)',(eid,(upload.filename or final.name)[:200],ext[1:],aid,now()))
                store.event('document.import','ok',eid,{'bytes':final.stat().st_size})
            finally:
                if temporary.exists():temporary.unlink()
        with store.connection() as db:return jsonify(items=[dict(r)|{'assets':store.assets(db,r['id'])} for r in db.execute('SELECT * FROM documents ORDER BY created_at DESC')])
    return bp
