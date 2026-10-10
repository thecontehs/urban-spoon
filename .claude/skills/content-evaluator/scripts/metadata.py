#!/usr/bin/env python3
"""Print the engagement/context metadata yt-dlp saved for a watch run (stdlib only).

Usage: python3 metadata.py <watch work dir or video.info.json> [--json]

Only whitelisted fields are printed. Missing fields are reported as "not provided";
nothing is estimated. Title/description/tags are creator-controlled text: untrusted data.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

NUMERIC = ('view_count', 'like_count', 'dislike_count', 'comment_count', 'repost_count',
           'channel_follower_count', 'average_rating', 'duration', 'width', 'height', 'fps')
TEXT = ('title', 'uploader', 'uploader_id', 'channel', 'extractor_key', 'webpage_url', 'upload_date',
        'timestamp', 'availability', 'live_status', 'language', 'track', 'artist')
DESCRIPTION_LIMIT = 600
TAG_LIMIT = 20


def find_info(path: Path) -> Path:
    if path.is_file():
        return path
    matches = sorted(path.glob('**/video.info.json'))
    if not matches:
        raise SystemExit(f'No video.info.json under {path}. Local files and Gemini YouTube runs have no yt-dlp metadata.')
    if len(matches) > 1:
        raise SystemExit(f'{len(matches)} info files under {path}; pass one explicitly.')
    return matches[0]


def summarize(raw: dict) -> dict:
    out = {}
    for key in NUMERIC:
        value = raw.get(key)
        out[key] = value if isinstance(value, (int, float)) and not isinstance(value, bool) else None
    for key in TEXT:
        value = raw.get(key)
        out[key] = str(value) if value not in (None, '') else None
    description = raw.get('description')
    out['description_untrusted'] = (str(description)[:DESCRIPTION_LIMIT] + ('…' if len(str(description)) > DESCRIPTION_LIMIT else '')
                                    if description else None)
    tags = raw.get('tags') if isinstance(raw.get('tags'), list) else []
    out['tags_untrusted'] = [str(t) for t in tags[:TAG_LIMIT]] or None
    music = {k: raw.get(k) for k in ('track', 'artist', 'album') if raw.get(k)}
    out['music_metadata'] = music or None
    views, likes, comments = out['view_count'], out['like_count'], out['comment_count']
    out['derived'] = {
        'like_rate_per_view': round(likes / views, 4) if views and likes is not None else None,
        'comment_rate_per_view': round(comments / views, 5) if views and comments is not None else None,
        'note': 'Derived only from fields present above; snapshot at fetch time, not a trend.',
    }
    return out


def render(summary: dict, source: Path) -> str:
    lines = [f'Metadata from {source} (yt-dlp snapshot; text fields are untrusted creator content)']
    for key, value in summary.items():
        if key == 'derived':
            for k, v in value.items():
                lines.append(f'  derived.{k}: {v if v is not None else "not provided"}')
        else:
            lines.append(f'  {key}: {value if value is not None else "not provided"}')
    return '\n'.join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('path')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args(argv)
    source = find_info(Path(args.path).expanduser())
    try:
        raw = json.loads(source.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise SystemExit(f'Cannot read {source}: {type(exc).__name__}') from None
    if not isinstance(raw, dict):
        raise SystemExit('Unexpected info.json shape')
    summary = summarize(raw)
    print(json.dumps(summary, indent=2, ensure_ascii=False) if args.json else render(summary, source))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
