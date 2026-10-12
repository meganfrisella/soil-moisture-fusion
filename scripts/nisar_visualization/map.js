/* Leaflet 1.9.4 manages navigation/projection; Canvas draws native EASE rows.
 * A cylindrical equal-area row has constant latitude and its x axis is linear
 * in longitude. Projecting EACH row edge avoids the displacement caused by
 * stretching a complete EPSG:6933 image into one Web Mercator rectangle.
 * Native color cells use nearest-neighbour display; no values are averaged.
 */
'use strict';
const data = JSON.parse(document.getElementById('map-data').textContent);
const byId = id => document.getElementById(id);
const status = byId('status');
const slider = byId('time-slider');
const selector = byId('value-select');
const flagToggle = byId('flag-toggle');
byId('map-title').textContent = data.title;
document.title = `${data.title} · NISAR soil moisture`;
document.documentElement.style.setProperty('--missing-color', data.missing_color);
document.documentElement.style.setProperty('--odd-color', data.odd_color);
for (const view of data.views) {
  const option = document.createElement('option');
  option.value = view.key; option.textContent = view.label;
  selector.append(option);
}
slider.max = data.frames.length - 1;
byId('first-date').textContent = data.frames[0].start.slice(0, 10);
byId('last-date').textContent = data.frames.at(-1).start.slice(0, 10);
byId('provenance').textContent = `${data.frames.length} qualifying acquisition groups from ${data.granule_count} source granules · >${data.provenance.min_numeric_percent ?? 50}% numeric soil-moisture cells in at least one algorithm after combining · catalog retrieved ${data.provenance.catalog_retrieved_at_utc || 'not supplied'}.`;
const combineWindow = data.provenance.nisar_combine_window_minutes ?? 0;
byId('combine-rule').textContent = combineWindow > 0
  ? `Same-track granules are combined within ${combineWindow} minutes of the group's first acquisition start, before coverage filtering. Overlapping cells use the earliest finite moisture with its uncertainty and flag, separately for each algorithm. Values are not averaged.`
  : 'Each time step displays one qualifying granule. Combining is disabled.';

