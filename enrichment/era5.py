"""Small ERA5 CDS subsets and explicitly retrospective environmental features."""
import json
import math
import os
from pathlib import Path
from urllib.parse import urlencode

import numpy as np
import pandas as pd

from .common import stable_id, utc

SURFACE_DATASET='reanalysis-era5-single-levels'
PRESSURE_DATASET='reanalysis-era5-pressure-levels'
# Native ECMWF CAPE, not relabelled as HRRR SBCAPE or mixed-layer CAPE.
SURFACE_FIELDS={
    59:('cape_j_kg','convective_available_potential_energy','J kg**-1'),
    167:('temperature_2m_k','2m_temperature','K'),
    168:('dewpoint_2m_k','2m_dewpoint_temperature','K'),
    165:('wind_u_10m_m_s','10m_u_component_of_wind','m s**-1'),
    166:('wind_v_10m_m_s','10m_v_component_of_wind','m s**-1'),
    134:('surface_pressure_pa','surface_pressure','Pa'),
    129:('surface_geopotential_m2_s2','geopotential','m**2 s**-2'),
}
PRESSURE_FIELDS={131:('wind_u_m_s','u_component_of_wind','m s**-1'),
                 132:('wind_v_m_s','v_component_of_wind','m s**-1'),
                 129:('geopotential_m2_s2','geopotential','m**2 s**-2')}
PRESSURE_LEVELS=(1000,975,950,925,900,875,850,825,800,775,750,700,650,600,550,500,450,400,350,300,250,200)


def group_key(event,config):
    stamp=utc(event['start_utc'])
    if pd.isna(stamp) or event['latitude'] is None or event['longitude'] is None:return None
    step=config.era5_tile_degrees
    return (stamp.strftime('%Y-%m'),math.floor(event['latitude']/step)*step,
            math.floor(event['longitude']/step)*step)


def plan_requests(events,config):
    groups={}
    for event in events:
        if event.get('usable'):
            key=group_key(event,config)
            if key is not None:groups.setdefault(key,[]).append(event)
    plans={}
    for (month,south,west),members in groups.items():
        times=[utc(e['start_utc']).floor('h') for e in members]
        common=dict(product_type=['reanalysis'],year=[month[:4]],month=[month[5:]],
            day=sorted({f'{t.day:02}' for t in times}),time=sorted({f'{t.hour:02}:00' for t in times}),
            area=[min(90,south+config.era5_tile_degrees),west,south,min(180,west+config.era5_tile_degrees)],
            data_format='grib',download_format='unarchived')
        requests={SURFACE_DATASET:dict(common,variable=[v[1] for v in SURFACE_FIELDS.values()]),
                  PRESSURE_DATASET:dict(common,variable=[v[1] for v in PRESSURE_FIELDS.values()],
                                        pressure_level=[str(v) for v in PRESSURE_LEVELS])}
        plan=dict(events=members,requests=requests)
        for event in members:plans[event['tornado_id']]=plan
    return plans


def cds_location(dataset,request,timeout):
    import cdsapi
    key=os.environ.get('CDSAPI_KEY')
    if not key and not Path('~/.cdsapirc').expanduser().is_file():
        raise RuntimeError('Configure CDS access in ~/.cdsapirc or CDSAPI_KEY and accept both ERA5 dataset terms')
    try:
        client=cdsapi.Client(url='https://cds.climate.copernicus.eu/api',key=key,
                             quiet=True,progress=False,timeout=timeout)
        result=client.retrieve(dataset,request)
        return result.location
    except Exception:
        # Never echo a credential, Authorization header or signed download URL.
        raise RuntimeError('CDS request failed; check credentials, ERA5 dataset terms and CDS service status') from None


def decode_batch(path,asset,dataset,events,config):
    from eccodes import codes_grib_new_from_file,codes_grib_find_nearest,codes_get,codes_release,codes_is_defined
    fields=SURFACE_FIELDS if dataset==SURFACE_DATASET else PRESSURE_FIELDS
    targets={}
    result={e['tornado_id']:[] for e in events}
    for event in events:targets.setdefault(utc(event['start_utc']).floor('h'),[]).append(event)
    seen=set()
    with path.open('rb') as stream:
        while (handle:=codes_grib_new_from_file(stream)) is not None:
            try:
                stamp=pd.to_datetime(str(codes_get(handle,'validityDate'))+
                    f"{codes_get(handle,'validityTime'):04d}",format='%Y%m%d%H%M',utc=True)
                if stamp not in targets:continue
                param=int(codes_get(handle,'paramId'))
                if param not in fields:raise ValueError('Unexpected ERA5 variable in requested subset')
                native_level=str(codes_get(handle,'typeOfLevel'))
                level=int(codes_get(handle,'level')) if dataset==PRESSURE_DATASET else 0
                if dataset==PRESSURE_DATASET and (native_level!='isobaricInhPa' or level not in PRESSURE_LEVELS):
                    raise ValueError('Unexpected ERA5 pressure level')
                variable,_,units=fields[param]
                if codes_get(handle,'units')!=units:raise ValueError(f'Unexpected ERA5 units for {variable}')
                if codes_is_defined(handle,'experimentVersionNumber'):
                    version=str(codes_get(handle,'experimentVersionNumber')).strip()
                    if version not in ('1','0001'):raise ValueError('Expected final ERA5 expver 1, not preliminary ERA5T')
                else:version=None
                identity=(stamp,param,level)
                if identity in seen:raise ValueError('Duplicate ERA5 field/time/level')
                seen.add(identity)
                for event in targets[stamp]:
                    nearest=min(codes_grib_find_nearest(handle,event['latitude'],event['longitude']),key=lambda v:v['distance'])
                    value=float(nearest['value'])
                    valid=np.isfinite(value) and abs(value)<1e10 and nearest['distance']<=config.era5_max_distance_km
                    result[event['tornado_id']].append(dict(
                        record_id=stable_id('era5-sample',[event['tornado_id'],stamp.isoformat(),variable,level]),
                        tornado_id=event['tornado_id'],variable=variable,level_hpa=level if level else None,
                        level_type='pressure' if level else 'single',dataset=dataset,valid_at=stamp.isoformat(),
                        value=value if valid else None,units=units,status='sampled' if valid else 'outside_distance_or_missing',
                        grid_latitude=nearest['lat'],grid_longitude=((nearest['lon']+180)%360)-180,
                        distance_km=nearest['distance'],param_id=param,native_type_of_level=native_level,
                        native_level=codes_get(handle,'level'),expver=version,
                        retrospective=True,available_by_onset=False,asset_id=asset))
            finally:codes_release(handle)
    expected=len(fields)*(len(PRESSURE_LEVELS) if dataset==PRESSURE_DATASET else 1)
    for rows in result.values():
        if len(rows)!=expected:raise ValueError('Incomplete ERA5 field/time/level response')
    return result


