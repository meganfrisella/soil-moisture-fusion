"""Render the reviewed provenance overview as self-contained interactive HTML."""

from html import escape
import json
from urllib.parse import urlsplit

from data_provenance.overview import project_overview
from data_provenance.png import caption_position, read_graph


def graph_svg(nodes, edges):
    """Render the existing overview routes and label-sized boxes as inline SVG."""
    occupied = []
    for edge in edges:
        if edge["label"]:
            edge["caption_position"] = caption_position(edge, nodes, occupied)
    boxes = [n["box"] for n in nodes.values()] + occupied
    x, y = min(b[0] for b in boxes)-40, min(b[1] for b in boxes)-40
    w = max(b[0]+b[2] for b in boxes)-x+40
    h = max(b[1]+b[3] for b in boxes)-y+40
    colors = sorted({e["color"] for e in edges})
    parts = [f'<svg id="graph" xmlns="http://www.w3.org/2000/svg" viewBox="{x} {y} {w} {h}" '
             'aria-label="Interactive product provenance graph"><defs>']
    for i, color in enumerate(colors):
        parts.append(f'<marker id="arrow{i}" viewBox="0 0 10 10" refX="9" refY="5" '
                     f'markerWidth="8" markerHeight="8" orient="auto-start-reverse">'
                     f'<path d="M 0 0 L 10 5 L 0 10 z" fill="{color}"/></marker>')
    parts.append('</defs>')
    for node in nodes.values():
        if node["frame"]:
            a, b, c, d = node["box"]
            parts.append(f'<rect x="{a}" y="{b}" width="{c}" height="{d}" rx="12" '
                         'fill="#F8FAFC" stroke="#CBD5E1"/>')
            parts.append(f'<text x="{a+c/2}" y="{b+25}" text-anchor="middle" font-size="15">'
                         f'{escape(node["label"])}</text>')
    for edge in edges:
        points = " ".join(f"{a},{b}" for a, b in edge["points"])
        dash = ' stroke-dasharray="8 5"' if edge["dashed"] else ''
        parts.append(f'<g class="edge" data-up="{escape(edge["up"])}" data-down="{escape(edge["down"])}">'
                     f'<title>{escape(edge["label"] or "Upstream input to dependent product")}</title>'
                     f'<polyline points="{points}" fill="none" stroke="{edge["color"]}" '
                     f'stroke-width="{max(1.4, edge["width"])}"{dash} '
                     f'marker-end="url(#arrow{colors.index(edge["color"])})"/></g>')
    for node_id, node in nodes.items():
        if node["frame"]:
            continue
        a, b, c, d = node["box"]
        label = escape(node["label"])
        parts.append(f'<g class="node" data-node="{escape(node_id)}" tabindex="0" role="button" '
                     f'aria-label="{label}: show linked products">'
                     f'<title>{label} — click for linked products</title>'
                     f'<rect x="{a}" y="{b}" width="{c}" height="{d}" rx="12" '
                     f'fill="{node["fill"]}" stroke="#64748B" stroke-width="1.5"/>')
        lines = node["label"].splitlines()
        for i, line in enumerate(lines):
            parts.append(f'<text x="{a+c/2}" y="{b+d/2+(i-(len(lines)-1)/2)*22}" '
                         f'text-anchor="middle" dominant-baseline="central" font-size="16" '
                         f'font-weight="{"600" if node["bold"] else "400"}">{escape(line)}</text>')
        parts.append('</g>')
    for edge in edges:
        if edge["label"]:
            a, b = edge["caption_position"]
            parts.append(f'<text class="caption" x="{a}" y="{b}" text-anchor="middle" '
                         f'dominant-baseline="central" font-size="12" fill="{edge["color"]}">'
                         f'{escape(edge["label"])}</text>')
    parts.append('</svg>')
    return "\n".join(parts)


