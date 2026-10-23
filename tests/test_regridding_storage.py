"""Check R32 storage integrity and R33 run settings on synthetic windows."""

import json
import os
import subprocess
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import zarr

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from regridding.schema import acquisition_records, store_template, utc_microseconds
from regridding.storage import RunStore
from regridding.run_plan import serial_io, storage_options

ROOT = Path(__file__).resolve().parents[1]


def fixture(method="native"):
    """Define two distinct equal-time acquisitions and a masked 2×3 metre grid."""
    sources = [{"filename": f"source-{i}.h5", "checksum": {"algorithm": "SHA256", "value": str(i)*64},
                "start_utc": "2026-06-17T02:53:18.123456Z",
                "end_utc": "2026-06-17T02:53:30.123456Z"} for i in range(2)]
    step = 200 if method == "native" else 90
    grid = {"epsg": 6933 if method == "native" else 5070,
            "x": np.array([0., step, 2*step]), "y": np.array([step, 0.]),
            "intersection_area_m2": np.array([[step**2, step**2, 0], [step**2, step**2, step**2]], dtype=float),
            "center_eligible": np.array([[True, True, False], [True, True, True]]),
            "native_rows": np.array([5, 6]), "native_columns": np.array([10, 11, 12])}
    records = acquisition_records([[1], [0]], sources, {"epsg": 6933, "shape": [2, 3], "rows": [5, 6], "columns": [10, 11, 12]})
    return store_template(method, grid, records, sources)


def values(method="native", source=0):
    """Include finite negative moisture, zero uncertainty and independent native missingness."""
    moisture = np.array([[0., -.2, np.nan], [np.nan, .3, .4]])
    sigma = np.array([[0., np.nan, np.nan], [.1 if method == "native" else np.nan, .2, .3]])
    result = {"soil_moisture": moisture,
              "reported_uncertainty" if method == "native" else "retrieval_error_std": sigma,
              "moisture_valid": np.isfinite(moisture), "uncertainty_valid": np.isfinite(sigma)}
    if method == "native":
        flags = np.array([[0, 1, -1], [1, -1, 0]], dtype=np.int64)
        result.update(reported_zero=np.isfinite(sigma) & (sigma == 0),
                      retrieval_quality_flag=flags, flag_valid=flags >= 0,
                      source_index=np.array([[source, source, -1], [source, source, source]], dtype=np.int32))
    return result


