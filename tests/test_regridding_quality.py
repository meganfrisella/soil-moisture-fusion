"""Synthetic R03/R05–R06 checks; no downloads, calibration claims, or method runs."""

from pathlib import Path
import sys
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from regridding.quality import (
    input_masks, conditional_variance, contributing_uncertainty_available, baseline_variance,
)


class QualityTests(unittest.TestCase):
    def test_baseline_variance_matches_diagonal_covariance(self):
        """Match W diag(σ²) Wᵀ marginals without treating repeated outputs as independent."""
        weights = np.array([[1., 0], [.5, .5], [.5, .5], [2., -1]])
        sigma = np.array([.2, .4])
        covariance = (weights * sigma**2) @ weights.T
        result = baseline_variance(weights, np.broadcast_to(sigma, weights.shape),
                                   np.ones(weights.shape, dtype=bool))
        np.testing.assert_allclose(result, np.diag(covariance))
        self.assertAlmostEqual(result[1], .05)
        self.assertAlmostEqual(covariance[1, 2], result[1])

    def test_baseline_missing_uncertainty_and_zero_coefficients(self):
        """Ignore missing zero-weight inputs, retain reported zero, and invalidate required gaps."""
        weights = [[1, 0], [.5, .5], [0, 1], [0, 0]]
        sigma = [[0, np.nan], [.2, np.nan], [np.nan, .4], [.2, .4]]
        valid = np.isfinite(sigma)
        np.testing.assert_allclose(baseline_variance(weights, sigma, valid),
                                   [0, np.nan, .16, np.nan], equal_nan=True)

    def test_baseline_empty_rows_and_shape_validation(self):
        """Return missing for absent contributors and reject accidental broadcasting."""
        result = baseline_variance(np.empty((2, 0)), np.empty((2, 0)),
                                   np.empty((2, 0), dtype=bool))
        self.assertTrue(np.isnan(result).all())
        with self.assertRaises(ValueError):
            baseline_variance([[1, 0]], [.2, .4], [[True, True]])

    def test_baseline_overflow_fails_without_renormalizing(self):
        """Reject overflow instead of changing coefficients or publishing infinite variance."""
        with self.assertRaises(FloatingPointError):
            baseline_variance([1e200], [.2], [True])

    def test_conditional_variance_squares_valid_values_and_preserves_missingness(self):
        """Square proxies, retaining zeros and NaN independently of moisture validity."""
        sigma = np.array([.2, 0, -9999, -.1, np.nan, np.inf, 2])
        original = sigma.copy()
        valid = input_masks(np.full(sigma.shape, np.nan), sigma,
                            moisture_fill_value=None, uncertainty_fill_value=-9999)["uncertainty_valid"]
        variance = conditional_variance(sigma, valid)
        np.testing.assert_allclose(variance, [.04, 0, np.nan, np.nan, np.nan, np.nan, 4],
                                   equal_nan=True)
        np.testing.assert_array_equal(sigma, original)

    def test_conditional_variance_uses_float64_without_silent_overflow(self):
        """Avoid float32 squaring overflow, and fail if the float64 result overflows."""
        sigma = np.array([1e20], dtype=np.float32)
        variance = conditional_variance(sigma, [True])
        self.assertEqual(variance.dtype, np.float64)
        np.testing.assert_allclose(variance, sigma.astype(np.float64)**2)
        with self.assertRaises(FloatingPointError):
            conditional_variance([1e200], [True])

    def test_conditional_variance_rejects_inconsistent_validity(self):
        """Reject wrong shapes/types and masks that accept negative or nonfinite values."""
        for sigma, valid in (([.1, .2], [True]), ([.1], [1]), ([-1], [True]),
                             ([np.nan], [True]), ([np.inf], [True])):
            with self.subTest(sigma=sigma, valid=valid), self.assertRaises(ValueError):
                conditional_variance(sigma, valid)

    def test_all_finite_moisture_survives_missing_uncertainty(self):
        """Retain finite negative/out-of-range moisture independently of uncertainty."""
        moisture = np.array([-.2, 1.3, 0, .4, np.nan, np.inf, -np.inf, -9999])
        uncertainty = np.array([np.nan, -.1, 0, 2, .1, .2, np.inf, -9999])
        original = moisture.copy(), uncertainty.copy()
        masks = input_masks(moisture, uncertainty,
                            moisture_fill_value=-9999, uncertainty_fill_value=-9999)
        np.testing.assert_array_equal(masks["moisture_valid"],
                                      [True, True, True, True, False, False, False, False])
        np.testing.assert_array_equal(masks["uncertainty_valid"],
                                      [False, False, True, True, True, True, False, False])
        self.assertEqual(np.count_nonzero(masks["reported_zero"]), 1)
        np.testing.assert_array_equal(moisture, original[0])
        np.testing.assert_array_equal(uncertainty, original[1])

    def test_declared_fills_override_finiteness_and_zero_policy(self):
        """Honor field-specific fills, even when a fill is positive or zero."""
        masks = input_masks([42, .2, .3], [0, 7, .1],
                            moisture_fill_value=42, uncertainty_fill_value=0)
        np.testing.assert_array_equal(masks["moisture_valid"], [False, True, True])
        np.testing.assert_array_equal(masks["uncertainty_valid"], [False, True, True])
        self.assertFalse(masks["reported_zero"].any())

    def test_decoded_fields_retain_zero_and_have_independent_masks(self):
        """Accept predecoded fields and retain uncertainty where moisture is missing."""
        masks = input_masks([np.nan, .2], [0, np.nan],
                            moisture_fill_value=None, uncertainty_fill_value=None)
        np.testing.assert_array_equal(masks["moisture_valid"], [False, True])
        np.testing.assert_array_equal(masks["uncertainty_valid"], [True, False])
        np.testing.assert_array_equal(masks["reported_zero"], [True, False])

    def test_missing_nonzero_contributor_invalidates_uncertainty_only(self):
        """Missing uncertainty matters for any nonzero weight, including tiny weights."""
        valid = np.array([[True, False]] * 4)
        weights = [[1, 0], [.5, .5], [1, np.finfo(float).tiny], [2, -1]]
        np.testing.assert_array_equal(contributing_uncertainty_available(weights, valid),
                                      [True, False, False, False])
        zero_valid = input_masks([.2], [0], moisture_fill_value=None,
                                 uncertainty_fill_value=None)["uncertainty_valid"]
        self.assertTrue(contributing_uncertainty_available([1], zero_valid))

    def test_rows_without_contributors_have_no_uncertainty(self):
        """All-zero and empty contributor rows cannot produce propagated uncertainty."""
        self.assertFalse(contributing_uncertainty_available([0, 0], [True, True]))
        np.testing.assert_array_equal(contributing_uncertainty_available(
            np.empty((2, 0)), np.empty((2, 0), dtype=bool)), [False, False])

    def test_invalid_shapes_and_coefficients_fail_explicitly(self):
        """Reject broadcasting, numeric validity masks, and nonfinite coefficients."""
        with self.assertRaisesRegex(ValueError, "shapes"):
            input_masks([.2, .3], [.1], moisture_fill_value=None,
                        uncertainty_fill_value=None)
        for weights, valid in (([1, 0], [True]), (1, True),
                               ([1], [1]), ([np.inf], [True]), ([np.nan], [False])):
            with self.subTest(weights=weights, valid=valid), self.assertRaises(ValueError):
                contributing_uncertainty_available(weights, valid)


if __name__ == "__main__":
    unittest.main()
