"""Check that reusable source packages can run independently of workflow code."""

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class SourcePackageTests(unittest.TestCase):
    def test_analysis_imports_need_no_scripts_or_plotting(self):
        """Import every source module from an unrelated directory without renderer dependencies."""
        code = '''
import importlib
import pkgutil
import sys

sys.path.insert(0, sys.argv[1])
for package_name in ('ingest', 'nisar'):
    package = importlib.import_module(package_name)
    for module in pkgutil.walk_packages(package.__path__, package_name + '.'):
        importlib.import_module(module.name)
for name in ('matplotlib', 'PIL', 'maps', 'nisar_visualization', 'utils'):
    assert name not in sys.modules, name

from nisar.geometry import BoundingBox
from nisar.quality import flag_masks
import numpy as np

assert BoundingBox.from_corners((1, 2), (0, 3)).bounds == (2, 0, 3, 1)
rejected, missing = flag_masks(np.array([0., 1., 4., 5., np.nan, -9999.]))
assert rejected.tolist() == [False, True, False, True, False, False]
assert missing.tolist() == [False, False, False, False, True, True]
'''
        with tempfile.TemporaryDirectory() as folder:
            result = subprocess.run(
                [sys.executable, '-I', '-c', code, str(ROOT / 'src')],
                cwd=folder, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
