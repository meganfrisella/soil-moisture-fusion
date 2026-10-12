"""Capture the rendered GAIA HTML with an isolated Chrome/Chromium profile.

Used by ``pixi run python scripts/build_gaia_map.py --browser /path/to/chrome``.
Leaflet and Esri tiles require internet. The PNG uses the HTML's Web Mercator
display and WGS84 features; its metadata identifies the source HTML snapshot.
"""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

from PIL import Image, PngImagePlugin


def render_preview(html, output, browser=None):
    """Publish a PNG only after the map reports that its layers and basemap loaded."""
    candidates = [str(browser)] if browser else [
        'google-chrome', 'chromium', 'chromium-browser',
        '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    ]
    executable = next((shutil.which(p) or str(Path(p).expanduser()) for p in candidates
                       if shutil.which(p) or Path(p).expanduser().is_file()), None)
    if executable is None:
        raise RuntimeError('PNG preview requires Chrome/Chromium; use --browser PATH')
    html, output = Path(html), Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    # Stage on the destination filesystem for atomic replacement; never reuse a user profile.
    with tempfile.TemporaryDirectory(prefix='.map-preview-', dir=output.parent) as folder:
        folder = Path(folder)
        screenshot = folder / 'browser.png'
        command = [executable, '--headless=new', '--hide-scrollbars', '--no-first-run',
                   '--no-default-browser-check', '--disable-background-networking',
                   '--disable-extensions', '--force-device-scale-factor=1',
                   '--window-size=1600,1050', '--virtual-time-budget=15000', '--timeout=60000',
                   f'--user-data-dir={folder / "profile"}', f'--screenshot={screenshot}',
                   '--dump-dom', html.resolve().as_uri()]
        # Chrome may keep background children alive after completing its capture.
        # File-backed output avoids waiting on inherited stdout/stderr pipes.
        dom = folder / 'page.html'
        ready = False
        with dom.open('wb') as stdout, (folder / 'browser.log').open('wb') as stderr:
            process = subprocess.Popen(command, stdout=stdout, stderr=stderr)
            try:
                deadline = time.monotonic() + 75
                while time.monotonic() < deadline:
                    if screenshot.exists():
                        with dom.open('rb') as page:
                            head = page.read(512)
                            page.seek(max(0, dom.stat().st_size - 32))
                            complete = b'</html>' in page.read()
                        if b'data-map-ready="true"' in head and complete:
                            try:
                                with Image.open(screenshot) as image:
                                    image.verify()
                                ready = True
                                break
                            except (OSError, SyntaxError):
                                pass  # PNG may still be in the process of being written.
                    if process.poll() is not None:
                        break
                    time.sleep(.1)
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
        if not ready:
            raise RuntimeError('PNG preview failed: Chrome must load the map library, styles and all basemap tiles; '
                               'check internet access. Existing PNG preserved.')
        metadata = PngImagePlugin.PngInfo()
        metadata.add_text('Description', json.dumps(dict(
            source_html=html.name, source_sha256=hashlib.sha256(html.read_bytes()).hexdigest(),
            captured_at_utc=datetime.now(timezone.utc).isoformat(),
            display_crs='EPSG:3857', feature_crs='EPSG:4326', viewport_pixels=[1600, 1050])))
        staged = folder / 'preview.png'
        with Image.open(screenshot) as image:
            image.save(staged, pnginfo=metadata)
        staged.replace(output)
