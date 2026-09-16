"""Retention contracts for extracted data versus independently replayable bytes."""
from pathlib import Path
import json

from .common import digest, stable_id


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
        # A compact cache can fetch a new version at the same request path.
        # The old optional body is still intentionally absent, not corrupted.
        # Prove the replacement against its own metadata; never accept changed
        # required files or untracked/corrupt cache bytes.
        sidecar = path.with_suffix(path.suffix + '.json')
        if asset.get('retention') == 'optional' and sidecar.exists():
            current = json.loads(sidecar.read_text())
            if (current.get('request_id') == asset.get('request_id')
                    and current.get('url') == asset.get('url')
                    and current.get('retention') == 'optional'
                    and current.get('asset_id') != asset['asset_id']
                    and current.get('asset_id') == stable_id('asset', [current.get('request_id'), current.get('sha256')])
                    and path.stat().st_size == current.get('bytes')
                    and digest(path) == current.get('sha256')):
                return 'intentionally_not_retained'
        raise ValueError(f'Source asset differs: {asset["asset_id"]}')
    return 'bytes_verified'
