"""Read-only local dataset inspection; no downloader imports or network clients."""

from collections import Counter
from itertools import islice
from pathlib import Path
import csv
import gzip
import hashlib
import json


def local_path(data, relative):
    """Resolve a manifest reference strictly inside the local data directory."""
    data = Path(data).resolve()
    path = (data / relative).resolve()
    if not path.is_relative_to(data):
        raise ValueError(f'Path is outside the dataset: {relative}')
    return path


def load_json(path, default=None):
    path = Path(path)
    return json.loads(path.read_text()) if path.is_file() else default


def csv_preview(path, limit=10):
    path = Path(path)
    if not path.is_file():
        return []
    opener = gzip.open if path.suffix == '.gz' else open
    with opener(path, 'rt', encoding='utf-8-sig', newline='') as f:
        return list(islice(csv.DictReader(f), limit))


def inventory(data):
    """Count bytes/files by source folder, without reading binary payloads."""
    data = Path(data)
    counts, sizes = Counter(), Counter()
    for path in data.rglob('*'):
        if path.is_file() and path.name != '.gitkeep':
            relative = path.relative_to(data)
            source = relative.parts[0] if len(relative.parts) > 1 else '(manifests)'
            counts[source] += 1
            sizes[source] += path.stat().st_size
    return [dict(folder=s, files=counts[s], gib=round(sizes[s]/1024**3, 4)) for s in sorted(counts)]


def reference_check(data, reference):
    """Verify a small manifest reference; never scans all data files implicitly."""
    path = local_path(data, reference['path'])
    if not path.is_file():
        return dict(path=reference['path'], status='missing')
    with path.open('rb') as f:
        actual = hashlib.file_digest(f, 'sha256').hexdigest()
    return dict(path=reference['path'], status='matches' if actual == reference.get('sha256') else 'changed')


def dat_preview(data, manifest, layer='points', limit=10):
    data = Path(data).resolve()
    for output in manifest.get('outputs', []):
        if output.get('source') != 'DAT' or output.get('layer') != layer:
            continue
        index_path = local_path(data, output['path'])
        index = load_json(index_path, {})
        for batch in index.get('batches', []):
            batch_path = local_path(data, str(index_path.parent.relative_to(data) / batch['file']))
            value = load_json(batch_path, {})
            if value.get('features'):
                return [dict(**f['properties'], geometry_type=(f.get('geometry') or {}).get('type'))
                        for f in value['features'][:limit]]
    return []