class StorageTests(unittest.TestCase):
    def setUp(self):
        """Keep every fixture store isolated in a temporary directory."""
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "run"
        self.identity = {"inputs": "synthetic-v1", "configuration": "test-only"}
        self.template = fixture()

    def initialize(self, run, method="native", template=None, stage=None):
        """Use small uncompressed test chunks, not production R33 settings."""
        run.initialize(method, self.template if template is None else template,
                       stage_identity=stage or {"code": "fixture-v1"},
                       spatial_chunks=(1, 2), compressors=None)

    def test_roundtrip_coordinates_times_nan_zero_and_source_tuples(self):
        """Preserve float64, valid zeros, independent masks, source flags and UTC alignment."""
        with RunStore(self.root, self.identity) as run:
            self.initialize(run)
            run.write_task("native", 0, "PMI", values(), provenance={"command": "synthetic"})
            result = run.read_task("native", 0, "PMI")
            for name, array in values().items():
                np.testing.assert_array_equal(result[name].values, array)
            self.assertEqual(result.soil_moisture.dtype, np.dtype("float64"))
            np.testing.assert_array_equal(result.x, self.template.x)
            np.testing.assert_array_equal(result.y_bounds, self.template.y_bounds)
            self.assertEqual(result.attrs["crs"], "EPSG:6933")
            self.assertEqual(result.acquisition_start.values,
                             np.datetime64("2026-06-17T02:53:18.123456"))
            self.assertEqual(result.retrieval_quality_flag.values[0, 0], 0)
            self.assertEqual(result.moisture_valid.values[0, 2], 0)

    def test_target_roundtrip_and_center_exclusion(self):
        """Use the target CRS and reject any value on an excluded target center."""
        template = fixture("nearest")
        with RunStore(self.root, self.identity) as run:
            self.initialize(run, "nearest", template)
            invalid = values("nearest")
            invalid["soil_moisture"][0, 2] = .1
            invalid["moisture_valid"][0, 2] = True
            with self.assertRaisesRegex(ValueError, "support"):
                run.write_task("nearest", 0, "PMI", invalid, provenance={"command": "synthetic"})
            run.write_task("nearest", 0, "PMI", values("nearest"), provenance={"command": "synthetic operator fixture"})
            result = run.read_task("nearest", 0, "PMI")
            self.assertEqual(result.attrs["crs"], "EPSG:5070")
            self.assertEqual(result.retrieval_error_std.dtype, np.dtype("float64"))

    def test_equal_times_remain_separate_and_ids_ignore_group_schedule(self):
        """Do not collapse acquisitions with equal times; retain stable identities when reordered."""
        records = self.template.attrs["acquisitions"]
        self.assertEqual(records[0]["start_us"], records[1]["start_us"])
        self.assertNotEqual(records[0]["id"], records[1]["id"])
        sources = self.template.attrs["sources"]
        layout = {"rows": [1, 2]}
        self.assertEqual(acquisition_records([[0], [1]], sources, layout),
                         acquisition_records([[1], [0]], sources, layout))
        with self.assertRaises(ValueError):
            acquisition_records([[0], [0]], sources, layout)

    def test_time_normalization_rejects_precision_loss(self):
        """Normalize explicit offsets and zone-less UTC, refusing sub-microsecond rounding."""
        expected = utc_microseconds("2026-01-01T00:00:00.123456Z")
        self.assertEqual(expected, utc_microseconds("2025-12-31T19:00:00.123456-05:00"))
        self.assertEqual(expected, utc_microseconds("2026-01-01T00:00:00.123456"))
        with self.assertRaises(ValueError):
            utc_microseconds("2026-01-01T00:00:00.123456789Z")

    def test_resume_rewrites_interrupted_region_without_touching_completed_task(self):
        """Restart full unfinished slabs, clearing stale values while preserving completed chunks."""
        with RunStore(self.root, self.identity) as run:
            self.initialize(run)
            run.write_task("native", 0, "PMI", values(), provenance={"command": "synthetic"})
            before = run._task_inventory("native", 0, 0)
            group = run.stores["native"][0]
            group["completion_status"][1, 1] = 1
            group["soil_moisture"][1, 1, :, :] = 42.
        with RunStore(self.root, self.identity, resume=True) as run:
            self.initialize(run)
            with self.assertRaisesRegex(ValueError, "incomplete"):
                run.read_task("native", 1, "TSR")
            run.write_task("native", 1, "TSR", values(source=1), provenance={"command": "retry fixture"})
            np.testing.assert_array_equal(run.read_task("native", 1, "TSR").soil_moisture, values(source=1)["soil_moisture"])
            self.assertEqual(before, run._task_inventory("native", 0, 0))
            with self.assertRaises(FileExistsError):
                run.write_task("native", 0, "PMI", values(), provenance={"command": "overwrite"})

    def test_all_missing_is_complete_only_with_explicit_reason(self):
        """Distinguish legitimately empty completed observations from unfinished work."""
        arrays = values("nearest")
        for name in arrays:
            arrays[name][:] = False if name.endswith("valid") else np.nan
        with RunStore(self.root, self.identity) as run:
            self.initialize(run, "nearest", fixture("nearest"))
            with self.assertRaisesRegex(ValueError, "reason"):
                run.write_task("nearest", 0, "PMI", arrays, provenance={"command": "fixture"})
            run.write_task("nearest", 0, "PMI", arrays,
                           provenance={"command": "fixture", "all_missing_reason": "no observations"})
            self.assertTrue(np.isnan(run.read_task("nearest", 0, "PMI").soil_moisture).all())

    def test_corrupt_or_deleted_completed_chunk_blocks_resume(self):
        """Reject missing and modified committed chunks, even when Zarr would return fills."""
        with RunStore(self.root, self.identity) as run:
            self.initialize(run)
            run.write_task("native", 0, "PMI", values(), provenance={"command": "fixture"})
            key = next(iter(run._task_inventory("native", 0, 0)))
        path = self.root / "native.zarr" / key
        original = path.read_bytes()
        for mutate in (lambda: path.write_bytes(b"corrupt"), lambda: path.unlink()):
            mutate()
            with self.assertRaisesRegex(ValueError, "checksum"), RunStore(self.root, self.identity, resume=True):
                pass
            path.write_bytes(original)

    def test_stale_identity_metadata_and_no_implicit_resume(self):
        """Refuse implicit reuse, changed scientific/stage identities and static metadata corruption."""
        with RunStore(self.root, self.identity) as run:
            self.initialize(run)
        with self.assertRaises(FileExistsError), RunStore(self.root, self.identity):
            pass
        with self.assertRaisesRegex(ValueError, "identity"), RunStore(self.root, {"inputs": "changed"}, resume=True):
            pass
        with RunStore(self.root, self.identity, resume=True) as run:
            with self.assertRaisesRegex(ValueError, "identity"):
                self.initialize(run, stage={"code": "changed"})
        metadata = self.root / "native.zarr" / "soil_moisture" / "zarr.json"
        metadata.write_text("{}")
        with self.assertRaisesRegex(ValueError, "metadata"), RunStore(self.root, self.identity, resume=True):
            pass

    def test_complete_without_record_and_completed_status_downgrade_fail(self):
        """A complete flag needs its inventory, and committed work cannot be relabeled pending."""
        with RunStore(self.root, self.identity) as run:
            self.initialize(run)
            group = run.stores["native"][0]
            group["completion_status"][0, 0] = 2
        with self.assertRaisesRegex(ValueError, "disagreement"), RunStore(self.root, self.identity, resume=True):
            pass
        group["completion_status"][0, 0] = 0
        with RunStore(self.root, self.identity, resume=True) as run:
            self.initialize(run)
            run.write_task("native", 0, "PMI", values(), provenance={"command": "fixture"})
        group["completion_status"][0, 0] = 0
        with self.assertRaisesRegex(ValueError, "disagreement"), RunStore(self.root, self.identity, resume=True):
            pass

    def test_single_writer_lock_and_strict_json(self):
        """Block concurrent writers and reject nonfinite provenance before publication."""
        with RunStore(self.root, self.identity) as run:
            with self.assertRaises(BlockingIOError), RunStore(self.root, self.identity, resume=True):
                pass
            self.initialize(run)
            with self.assertRaises(ValueError):
                run.write_task("native", 0, "PMI", values(), provenance={"invalid": np.nan})

    def test_failed_readback_verification_is_retryable(self):
        """Leave failed tasks incomplete and recover by writing a fresh complete slab."""
        with RunStore(self.root, self.identity) as run:
            self.initialize(run)
            with patch.object(run, "_verify_task", side_effect=ValueError("injected failure")):
                with self.assertRaises(ValueError):
                    run.write_task("native", 0, "PMI", values(), provenance={"command": "fixture"})
            self.assertEqual(run.stores["native"][0]["completion_status"][0, 0], 3)
            self.assertEqual(run.manifest["stores"]["native"]["failures"]["0-0"][0]["message"],
                             "injected failure")
            run.write_task("native", 0, "PMI", values(), provenance={"command": "retry"})
            run.read_task("native", 0, "PMI")

    def test_resume_finalizes_verified_prepared_commit(self):
        """Recover a crash after the complete flag but before the final manifest update."""
        with RunStore(self.root, self.identity) as run:
            self.initialize(run)
            save = run._save

            def fail_final_save():
                if run.manifest["stores"]["native"]["tasks"]["0-0"]["state"] == "complete":
                    raise OSError("injected final manifest failure")
                save()

            with patch.object(run, "_save", side_effect=fail_final_save):
                with self.assertRaises(OSError):
                    run.write_task("native", 0, "PMI", values(), provenance={"command": "fixture"})
        manifest = json.loads((self.root / "run_manifest.json").read_text())
        self.assertEqual(manifest["stores"]["native"]["tasks"]["0-0"]["state"], "prepared")
        with RunStore(self.root, self.identity, resume=True) as run:
            self.initialize(run)
            self.assertEqual(run.manifest["stores"]["native"]["tasks"]["0-0"]["state"], "complete")
            run.read_task("native", 0, "PMI")


