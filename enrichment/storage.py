"""Retention contracts for extracted data versus independently replayable bytes."""
from pathlib import Path

from .common import digest


def supporting_file(url, suffix):
    # Original warning text and small inventories/schema/grid descriptions are
    # useful evidence. API records and geometries survive in the linked tables.
    return suffix in {'.txt', '.idx', '.xml', '.html'} or url.rstrip('/').endswith('/swdiws/json')


def verify_asset(root, asset):
    path=(Path(root)/asset['path']).resolve()
    if not path.is_relative_to(Path(root).resolve()):
        raise ValueError('Nonportable asset path')
    if not path.exists():
        if asset.get('retention')=='optional':
            return 'intentionally_not_retained'
        raise ValueError(f'Required source asset missing: {asset["asset_id"]}')
    if path.stat().st_size!=asset['bytes'] or digest(path)!=asset['sha256']:
        raise ValueError(f'Source asset differs: {asset["asset_id"]}')
    return 'bytes_verified'
