"""Append-only structural transfer evidence; URLs and source prose are excluded."""
import hashlib,json,os,threading,uuid
from datetime import datetime,timezone
from pathlib import Path

class DownloadLedger:
    def __init__(self,base):
        self.base=Path(base).resolve();self.path=self.base/'download-ledger.jsonl';self.lock=threading.RLock();self.cache={};self.known_hashes=set();self.reuse_keys=set()
        if self.path.exists():
            with self.path.open(encoding='utf-8') as stream:
                for line in stream:
                    try:entry=json.loads(line)
                    except (ValueError,TypeError):continue
                    if entry.get('sha256') and entry.get('status')=='saved':self.known_hashes.add(entry['sha256'])
                    if entry.get('requestFingerprint') and entry.get('status')=='saved':self.cache[entry['requestFingerprint']]=entry
    @staticmethod
    def fingerprint(url,kind):return hashlib.sha256((kind+'\0'+str(url)).encode()).hexdigest()
    def begin(self,job):
        self.reuse_keys.clear()
        job['downloadLedgerPath']='download-ledger.jsonl'
        job['downloadActivity']={'runId':uuid.uuid4().hex,'startedAt':datetime.now(timezone.utc).isoformat(),'networkDownloads':0,'reusedFiles':0,'duplicateContent':0,'failedTransfers':0,'downloadedBytes':0,'skippedPosts':0}
        self.record(job,'run_started',{})
    def record(self,job,action,descriptor,kind=None,index=None,fingerprint=None):
        with self.lock:
            run=job.get('downloadActivity') or {}
            fields={k:descriptor[k] for k in ('path','file','sha256','bytes','mime','status') if k in descriptor}
            # Only local archive-relative paths may enter structural logs.
            for key in ('path','file'):
                if key in fields:
                    q=Path(str(fields[key]));resolved=(self.base/q).resolve()
                    if q.is_absolute() or not resolved.is_relative_to(self.base):fields.pop(key,None)
            entry={'schema':'rednote.download-event/1','eventId':uuid.uuid4().hex,'at':datetime.now(timezone.utc).isoformat(),'jobId':job.get('jobId'),'profileId':job.get('profileId'),'postId':descriptor.get('postId',job.get('currentPostId')),'runId':run.get('runId'),'action':action,'kind':kind or descriptor.get('kind'),'sourceIndex':index if index is not None else descriptor.get('sourceIndex'),**fields}
            if entry['kind']=='avatar':entry['postId']=None
            if action in {'reused','post_skipped'}:
                key=(entry['runId'],entry['postId'],action,entry['kind'],entry['sourceIndex'],entry.get('path') or entry.get('file'),entry.get('sha256'))
                if key in self.reuse_keys:return {'duplicateEventSuppressed':True}
                self.reuse_keys.add(key)
            if fingerprint:entry['requestFingerprint']=fingerprint
            self.path.parent.mkdir(parents=True,exist_ok=True)
            with self.path.open('a',encoding='utf-8') as stream:stream.write(json.dumps(entry,ensure_ascii=False)+'\n');stream.flush();os.fsync(stream.fileno())
            if entry.get('sha256') and entry.get('status')=='saved':self.known_hashes.add(entry['sha256'])
            if fingerprint and entry.get('status')=='saved':self.cache[fingerprint]=entry
            if action=='downloaded':run['networkDownloads']=run.get('networkDownloads',0)+1;run['downloadedBytes']=run.get('downloadedBytes',0)+int(descriptor.get('bytes',0))
            elif action=='reused':run['reusedFiles']=run.get('reusedFiles',0)+1
            elif action=='duplicate_content':run['networkDownloads']=run.get('networkDownloads',0)+1;run['duplicateContent']=run.get('duplicateContent',0)+1;run['downloadedBytes']=run.get('downloadedBytes',0)+int(descriptor.get('bytes',0))
            elif action=='failed':run['failedTransfers']=run.get('failedTransfers',0)+1
            elif action=='post_skipped':run['skippedPosts']=run.get('skippedPosts',0)+1
            if run:job['downloadActivity']=run
            return entry
