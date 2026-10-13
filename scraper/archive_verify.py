"""Offline integrity checks and indexed contact sheets for a captured profile.

This command reads local archive descriptors and media only. It makes no website
requests, uploads nothing, and never deletes or rewrites source media.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import re
import shutil
import subprocess
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


IMAGE_FORMAT_MIMES={'JPEG':'image/jpeg','PNG':'image/png','WEBP':'image/webp','GIF':'image/gif','AVIF':'image/avif','BMP':'image/bmp','TIFF':'image/tiff'}
IMAGE_SUFFIXES={'.jpg','.jpeg','.png','.webp','.gif','.avif','.bmp','.tif','.tiff'}
VIDEO_SUFFIXES={'.mp4','.webm','.mov','.mkv','.m4v'}


def now():
    return datetime.now(timezone.utc).isoformat()


def read_json(path):
    try:return json.loads(path.read_text(encoding='utf-8'))
    except (OSError,ValueError):return None


def digest(path):
    value=hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):value.update(chunk)
    return value.hexdigest()


def contained(base,path):
    try:
        target=(base/path).resolve()
        return target if target.is_relative_to(base) else None
    except (TypeError,ValueError,OSError):return None


def collect_descriptors(base,profile_id):
    """Read asset descriptors from current manifests, treating bodies as opaque."""
    claims=defaultdict(list)
    errors=[]
    def add(descriptor,profile,post,source,comment_id=''):
        if not isinstance(descriptor,dict):return
        raw=descriptor.get('file') or descriptor.get('path') or ''
        if not raw:return
        path=contained(base,str(raw))
        if path is None:
            errors.append({'source':source,'reason':'A descriptor path is outside the archive base.'});return
        relative=path.relative_to(base).as_posix()
        claims[relative].append({'profileId':profile,'postId':post,'commentId':comment_id,'kind':descriptor.get('kind',''),'sourceIndex':descriptor.get('sourceIndex',0),'bytes':descriptor.get('bytes'),'sha256':descriptor.get('sha256'),'mime':descriptor.get('mime'),'status':descriptor.get('status',''),'manifest':source})
    roots=[base/'profile_archives'/profile_id] if profile_id else list((base/'profile_archives').glob('*'))
    for root in roots:
        profile=read_json(root/'profile.json') or {}
        pid=profile.get('profileId') or root.name
        add(profile.get('avatarMedia'),pid,'',(root/'profile.json').relative_to(base).as_posix())
        for card in profile.get('posts',[]):
            if isinstance(card,dict):add(card.get('coverMedia'),pid,card.get('postId',''),(root/'profile.json').relative_to(base).as_posix())
        for path in (root/'posts').glob('*.json'):
            data=read_json(path)
            if not isinstance(data,dict):
                errors.append({'source':path.relative_to(base).as_posix(),'reason':'The current note manifest is unreadable.'});continue
            for asset in data.get('media',[]):add(asset,pid,data.get('postId',path.stem),path.relative_to(base).as_posix())
            for comment in data.get('comments',[]):
                if not isinstance(comment,dict):continue
                for asset in comment.get('attachments',[]):
                    add(asset,pid,data.get('postId',path.stem),path.relative_to(base).as_posix(),str(comment.get('id','')))
    queue=read_json(base/'clone_queue.json') or {}
    for job in queue.get('jobs',[]):
        if not isinstance(job,dict) or (profile_id and job.get('profileId')!=profile_id):continue
        pid=job.get('profileId','')
        add(job.get('profile',{}).get('avatarMedia'),pid,'','clone_queue.json')
        for post in job.get('posts',[]):
            for asset in post.get('media',[]):add(asset,pid,post.get('postId',''),'clone_queue.json')
    return claims,errors


def verify_image(path):
    try:
        from PIL import Image
    except ImportError:
        return {'status':'unverified','reason':'Pillow is unavailable in this Python runtime.'}
    try:
        with Image.open(path) as image:
            fmt=image.format;width,height=image.size;frames=getattr(image,'n_frames',1)
            image.verify()
        with Image.open(path) as image:
            image.load()
            if frames>1:
                for index in range(1,frames):image.seek(index);image.load()
        if width<=0 or height<=0:raise ValueError('The decoded image has invalid dimensions.')
        return {'status':'verified','width':width,'height':height,'format':fmt,'mime':IMAGE_FORMAT_MIMES.get(fmt,mimetypes.guess_type(path.name)[0]),'frames':frames,'checks':['image.verify','pixel_decode']}
    except Exception as exc:
        return {'status':'failed','reason':str(exc)[:300]}


def verify_video(path,ffprobe):
    if not ffprobe:return {'status':'unverified','reason':'ffprobe is unavailable.'}
    try:
        result=subprocess.run([str(ffprobe),'-v','error','-show_format','-show_streams','-of','json',str(path)],capture_output=True,encoding='utf-8',timeout=45)
        if result.returncode:raise ValueError(result.stderr.strip()[:300] or 'ffprobe rejected this media.')
        info=json.loads(result.stdout)
        streams=info.get('streams',[])
        video=next((s for s in streams if s.get('codec_type')=='video'),None)
        if not video or not video.get('width') or not video.get('height'):raise ValueError('No valid video stream was reported.')
        container=info.get('format',{}).get('format_name','')
        mime='video/mp4' if 'mp4' in container else 'video/webm' if 'webm' in container else mimetypes.guess_type(path.name)[0]
        return {'status':'verified','width':video['width'],'height':video['height'],'codec':video.get('codec_name'),'duration':info.get('format',{}).get('duration'),'container':container,'mime':mime,'audioStreams':sum(s.get('codec_type')=='audio' for s in streams),'checks':['ffprobe_container','video_stream_metadata'],'limitation':'Container and stream metadata were checked; every video frame was not decoded.'}
    except Exception as exc:
        return {'status':'failed','reason':str(exc)[:300]}


def verify_file(base,relative,claims,ffprobe):
    path=contained(base,relative)
    row={'file':relative,'claims':claims,'referenced':bool(claims),'status':'failed','issues':[]}
    if path is None or not path.is_file():row['issues'].append('Local file is missing or outside the archive.');return row
    before=path.stat()
    row.update(bytes=before.st_size,sha256=digest(path))
    if path.stat().st_size!=before.st_size or path.stat().st_mtime_ns!=before.st_mtime_ns:
        row['status']='in_progress';row['issues'].append('The file changed while verification was reading it.');return row
    for claim in claims:
        if claim.get('sha256') and claim['sha256']!=row['sha256']:row['issues'].append('SHA256 differs from '+claim['manifest']+'.')
        if claim.get('bytes') is not None:
            try:
                if int(claim['bytes'])!=row['bytes']:row['issues'].append('Byte length differs from '+claim['manifest']+'.')
            except (TypeError,ValueError):row['issues'].append('Recorded byte length is invalid in '+claim['manifest']+'.')
    kinds={claim.get('kind') for claim in claims}
    suffix=path.suffix.lower()
    is_image=suffix in IMAGE_SUFFIXES or bool(kinds&{'photo','image','cover','avatar','emoji'})
    is_video=suffix in VIDEO_SUFFIXES or 'video' in kinds
    row['mediaType']='image' if is_image else 'video' if is_video else 'unknown'
    row['decode']=verify_image(path) if is_image else verify_video(path,ffprobe) if is_video else {'status':'unverified','reason':'Unrecognized media type.'}
    observed_mime=row['decode'].get('mime')
    if observed_mime:
        for claim in claims:
            if claim.get('mime') and claim['mime']!=observed_mime:row['issues'].append('Decoded MIME differs from '+claim['manifest']+'.')
    if row['decode']['status']=='failed':row['issues'].append(row['decode'].get('reason','Media decode failed.'))
    row['status']='failed' if row['issues'] else 'verified' if row['decode']['status']=='verified' else 'unverified'
    row['hashMatched']=bool(any(c.get('sha256') for c in claims)) and not any('SHA256' in issue for issue in row['issues'])
    return row


def make_contact_sheets(base,rows,output,columns=5,page_size=25):
    try:
        from PIL import Image,ImageDraw,ImageOps
    except ImportError:return []
    images=[row for row in rows if row.get('mediaType')=='image' and row.get('decode',{}).get('status')=='verified']
    result=[];tile_width=260;tile_height=330
    for page,start in enumerate(range(0,len(images),page_size),1):
        batch=images[start:start+page_size]
        height=((len(batch)+columns-1)//columns)*tile_height+45
        sheet=Image.new('RGB',(columns*tile_width,height),'#151515');draw=ImageDraw.Draw(sheet)
        draw.text((12,12),'Archived image integrity review - page '+str(page),(245,245,245))
        for index,row in enumerate(batch):
            x=(index%columns)*tile_width;y=(index//columns)*tile_height+45
            path=contained(base,row['file'])
            try:
                with Image.open(path) as image:
                    image=ImageOps.exif_transpose(image).convert('RGB')
                    image.thumbnail((tile_width-16,tile_height-62))
                    sheet.paste(image,(x+(tile_width-image.width)//2,y))
                claim=row['claims'][0] if row['claims'] else {}
                label=str(start+index+1).zfill(4)+' '+str(claim.get('kind','image'))+' '+str(claim.get('sourceIndex',''))
                label+=' '+str(row['decode']['width'])+'x'+str(row['decode']['height'])
                draw.text((x+8,y+tile_height-55),label,(245,245,245))
                draw.text((x+8,y+tile_height-36),Path(row['file']).name[:35],(205,205,205))
                draw.text((x+8,y+tile_height-20),row['status']+' '+row['sha256'][:12],(130,220,160) if row['status']=='verified' else (255,120,120))
            except Exception:draw.text((x+8,y+20),'Preview could not be created.',(255,120,120))
        path=output/('contact-sheet-'+str(page).zfill(3)+'.jpg')
        sheet.save(path,quality=92)
        result.append({'file':str(path.resolve()),'items':[{'index':start+i+1,'file':r['file'],'sha256':r['sha256']} for i,r in enumerate(batch)]})
    return result


def verify_archive(base_dir,output_dir,profile_id=None,ffprobe=None,columns=5,page_size=25):
    base=Path(base_dir).resolve();output=Path(output_dir).resolve()
    if profile_id and not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',profile_id):raise ValueError('Invalid profile source ID.')
    if output==base or output.is_relative_to(base/'clone_media'):
        raise ValueError('Verification output must be separate from source media.')
    output.mkdir(parents=True,exist_ok=True)
    claims,errors=collect_descriptors(base,profile_id)
    files=set(claims)
    root=base/'clone_media'/profile_id if profile_id else base/'clone_media'
    partial=[]
    if root.exists():
        for path in root.rglob('*'):
            if not path.is_file():continue
            if path.parent==base/'clone_media'/'comment_assets' and path.suffix=='.json':continue
            if path.name.endswith('.partial'):partial.append(path.relative_to(base).as_posix())
            else:files.add(path.relative_to(base).as_posix())
    probe=ffprobe or shutil.which('ffprobe')
    rows=[verify_file(base,relative,claims.get(relative,[]),probe) for relative in sorted(files)]
    duplicates=defaultdict(list)
    for row in rows:
        if row.get('sha256'):duplicates[row['sha256']].append(row['file'])
    duplicate_groups=[{'sha256':key,'files':paths,'bytesEach':next(r['bytes'] for r in rows if r.get('sha256')==key)} for key,paths in duplicates.items() if len(paths)>1]
    sheets=make_contact_sheets(base,rows,output,columns,page_size)
    report={'schema':'rednote.archive-integrity/1','verifiedAt':now(),'baseDir':str(base),'profileId':profile_id,'networkRequests':0,'sourceFilesModified':0,'summary':{'files':len(rows),'verified':sum(r['status']=='verified' for r in rows),'failed':sum(r['status']=='failed' for r in rows),'unverified':sum(r['status']=='unverified' for r in rows),'inProgress':len(partial)+sum(r['status']=='in_progress' for r in rows),'images':sum(r.get('mediaType')=='image' for r in rows),'videos':sum(r.get('mediaType')=='video' for r in rows),'duplicateGroups':len(duplicate_groups),'manifestErrors':len(errors)},'files':rows,'partialFiles':partial,'duplicateGroups':duplicate_groups,'manifestErrors':errors,'contactSheets':sheets,'limitations':['Image files were fully decoded; contact sheets are for human visual inspection.','Video containers and stream metadata were checked with ffprobe, not every frame.','Capture coverage and comment completeness are recorded separately by the capture service.']}
    target=output/'archive-verification.json';target.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-dir',required=True);parser.add_argument('--output-dir',required=True)
    parser.add_argument('--profile-id');parser.add_argument('--ffprobe');parser.add_argument('--columns',type=int,default=5);parser.add_argument('--page-size',type=int,default=25)
    args=parser.parse_args()
    if args.columns<1 or args.columns>10 or args.page_size<1 or args.page_size>100:parser.error('Contact sheets require columns1..10 and page-size1..100.')
    report=verify_archive(args.base_dir,args.output_dir,args.profile_id,args.ffprobe,args.columns,args.page_size)
    print(json.dumps({'summary':report['summary'],'report':str(Path(args.output_dir).resolve()/'archive-verification.json'),'contactSheets':[s['file'] for s in report['contactSheets']]}))
    return 1 if report['summary']['failed'] or report['summary']['manifestErrors'] else 0


if __name__=='__main__':raise SystemExit(main())
