"""Windowless local engine. Does not import app.py or desktop_bridge.py.

Archive API is shared with the legacy app as a backend module. No HTML routes,
static UI, browser launch or automatic capture recovery are registered.
"""
from pathlib import Path
import argparse
import json
from urllib.error import URLError
from urllib.request import urlopen
from flask import Flask, jsonify, request, redirect
from .service import EngineService


def create_engine_app(base_dir, browser=None, archive_base=None, research_port=5188, enable_derived=False):
    base = Path(base_dir).resolve()
    app = Flask('rednote_engine', static_folder=None)
    from rednote_v2.api import create_blueprint
    read_only=archive_base is not None
    if read_only:
        from .archive import ReadOnlyArchive
        bp=create_blueprint(Path(archive_base).resolve(),store_override=ReadOnlyArchive(archive_base),enable_assistant=False)
    else:
        bp=create_blueprint(base)
        # Only an owned writable archive may run import and derived workers.
        # The read-only mirror preview never starts either worker.
        if enable_derived:
            bp.start_owned_workers()
    app.register_blueprint(bp)
    engine = EngineService(base, browser)
    app.extensions['rednote_engine'] = engine
    app.extensions['rednote_archive'] = bp

    @app.before_request
    def local_only():
        if request.host.split(':')[0] != '127.0.0.1' or request.headers.get('Sec-Fetch-Site') == 'cross-site':
            return jsonify(error='Local origin required'),403
        if read_only and request.method not in {'GET','HEAD','OPTIONS'}:
            return jsonify(error='Read-only archive preview; mutation disabled'),403
        if read_only and request.path.startswith('/api/v2/codex'):
            return jsonify(error='Assistant not connected in archive preview'),403
        if read_only and request.path.startswith('/api/v2/assets/') and request.path.endswith('/preview'):
            # Read-only mode must not create derivative preview files in the old archive.
            return redirect(request.path.removesuffix('/preview'))

    @app.get('/api/engine/health')
    def health():
        capture_available=engine.browser_attached and not read_only
        return jsonify(version='2.2', ui=False, browserAttached=engine.browser_attached,
                       autoResume=False, capabilities=['archive-read', 'capture-status']+(['capture-control'] if capture_available else []),
                       captureAvailable=capture_available, archiveReadOnly=read_only)

    @app.get('/api/engine/system')
    def system():
        """Expose verified module boundaries without claiming model or capture readiness."""
        with bp.store.connection() as db:
            counts={name:db.execute('SELECT COUNT(*) FROM '+name).fetchone()[0]
                    for name in ('profiles','posts','comments','assets')}
            active_derived=db.execute("SELECT COUNT(*) FROM jobs WHERE state IN ('queued','running')").fetchone()[0]
        research={'state':'offline','presets':False,'studies':False,'mirror':False}
        try:
            with urlopen(f'http://127.0.0.1:{research_port}/api/health',timeout=0.8) as response:
                info=json.load(response)
            research={'state':'reachable','presets':bool(info.get('preset_database')),
                      'studies':bool(info.get('study_database')),
                      'mirror':bool(info.get('mirror_available'))}
        except (URLError,TimeoutError,ValueError,OSError):
            pass
        # The read-only preview and the native capture owner are different
        # processes. Report the native host separately; never claim this
        # preview can mutate the archive or resume a clone job.
        native={'state':'offline','browserControl':False,'archiveApi':False}
        host_file=Path(archive_base or base)/'harvest-host.json'
        try:
            host=json.loads(host_file.read_text(encoding='utf-8'))
            port=host.get('port')
            if host.get('schema')=='noir.harvest-host/1' and isinstance(port,int) and 1<=port<=65535:
                with urlopen(f'http://127.0.0.1:{port}/api/bridge/status',timeout=0.8) as response:
                    status=json.load(response)
                if status.get('schema')=='noir.harvest-host/1':
                    native={'state':'reachable','browserControl':bool(status.get('browserControl')),
                            'archiveApi':False}
                    try:
                        with urlopen(f'http://127.0.0.1:{port}/api/v2/health',timeout=0.8) as response:
                            health=json.load(response)
                        native['archiveApi']=health.get('schema')==1
                        worker=health.get('derivedWorkerAttached')
                        native['derivedWorkerAttached']=worker if isinstance(worker,bool) else None
                    except (URLError,TimeoutError,ValueError,OSError):
                        pass
        except (OSError,ValueError,TypeError,URLError,TimeoutError):
            pass
        return jsonify(schema='rednote.engine.system/1',ui=False,
                       archive={'state':'read_only' if read_only else 'owned', 'counts':counts},
                       acquisition={'browserAttached':engine.browser_attached,
                                    'captureAvailable':engine.browser_attached and not read_only,
                                    'autoResume':False,'nativeHost':native},
                       derivedJobs={'active':active_derived,'workerAttached':bp.worker is not None},
                       assistant={'registered':not read_only,'inferenceVerified':False},
                       research=research)

    @app.get('/api/engine/jobs')
    def jobs():
        # Only safe summary fields; do not return source URLs or comment bodies.
        data=engine.command('status')
        return jsonify(jobs=[{k:j.get(k) for k in ('jobId','profileId','status','reason','phase','resumeOnStartup','updatedAt','progress')} for j in data.get('jobs',[])])

    @app.post('/api/engine/commands/<action>')
    def command(action):
        if request.host.split(':')[0] != '127.0.0.1' or request.headers.get('Sec-Fetch-Site') == 'cross-site':
            return jsonify(error='Local origin required'),403
        if request.headers.get('Origin') != request.host_url.rstrip('/') or request.headers.get('X-RedNote-Action') != 'explicit':
            return jsonify(error='Explicit same-origin action required'),403
        request.max_content_length=256*1024
        p=request.get_json(silent=True)
        if not isinstance(p,dict):return jsonify(error='Object payload required'),400
        try:
            result=engine.command(action,p)
            # Collector results contain source details; command response is a receipt only.
            return jsonify(**{k:result.get(k) for k in ('jobId','status','reason','phase')})
        except (ValueError,KeyError) as exc:
            from profile_clone import safe_error
            return jsonify(error=safe_error(exc)),409

    return app


def main():
    parser=argparse.ArgumentParser(description='RedNote 2.2 engine, no UI')
    parser.add_argument('--base',type=Path,required=True)
    parser.add_argument('--port',type=int,default=5182)
    parser.add_argument('--read-only-archive',type=Path)
    parser.add_argument('--enable-derived',action='store_true',
                        help='Start derived-job and archive-import workers in owned writable mode')
    args=parser.parse_args()
    if args.enable_derived and args.read_only_archive:
        parser.error('--enable-derived requires an owned writable archive')
    if args.enable_derived and not (args.base/'data_v2'/'rednote-v2.sqlite').is_file():
        parser.error('--enable-derived requires an existing archive database at --base')
    from desktop_session import claim_session
    if claim_session(args.base) is None:
        raise SystemExit('Data directory already owned by another app. No second collector started.')
    app=create_engine_app(args.base,archive_base=args.read_only_archive,
                          enable_derived=args.enable_derived)
    from werkzeug.serving import make_server
    server=make_server('127.0.0.1',args.port,app,threaded=True)
    try:
        print(f'RedNote engine listening on 127.0.0.1:{server.server_port}; UI disabled; auto-resume disabled.',flush=True)
        server.serve_forever()
    finally:
        app.extensions['rednote_engine'].shutdown()
        app.extensions['rednote_archive'].store.stop.set()
        server.server_close()

if __name__ == '__main__':main()