class RunPlanTests(unittest.TestCase):
    def test_cli_plan_overrides_threads_without_input_or_output_io(self):
        """Inspect settings with absent input paths and create no output directory."""
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "not-created"
            result = subprocess.run(
                [sys.executable, "scripts/regrid_eastern_washington.py", "--stage", "native",
                 "--plan-only", "--selection", "absent.json", "--cache-dir", "absent-cache",
                 "--proj-cache-dir", "absent-proj", "--output-dir", str(output), "--resume"],
                cwd=ROOT, env={**os.environ, "OPENBLAS_NUM_THREADS": "8"},
                check=True, capture_output=True, text=True)
            plan = json.loads(result.stdout)
            self.assertFalse(output.exists())
            self.assertTrue(plan["resume"])
            self.assertEqual(plan["stage_order"], ["native", "nearest", "bilinear"])
            self.assertEqual(set(plan["execution"]["numerical_threads"].values()), {"1"})
            self.assertEqual(plan["storage"]["chunks"], [1, 1, 1024, 1024])
            self.assertIn("src/regridding/run_plan.py", plan["code_sha256"])
            self.assertIn("not audited", plan["status"])

    def test_field_execution_stops_before_output_creation(self):
        """An exploration-only method cannot be selected for the retained workflow."""
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "not-created"
            result = subprocess.run(
                [sys.executable, "scripts/regrid_eastern_washington.py", "--stage", "regression",
                 "--resume", "--output-dir", str(output)], cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertIn("invalid choice", result.stderr)
            self.assertFalse(output.exists())

    def test_production_codec_chunks_roundtrip_and_resume(self):
        """Preserve NaN/zero with production compression and isolate Zarr thread settings."""
        previous = {name: zarr.config.get(name) for name in ("async.concurrency", "threading.max_workers")}
        template = fixture("nearest")
        with tempfile.TemporaryDirectory() as directory, serial_io():
            self.assertEqual(zarr.config.get("async.concurrency"), 1)
            self.assertEqual(zarr.config.get("threading.max_workers"), 1)
            root = Path(directory) / "run"
            with RunStore(root, {"input": "synthetic"}) as run:
                run.initialize("nearest", template, stage_identity={"code": "fixture"}, **storage_options())
                run.write_task("nearest", 0, "PMI", values("nearest"), provenance={"command": "fixture"})
                array = run.stores["nearest"][0]["soil_moisture"]
                self.assertEqual(array.chunks, (1, 1, 1024, 1024))
                self.assertEqual(array.compressors[0].to_dict(),
                                 {"name": "zstd", "configuration": {"level": 3, "checksum": True}})
            with RunStore(root, {"input": "synthetic"}, resume=True) as run:
                run.initialize("nearest", template, stage_identity={"code": "fixture"}, **storage_options())
                np.testing.assert_array_equal(run.read_task("nearest", 0, "PMI").soil_moisture,
                                              values("nearest")["soil_moisture"])
        self.assertEqual(previous, {name: zarr.config.get(name) for name in previous})


if __name__ == "__main__":
    unittest.main()
