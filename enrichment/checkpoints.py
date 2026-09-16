"""Immutable row batches shared by extraction jobs and verified checkpoints."""
from pathlib import Path
import gzip
import json

from .common import atomic_json, digest, stable_id


class SharedRows(tuple):
    """Rows treated as immutable; the logical digest is computed once per batch."""
    def __new__(cls, rows):
        obj = super().__new__(cls, rows)
        obj.identity = stable_id('rows', obj)
        return obj


class BatchedRows:
    def __init__(self, *parts):
        self.parts = tuple(p if isinstance(p, SharedRows) else SharedRows(p) for p in parts)

    def __iter__(self):
        for part in self.parts:
            yield from part

    def __len__(self):
        return sum(map(len, self.parts))

    def __getitem__(self, index):
        return list(self)[index]


def batches(rows):
    if isinstance(rows, BatchedRows):
        return rows.parts
    return (rows if isinstance(rows, SharedRows) else SharedRows(rows),)


class Checkpoints:
    """The coordinator writes each shared blob once, then references it by hash."""
    def __init__(self, output):
        self.output = Path(output)
        self.verified = {}

    def save(self, path, result):
        refs = {}
        for name, rows in result['tables'].items():
            refs[name] = []
            for part in batches(rows):
                identity = stable_id(name, part.identity).replace(':', '_')
                table_path = self.output / 'job_tables' / (identity + '.json.gz')
                stamp = (table_path.stat().st_size, table_path.stat().st_mtime_ns) if table_path.exists() else None
                cached = self.verified.get(identity)
                if not cached or cached[0] != stamp:
                    if not table_path.exists():
                        table_path.parent.mkdir(exist_ok=True)
                        temp = table_path.with_suffix('.tmp')
                        with gzip.open(temp, 'wt', encoding='utf-8') as stream:
                            json.dump(part, stream, sort_keys=True, default=str, allow_nan=False)
                        with gzip.open(temp, 'rt', encoding='utf-8') as stream:
                            if stable_id('rows', json.load(stream)) != part.identity:
                                raise ValueError('Checkpoint round trip failed')
                        temp.replace(table_path)
                    else:
                        with gzip.open(table_path, 'rt', encoding='utf-8') as stream:
                            if stable_id('rows', json.load(stream)) != part.identity:
                                raise ValueError('Changed shared job table')
                    ref = dict(path=table_path.relative_to(self.output).as_posix(), sha256=digest(table_path), identity=part.identity)
                    self.verified[identity] = ((table_path.stat().st_size, table_path.stat().st_mtime_ns), ref)
                refs[name].append(self.verified[identity][1])
        saved = dict(result, checkpoint_version=3, tables={}, table_refs=refs,
                     tables_digest=stable_id('checkpoint-batches', refs))
        atomic_json(path, saved)


def references(saved):
    for value in saved.get('table_refs', {}).values():
        yield from value if isinstance(value, list) else [value]


def load(path, output, memo=None):
    result = json.loads(Path(path).read_text())
    version = result.get('checkpoint_version', 2)
    if version == 3 and result['tables_digest'] != stable_id('checkpoint-batches', result['table_refs']):
        raise ValueError('Checkpoint extraction digest missing or changed')
    for name, value in result.get('table_refs', {}).items():
        parts = []
        for ref in value if isinstance(value, list) else [value]:
            table_path = (Path(output) / ref['path']).resolve()
            if not table_path.is_relative_to((Path(output) / 'job_tables').resolve()):
                raise ValueError('Unsafe shared job table path')
            def read():
                if digest(table_path) != ref['sha256']:
                    raise ValueError('Changed shared job table')
                if table_path.suffix == '.gz':
                    with gzip.open(table_path, 'rt', encoding='utf-8') as stream:
                        rows = json.load(stream)
                else:
                    rows = json.loads(table_path.read_text())
                part = SharedRows(rows)
                if version == 3 and part.identity != ref['identity']:
                    raise ValueError('Changed shared extraction rows')
                return part
            key = ('checkpoint', str(table_path), ref['sha256'], table_path.stat().st_mtime_ns, table_path.stat().st_size)
            parts.append(memo(key, read) if memo else read())
        result['tables'][name] = BatchedRows(*parts) if version == 3 else list(parts[0])
    if version != 3 and result.get('tables_digest') != stable_id('checkpoint-tables', result['tables']):
        raise ValueError('Checkpoint extraction digest missing or changed')
    return result
