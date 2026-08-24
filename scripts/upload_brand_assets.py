"""Publish the logo files to a public Supabase bucket for use in email.

An image in a sent email has to load from a URL that never expires and does
not depend on a deploy, which rules out our private buckets and the app's own
``/static`` path. This pushes the brand art to a public bucket on stable paths
and prints the URLs.

    python3 scripts/upload_brand_assets.py            # upload and print URLs
    python3 scripts/upload_brand_assets.py --dry-run  # show the plan only
    python3 scripts/upload_brand_assets.py --urls     # print URLs, upload nothing

Re-running overwrites in place, so the URLs in config stay good. Run
scripts/split_brand_logo.py first if the master art changed.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

BUCKET = 'brand-assets'

# A year: these files are versioned by name, never edited in place.
CACHE_CONTROL = '31536000'

# Where each local folder lands in the bucket. Order is the upload order.
FOLDERS: tuple[tuple[Path, str], ...] = (
    (REPO / 'origen_realty_logos', 'origen'),
    (REPO / 'static' / 'images' / 'brand', 'origen/email'),
    (REPO / 'static' / 'images', 'agentflow'),
)

# static/images holds favicons and share cards alongside the logos. Only the
# brand art belongs in a bucket other people will pull from.
AGENTFLOW_KEEP = re.compile(r'logo|favicon|apple-touch-icon|og-share')

CONTENT_TYPES = {'.png': 'image/png', '.jpg': 'image/jpeg', '.svg': 'image/svg+xml'}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true', help='list the plan, upload nothing')
    parser.add_argument('--urls', action='store_true', help='print public URLs, upload nothing')
    args = parser.parse_args(argv)

    from dotenv import load_dotenv
    load_dotenv()

    plan = collect()
    if not plan:
        print('Nothing to upload. Run scripts/split_brand_logo.py first.', file=sys.stderr)
        return 1

    if args.dry_run:
        for local, remote in plan:
            print(f'{local.relative_to(REPO)}  ->  {BUCKET}/{remote}')
        return 0

    from services.supabase_storage import get_supabase_client

    client = get_supabase_client()
    if not args.urls:
        ensure_bucket(client)

    width = max(len(remote) for _, remote in plan)
    for local, remote in plan:
        if not args.urls:
            put(client, local, remote)
        print(f'{remote.ljust(width)}  {public_url(client, remote)}')
    return 0


def collect() -> list[tuple[Path, str]]:
    plan: list[tuple[Path, str]] = []
    for folder, prefix in FOLDERS:
        if not folder.is_dir():
            continue
        for path in sorted(folder.iterdir()):
            if path.suffix.lower() not in CONTENT_TYPES or not path.is_file():
                continue
            if prefix == 'agentflow' and not AGENTFLOW_KEEP.search(path.stem.lower()):
                continue
            plan.append((path, f'{prefix}/{slug(path)}'))
    return plan


def slug(path: Path) -> str:
    """Bucket-safe name. Spaces and capitals in a URL are a support ticket."""
    stem = re.sub(r'[^a-z0-9]+', '-', path.stem.lower()).strip('-')
    return f'{stem}{path.suffix.lower()}'


def ensure_bucket(client) -> None:
    existing = {bucket.id for bucket in client.storage.list_buckets()}
    if BUCKET in existing:
        return
    client.storage.create_bucket(
        BUCKET,
        options={
            'public': True,
            'allowedMimeTypes': sorted(set(CONTENT_TYPES.values())),
            'fileSizeLimit': 5 * 1024 * 1024,
        },
    )
    print(f'Created public bucket {BUCKET}')


def put(client, local: Path, remote: str) -> None:
    client.storage.from_(BUCKET).upload(
        remote,
        local.read_bytes(),
        {
            'content-type': CONTENT_TYPES[local.suffix.lower()],
            'cache-control': CACHE_CONTROL,
            'upsert': 'true',
        },
    )


def public_url(client, remote: str) -> str:
    return client.storage.from_(BUCKET).get_public_url(remote).rstrip('?')


if __name__ == '__main__':
    raise SystemExit(main())
