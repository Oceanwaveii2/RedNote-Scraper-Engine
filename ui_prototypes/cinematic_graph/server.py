"""Loopback graph preview with a bounded read-only Mirror Storage bridge."""
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import json,re,socket
from urllib.request import Request,urlopen
from urllib.error import HTTPError,URLError
from ui_bridge import bridge
ROOT=Path(__file__).resolve().parent
INPUTS=Path(r'D:\0\Noir Fiction Engine\00_ADMIN\RedNote_Graph_UI_Pass1_20261008\video-inputs.json')
VIDEOS={str(v['slot']):Path(v['path']) for v in json.loads(INPUTS.read_text(encoding='utf-8-sig'))}
VIDEOS['4']=Path(r'C:\Users\User\OneDrive\Videos\4K Video Downloader+\Vanity 6 - Nasty Girl (1982) • TopPop.mp4')
class Handler(SimpleHTTPRequestHandler):
 def __init__(self,*a,**kw):super().__init__(*a,directory=str(ROOT),**kw)
 def json_response(self,data,status=200):
  body=json.dumps(data).encode('utf-8');self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
 def allowed_host(self):return self.headers.get('Host')=='127.0.0.1:5186'
 def do_POST(self):
  if not self.allowed_host() or self.headers.get('Origin')!='http://127.0.0.1:5186' or self.headers.get('Content-Type','').split(';')[0]!='application/json':self.json_response({'error':'Same-origin JSON requests required'},403);return
  try:
   route=self.path
   size=int(self.headers.get('Content-Length','0'))
   pipeline_routes={'/pipeline/compile':'compile','/pipeline/execute':'execute','/pipeline/export-docx':'export-docx'}
   if not 0<size<=(2097152 if route in pipeline_routes else 32768):raise ValueError('Request too large')
   raw=self.rfile.read(size)
   data=json.loads(raw)
   if not isinstance(data,dict):raise ValueError('Object required')
   if route in pipeline_routes:
    request=Request('http://127.0.0.1:5188/api/scatter/'+pipeline_routes[route],data=raw,
                    headers={'Content-Type':'application/json','X-Red-Engine-Action':'explicit'},method='POST')
    try:
     with urlopen(request,timeout=300 if route=='/pipeline/execute' else 45) as upstream:
      payload=upstream.read(4*1024*1024+1)
      if len(payload)>4*1024*1024:raise ValueError('Pipeline response too large')
      self.send_response(upstream.status);self.send_header('Content-Type','application/json');self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload)
    except HTTPError as exc:
     try:detail=json.loads(exc.read(4096)).get('error','Scatter pipeline rejected the request')
     except (ValueError,AttributeError):detail='Scatter pipeline rejected the request'
     self.json_response({'error':str(detail),'status':exc.code},exc.code)
    except (URLError,TimeoutError):
     self.json_response({'error':'Headless Scatter pipeline is offline or timed out'},503)
    return
   if route=='/bridge/connect':result=bridge.launch('connect',data)
   elif route=='/bridge/review':result=bridge.launch('review',data)
   elif route=='/bridge/chat':result=bridge.launch('chat',data)
   elif route=='/bridge/cancel':result=bridge.cancel(data.get('jobId'))
   else:self.json_response({'error':'Unknown route'},404);return
   self.json_response(result)
  except ValueError as e:self.json_response({'error':str(e)},400)
  except Exception:self.json_response({'error':'Bridge unavailable'},503)
 def do_GET(self):
  if not self.allowed_host():self.send_error(403);return
  route=self.path.split('?')[0]
  if route.startswith('/mirror/'):
   target=route.removeprefix('/mirror/')
   allowed=re.fullmatch(r'api/v2/(?:profiles|profiles/[A-Za-z0-9-]+/(?:posts|summary)|posts/[A-Za-z0-9-]+(?:/comments)?|assets/[A-Za-z0-9-]+/preview)',target)
   if not allowed:self.json_response({'error':'Mirror route not exposed'},403);return
   try:
    with urlopen(Request('http://127.0.0.1:5185/engine/'+target+('?' + self.path.split('?',1)[1] if '?' in self.path else '')),timeout=12) as source:
     data=source.read(4*1024*1024+1)
     if len(data)>4*1024*1024:self.json_response({'error':'Mirror response too large'},413);return
     self.send_response(source.status);self.send_header('Content-Type',source.headers.get('Content-Type','application/json'));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
   except HTTPError as exc:self.json_response({'error':'Mirror record unavailable'},exc.code)
   except (URLError,TimeoutError):self.json_response({'error':'Mirror Storage offline'},503)
   return
  if route=='/bridge/status':self.json_response(bridge.status());return
  if route=='/bridge/capabilities':self.json_response({'chat':True,'review':True,'mediaUpload':False});return
  if route=='/bridge/snapshot':self.json_response(bridge.snapshot());return
  if route.startswith('/bridge/job/'):
   try:self.json_response(bridge.job(route.removeprefix('/bridge/job/')))
   except ValueError:self.json_response({'error':'Unknown job'},404)
   return
  if route.startswith('/video/'):
   path=VIDEOS.get(route.removeprefix('/video/'))
   if not path or not path.is_file():self.send_error(404);return
   size=path.stat().st_size;start=0;end=size-1;partial=False
   if self.headers.get('Range'):
    m=re.fullmatch(r'bytes=(\d+)-(\d*)',self.headers['Range'])
    if not m:self.send_error(416);return
    start=int(m[1]);end=min(int(m[2]) if m[2] else end,end);partial=True
    if start>end:self.send_error(416);return
   self.send_response(206 if partial else 200);self.send_header('Content-Type','video/mp4');self.send_header('Accept-Ranges','bytes');self.send_header('Content-Length',str(end-start+1))
   if partial:self.send_header('Content-Range',f'bytes {start}-{end}/{size}')
   self.end_headers()
   try:
    with path.open('rb') as f:
     f.seek(start);left=end-start+1
     while left:
      chunk=f.read(min(left,1024*1024))
      if not chunk:break
      self.wfile.write(chunk);left-=len(chunk)
   except (BrokenPipeError,ConnectionResetError):pass
   return
  if route not in ('/','/index.html','/app.js','/app-pass2.js','/bridge-ui.js','/storage-ui.js','/board-ui.js','/style.css','/layout.json','/reference-review.html','/branch-ui.js','/cinematic-ui.js','/cinematic.css','/graph-navigation-ui.js','/graph-navigation-ui.css','/graph-jump-ui.js','/graph-jump-ui.css') and not route.startswith('/assets/'):
   self.send_error(404);return
  if '..' in route:self.send_error(404);return
  super().do_GET()
 def list_directory(self,path):self.send_error(404)
 def log_message(self,fmt,*args):pass
class PreviewServer(ThreadingHTTPServer):
 allow_reuse_address=False
 def server_bind(self):
  if hasattr(socket,'SO_EXCLUSIVEADDRUSE'):self.socket.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
  super().server_bind()
if __name__=='__main__':
 print('UI-only graph preview: http://127.0.0.1:5186/',flush=True)
 try:PreviewServer(('127.0.0.1',5186),Handler).serve_forever()
 finally:bridge.close()



