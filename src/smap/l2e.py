"""SPL2SMP_E v006 HDF5 verification and extraction; moisture m³/m³, time UTC."""

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import re
from urllib.parse import urlparse

import h5py
import numpy as np

SHORT_NAME = "SPL2SMP_E"
VERSION = "006"
COLLECTION_ID = "C2938663676-NSIDC_CPRD"
DOWNLOAD_HOST = "data.nsidc.earthdatacloud.nasa.gov"
GROUP = "Soil_Moisture_Retrieval_Data"
FILL = -9999.0
FLAG_FILL = 65534
HDF5_SIGNATURE = b"\x89HDF\r\n\x1a\n"
CHUNK_BYTES = 1024 * 1024
NAME_PATTERN = re.compile(
    r"SMAP_L2_SM_P_E_(?P<orbit>\d{5})_(?P<direction>[AD])_(?P<start>\d{8}T\d{6})"
    r"_(?P<crid>R\d{5})_(?P<counter>\d{3})\.h5")
# Float fields kept on the box grid; fill becomes NaN. Integer flags keep raw bits.
FLOAT_FIELDS = ("soil_moisture", "soil_moisture_error", "vegetation_water_content",
                "surface_temperature", "static_water_body_fraction")
FLAG_FIELDS = ("retrieval_qual_flag", "surface_flag")
# Cell-centre degrees, read only to check the EASE row/column geometry.
GRID_FIELDS = ("latitude", "longitude")


@dataclass(frozen=True)
class Granule:
    """Catalog identity and integrity record for one half-orbit file."""

    name: str
    url: str
    md5: str
    size_bytes: int
    concept_id: str
    revision_id: int
    start: str
    end: str
    production: str
    orbit: int
    direction: str
    crid: str


def parse_granule(item):
    """Select the primary HDF5 and its MD5/size from one CMR UMM-JSON item."""
    record = item["umm"]
    reference = record["CollectionReference"]
    if (reference["ShortName"], reference["Version"]) != (SHORT_NAME, VERSION):
        raise ValueError("CMR returned an unexpected collection or version")
    # GranuleUR carries a provider suffix; the file name is the producer granule ID.
    names = [i["Identifier"] for i in record["DataGranule"].get("Identifiers", [])
             if i["IdentifierType"] == "ProducerGranuleId"]
    if len(names) != 1:
        raise ValueError(f"Missing producer granule ID: {record['GranuleUR']}")
    name = names[0]
    match = NAME_PATTERN.fullmatch(name)
    if match is None:
        raise ValueError(f"Unexpected granule filename: {name}")
    links = [u["URL"] for u in record["RelatedUrls"]
             if u["Type"] == "GET DATA" and urlparse(u["URL"]).scheme == "https"
             and urlparse(u["URL"]).hostname == DOWNLOAD_HOST
             and Path(urlparse(u["URL"]).path).name == name]
    files = [f for f in record["DataGranule"]["ArchiveAndDistributionInformation"]
             if f["Name"] == name]
    if len(links) != 1 or len(files) != 1:
        raise ValueError(f"Missing or ambiguous primary HDF5 product: {name}")
    checksum = files[0]["Checksum"]
    if checksum["Algorithm"].upper() != "MD5" or not re.fullmatch(r"[0-9a-fA-F]{32}", checksum["Value"]):
        raise ValueError(f"Missing MD5 checksum: {name}")
    extent = record["TemporalExtent"]["RangeDateTime"]
    return Granule(name, links[0], checksum["Value"].lower(), int(files[0]["SizeInBytes"]),
                   item["meta"]["concept-id"], int(item["meta"]["revision-id"]),
                   extent["BeginningDateTime"], extent["EndingDateTime"],
                   record["DataGranule"]["ProductionDateTime"], int(match["orbit"]),
                   match["direction"], match["crid"])


def recommended(moisture, quality_flag):
    """Finite moisture whose retrieval_qual_flag bit 0 (not recommended) is clear."""
    return np.isfinite(moisture) & (quality_flag >= 0) & ((quality_flag & 1) == 0)


