"""Render the Kaggle cover from the collected SPC points and Census county map."""
from pathlib import Path
import json
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]

def main():
    spc = pd.read_csv(ROOT/'data/spc/tornadoes_2010_2025.csv')
    counties = json.loads((ROOT/'data/census_boundaries/counties_2020_5m.geojson').read_text())
    lines = []
    for feature in counties['features']:
        geometry = feature['geometry']
        polygons = geometry['coordinates'] if geometry['type'] == 'MultiPolygon' else [geometry['coordinates']]
        for polygon in polygons:
            ring = polygon[0]
            if all(-126 <= x <= -66 and 24 <= y <= 50 for x,y,*_ in ring):
                lines.append(ring)
    fig = plt.figure(figsize=(5.6, 2.8), dpi=100, facecolor='#101f30')
    ax = fig.add_axes([.07,.18,.86,.57], facecolor='#101f30')
    ax.add_collection(LineCollection(lines, colors='#42677d', linewidths=.18, alpha=.7))
    selected = spc[spc.slon.between(-126,-66) & spc.slat.between(24,50)]
    ax.scatter(selected.slon, selected.slat, s=.6, color='#ffd28b', alpha=.45, linewidths=0)
    ax.set(xlim=(-126,-66), ylim=(24,50)); ax.set_aspect(1.25); ax.axis('off')
    fig.text(.5,.88,'U.S. TORNADO DATA',ha='center',color='#f1f6fb',fontsize=17,weight='bold')
    fig.text(.5,.79,'2010–2025',ha='center',color='#7bdfdb',fontsize=12,weight='bold')
    fig.text(.5,.12,'SPC start locations · contiguous U.S.',ha='center',color='#b3c6d4',fontsize=7)
    fig.text(.5,.045,'SPC  /  NCEI  /  FOOTPRINTS  /  CENSUS',ha='center',color='#7bdfdb',fontsize=7)
    path=ROOT/'release/kaggle/dataset-cover-image.png'
    path.parent.mkdir(parents=True,exist_ok=True)
    fig.savefig(path,dpi=100,facecolor=fig.get_facecolor()); plt.close(fig)
    print(path)

if __name__ == '__main__':main()
