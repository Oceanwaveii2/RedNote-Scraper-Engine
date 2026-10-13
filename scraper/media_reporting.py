"""Structural archive accounting. Source prose is never interpreted or cached.

Saved counts describe recorded logical assets. Verification is a separate file,
size and recorded-SHA check; it does not assert image or video decoding.
"""
from collections import Counter, OrderedDict, defaultdict
from datetime import datetime, timezone
import re
import time


_ARCHIVE_CACHE_TTL = 300
_ARCHIVE_CACHE_LIMIT = 1500
_DESCRIPTOR_FIELDS = (
    'kind', 'type', 'sourceIndex', 'assetId', 'mediaId', 'file', 'path',
    'sha256', 'bytes', 'status', 'archiveStatus', 'mime',
)
_COMMENT_COVERAGE_FIELDS = (
    'status', 'captured', 'expected', 'attachments', 'savedAttachments',
    'unarchivedAttachments', 'attachmentCoverage', 'attachmentReason',
    'attachmentsDeferred', 'idHash',
)


def _number(value):
    """Return an exact nonnegative count, never coerce missing totals to zero."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, str) and re.fullmatch(r'\d+', value):
        return int(value)
    return None


def _descriptor(asset):
    return {key: asset[key] for key in _DESCRIPTOR_FIELDS if key in asset}


def _inventory(value):
    value = value if isinstance(value, dict) else {}
    return {key: value[key] for key in (
        'sourceDeclared', 'images', 'videos', 'discoveredImages',
        'discoveredVideos',
    ) if key in value}


def _descriptor_signature(media):
    return tuple(tuple(str(asset.get(key, '')) for key in _DESCRIPTOR_FIELDS)
                 for asset in media if isinstance(asset, dict))


def _archive_descriptors(service, job, row):
    """Read changed archive rows; cache descriptors, never source bodies.

    Row counters and asset state invalidate immediately. A bounded TTL covers
    external archive changes that do not alter queue counters. Files are checked
    each reconciliation using the service's stat-keyed SHA cache.
    """
    cache = getattr(service, '_media_reporting_archive_cache', None)
    if cache is None:
        cache = service._media_reporting_archive_cache = OrderedDict()
    key = (str(job.get('profileId', '')), str(row.get('postId', '')))
    comments = (row.get('coverage') or {}).get('comments') or {}
    signature = (
        row.get('commentCount'), row.get('status'),
        tuple(str(comments.get(field, '')) for field in _COMMENT_COVERAGE_FIELDS),
        _descriptor_signature(row.get('media') or []),
        tuple(sorted((str(k), str(v)) for k, v in
                     _inventory(row.get('sourceMediaInventory')).items())),
    )
    now = time.monotonic()
    entry = cache.get(key)
    if entry and entry['signature'] == signature and now - entry['loadedAt'] < _ARCHIVE_CACHE_TTL:
        cache.move_to_end(key)
        return entry
    # An untouched queue row has no retained discussion descriptors to read.
    retained = bool(row.get('commentCount') or row.get('media') or comments)
    prior = service._prior_post(job.get('profileId'), row.get('postId')) if retained else {}
    prior = prior if isinstance(prior, dict) else {}
    attachments = []
    seen_comments = set()
    pending = list(prior.get('comments') or [])
    while pending:
        comment = pending.pop()
        if not isinstance(comment, dict):
            continue
        identity = comment.get('id') or comment.get('commentId') or comment.get('comment_id')
        identity = ('id', str(identity)) if identity else ('object', id(comment))
        if identity in seen_comments:
            continue
        seen_comments.add(identity)
        attachments.extend(_descriptor(asset) for asset in comment.get('attachments') or []
                           if isinstance(asset, dict))
        for field in ('replies', 'children', 'reply', 'subComments', 'sub_comments'):
            children = comment.get(field)
            if isinstance(children, dict):
                pending.append(children)
            elif isinstance(children, list):
                pending.extend(children)
    entry = {
        'signature': signature, 'loadedAt': now, 'attachments': attachments,
        'media': [_descriptor(asset) for asset in prior.get('media') or []
                  if isinstance(asset, dict)],
        'inventory': _inventory(prior.get('sourceMediaInventory')),
    }
    cache[key] = entry
    cache.move_to_end(key)
    while len(cache) > _ARCHIVE_CACHE_LIMIT:
        cache.popitem(last=False)
    return entry


def _kind(asset, attachment=False):
    value = str(asset.get('kind') or asset.get('type') or 'unknown')
    if attachment:
        if value in {'emoji', 'comment_emoji'}:
            return 'comment_emoji'
        if value in {'gif', 'sticker', 'gift'}:
            return 'comment_' + value
        if value in {'author_avatar', 'comment_avatar', 'avatar'}:
            return 'comment_avatar'
        return 'comment_image' if value in {'image', 'photo', 'comment_image'} else 'comment_other'
    return 'photo' if value == 'image' else value


def _slot(asset, ordinal):
    kind = _kind(asset)
    # Quality renditions are one logical video. Indexed carousel items remain
    # separate even when two items happen to share identical bytes.
    if kind == 'video':
        return kind, 1
    index = asset.get('sourceIndex')
    if index is not None:
        return kind, str(index)
    identity = (asset.get('assetId') or asset.get('mediaId') or asset.get('path')
                or asset.get('file') or asset.get('sha256'))
    return kind, str(identity) if identity else ('unindexed', ordinal)


def _is_saved(asset):
    return (asset.get('status') in {'saved', 'downloaded'}
            or asset.get('archiveStatus') == 'saved')


def _state(service, asset):
    expected = asset.get('sha256')
    if expected and not re.fullmatch(r'[a-fA-F0-9]{64}', str(expected)):
        return 'invalid'
    # Presence-only legacy descriptors must remain visible as unverified even
    # when the worker's stricter reuse validator requires both size and SHA.
    try:
        base = service.base_dir.resolve()
        raw = asset.get('path') or asset.get('file') or ''
        path = (base / raw).resolve()
        if not raw or not path.is_relative_to(base) or not path.is_file():
            return 'invalid'
        size = path.stat().st_size
        if size <= 0:
            return 'invalid'
        recorded_size = asset.get('bytes')
        if recorded_size is not None:
            exact_size = _number(recorded_size)
            if exact_size is None or exact_size <= 0 or size != exact_size:
                return 'invalid'
        if not expected or recorded_size is None:
            return 'unverified'
    except (OSError, TypeError, ValueError):
        return 'invalid'
    if not service._saved_exists(asset):
        return 'invalid'
    return 'verified'


def _path(asset):
    raw = asset.get('path') or asset.get('file')
    return str(raw).replace('\\', '/') if raw else None


def reconcile(service, job):
    checked_at = datetime.now(timezone.utc).isoformat()
    counts = Counter(); verified = Counter(); invalid = Counter(); unverified = Counter()
    declared = Counter(); observed = Counter(); unknown = 0
    paths = set(); hashes = set(); attachment_files = {}
    attachment_refs = Counter()
    rank = {'invalid': 0, 'unverified': 1, 'verified': 2}
    for row in job.get('posts') or []:
        retained = _archive_descriptors(service, job, row)
        inventory = retained['inventory'].copy()
        inventory.update(_inventory(row.get('sourceMediaInventory')))
        declared_images = _number(inventory.get('images')) if inventory.get('sourceDeclared') is True else None
        declared_videos = _number(inventory.get('videos')) if inventory.get('sourceDeclared') is True else None
        source_complete = declared_images is not None and declared_videos is not None
        if not source_complete:
            unknown += 1
        if declared_images is not None: declared['images'] += declared_images
        if declared_videos is not None: declared['videos'] += declared_videos
        grouped = defaultdict(list)
        # Queue media is authoritative; archive media is a legacy fallback.
        media = row.get('media') or retained['media']
        for ordinal, asset in enumerate(media):
            if isinstance(asset, dict):
                grouped[_slot(asset, ordinal)].append(asset)
        unique_images = sum(kind == 'photo' for kind, _ in grouped)
        unique_videos = sum(kind == 'video' for kind, _ in grouped)
        media_coverage = (row.get('coverage') or {}).get('media') or {}
        discovered_images = max(unique_images, _number(inventory.get('discoveredImages')) or 0,
                                _number(media_coverage.get('discoveredImages')) or 0,
                                _number(media_coverage.get('observedImages')) or 0)
        discovered_videos = min(1, max(unique_videos, _number(inventory.get('discoveredVideos')) or 0,
                                     _number(media_coverage.get('discoveredVideos')) or 0,
                                     _number(media_coverage.get('observedVideos')) or 0))
        observed['images'] += discovered_images; observed['videos'] += discovered_videos
        post_counts = Counter(); post_states = Counter()
        for (kind, _), variants in grouped.items():
            saved = [asset for asset in variants if _is_saved(asset)]
            if not saved:
                continue
            classified = [(_state(service, asset), asset) for asset in saved]
            state, asset = max(classified, key=lambda pair: rank[pair[0]])
            counts[kind] += 1
            {'verified': verified, 'invalid': invalid, 'unverified': unverified}[state][kind] += 1
            if kind in {'photo', 'video'}:
                post_counts[kind] += 1; post_states[(state, kind)] += 1
                path = _path(asset)
                if path: paths.add(path)
                expected = asset.get('sha256')
                if expected and re.fullmatch(r'[a-fA-F0-9]{64}', str(expected)):
                    hashes.add(str(expected).lower())
        row['mediaAccounting'] = {
            'sourceDeclaredImages': declared_images, 'sourceDeclaredVideos': declared_videos,
            'discoveredImages': discovered_images, 'discoveredVideos': discovered_videos,
            'savedImages': post_counts['photo'], 'savedVideos': post_counts['video'],
            'verifiedImages': post_states[('verified', 'photo')],
            'verifiedVideos': post_states[('verified', 'video')],
            'invalidAssets': post_states[('invalid', 'photo')] + post_states[('invalid', 'video')],
            'unverifiedAssets': post_states[('unverified', 'photo')] + post_states[('unverified', 'video')],
            'checkedAt': checked_at, 'sourceCountComplete': source_complete,
        }
        for asset in retained['attachments']:
            attachment_refs['all'] += 1
            if not _is_saved(asset):
                continue
            kind = _kind(asset, attachment=True)
            counts[kind] += 1; attachment_refs['saved'] += 1
            state = _state(service, asset)
            {'verified': verified, 'invalid': invalid, 'unverified': unverified}[state][kind] += 1
            attachment_refs[state] += 1
            path = _path(asset)
            if path:
                # A shared path with conflicting descriptors keeps the weakest
                # result so one invalid reference is never hidden.
                previous = attachment_files.get(path)
                if previous is None or rank[state] < rank[previous]:
                    attachment_files[path] = state
    avatar = (job.get('profile') or {}).get('avatarMedia') or {}
    if _is_saved(avatar):
        counts['avatar'] += 1
        state = _state(service, avatar)
        {'verified': verified, 'invalid': invalid, 'unverified': unverified}[state]['avatar'] += 1
    unique_states = Counter(attachment_files.values())
    for kind in ('photo', 'video', 'cover', 'avatar', 'comment_emoji', 'comment_gif', 'comment_sticker', 'comment_gift', 'comment_image', 'comment_avatar', 'comment_other'):
        counts[kind] += 0; verified[kind] += 0; invalid[kind] += 0; unverified[kind] += 0
    return {
        'schema': 'rednote.media-counts/2', 'checkedAt': checked_at,
        'scope': job.get('scope','profile'),
        'savedByKind': dict(counts), 'hashVerifiedByKind': dict(verified),
        'invalidByKind': dict(invalid), 'unverifiedByKind': dict(unverified),
        'uniquePostMediaFiles': len(paths), 'uniqueRecordedPostMediaHashes': len(hashes),
        'uniqueSavedAttachmentFiles': len(attachment_files),
        'uniqueVerifiedAttachmentFiles': unique_states['verified'],
        'uniqueUnverifiedAttachmentFiles': unique_states['unverified'],
        'uniqueInvalidAttachmentFiles': unique_states['invalid'],
        'attachmentReferences': attachment_refs['all'],
        'savedAttachmentReferences': attachment_refs['saved'],
        'verifiedAttachmentReferences': attachment_refs['verified'],
        'unverifiedAttachmentReferences': attachment_refs['unverified'],
        'invalidAttachmentReferences': attachment_refs['invalid'],
        'sourceDeclaredImagesObserved': declared['images'],
        'sourceDeclaredVideosObserved': declared['videos'],
        'discoveredImages': observed['images'], 'discoveredVideos': observed['videos'],
        'notesWithoutSourceDeclaredTotals': unknown,
        'postMediaTotal': declared['images'] + declared['videos'] if job.get('scope')=='single_post' and not unknown else None,
        'profileMediaTotal': None if job.get('scope')=='single_post' or unknown or (job.get('enumeration') or {}).get('status') != 'complete' else declared['images'] + declared['videos'],
        'archiveDescriptorCacheTtlSeconds': _ARCHIVE_CACHE_TTL,
        'verification': 'Saved counts are recorded logical assets; valid recorded SHA-256, file presence and size distinguish verified, invalid and unverified assets. Playback/decoding is not asserted.',
    }