if (!window.L) {
  status.dataset.error = 'true';
  status.textContent = 'Leaflet could not load. Connect to the internet and reload.';
  slider.disabled = selector.disabled = flagToggle.disabled = byId('previous').disabled = byId('next').disabled = true;
} else {
  const map = L.map('map', {zoomSnap: .25, zoomAnimation: false, minZoom: 1, maxZoom: 20});
  L.tileLayer(data.map_config.basemap_url, {
    maxZoom: 20, maxNativeZoom: 19, attribution: data.map_config.basemap_attribution
  }).addTo(map);
  L.control.scale({imperial: false}).addTo(map);
  const boundary = L.geoJSON(data.domain.geometry, {style: {color: data.map_config.boundary_color, weight: 2, dashArray: data.map_config.boundary_dash, fill: false}}).addTo(map);
  map.fitBounds(boundary.getBounds(), {padding: [25, 80]});

  const NativeRaster = L.Layer.extend({
    onAdd(map) {
      this.map = map;
      this.canvas = L.DomUtil.create('canvas', 'sme2-raster leaflet-zoom-hide');
      map.getPanes().overlayPane.prepend(this.canvas);
      map.on('move zoom resize', this.schedule, this);
      this.schedule();
    },
    onRemove(map) {
      map.off('move zoom resize', this.schedule, this);
      cancelAnimationFrame(this.pending);
      this.canvas.remove();
    },
    setImages(values, outlines) { this.values = values; this.outlines = outlines; this.schedule(); },
    schedule() {
      cancelAnimationFrame(this.pending);
      this.pending = requestAnimationFrame(() => this.draw());
    },
    draw() {
      const map = this.map, canvas = this.canvas, size = map.getSize();
      const ratio = Math.min(devicePixelRatio || 1, 2);
      L.DomUtil.setPosition(canvas, map.containerPointToLayerPoint([0, 0]));
      canvas.width = Math.round(size.x * ratio); canvas.height = Math.round(size.y * ratio);
      canvas.style.width = `${size.x}px`; canvas.style.height = `${size.y}px`;
      const ctx = canvas.getContext('2d');
      ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
      ctx.imageSmoothingEnabled = false;
      if (!this.values) return;
      // Edge cells intersect the box but may extend past it. Clip both values
      // and flag borders to the exact requested rectangle, as in GIF/PNG.
      const [west, south, east, north] = data.bounds_wgs84;
      const nw = map.latLngToContainerPoint([north, west]);
      const se = map.latLngToContainerPoint([south, east]);
      ctx.beginPath(); ctx.rect(nw.x, nw.y, se.x - nw.x, se.y - nw.y); ctx.clip();
      const geo = data.grid, lons = geo.longitude_edges, lats = geo.latitude_edges;
      const left = map.latLngToContainerPoint([lats[0], lons[0]]).x;
      const right = map.latLngToContainerPoint([lats[0], lons.at(-1)]).x;
      if (right <= 0 || left >= size.x) return;
      const totalWidth = right - left;
      const c0 = Math.max(0, Math.floor(-left / totalWidth * geo.cols));
      const c1 = Math.min(geo.cols, Math.ceil((size.x - left) / totalWidth * geo.cols));
      const x = left + c0 / geo.cols * totalWidth, width = (c1 - c0) / geo.cols * totalWidth;
      const edges = lats.map(lat => Math.round(map.latLngToContainerPoint([lat, lons[0]]).y * ratio) / ratio);
      for (let r = 0; r < geo.rows; r++) {
        const y = edges[r], height = edges[r + 1] - y;
        if (height <= 0 || y >= size.y || y + height <= 0) continue;
        for (const image of [this.values, this.outlines]) {
          if (!image) continue;
          const scale = image.naturalWidth / geo.cols;
          ctx.drawImage(image, c0 * scale, r * scale, (c1 - c0) * scale, scale, x, y, width, height);
        }
      }
    }
  });
  const raster = new NativeRaster().addTo(map);
  const cache = new Map();
  function loadImage(id) {
    if (!id) return Promise.resolve(null);
    if (cache.has(id)) {
      const saved = cache.get(id); cache.delete(id); cache.set(id, saved); return saved;
    }
    const image = new Image(); image.src = data.assets[id];
    const promise = image.decode().then(() => image).catch(error => {cache.delete(id); throw error;});
    cache.set(id, promise);
    while (cache.size > 4) cache.delete(cache.keys().next().value);
    return promise;
  }
  let request = 0;
  async function showSelection() {
    const token = ++request;
    const index = Number(slider.value), key = selector.value;
    const frame = data.frames[index], view = data.views.find(v => v.key === key), layer = frame.layers[key];
    const outlined = flagToggle.checked;
    raster.setImages(null, null);
    status.dataset.error = 'false'; status.textContent = 'Loading acquisition group…';
    const endLabel = frame.start.slice(0, 10) === frame.end.slice(0, 10) ? frame.end.slice(11, 19) : frame.end.slice(0, 19).replace('T', ' ');
    byId('time-label').textContent = `${frame.start.slice(0, 10)} · ${frame.start.slice(11, 19)}–${endLabel} UTC`;
    byId('frame-count').textContent = `${index + 1} / ${data.frames.length}`;
    byId('legend-title').textContent = view.label;
    byId('color-scale').style.background = `linear-gradient(to right, ${view.colors.join(',')})`;
    byId('scale-min').textContent = view.minimum;
    byId('scale-middle').textContent = (view.minimum + view.maximum) / 2;
    byId('color-scale').dataset.extendMin = String(view.extend_min);
    byId('color-scale').style.setProperty('--min-color', view.colors[0]);
    byId('color-scale').style.setProperty('--max-color', view.colors.at(-1));
    byId('scale-max').textContent = view.maximum;
    byId('flag-key').hidden = !outlined;
    byId('coverage').textContent = '';
    byId('previous').disabled = index === 0;
    byId('next').disabled = index === data.frames.length - 1;
    byId('source-count').textContent = `${frame.granules.length} source granule${frame.granules.length === 1 ? '' : 's'}`;
    byId('sources').replaceChildren();
    for (const record of frame.granules) {
      const block = document.createElement('div'); block.className = 'granule';
      block.textContent = `Track ${String(record.trackNumber).padStart(3, '0')} / frame ${String(record.frameNumber).padStart(3, '0')} · ${record.orbitPassDirection} · orbit ${record.absoluteOrbitNumber} · product ${record.productVersion} · release ${record.compositeReleaseId} · ${record.source_pixels.toLocaleString()} source pixels`;
      const filename = document.createElement('code'); filename.textContent = record.granule;
      block.append(filename); byId('sources').append(block);
    }
    try {
      const [values, outlines] = await Promise.all([loadImage(layer.values), loadImage(outlined ? layer.outlines : null)]);
      if (token !== request) return;
      raster.setImages(values, outlines);
      byId('coverage').textContent = `${layer.numeric_cells.toLocaleString()}/${data.domain_native_pixels.toLocaleString()} numeric cells, ${layer.recommended_cells.toLocaleString()}/${data.domain_native_pixels.toLocaleString()} recommended cells.`;
      status.textContent = layer.numeric_cells ? '' : 'No numeric values for this product in this acquisition group; shown in gray.';
    } catch {
      if (token !== request) return;
      status.dataset.error = 'true'; status.textContent = 'This raster could not load. Keep the HTML and its assets folder together, then reload.';
    }
  }
  for (const [id, step] of [['previous', -1], ['next', 1]]) {
    byId(id).addEventListener('click', () => {
      slider.value = Math.max(0, Math.min(data.frames.length - 1, Number(slider.value) + step));
      showSelection();
    });
  }
  flagToggle.addEventListener('change', showSelection);
  slider.addEventListener('input', showSelection);
  selector.addEventListener('change', showSelection);
  showSelection();
}
