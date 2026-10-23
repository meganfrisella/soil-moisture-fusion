"""Reusable native, nearest-neighbor and bilinear SME2 regridding methods."""

# The optional exploration tree retains the historical NLCD modules under the
# same package name so its frozen regression workflows can import this core.
from pkgutil import extend_path

__path__ = extend_path(__path__, __name__)
