"""Attach actual acquisition dates to the citation/terms/source mapping registry."""
import json
from pathlib import Path


def build_registry(data, template):
    data=Path(data);registry=json.loads(Path(template).read_text())
    assets=json.loads((data/'enrichment/manifest.json').read_text())['assets']
    def dates(value):
        if isinstance(value,dict):
            if value.get('retrieved_at'):yield value['retrieved_at']
            for child in value.values():yield from dates(child)
        elif isinstance(value,list):
            for child in value:yield from dates(child)
    for source in registry['sources']:
        stamps=[];metadata=[]
        for prefix in source.pop('metadata_prefixes'):
            for path in sorted((data/prefix).rglob('*.metadata.json')):
                stamps.extend(dates(json.loads(path.read_text())));metadata.append(str(path.relative_to(data)))
        match={'swdi':'ncei.noaa.gov/swdiws/','iem':'mesonet.agron.iastate.edu/','nlcd':'dmsdata.cr.usgs.gov/'}
        if source['id'] in match:
            selected=[a for a in assets if match[source['id']] in a['url']]
            stamps.extend(a['retrieved_at'] for a in selected);metadata=['enrichment/manifest.json']
            source['source_asset_count']=len(selected)
        if not stamps:raise ValueError('Missing source access dates: '+source['id'])
        source['first_accessed_at']=min(stamps);source['last_accessed_at']=max(stamps)
        source['metadata_files']=metadata
        source['recommended_citation']+=f" {source['url']} Accessed {min(stamps)[:10]} to {max(stamps)[:10]}; exact per-file timestamps and hashes in the release metadata."
    registry['provenance_statement']='Independent compilation. Source observations and original records are distinguished from filtering, format conversions, source linkage, geometry construction and ML aggregation. Providers do not endorse derived outputs.'
    return registry
