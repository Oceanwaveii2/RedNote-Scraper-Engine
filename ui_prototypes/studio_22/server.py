"""Separate prototype presentation host; GET-only allowlisted engine handoff."""
from pathlib import Path
import argparse
from urllib.request import urlopen,Request
from urllib.error import HTTPError,URLError
from flask import Flask,send_from_directory,request,Response,jsonify
import re


def create_app(engine_port=5182):
    if not 1<=engine_port<=65535:raise ValueError('Invalid engine port')
    root=Path(__file__).parent
    app=Flask('rednote_prototype22',static_folder=None)
    @app.before_request
    def guard():
        if request.host.split(':')[0]!='127.0.0.1' or request.headers.get('Sec-Fetch-Site')=='cross-site':
            return jsonify(error='Local origin required'),403
    @app.get('/')
    def index():return send_from_directory(root,'index.html')
    @app.get('/<path:path>')
    def static_file(path):
        if path not in {'app.js','style.css','icons.svg'} and not path.startswith('art/'):
            return jsonify(error='Not found'),404
        return send_from_directory(root,path)
    @app.get('/engine/<path:path>')
    def engine_read(path):
        allowed=re.fullmatch(r'api/(?:engine/health|v2/(?:health|profiles|jobs|profiles/[A-Za-z0-9-]+/(?:posts|summary)|posts/[A-Za-z0-9-]+(?:/comments)?|assets/[A-Za-z0-9-]+(?:/preview)?))',path)
        if not allowed:return jsonify(error='Read route not exposed'),403
        query=request.query_string.decode('ascii','ignore')
        url=f'http://127.0.0.1:{engine_port}/{path}'+('?' + query if query else '')
        try:
            result=urlopen(Request(url,headers={'Range':request.headers['Range']} if request.headers.get('Range') else {}),timeout=20)
            if path.startswith('api/v2/assets/'):
                def chunks():
                    try:
                        while True:
                            chunk=result.read(65536)
                            if not chunk:break
                            yield chunk
                    finally:result.close()
                headers={k:result.headers[k] for k in ['Content-Length','Content-Range','Accept-Ranges'] if result.headers.get(k)}
                headers.update({'Cache-Control':'private, max-age=60','X-Content-Type-Options':'nosniff'})
                return Response(chunks(),status=result.status,content_type=result.headers.get('Content-Type','application/octet-stream'),headers=headers)
            with result:
                data=result.read(32*1024*1024+1)
                if len(data)>32*1024*1024:return jsonify(error='Response too large'),413
                return Response(data,status=result.status,content_type=result.headers.get('Content-Type','application/json'),headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})
        except HTTPError as exc:return jsonify(error='Engine record unavailable'),exc.code
        except (URLError,TimeoutError):return jsonify(error='Engine disconnected'),503
    return app

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--port',type=int,default=5183)
    parser.add_argument('--engine-port',type=int,default=5182)
    args=parser.parse_args()
    from werkzeug.serving import make_server
    server=make_server('127.0.0.1',args.port,create_app(args.engine_port),threaded=True)
    print(f'RedNote prototype http://127.0.0.1:{server.server_port} (separate presentation host)',flush=True)
    try:server.serve_forever()
    finally:server.server_close()
