from __future__ import annotations

import contextlib
import hashlib
import json
import re
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote


def now():
    return datetime.now(timezone.utc).isoformat()


def identity(kind, source):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, 'rednote:v2:' + kind + ':' + source))


SCHEMA = '''
CREATE TABLE IF NOT EXISTS migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS imports(path TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, revision TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS entities(id TEXT PRIMARY KEY, kind TEXT NOT NULL, source_id TEXT, UNIQUE(kind,source_id));
CREATE TABLE IF NOT EXISTS profiles(id TEXT PRIMARY KEY REFERENCES entities(id), name TEXT NOT NULL, bio TEXT NOT NULL, source_url TEXT, avatar_id TEXT, metadata TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS posts(id TEXT PRIMARY KEY REFERENCES entities(id), profile_id TEXT NOT NULL REFERENCES profiles(id), source_id TEXT NOT NULL UNIQUE, ordinal INTEGER NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL, kind TEXT NOT NULL, source_url TEXT, captured_at TEXT, coverage TEXT NOT NULL, inventory TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS posts_profile_order ON posts(profile_id,ordinal,id);
CREATE TABLE IF NOT EXISTS comments(id TEXT PRIMARY KEY REFERENCES entities(id), post_id TEXT NOT NULL REFERENCES posts(id), source_id TEXT NOT NULL, parent_source_id TEXT, ordinal INTEGER NOT NULL, author_id TEXT, author TEXT NOT NULL, body TEXT NOT NULL, likes TEXT, created_at TEXT, avatar_id TEXT, UNIQUE(post_id,source_id));
CREATE INDEX IF NOT EXISTS comments_tree ON comments(post_id,parent_source_id,ordinal,id);
CREATE TABLE IF NOT EXISTS comment_rich(comment_id TEXT PRIMARY KEY REFERENCES comments(id), segments TEXT NOT NULL, source_text_hash TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS files(id TEXT PRIMARY KEY, path TEXT NOT NULL UNIQUE, sha256 TEXT, expected_hash TEXT, bytes INTEGER, mime TEXT, verification TEXT NOT NULL, fingerprint TEXT, checked_at TEXT);
CREATE TABLE IF NOT EXISTS assets(id TEXT PRIMARY KEY REFERENCES entities(id), owner_id TEXT NOT NULL REFERENCES entities(id), role TEXT NOT NULL CHECK(role IN ('image','video','cover','avatar','comment_attachment','document','audio')), slot TEXT NOT NULL, file_id TEXT REFERENCES files(id), mime TEXT, source_available INTEGER NOT NULL, UNIQUE(owner_id,role,slot));
CREATE INDEX IF NOT EXISTS assets_owner ON assets(owner_id,role,slot);
CREATE TABLE IF NOT EXISTS observations(id TEXT PRIMARY KEY, entity_id TEXT NOT NULL REFERENCES entities(id), revision TEXT NOT NULL, observed_at TEXT, evidence_path TEXT NOT NULL, summary TEXT NOT NULL, UNIQUE(entity_id,revision));
CREATE TABLE IF NOT EXISTS derivatives(id TEXT PRIMARY KEY, entity_id TEXT NOT NULL REFERENCES entities(id), kind TEXT NOT NULL, language TEXT, source_hash TEXT NOT NULL, engine TEXT NOT NULL, engine_revision TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(entity_id,kind,language,source_hash,engine_revision));
CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, kind TEXT NOT NULL, entity_id TEXT REFERENCES entities(id), state TEXT NOT NULL, payload TEXT NOT NULL, progress REAL NOT NULL DEFAULT 0, reason TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS jobs_single_active ON jobs(kind,entity_id) WHERE state IN ('queued','running');
CREATE TABLE IF NOT EXISTS relationships(id TEXT PRIMARY KEY, from_id TEXT NOT NULL REFERENCES entities(id), to_id TEXT NOT NULL REFERENCES entities(id), kind TEXT NOT NULL, provenance TEXT NOT NULL CHECK(provenance IN ('source','user')), selector TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(from_id,to_id,kind,selector));
CREATE INDEX IF NOT EXISTS relationships_from ON relationships(from_id,kind);
CREATE TABLE IF NOT EXISTS selections(profile_id TEXT NOT NULL REFERENCES profiles(id), post_id TEXT NOT NULL REFERENCES posts(id), state TEXT NOT NULL CHECK(state IN ('keep','ignore')), updated_at TEXT NOT NULL, PRIMARY KEY(profile_id,post_id));
CREATE TABLE IF NOT EXISTS glossary(id TEXT PRIMARY KEY, source TEXT NOT NULL, target TEXT NOT NULL, note TEXT NOT NULL, reviewed INTEGER NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS documents(id TEXT PRIMARY KEY REFERENCES entities(id), name TEXT NOT NULL, kind TEXT NOT NULL, asset_id TEXT NOT NULL REFERENCES assets(id), created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS telemetry(seq INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, operation TEXT NOT NULL, entity_id TEXT, result TEXT NOT NULL, duration_ms INTEGER, counts TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS quarantine(path TEXT PRIMARY KEY, reason TEXT NOT NULL, updated_at TEXT NOT NULL);
'''


