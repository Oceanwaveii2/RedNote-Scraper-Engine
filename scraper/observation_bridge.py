"""Credential-free browser evidence inbox and game-development export."""
import argparse
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit
from trait_signals import derive_trait_candidates

SCHEMA = 'noir.browser-observation/1'

def clean_url(value):
    url = urlsplit(value)
    if url.scheme != 'https' or url.hostname not in {'www.rednote.com', 'www.xiaohongshu.com'}:
        raise ValueError('Unsupported source URL')
    return f'https://{url.hostname}{url.path}'

def accept_observation(data, root):
    if not isinstance(data, dict) or data.get('schema') != SCHEMA:
        raise ValueError('Unsupported observation schema')
    text = data.get('title', '')
    if not isinstance(text, str) or len(text) > 4000:
        raise ValueError('Invalid title')
    coverage = data.get('coverage')
    if coverage not in {'profile-card', 'visible-note', 'visible-comments', 'reviewed-video'}:
        raise ValueError('Explicit coverage required')
    source = clean_url(data['sourceUrl'])
    observed = data.get('observedAt')
    if not isinstance(observed, str) or not observed:
        raise ValueError('Observation timestamp required')
    comments = data.get('comments', [])
    if not isinstance(comments, list) or len(comments) > 500:
        raise ValueError('Invalid comments')
    safe_comments = []
    for comment in comments:
        if not isinstance(comment, dict) or not isinstance(comment.get('text'), str) or len(comment['text']) > 10000:
            raise ValueError('Invalid comment text')
        author=comment.get('author','')
        identifier=comment.get('id','')
        if not isinstance(author,str) or len(author)>300 or not isinstance(identifier,str) or len(identifier)>300:
            raise ValueError('Invalid visible comment attribution')
        safe_comments.append({'id':identifier,'author':author,'text':comment['text'],'reply':bool(comment.get('reply',False))})
    caption=data.get('text','')
    if not isinstance(caption,str) or len(caption)>50000:
        raise ValueError('Invalid post text')
    record = dict(schema=SCHEMA, sourceUrl=source, title=text, observedAt=observed,
                  coverage=coverage, text=caption, comments=safe_comments,
                  authorStatus='unverified', mediaStatus='not-downloaded',
                  collector='codex-visible-browser', scope='observed-content-only')
    digest = hashlib.sha256(json.dumps(record, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    record['evidenceId'] = digest
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    destination = root / (digest + '.json')
    if not destination.exists():
        with destination.open('x', encoding='utf-8') as file:
            json.dump(record, file, ensure_ascii=False, indent=2)
    return record

def development_pack(records):
    candidates = []
    for record in records:
        result = derive_trait_candidates({'postId': record['evidenceId'], 'title': record['title'],'text':record.get('text','')}, {'comments': record['comments']})
        candidates.extend(result['candidates'])
    return {'schema': 'noir.development-evidence/1', 'evidence': records,
            'candidates': candidates, 'autoApplied': False,
            'runtimeAuthority': False, 'reviewRequired': True}

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('input', type=Path)
    parser.add_argument('--inbox', type=Path, required=True)
    parser.add_argument('--export', type=Path, required=True)
    args = parser.parse_args()
    rows = json.loads(args.input.read_text(encoding='utf-8-sig'))
    if not isinstance(rows, list) or len(rows) > 500:
        raise ValueError('Expected at most 500 observations')
    records = [accept_observation(row, args.inbox) for row in rows]
    args.export.parent.mkdir(parents=True, exist_ok=True)
    args.export.write_text(json.dumps(development_pack(records), ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'accepted': len(records), 'pack': str(args.export)}))