def md5_file(path):
    """Stream an MD5 digest in bounded chunks."""
    digest = hashlib.md5()
    with Path(path).open("rb") as source:
        while chunk := source.read(CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def verify_file(path, md5, size_bytes):
    """Check HDF5 signature, byte count, archive MD5 and the required fields."""
    path = Path(path)
    with path.open("rb") as source:
        if source.read(len(HDF5_SIGNATURE)) != HDF5_SIGNATURE:
            raise ValueError(f"Not an HDF5 product: {path.name}")
    if path.stat().st_size != size_bytes:
        raise ValueError(f"Byte count mismatch: {path.name}")
    if md5_file(path) != md5:
        raise ValueError(f"Archive MD5 mismatch: {path.name}")
    with h5py.File(path, "r") as h5:
        missing = [f for f in ("EASE_row_index", "EASE_column_index", "tb_time_utc",
                               *FLOAT_FIELDS, *FLAG_FIELDS, *GRID_FIELDS) if f not in h5[GROUP]]
        if missing:
            raise ValueError(f"{path.name} lacks fields: {missing}")


MASKED_FRACTION = re.compile(r"\.\*+(?=Z$)")


def parse_utc(values):
    """Decode `tb_time_utc` strings to UTC datetime64[ms]; blanks or fill become NaT.

    Some provider strings mask the milliseconds (`...:04.***Z`); these keep whole-second
    precision (< 1 s error) and are counted by `masked_milliseconds`.
    """
    out = np.full(len(values), np.datetime64("NaT", "ms"), dtype="datetime64[ms]")
    for i, raw in enumerate(values):
        text = raw.decode() if isinstance(raw, bytes) else str(raw)
        text = MASKED_FRACTION.sub("", text.strip().rstrip("\x00"))
        if not text or text.startswith("-9999") or text.upper().startswith("N/A"):
            continue
        stamp = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            raise ValueError(f"tb_time_utc lacks a UTC designator: {text}")
        out[i] = np.datetime64(stamp.astimezone(timezone.utc).replace(tzinfo=None), "ms")
    return out


def read_box(path, rows, columns):
    """Scatter one granule's swath cells onto the (rows, columns) box grid.

    Returns a dict of 2D arrays: float fields with fill as NaN, raw integer flags
    with -1 where the swath has no cell or the flag is fill (65534), `tb_time_utc` as datetime64[ms], and
    `swath_cell` (1 where the granule reports a cell). Duplicate grid cells in one
    granule are rejected rather than silently overwritten.
    """
    shape = (len(rows), len(columns))
    with h5py.File(path, "r") as h5:
        group = h5[GROUP]
        row = group["EASE_row_index"][:].astype(int) - rows[0]
        col = group["EASE_column_index"][:].astype(int) - columns[0]
        keep = np.flatnonzero((row >= 0) & (row < shape[0]) & (col >= 0) & (col < shape[1]))
        row, col = row[keep], col[keep]
        if len(np.unique(row * shape[1] + col)) != len(keep):
            raise ValueError(f"Duplicate grid cells in {Path(path).name}")
        out = {}
        for name in FLOAT_FIELDS + GRID_FIELDS:
            values = group[name][:][keep].astype(float)
            values[values == FILL] = np.nan
            out[name] = np.full(shape, np.nan)
            out[name][row, col] = values
        for name in FLAG_FIELDS:
            out[name] = np.full(shape, -1, dtype=np.int32)
            flags = group[name][:][keep].astype(np.int32)
            out[name][row, col] = np.where(flags == FLAG_FILL, -1, flags)
        out["tb_time_utc"] = np.full(shape, np.datetime64("NaT", "ms"), dtype="datetime64[ms]")
        out["tb_time_utc"][row, col] = parse_utc(group["tb_time_utc"][:][keep])
        raw = group["tb_time_utc"][:][keep]
        out["masked_milliseconds"] = int(sum(b"*" in t for t in raw))
        out["swath_cell"] = np.zeros(shape, dtype=np.uint8)
        out["swath_cell"][row, col] = 1
    return out