class Store:
    def __init__(self, base):
        self.base = Path(base).resolve()
        self.root = self.base / 'data_v2'
        self.root.mkdir(exist_ok=True)
        self.path = self.root / 'rednote-v2.sqlite'
        self.lock = threading.RLock()
        self.sync_lock = threading.Lock()
        self.stop = threading.Event()
        with self.connection() as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='migrations'").fetchone():
                version=db.execute('SELECT MAX(version) FROM migrations').fetchone()[0]
                if version is not None and version>1:raise ValueError('Database requires a newer RedNote build; no downgrade write was performed.')
            # Use rollback journaling on the supplied SQLite 3.50 runtime. WAL
            # requires a runtime with the upstream WAL-reset fix first.
            db.execute('PRAGMA journal_mode=DELETE')
            db.executescript(SCHEMA)
            db.execute('INSERT OR IGNORE INTO migrations VALUES(1,?)', (now(),))

    def recover_derived(self):
        # Only the process that owns the desktop session may interrupt stale jobs.
        with self.connection() as db:
            db.execute("UPDATE jobs SET state='interrupted', reason='App closed during derived work', updated_at=? WHERE state IN ('running','queued')", (now(),))

    @contextlib.contextmanager
    def connection(self):
        with self.lock:
            db = sqlite3.connect(self.path, timeout=30)
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA foreign_keys=ON')
            db.execute('PRAGMA busy_timeout=30000')
            db.execute('PRAGMA synchronous=FULL')
            try:
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise
            finally:
                db.close()

    def event(self, operation, result, entity=None, counts=None, duration=None):
        stamp=now()
        payload={'at':stamp,'operation':operation,'entityId':entity,'result':result,'durationMs':duration,'counts':counts or {}}
        with self.connection() as db:
            db.execute('INSERT INTO telemetry(at,operation,entity_id,result,duration_ms,counts) VALUES(?,?,?,?,?,?)', (stamp,operation,entity,result,duration,json.dumps(counts or {})))
            with (self.root/'telemetry.jsonl').open('a',encoding='utf-8') as log:
                log.write(json.dumps(payload,ensure_ascii=False)+'\n')

    def entity(self, db, kind, source):
        eid = identity(kind, str(source))
        db.execute('INSERT OR IGNORE INTO entities VALUES(?,?,?)', (eid,kind,str(source)))
        return eid

    def local(self, relative):
        if not isinstance(relative, str) or not relative or '://' in relative:
            return None
        candidate = (self.base / relative).resolve()
        try:
            rel = candidate.relative_to(self.base)
        except ValueError:
            return None
        if rel.parts[0] not in {'clone_media','downloads','static','documents_v2','data_v2'}:
            return None
        if rel.parts[0] == 'data_v2' and (len(rel.parts)<2 or rel.parts[1]!='derived'):
            return None
        if rel.parts[0] == 'static' and (len(rel.parts)<2 or rel.parts[1] not in {'media','branding'}):
            return None
        return candidate if candidate.is_file() else None

    def asset(self, db, owner, role, slot, descriptor):
        if not isinstance(descriptor, dict):
            return None
        rel = descriptor.get('file') or descriptor.get('path') or descriptor.get('localPath') or ''
        file_id = None
        p = self.local(rel)
        if rel and p:
            rel = p.relative_to(self.base).as_posix()
            file_id = identity('file-path',rel)
            stat = p.stat()
            fingerprint = f'{stat.st_size}:{stat.st_mtime_ns}'
            prior = db.execute('SELECT * FROM files WHERE id=?',(file_id,)).fetchone()
            expected = descriptor.get('sha256') or (prior['expected_hash'] if prior else None)
            if prior and prior['fingerprint']==fingerprint and prior['expected_hash']==expected:
                digest, status = prior['sha256'], prior['verification']
            else:
                with p.open('rb') as stream:
                    digest = hashlib.file_digest(stream,'sha256').hexdigest() if stat.st_size else ''
                status = 'invalid' if not stat.st_size or (expected and digest!=expected) else ('verified' if expected else 'unverified')
            db.execute('INSERT INTO files VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET sha256=excluded.sha256,expected_hash=excluded.expected_hash,bytes=excluded.bytes,mime=excluded.mime,verification=excluded.verification,fingerprint=excluded.fingerprint,checked_at=excluded.checked_at', (file_id,rel,digest,expected,stat.st_size,descriptor.get('mime'),status,fingerprint,now()))
        elif rel:
            file_id=identity('file-path',rel)
            db.execute('INSERT INTO files VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET verification=excluded.verification,checked_at=excluded.checked_at', (file_id,rel,None,descriptor.get('sha256'),None,descriptor.get('mime'),'missing',None,now()))
        aid=self.entity(db,'asset',owner+':'+role+':'+str(slot))
        db.execute('INSERT INTO assets VALUES(?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET file_id=COALESCE(excluded.file_id,assets.file_id),mime=COALESCE(excluded.mime,assets.mime),source_available=excluded.source_available', (aid,owner,role,str(slot),file_id,descriptor.get('mime'),int(bool(descriptor.get('url') or descriptor.get('sourceUrl') or rel))))
        return aid

    def sync(self):
        if not self.sync_lock.acquire(blocking=False):
            return {'state':'busy'}
        changed=0
        try:
            root=self.base/'profile_archives'
            for profile in sorted(root.glob('*/profile.json')):
                paths=[profile,*sorted((profile.parent/'posts').glob('*.json'))]
                for path in paths:
                    if self.stop.is_set():break
                    rel=path.relative_to(self.base).as_posix();stat=path.stat();fingerprint=f'{stat.st_size}:{stat.st_mtime_ns}'
                    with self.connection() as db:
                        previous=db.execute('SELECT fingerprint FROM imports WHERE path=?',(rel,)).fetchone()
                    if previous and previous[0]==fingerprint:continue
                    try:
                        raw=path.read_bytes();data=json.loads(raw);revision=hashlib.sha256(raw).hexdigest()
                        with self.connection() as db:
                            if path.name=='profile.json':self.import_profile(db,data)
                            else:self.import_post(db,data,rel,revision)
                            db.execute('INSERT OR REPLACE INTO imports VALUES(?,?,?,?)',(rel,fingerprint,revision,now()))
                            db.execute('DELETE FROM quarantine WHERE path=?',(rel,))
                        changed+=1
                    except Exception as exc:
                        with self.connection() as db:db.execute('INSERT OR REPLACE INTO quarantine VALUES(?,?,?)',(rel,type(exc).__name__,now()))
            if changed:self.event('archive.import','ok',counts={'changed':changed})
            return {'state':'ready','changed':changed}
        finally:self.sync_lock.release()

    def import_profile(self, db, data):
        pid=self.entity(db,'profile',data['profileId'])
        avatar=self.asset(db,pid,'avatar',0,data.get('avatarMedia'))
        meta={k:data.get(k) for k in ['displayId','followers','following','likes','tags','location','coverage','capturedAt']}
        db.execute('INSERT INTO profiles VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,bio=excluded.bio,source_url=excluded.source_url,avatar_id=excluded.avatar_id,metadata=excluded.metadata',(pid,data.get('name') or data.get('profileName') or data['profileId'],data.get('bio') or '',data.get('profileUrl') or data.get('sourceUrl'),avatar,json.dumps(meta,ensure_ascii=False)))
        for ordinal,card in enumerate(data.get('posts') or []):
            if not card.get('postId'):continue
            eid=self.entity(db,'post',card['postId'])
            db.execute('INSERT OR IGNORE INTO posts VALUES(?,?,?,?,?,?,?,?,?,?,?)',(eid,pid,card['postId'],ordinal,card.get('title') or '', '',card.get('mediaType') or 'unknown',card.get('sourceUrl') or '',None,'{}','{}'))
            db.execute('UPDATE posts SET ordinal=? WHERE id=?',(ordinal,eid))
            db.execute('INSERT OR IGNORE INTO relationships VALUES(?,?,?,?,?,?,?)',(identity('relationship',eid+pid),eid,pid,'authored_by','source','{}',now()))

    def import_post(self, db, data, path, revision):
        pid=identity('profile',data['profileId']);eid=self.entity(db,'post',data['postId'])
        if not db.execute('SELECT 1 FROM profiles WHERE id=?',(pid,)).fetchone():raise ValueError('Missing profile')
        prior=db.execute('SELECT ordinal FROM posts WHERE id=?',(eid,)).fetchone()
        db.execute('INSERT INTO posts VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET title=excluded.title,body=excluded.body,kind=excluded.kind,source_url=excluded.source_url,captured_at=excluded.captured_at,coverage=excluded.coverage,inventory=excluded.inventory',(eid,pid,data['postId'],prior[0] if prior else 999999,data.get('title') or '',data.get('text') or '',data.get('mediaType') or 'unknown',data.get('sourceUrl') or '',data.get('capturedAt'),json.dumps(data.get('coverage') or {}),json.dumps(data.get('sourceMediaInventory') or {})))
        db.execute('INSERT OR IGNORE INTO observations VALUES(?,?,?,?,?,?)',(identity('observation',eid+revision),eid,revision,data.get('capturedAt'),path,json.dumps({'coverage':data.get('coverage') or {},'inventory':data.get('sourceMediaInventory') or {}})))
        db.execute('DELETE FROM assets WHERE owner_id=?',(eid,))
        for index,m in enumerate(data.get('media') or []):
            role=m.get('kind');role='image' if role in {'photo','image'} else role
            if role in {'image','video','cover','avatar'}:self.asset(db,eid,role,m.get('sourceIndex',index),m)
        for index,c in enumerate(data.get('comments') or []):
            sid=str(c.get('id') or c.get('commentId') or identity('derived-comment',eid+str(index)))
            cid=self.entity(db,'comment',eid+':'+sid)
            author=c.get('author') or '';author=author.get('name','') if isinstance(author,dict) else str(author)
            person=self.entity(db,'author',c.get('authorId') or 'unknown:'+cid)
            self.asset(db,person,'avatar',0,{'url':c.get('authorAvatar')})
            db.execute('INSERT OR IGNORE INTO relationships VALUES(?,?,?,?,?,?,?)',(identity('relationship',cid+person),cid,person,'authored_by','source','{}',now()))
            parent=c.get('parentId') or None
            db.execute('INSERT INTO comments VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET parent_source_id=excluded.parent_source_id,ordinal=excluded.ordinal,author_id=COALESCE(excluded.author_id,comments.author_id),author=excluded.author,body=excluded.body,likes=excluded.likes,created_at=excluded.created_at',(cid,eid,sid,parent,index,c.get('authorId'),author,c.get('text') or '',str(c.get('likes') or ''),str(c.get('time') or ''),None))
            rich=c.get('richSegments')
            if isinstance(rich,list) and rich:
                body_hash=hashlib.sha256((c.get('text') or '').encode('utf-8')).hexdigest()
                db.execute('INSERT INTO comment_rich VALUES(?,?,?) ON CONFLICT(comment_id) DO UPDATE SET segments=excluded.segments,source_text_hash=excluded.source_text_hash',(cid,json.dumps(rich,ensure_ascii=False),body_hash))
            db.execute('DELETE FROM assets WHERE owner_id=?',(cid,))
            for slot,m in enumerate(c.get('attachments') or []):
                if m.get('role')=='author_avatar':self.asset(db,person,'avatar',0,m)
                else:self.asset(db,cid,'avatar' if m.get('kind')=='avatar' else 'comment_attachment',str(m.get('kind','image'))+':'+str(m.get('sourceIndex',slot)),m)
            parent_id=identity('comment',eid+':'+str(parent)) if parent else eid
            # Parent constraints are applied after all comments have been imported.
            if parent_id!=cid and db.execute('SELECT 1 FROM entities WHERE id=?',(parent_id,)).fetchone():
                db.execute('INSERT OR IGNORE INTO relationships VALUES(?,?,?,?,?,?,?)',(identity('relationship',cid+parent_id),cid,parent_id,'reply_to' if parent else 'comments_on','source','{}',now()))

    def assets(self, db, owner):
        rows=db.execute('SELECT a.*,f.path,f.bytes,f.sha256,f.verification,f.checked_at FROM assets a LEFT JOIN files f ON f.id=a.file_id WHERE owner_id=? ORDER BY role,CAST(slot AS INTEGER),slot',(owner,)).fetchall()
        return [dict(row)|{'url':'/api/v2/assets/'+row['id'] if row['path'] and row['verification'] in {'verified','unverified'} else None} for row in rows]

    def comment_assets(self, db, owner):
        assets=self.assets(db,owner)
        for r in db.execute("SELECT to_id FROM relationships WHERE from_id=? AND kind='authored_by'",(owner,)):
            assets.extend(self.assets(db,r[0]))
        return assets

    def profile_list(self):
        with self.connection() as db:
            return [dict(r)|{'metadata':json.loads(r['metadata']),'assets':self.assets(db,r['id'])} for r in db.execute('SELECT p.*,e.source_id FROM profiles p JOIN entities e USING(id) ORDER BY name')]

    def posts(self, profile, after=-1, query='', limit=36):
        limit=max(1,min(60,limit));params=[profile,after];where='profile_id=? AND ordinal>?'
        if query:
            # LIKE supports 1/2-character Chinese queries too. Wildcards are literal.
            safe=query.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')
            where+=" AND (title LIKE ? ESCAPE '\\' OR body LIKE ? ESCAPE '\\' OR source_id=?)";params.extend(['%'+safe+'%','%'+safe+'%',query])
        with self.connection() as db:
            rows=db.execute('SELECT * FROM posts WHERE '+where+' ORDER BY ordinal,id LIMIT ?',(*params,limit+1)).fetchall()
            result=[]
            for r in rows[:limit]:
                item=dict(r);item.pop('body');item['coverage']=json.loads(item['coverage']);item['assets']=self.assets(db,item['id']);result.append(item)
            return {'items':result,'next':rows[limit-1]['ordinal'] if len(rows)>limit else None}

    def post(self, eid):
        with self.connection() as db:
            row=db.execute('SELECT * FROM posts WHERE id=?',(eid,)).fetchone()
            if not row:return None
            source_hash=hashlib.sha256((row['title']+'\n'+row['body']).encode('utf-8')).hexdigest()
            derivatives=[dict(r) for r in db.execute('SELECT * FROM derivatives WHERE entity_id=? ORDER BY created_at DESC',(eid,))]
            derivatives=[d for d in derivatives if d['kind']!='translation' or d['source_hash']==source_hash]
            for kind in ('transcript','ocr'):
                chinese=next((d for d in derivatives if d['kind']==kind and d['language']=='zh'),None)
                current=hashlib.sha256(chinese['content'].encode('utf-8')).hexdigest() if chinese else None
                derivatives=[d for d in derivatives if not (d['language']=='en' and d['kind'] in ({'transcript','subtitles'} if kind=='transcript' else {'ocr'}) and d['source_hash']!=current)]
            return dict(row)|{'coverage':json.loads(row['coverage']),'inventory':json.loads(row['inventory']),'assets':self.assets(db,eid),'derivatives':derivatives}

    def comments(self, eid, parent=None, after=-1, limit=40, query=''):
        limit=max(1,min(60,limit))
        with self.connection() as db:
            params=[eid,after];where='c.post_id=? AND c.ordinal>?'
            if query:
                safe=query.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')
                where+=" AND (c.body LIKE ? ESCAPE '\\' OR c.author LIKE ? ESCAPE '\\' OR c.source_id=?)";params.extend(['%'+safe+'%','%'+safe+'%',query])
            elif parent is not None:where+=' AND c.parent_source_id=?';params.append(parent)
            else:where+=' AND (c.parent_source_id IS NULL OR c.parent_source_id=c.source_id OR NOT EXISTS(SELECT 1 FROM comments x WHERE x.post_id=c.post_id AND x.source_id=c.parent_source_id))'
            rows=db.execute('SELECT c.*,cr.segments AS rich_segments,cr.source_text_hash AS rich_text_hash,(SELECT COUNT(*) FROM comments x WHERE x.post_id=c.post_id AND x.parent_source_id=c.source_id AND x.id!=c.id) AS replies FROM comments c LEFT JOIN comment_rich cr ON cr.comment_id=c.id WHERE '+where+' ORDER BY c.ordinal,c.id LIMIT ?',(*params,limit+1)).fetchall()
            return {'items':[dict(r)|{'rich_segments':json.loads(r['rich_segments']) if r['rich_segments'] and r['rich_text_hash']==hashlib.sha256(r['body'].encode('utf-8')).hexdigest() else [],'author_profile_url':'https://www.rednote.com/user/profile/'+r['author_id'].lower() if isinstance(r['author_id'],str) and re.fullmatch(r'[a-fA-F0-9]{24}',r['author_id']) else None,'assets':self.comment_assets(db,r['id']),'translations':[dict(t) for t in db.execute("SELECT language,content,source_hash FROM derivatives WHERE entity_id=? AND kind='translation' AND source_hash=? ORDER BY created_at DESC",(r['id'],hashlib.sha256(r['body'].encode('utf-8')).hexdigest()))]} for r in rows[:limit]],'next':rows[limit-1]['ordinal'] if len(rows)>limit else None}

    def summary(self, profile):
        with self.connection() as db:
            rows=db.execute('SELECT id,coverage,captured_at FROM posts WHERE profile_id=?',(profile,)).fetchall()
            coverage=[json.loads(r['coverage']) for r in rows];total=len(rows)
            saved=sum(bool(r['captured_at']) for r in rows)
            content=0;fully=0
            for r,c in zip(rows,coverage):
                invalid=db.execute("SELECT COUNT(*) FROM assets a LEFT JOIN files f ON f.id=a.file_id WHERE a.owner_id=? AND a.role IN ('image','video') AND (f.verification IS NULL OR f.verification!='verified')",(r['id'],)).fetchone()[0]
                complete=c.get('media',{}).get('status')=='complete' and c.get('comments',{}).get('status')=='complete' and not invalid
                content+=complete
                discussion_missing=db.execute("SELECT COUNT(*) FROM assets a LEFT JOIN files f ON f.id=a.file_id WHERE (a.owner_id IN (SELECT id FROM comments WHERE post_id=?) OR a.owner_id IN (SELECT rel.to_id FROM relationships rel JOIN comments co ON co.id=rel.from_id WHERE co.post_id=? AND rel.kind='authored_by')) AND (f.verification IS NULL OR f.verification!='verified')",(r['id'],r['id'])).fetchone()[0]
                fully+=bool(complete and c.get('status')=='complete' and c.get('comments',{}).get('attachmentCoverage')=='complete' and not discussion_missing)
            roles=[dict(r) for r in db.execute('SELECT a.role,COUNT(*) discovered,SUM(f.verification IN (\'verified\',\'unverified\')) saved,SUM(f.verification=\'verified\') verified,SUM(f.verification=\'invalid\') invalid,SUM(f.verification=\'missing\') missing FROM assets a JOIN entities e ON e.id=a.owner_id LEFT JOIN comments c ON c.id=a.owner_id LEFT JOIN files f ON f.id=a.file_id JOIN posts p ON p.id=COALESCE(c.post_id,a.owner_id) WHERE p.profile_id=? GROUP BY a.role',(profile,))]
            comments=db.execute('SELECT COUNT(*) FROM comments WHERE post_id IN (SELECT id FROM posts WHERE profile_id=?)',(profile,)).fetchone()[0]
            avatars=db.execute("SELECT COUNT(DISTINCT a.id) discovered,COUNT(DISTINCT CASE WHEN f.verification IN ('verified','unverified') THEN a.id END) saved,COUNT(DISTINCT CASE WHEN f.verification='verified' THEN a.id END) verified FROM assets a LEFT JOIN files f ON f.id=a.file_id WHERE a.role='avatar' AND (a.owner_id=? OR a.owner_id IN (SELECT rel.to_id FROM relationships rel JOIN comments c ON c.id=rel.from_id JOIN posts p ON p.id=c.post_id WHERE p.profile_id=? AND rel.kind='authored_by'))",(profile,profile)).fetchone()
            roles.append({'role':'avatar',**dict(avatars),'invalid':None,'missing':None})
            return {'totalPosts':total,'savedPosts':saved,'contentComplete':content,'fullyComplete':fully,'comments':comments,'roles':roles,'mediaTotal':None,'countedAt':now(),'updatedAt':max((r['captured_at'] or '' for r in rows),default=None),'quarantined':db.execute('SELECT COUNT(*) FROM quarantine').fetchone()[0]}

    def backup(self):
        target=self.root/'backups';target.mkdir(exist_ok=True)
        path=target/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')+'-'+uuid.uuid4().hex[:8]+'.sqlite')
        with self.connection() as db,sqlite3.connect(path) as dest:db.backup(dest)
        digest=hashlib.sha256(path.read_bytes()).hexdigest()
        path.with_suffix('.manifest.json').write_text(json.dumps({'schema':'rednote.backup/2','createdAt':now(),'sha256':digest,'mediaIncluded':False,'restore':'Close app; preserve current data_v2; restore this database and the referenced original media separately.'}),encoding='utf-8')
        return {'file':str(path),'sha256':digest}

    def export(self, profile):
        with self.connection() as db:
            ids=[r[0] for r in db.execute('SELECT id FROM posts WHERE profile_id=?',(profile,))]
            documents=[dict(r)|{'assets':self.assets(db,r['id'])} for r in db.execute('SELECT * FROM documents')]
            comments=[dict(r)|{'assets':self.comment_assets(db,r['id'])} for r in db.execute('SELECT c.* FROM comments c JOIN posts p ON p.id=c.post_id WHERE p.profile_id=? ORDER BY c.post_id,c.ordinal',(profile,))]
            scope={profile,*ids,*(c['id'] for c in comments),*(d['id'] for d in documents)}
            all_links=[dict(r) for r in db.execute('SELECT * FROM relationships')]
            scope.update(r['to_id'] for r in all_links if r['from_id'] in scope and r['kind']=='authored_by')
            assets=[dict(r) for r in db.execute('SELECT * FROM assets') if r['owner_id'] in scope]
            scope.update(a['id'] for a in assets)
            fileids={a['file_id'] for a in assets}
            entities=[dict(r) for r in db.execute('SELECT * FROM entities') if r['id'] in scope]
            relationships=[r for r in all_links if r['from_id'] in scope and r['to_id'] in scope]
            files=[dict(r) for r in db.execute('SELECT * FROM files') if r['id'] in fileids]
        # Portable contract carries stable IDs and local references; no graph layout,
        # signed source URLs, browser credentials or prototype schemas.
        return {'schema':'rednote.multimodal/2.0','createdAt':now(),'profileId':profile,'entities':entities,'posts':[self.post(eid) for eid in ids],'comments':comments,'documents':documents,'assets':assets,'relationships':relationships,'files':files,'selectors':{'text':{'start':0,'end':None},'pdf':{'page':None,'rect':None},'video':{'startMs':0,'endMs':None},'audio':{'startMs':0,'endMs':None}},'graphEditorImplemented':False}
