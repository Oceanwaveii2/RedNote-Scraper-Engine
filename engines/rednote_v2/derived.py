"""Serialized, explicit local ASR/OCR work. Original content never leaves this PC."""
import hashlib
import importlib.metadata
import json
import os
import html
import threading
import uuid
from pathlib import Path
from .store import now


class DerivedWorker:
    def __init__(self, store):
        self.store=store
        self.wake=threading.Event()
        self.thread=threading.Thread(target=self.run,name='rednote-derived-worker',daemon=True)
        self.thread.start()

    def queue(self, entity, kind):
        if kind not in {'subtitles','subtitles-en','ocr','ocr-en'}:raise ValueError('Choose subtitles, OCR, or an English translation.')
        with self.store.connection() as db:
            if not db.execute('SELECT 1 FROM entities WHERE id=?',(entity,)).fetchone():raise ValueError('Content is unavailable.')
            prior=db.execute("SELECT id FROM jobs WHERE entity_id=? AND kind=? AND state IN ('queued','running')",(entity,kind)).fetchone()
            if prior:return {'jobId':prior[0],'reused':True}
            jid=str(uuid.uuid4());stamp=now()
            db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?)',(jid,kind,entity,'queued','{}',0,None,stamp,stamp))
        self.store.event('derived.queue','ok',entity,{'kind':kind});self.wake.set()
        return {'jobId':jid}

    def cancel(self,jid):
        with self.store.connection() as db:db.execute("UPDATE jobs SET state='cancelled',reason='User cancelled',updated_at=? WHERE id=? AND state IN ('queued','running')",(now(),jid))

    def active(self,jid):
        with self.store.connection() as db:
            row=db.execute('SELECT state FROM jobs WHERE id=?',(jid,)).fetchone()
        return row and row[0]=='running' and not self.store.stop.is_set()

    def progress(self,jid,value):
        with self.store.connection() as db:db.execute("UPDATE jobs SET progress=?,updated_at=? WHERE id=? AND state='running'",(min(.99,value),now(),jid))

    def run(self):
        while not self.store.stop.is_set():
            self.wake.wait(2);self.wake.clear()
            with self.store.connection() as db:
                db.execute('BEGIN IMMEDIATE')
                job=db.execute("SELECT * FROM jobs WHERE state='queued' ORDER BY created_at LIMIT 1").fetchone()
                if not job:continue
                db.execute("UPDATE jobs SET state='running',updated_at=? WHERE id=?",(now(),job['id']))
            try:
                if job['kind']=='subtitles':self.subtitles(job)
                elif job['kind']=='subtitles-en':self.translate_subtitles(job)
                elif job['kind']=='ocr-en':self.translate_ocr(job)
                else:self.ocr(job)
                with self.store.connection() as db:db.execute("UPDATE jobs SET state='complete',progress=1,updated_at=? WHERE id=? AND state='running'",(now(),job['id']))
                self.store.event('derived.'+job['kind'],'finished',job['entity_id'])
            except Exception as exc:
                # Do not put exception messages containing source text/paths in logs.
                location=exc.__traceback__
                while location and location.tb_next:location=location.tb_next
                site=(location.tb_frame.f_code.co_name+':'+str(location.tb_lineno)) if location else 'unknown'
                reason='Local '+job['kind']+' failed ('+type(exc).__name__+' at '+site+'). Original retained.'
                with self.store.connection() as db:db.execute("UPDATE jobs SET state='failed',reason=?,updated_at=? WHERE id=? AND state='running'",(reason,now(),job['id']))
                self.store.event('derived.'+job['kind'],'failed',job['entity_id'],{'errorType':type(exc).__name__})

    def save(self, job, kind, language, source_hash, content, engine, revision):
        if not self.active(job['id']):return
        with self.store.connection() as db:db.execute('INSERT OR REPLACE INTO derivatives VALUES(?,?,?,?,?,?,?,?,?)',(str(uuid.uuid4()),job['entity_id'],kind,language,source_hash,engine,revision,content,now()))

    def source(self, entity, roles):
        with self.store.connection() as db:
            assets=self.store.assets(db,entity)
            rows=[]
            for a in assets:
                if a['role'] not in roles or not a['url']:continue
                f=db.execute('SELECT * FROM files WHERE id=?',(a['file_id'],)).fetchone()
                path=self.store.local(f['path'])
                if path:rows.append((path,f['sha256']))
        return rows

    def subtitles(self, job):
        from faster_whisper import WhisperModel
        rows=self.source(job['entity_id'],{'video','audio'})
        if not rows:raise ValueError('No local audio/video')
        # Model files are downloaded only for an explicit subtitle action. CPU
        # int8 avoids taking VRAM from the active browser/Tencent translator.
        model=WhisperModel('small',device='cpu',compute_type='int8',cpu_threads=4,num_workers=1,download_root=str(self.store.root/'models'))
        path,digest=rows[0]
        segments,info=model.transcribe(str(path),language='zh',beam_size=5,vad_filter=True,word_timestamps=True)
        result=[]
        for segment in segments:
            if not self.active(job['id']):return
            result.append({'startMs':round(segment.start*1000),'endMs':round(segment.end*1000),'text':segment.text,'avgLogprob':segment.avg_logprob,'noSpeechProbability':segment.no_speech_prob,'words':[{'startMs':round(w.start*1000),'endMs':round(w.end*1000),'text':w.word,'probability':w.probability} for w in (segment.words or [])]})
            self.progress(job['id'],segment.end/max(1,info.duration))
        self.save(job,'transcript','zh',digest,json.dumps(result,ensure_ascii=False),'faster-whisper',importlib.metadata.version('faster-whisper')+':small:zh:int8')
        self.write_vtt(job,result,'zh',digest)

    def write_vtt(self,job,segments,language,digest):
        def stamp(ms):
            hours,remain=divmod(max(0,ms),3600000);minutes,remain=divmod(remain,60000);seconds,millis=divmod(remain,1000)
            return f'{hours:02}:{minutes:02}:{seconds:02}.{millis:03}'
        text='WEBVTT\n\n'+'\n\n'.join(f'{i+1}\n{stamp(s["startMs"])} --> {stamp(s["endMs"])}\n{html.escape(s["text"].replace("-->","→"))}' for i,s in enumerate(segments))+'\n'
        root=self.store.root/'derived';root.mkdir(exist_ok=True)
        filename=job['entity_id']+'-'+language+'-'+job['id']+'.vtt';path=root/filename;temp=path.with_suffix('.partial')
        temp.write_text(text,encoding='utf-8');os.replace(temp,path)
        self.save(job,'subtitles',language,digest,'data_v2/derived/'+filename,'webvtt','1')

    def translate_subtitles(self, job):
        with self.store.connection() as db:
            row=db.execute("SELECT * FROM derivatives WHERE entity_id=? AND kind='transcript' AND language='zh' ORDER BY created_at DESC LIMIT 1",(job['entity_id'],)).fetchone()
        if not row:raise ValueError('Generate Chinese transcript first')
        import app
        translator=app.v2_blueprint.codex_engine
        try:
            segments=json.loads(row['content']);output=[]
            source_digest=hashlib.sha256(row['content'].encode('utf-8')).hexdigest()
            for start in range(0,len(segments),12):
                if not self.active(job['id']):return
                batch=segments[start:start+12]
                result=translator.translate([s['text'] for s in batch],'en')
                output.extend(s|{'text':translated,'sourceText':s['text']} for s,translated in zip(batch,result['translations']))
                self.progress(job['id'],len(output)/max(1,len(segments)))
            if not output:raise ValueError('No transcript segments to translate')
            with self.store.connection() as db:
                current=db.execute("SELECT content FROM derivatives WHERE entity_id=? AND kind='transcript' AND language='zh' ORDER BY created_at DESC LIMIT 1",(job['entity_id'],)).fetchone()
            if not current or hashlib.sha256(current['content'].encode('utf-8')).hexdigest()!=source_digest:raise ValueError('Chinese transcript changed; retry translation')
            self.save(job,'transcript','en',source_digest,json.dumps(output,ensure_ascii=False),result['engine'],result['revision'])
            self.write_vtt(job,output,'en',source_digest)
        finally:pass  # Shared local runtime is closed only at app exit.

    def translate_ocr(self, job):
        with self.store.connection() as db:
            row=db.execute("SELECT content FROM derivatives WHERE entity_id=? AND kind='ocr' AND language='zh' ORDER BY created_at DESC LIMIT 1",(job['entity_id'],)).fetchone()
        if not row:raise ValueError('Extract Chinese image text first')
        import app
        source=json.loads(row['content']);result=[]
        lines=[line for asset in source for line in asset.get('lines',[]) if str(line.get('text') or '').strip()]
        if not lines:raise ValueError('No OCR text to translate')
        translated=[];engine='';revision=''
        for start in range(0,len(lines),12):
            if not self.active(job['id']):return
            batch=lines[start:start+12]
            response=app.v2_blueprint.codex_engine.translate([str(line['text']) for line in batch],'en')
            translated.extend(response['translations']);engine=response['engine'];revision=response['revision']
            self.progress(job['id'],len(translated)/len(lines))
        iterator=iter(translated)
        for asset in source:
            result.append({**asset,'lines':[{**line,'sourceText':line.get('text',''),'text':next(iterator) if str(line.get('text') or '').strip() else line.get('text','')} for line in asset.get('lines',[])]})
        source_digest=hashlib.sha256(row['content'].encode('utf-8')).hexdigest()
        with self.store.connection() as db:
            current=db.execute("SELECT content FROM derivatives WHERE entity_id=? AND kind='ocr' AND language='zh' ORDER BY created_at DESC LIMIT 1",(job['entity_id'],)).fetchone()
        if not current or hashlib.sha256(current['content'].encode('utf-8')).hexdigest()!=source_digest:raise ValueError('Chinese OCR changed; retry translation')
        self.save(job,'ocr','en',source_digest,json.dumps(result,ensure_ascii=False),engine,revision)

    def ocr(self,job):
        import sys
        from rapidocr_onnxruntime import ch_ppocr_v2_cls,ch_ppocr_v3_det,ch_ppocr_v3_rec
        from rapidocr_onnxruntime import RapidOCR
        from PIL import Image
        # RapidOCR's YAML imports these modules by top-level name. Register the
        # bundled package modules explicitly for a frozen desktop build.
        for name,module in [('ch_ppocr_v2_cls',ch_ppocr_v2_cls),('ch_ppocr_v3_det',ch_ppocr_v3_det),('ch_ppocr_v3_rec',ch_ppocr_v3_rec)]:
            sys.modules.setdefault(name,module)
        engine=RapidOCR();rows=self.source(job['entity_id'],{'image','document','video'})
        if not rows:raise ValueError('No local image/video')
        result=[];hashes=[]
        def lines(detected):
            return [{'box':r[0],'text':r[1],'confidence':r[2]} for r in (detected or [])]
        for index,(path,digest) in enumerate(rows):
            if not self.active(job['id']):return
            hashes.append(digest)
            if path.suffix.lower() in {'.mp4','.webm','.mov','.mkv'}:
                import av
                with av.open(str(path)) as container:
                    stream=container.streams.video[0]
                    duration=(float(stream.duration*stream.time_base) if stream.duration and stream.time_base else
                              float(container.duration/av.time_base) if container.duration else 0)
                    # Bounded coverage: at most twelve frames, roughly fifteen seconds apart.
                    count=min(12,max(2,int(duration/15)+1)) if duration>0 else 1
                    targets=[duration*0.98*i/max(1,count-1) for i in range(count)]
                    for frame_index,target in enumerate(targets):
                        if not self.active(job['id']):return
                        if target and stream.time_base:
                            container.seek(int(target/float(stream.time_base)),stream=stream,backward=True)
                        frame=None
                        for step,candidate in enumerate(container.decode(video=0)):
                            frame=candidate
                            if candidate.time is None or candidate.time>=target or step>=180:break
                        if frame is None:raise ValueError('Video frame unavailable for OCR')
                        detected,_=engine(frame.to_ndarray(format='bgr24'))
                        result.append({'assetIndex':index,'scope':'video_frame','timeMs':round(1000*(frame.time if frame.time is not None else target)),
                                       'requestedMs':round(1000*target),'durationMs':round(1000*duration) if duration else None,
                                       'frameIndex':frame_index,'framesPlanned':count,'lines':lines(detected)})
                        self.progress(job['id'],(index+(frame_index+1)/count)/len(rows))
            else:
                with Image.open(path) as image:
                    if image.width*image.height>80_000_000:raise ValueError('Image exceeds OCR pixel budget')
                detected,_=engine(str(path))
                result.append({'assetIndex':index,'scope':'image','lines':lines(detected)})
                self.progress(job['id'],(index+1)/len(rows))
        self.save(job,'ocr','zh',hashlib.sha256(json.dumps(hashes).encode()).hexdigest(),json.dumps(result,ensure_ascii=False),'RapidOCR',importlib.metadata.version('rapidocr-onnxruntime')+':multiframe-v1')