PAGE = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Soil moisture data provenance</title>
<style>
*{box-sizing:border-box}body{margin:0;color:#0f172a;font:15px/1.5 system-ui,sans-serif;background:#fff}
header{padding:18px 24px;border-bottom:1px solid #cbd5e1}h1{font-size:24px;margin:0 0 3px}h2{font-size:20px;margin:0 0 10px}h3{font-size:16px;margin:18px 0 7px}p{margin:8px 0}.muted{color:#475569;font-size:13px}
main{display:grid;grid-template-columns:minmax(0,1fr) 370px;height:calc(100dvh - 73px);min-height:500px}
.canvas{display:flex;flex-direction:column;min-width:0;overflow:hidden}.toolbar{display:flex;gap:8px;flex-wrap:wrap;padding:12px;border-bottom:1px solid #e2e8f0;align-items:center}
button{font:inherit;border:1px solid #94a3b8;border-radius:6px;background:white;padding:6px 10px;color:inherit}button{cursor:pointer}button:hover{background:#eff6ff}
#graph{width:100%;height:100%;flex:1;min-height:0;touch-action:none;cursor:grab;user-select:none}#graph.dragging{cursor:grabbing}#graph text{font-family:system-ui,sans-serif;pointer-events:none}.node{cursor:pointer;outline:none}.node:hover rect,.node:focus rect,.node.active rect{stroke:#1d4ed8;stroke-width:3}.edge.related polyline{stroke-width:3.5}.edge.dimmed{opacity:.12}.caption{paint-order:stroke;stroke:white;stroke-width:5;stroke-linejoin:round;pointer-events:none}
aside{border-left:1px solid #cbd5e1;overflow:auto;padding:20px;background:#f8fafc}a{color:#1d4ed8;overflow-wrap:anywhere}.product-card{background:white;padding:10px 12px;border:1px solid #e2e8f0;border-radius:6px;margin-top:8px}.product-card a{display:block}.product-card a+a{margin-top:4px}.swatch{display:inline-block;width:20px;height:13px;border:1px solid #94a3b8;border-radius:3px;margin-right:7px}.line{display:inline-block;width:26px;border-top:2px solid #64748b;margin-right:7px;vertical-align:middle}.dash{border-top-style:dashed}.legend p{font-size:13px;margin:8px 0}#selection{padding-top:20px;border-top:1px solid #cbd5e1;margin-top:20px}#status{font-size:12px;color:#475569}
@media(max-width:850px){main{grid-template-columns:1fr;height:auto}.canvas{height:72dvh;min-height:460px}aside{border-left:0;border-top:1px solid #cbd5e1;overflow:visible}header{padding:14px}h1{font-size:21px}}
</style></head><body>
<header><h1>Soil moisture data provenance</h1></header>
<main><section class="canvas" aria-label="Graph viewer"><nav class="toolbar" aria-label="Graph controls">
<button id="fit">Fit graph</button><button id="zoom-in" aria-label="Zoom in">+</button><button id="zoom-out" aria-label="Zoom out">−</button>
<span id="status">Drag to pan · Scroll to zoom · Click a node</span></nav>
<!--GRAPH--></section><aside>
<section class="legend"><h2>Legend</h2>
<h3>Node colors</h3>
<p><span class="swatch" style="background:#DCFCE7"></span>Shared upstream source or family</p><p><span class="swatch" style="background:#F8FAFC"></span>Product or processing group</p>
<h3>Edges</h3><p><span class="line"></span>Solid: documented relationship, including grouped dependencies</p>
<p><span class="line dash"></span>Dashed / <b>?</b>: uncertainty in the dependency itself</p>
</section>
<section id="selection" aria-live="polite"><h2 id="node-title">Explore a node</h2>
<div id="members"></div></section>
</aside></main><noscript>Enable JavaScript to inspect node links and use graph controls.</noscript>
<script id="provenance-data" type="application/json"><!--DATA--></script>
<script>
'use strict';
const data=JSON.parse(document.getElementById('provenance-data').textContent);
const svg=document.getElementById('graph');
const initial=svg.getAttribute('viewBox').split(/\s+/).map(Number);let view=[...initial],selected=null;
const byId=id=>document.getElementById(id);
function element(tag,text,className){const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(className)e.className=className;return e;}
function update(){svg.setAttribute('viewBox',view.join(' '));}
function zoom(factor,px=.5,py=.5){const w=Math.max(initial[2]/16,Math.min(initial[2]*2,view[2]*factor)),ratio=w/view[2],h=view[3]*ratio;view=[view[0]+(view[2]-w)*px,view[1]+(view[3]-h)*py,w,h];update();}
byId('fit').onclick=()=>{view=[...initial];update();clearSelection();};byId('zoom-in').onclick=()=>zoom(.75);byId('zoom-out').onclick=()=>zoom(1/.75);
function graphPoint(event){return new DOMPoint(event.clientX,event.clientY).matrixTransform(svg.getScreenCTM().inverse());}
svg.addEventListener('wheel',event=>{event.preventDefault();const p=graphPoint(event);zoom(event.deltaY>0?1.12:1/1.12,(p.x-view[0])/view[2],(p.y-view[1])/view[3]);},{passive:false});
let drag=null,moved=false;
svg.addEventListener('pointerdown',event=>{if(event.button!==0)return;drag={x:event.clientX,y:event.clientY,start:[...view],matrix:svg.getScreenCTM().inverse()};moved=false;svg.setPointerCapture(event.pointerId);svg.classList.add('dragging');});
svg.addEventListener('pointermove',event=>{if(!drag)return;const dx=event.clientX-drag.x,dy=event.clientY-drag.y;if(Math.hypot(dx,dy)>4)moved=true;view=[drag.start[0]-dx*drag.matrix.a,drag.start[1]-dy*drag.matrix.d,...drag.start.slice(2)];update();});
function endDrag(event){if(!drag)return;const didMove=moved;drag=null;svg.classList.remove('dragging');if(event.type==='pointerup'&&!didMove){const hit=document.elementFromPoint(event.clientX,event.clientY)?.closest('[data-node]');if(hit)selectNode(hit.dataset.node);else clearSelection();}}
svg.addEventListener('pointerup',endDrag);svg.addEventListener('pointercancel',endDrag);
svg.querySelectorAll('.node').forEach(node=>node.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();selectNode(node.dataset.node);}}));
function renderMembers(){const target=byId('members');target.replaceChildren();
for(const id of data.groups[selected].members){const p=data.products[id],card=element('div',undefined,'product-card');
const link=element('a',p.product);link.href=p.provider_url;link.target='_blank';link.rel='noopener noreferrer';card.append(link);target.append(card);}}
function clearSelection(){selected=null;byId('node-title').textContent='Explore a node';byId('members').replaceChildren();svg.querySelectorAll('.node').forEach(n=>n.classList.remove('active'));svg.querySelectorAll('.edge').forEach(e=>e.classList.remove('related','dimmed'));}
function selectNode(id){selected=id;byId('node-title').textContent=data.groups[id].label.replaceAll('\n',' ');
svg.querySelectorAll('.node').forEach(n=>n.classList.toggle('active',n.dataset.node===id));svg.querySelectorAll('.edge').forEach(e=>{const related=e.dataset.up===id||e.dataset.down===id;e.classList.toggle('related',related);e.classList.toggle('dimmed',!related);});renderMembers();}
svg.addEventListener('keydown',event=>{if(event.key==='Escape')clearSelection();});
</script></body></html>'''


# Prefer provider catalog/documentation pages to downloadable evidence documents.
PROVIDER_PAGES = {
    'S-VWC-REPORT': 'https://nsidc.org/data/smap/ancillary-data',
    'S-IMERG': 'https://gpm.nasa.gov/data/imerg',
    'S-SOILGRIDS': 'https://docs.isric.org/globaldata/soilgrids/SoilGrids_faqs_02.html',
    'S-GTOPO': 'https://www.usgs.gov/centers/eros/science/usgs-eros-archive-digital-elevation-global-30-arc-second-elevation-gtopo30',
    'S-DSMW': 'https://www.fao.org/soils-portal/soil-survey/soil-maps-and-databases/faounesco-soil-map-of-the-world/en',
    'S-EGM2008': 'https://earth-info.nga.mil/geograv',
    'S-NASADEM-GUIDE': 'https://data.nasa.gov/dataset/nasadem-merged-dem-global-1-arc-second-v001',
    'S-CMR-UPSTREAM-COLLECTIONS': 'https://nisar-docs.asf.alaska.edu/gcov/',
    'S-CMR-ECMWF-COLLECTION': 'https://search.earthdata.nasa.gov/search?p=C3653529349-ASF',
    'S-CMR-ECMWF-GRANULES': 'https://search.earthdata.nasa.gov/search?p=C3653529349-ASF',
    'S-NLDAS-GUIDE': 'https://ldas.gsfc.nasa.gov/nldas/v2/models',
    'S-SMAP-GUIDE': 'https://nsidc.org/data/spl4smgp/versions/8',
    'S-SMAP-WORKBOOK': 'https://nsidc.org/data/spl4smgp/versions/8',
    'S-SMAP-ASSESS': 'https://nsidc.org/data/spl4smgp/versions/8',
    'S-SMAP-ATBD': 'https://nsidc.org/data/spl4smgp/versions/8',
    'S-L1C': 'https://nsidc.org/data/spl1ctb/versions/6',
    'S-L1B': 'https://nsidc.org/data/spl1btb/versions/6',
    'S-L2E': 'https://nsidc.org/data/spl2smp_e/versions/6',
    'S-L2-ATBD': 'https://nsidc.org/data/spl2smp_e/versions/6',
    'S-ERA5LAND-PAPER': 'https://cds.climate.copernicus.eu/datasets/reanalysis-era5-land?tab=overview',
    'S-GEOSFP-SPEC': 'https://gmao.gsfc.nasa.gov/gmao-products/geos-near-real-time-data-products/',
    'S-CPCU': 'https://www.cpc.ncep.noaa.gov/products/GIS/GIS_DATA/',
    'S-NLDAS-INIT': 'https://ldas.gsfc.nasa.gov/nldas/v1/models',
    'S-LOCAL-SME2': 'https://nisar-docs.asf.alaska.edu/sme2/',
    'S-LANDSAT-L2-GUIDE': 'https://www.usgs.gov/landsat-missions/landsat-collection-2-surface-reflectance',
    'S-LANDSAT-ARCHIVE': 'https://www.usgs.gov/landsat-missions/landsat-levels-processing',
    'S-LANDSAT-STAC-STUDY': 'https://www.usgs.gov/landsat-missions/landsat-collection-2-surface-reflectance',
    'S-OPTRAM': 'https://www.usgs.gov/landsat-missions/landsat-collection-2-surface-reflectance',
    'S-CMR-NISAR-GRANULES': 'https://nisar-docs.asf.alaska.edu/sme2/',
    'S-CMR-SMAP-GRANULES': 'https://nsidc.org/data/spl4smgp/versions/8',
    'S-CMR-GCOV': 'https://nisar-docs.asf.alaska.edu/gcov/',
    'S-CMR-RSLC': 'https://nisar-docs.asf.alaska.edu/rslc/',
    'S-CMR-RRSD': 'https://nisar-docs.asf.alaska.edu/rrsd/',
}


def provider_url(node_id, product, sources):
    """Choose a product's provider page, falling back to its audited documentation."""
    for model in ('NOAH', 'MOS', 'VIC'):
        if node_id == f'NLDAS-{model}-2.0':
            return f'https://disc.gsfc.nasa.gov/datacollection/NLDAS_{model}0125_H_2.0.html'
    if node_id == 'EGM2008-UNRESOLVED':
        return PROVIDER_PAGES['S-EGM2008']
    candidates = product['source_ids']
    # Collection searches are evidence, not product landing pages. Prefer the
    # product's other cited sources, retaining their reviewed ordering.
    candidates = sorted(candidates, key=lambda sid: (
        sid.startswith('S-CMR-'),
        urlsplit(PROVIDER_PAGES.get(sid, sources[sid]['url'])).path.endswith(('.pdf', '.xlsx')),
    ))
    sid = candidates[0]
    return PROVIDER_PAGES.get(sid, sources[sid]['url'])


def render_html(input_path, evidence_dir, output_path):
    """Join explicit overview membership to original product and source records."""
    evidence = json.loads((evidence_dir / 'edge_evidence.json').read_text())
    sources = json.loads((evidence_dir / 'sources.json').read_text())
    nodes, edges = read_graph(input_path)
    displayed = {k for k, n in nodes.items() if not n['frame']}
    overview = project_overview(evidence, include_validation='VIEW-VALIDATION' in displayed)
    visible_edges = {eid for eid, e in overview['edges'].items() if e['upstream_id'] != e['downstream_id']}
    if displayed != set(overview['nodes']) or {e['id'] for e in edges} != visible_edges:
        raise ValueError('GraphML and reviewed overview membership disagree; rebuild GraphML first')
    groups = {k: {'label': nodes[k]['label'], 'note': v.get('note', ''),
                  'members': v['member_node_ids']} for k, v in overview['nodes'].items()}
    member_ids = {m for g in groups.values() for m in g['members']}
    products = {n['node_id']: {k: n[k] for k in
                ('product', 'versions', 'version_status', 'lineage_boundary', 'source_ids')}
                for n in evidence['nodes'] if n['node_id'] in member_ids}
    if member_ids != set(products):
        raise ValueError('Missing product records')
    source_ids = {s for p in products.values() for s in p['source_ids']}
    linked_sources = {s['source_id']: {k: s[k] for k in
                      ('provider', 'title', 'url', 'document_version', 'access_date', 'locations', 'review_note')}
                      for s in sources['sources'] if s['source_id'] in source_ids}
    if source_ids != set(linked_sources) or any(not p['source_ids'] for p in products.values()):
        raise ValueError('Every member product must resolve to audited sources')
    for source in linked_sources.values():
        url = urlsplit(source['url'])
        if url.scheme not in ('https', 'http') or not url.netloc:
            raise ValueError(f'Not a public HTTP(S) source URL: {source["url"]}')
    cards = {node_id: {'product': product['product'],
                       'provider_url': provider_url(node_id, product, linked_sources)}
             for node_id, product in products.items()}
    data = json.dumps({'groups': groups, 'products': cards}, ensure_ascii=False)
    data = data.replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
    page = PAGE.replace('<!--GRAPH-->', graph_svg(nodes, edges)).replace('<!--DATA-->', data)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(page)
    return len(groups), len(edges), len(products), len(linked_sources)