def collect_era5(event,cache,config):
    plan=cache.era5_plans[event['tornado_id']]
    rows=[]
    for dataset,request in plan['requests'].items():
        identity='https://cds.climate.copernicus.eu/api/resources/'+dataset+'?'+urlencode({'selection':stable_id('request',request)})
        path,asset=cache.fetch(lambda:cds_location(dataset,request,cache.timeout),suffix='.grib',
            identity_url=identity,request_metadata=dict(dataset=dataset,selection=request))
        batch=cache.memo(('era5',asset),lambda:decode_batch(path,asset,dataset,plan['events'],config))
        rows.extend(batch[event['tornado_id']])
    return {'era5_samples':rows}


def environmental_summary(rows,cutoff):
    """Features only for the retrospective view, even for a pre-onset valid hour."""
    result={k:None for k,_,_ in SURFACE_FIELDS.values()}
    result.update(bulk_shear_0_1km_m_s=None,bulk_shear_0_6km_m_s=None,
                  srh_0_1km_m2_s2=None,srh_0_3km_m2_s2=None,wind_speed_10m_m_s=None,
                  grid_distance_km=None,profile_status='missing')
    if not len(rows) or pd.isna(cutoff):return result
    valid=rows.loc[(utc(rows.valid_at)==utc(cutoff).floor('h')) & rows.status.eq('sampled')]
    surface=valid.loc[valid.level_type.eq('single')].set_index('variable')
    for key,_,_ in SURFACE_FIELDS.values():
        if key in surface.index:result[key]=float(surface.loc[key,'value'])
    if len(valid):result['grid_distance_km']=float(valid.distance_km.max())
    u,v=result['wind_u_10m_m_s'],result['wind_v_10m_m_s']
    if u is not None and v is not None:result['wind_speed_10m_m_s']=float(np.hypot(u,v))
    if any(result[k] is None for k in ['surface_pressure_pa','surface_geopotential_m2_s2','wind_u_10m_m_s','wind_v_10m_m_s']):return result
    profile=valid.loc[valid.level_type.eq('pressure')].pivot(index='level_hpa',columns='variable',values='value')
    needed={'geopotential_m2_s2','wind_u_m_s','wind_v_m_s'}
    if not needed<=set(profile.columns):return result
    profile=profile.dropna(subset=list(needed))
    profile['height']=(profile.geopotential_m2_s2-result['surface_geopotential_m2_s2'])/9.80665
    profile=profile.loc[(profile.index*100<result['surface_pressure_pa']) & (profile.height>10)].sort_values('height')
    # Surface anchor uses observed-model 10 m wind at 0 m AGL, an explicit approximation.
    z=np.r_[0.,profile.height.to_numpy()]
    up=np.r_[u,profile.wind_u_m_s.to_numpy()];vp=np.r_[v,profile.wind_v_m_s.to_numpy()]
    pressure=np.r_[result['surface_pressure_pa']/100,profile.index.to_numpy()]
    if len(z)<2 or np.any(np.diff(z)<=0) or np.any(np.diff(pressure)>=0):
        result['profile_status']='invalid_vertical_profile';return result
    for depth in (1,6):
        if z[-1]>=depth*1000:
            result[f'bulk_shear_0_{depth}km_m_s']=float(np.hypot(np.interp(depth*1000,z,up)-u,np.interp(depth*1000,z,vp)-v))
    if z[-1]<6000:
        result['profile_status']='insufficient_height_for_storm_motion';return result
    from metpy.calc import bunkers_storm_motion,storm_relative_helicity
    from metpy.units import units
    right,_,_=bunkers_storm_motion(pressure*units.hPa,up*units('m/s'),vp*units('m/s'),z*units.m)
    if not np.isfinite(right.magnitude).all():
        result['profile_status']='undefined_storm_motion';return result
    for depth in (1,3):
        _,_,total=storm_relative_helicity(z*units.m,up*units('m/s'),vp*units('m/s'),
            depth=depth*units.km,storm_u=right[0],storm_v=right[1])
        result[f'srh_0_{depth}km_m2_s2']=float(total.magnitude)
    result['profile_status']='derived_pressure_level_profile_10m_surface_anchor'
    return result
