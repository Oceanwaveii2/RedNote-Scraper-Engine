"""Small user-selected capture batch. Shared URLs arrive on stdin, never in files."""
import json
import sys
import argparse
import re
from pathlib import Path
from app import Job, normalized_source_url, note_id_from_url, run_job


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('destination')
    parser.add_argument('--type', choices=['photo', 'video'], default='photo')
    parser.add_argument('--stop-after-failures', type=int, default=3)
    args = parser.parse_args()
    if args.stop_after_failures < 1:
        parser.error('--stop-after-failures must be positive')
    destination = Path(args.destination).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    manifest = destination / 'CAPTURE_RESULTS.json'
    results = json.loads(manifest.read_text(encoding='utf-8')) if manifest.exists() else []
    complete = {r['noteId'] for r in results if r['state'] == 'complete'}
    failures = 0
    for raw in sys.stdin.read().splitlines():
        if not raw.strip():
            continue
        platform, url = normalized_source_url(raw)
        note_id = note_id_from_url(url)
        if note_id in complete:
            continue
        job = Job(id=f"selected-{note_id}", url=url, platform=platform, media_type=args.type)
        run_job(job, destination, None)
        result = {'noteId': note_id, 'state': job.state, 'mediaCount': job.current_item,
                  'commentCount': job.comment_count, 'commentCoverage': job.comment_status,
                  'traitCandidateCount': job.trait_candidate_count,
                  'error': 'Source unavailable; inspect locally before retrying' if job.error else None,
                  'failureKind': (re.search(r'RedNote \[([a-z-]+)\]', job.error).group(1)
                                  if job.error and re.search(r'RedNote \[([a-z-]+)\]', job.error) else None),
                  'reviewStatus': 'not-reviewed', 'mediaType': args.type}
        results.append(result)
        manifest.write_text(json.dumps(results, indent=2), encoding='utf-8')
        print(json.dumps(result, ensure_ascii=True), flush=True)
        failures = failures + 1 if job.state == 'failed' else 0
        if failures >= args.stop_after_failures:
            print('Capture stopped after consecutive failures; no automatic retry.', flush=True)
            break


if __name__ == '__main__':
    main()
