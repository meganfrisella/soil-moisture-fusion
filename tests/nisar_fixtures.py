"""Small, contract-valid SME2 originals for offline ingest/QA tests."""

import h5py
import numpy as np

from nisar.observations import GRID
from regridding.inputs import VERSION_FIELDS


def write_valid_product(path):
    """Write a three-by-three PMI field with no packing and documented scientific fills."""
    with h5py.File(path, 'w') as product:
        grid = product.create_group(GRID)
        for name, value in dict(xCoordinates=[-400., -200., 0.], yCoordinates=[200., 0., -200.],
                EASEGridRowIndex=[0, 1, 2], EASEGridColumnIndex=[0, 1, 2],
                xCoordinateSpacing=200., yCoordinateSpacing=-200., projection=6933).items():
            grid[name] = value
        for name, value in dict(soilMoisture=.2, soilMoistureUncertainty=.04, retrievalQualityFlag=0).items():
            flag = name == 'retrievalQualityFlag'
            ds = grid.create_dataset('algorithmCandidates/PMI/' + name,
                data=np.full((3, 3), value, dtype='i2' if flag else 'f4'))
            ds.attrs.update(_FillValue=-9999, units='1' if flag else 'meter^3/meter^3', grid_mapping='projection')
        for name, value in VERSION_FIELDS.items():
            product[name] = value
        identity = product['science/LSAR/identification']
        for name, value in dict(zeroDopplerStartTime='2026-06-24T03:02:12Z',
                zeroDopplerEndTime='2026-06-24T03:02:42Z', trackNumber=143, frameNumber=65,
                absoluteOrbitNumber=23, orbitPassDirection='Descending', processingDateTime='2026-06-25T00:00:00Z').items():
            identity[name] = value
