"""User-facing table locations, independent of download/checkpoint metadata."""
from pathlib import Path


def table_directory(output, manifest):
    relative = manifest.get('table_directory', 'tables')
    if relative not in ('tables', 'analysis', '../analysis'):
        raise ValueError('Unsupported source table directory')
    return Path(output) / relative


def new_table_directory(data, output):
    return '../analysis' if Path(output).resolve() == (Path(data)/'enrichment').resolve() else 'analysis'


def table_path(output, manifest, item):
    name = item['path']
    if Path(name).name != name or name in ('', '.', '..'):
        raise ValueError('Unsafe source table filename')
    return table_directory(output, manifest) / name
