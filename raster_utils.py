# -*- coding: utf-8 -*-
# Copyright (C) 2026 Dott. Sarino Alfonso Grande <sino.grande@gmail.com>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.

"""
raster_utils.py — Small GDAL helpers shared by :mod:`timeseries` and
:mod:`decompose` (band reading with nodata->NaN, warp-to-reference-grid,
single-band GeoTIFF writing).
"""

import numpy as np

try:
    from osgeo import gdal
    gdal.UseExceptions()
    _HAS_GDAL = True
except ImportError:
    _HAS_GDAL = False


class RasterUtilsError(Exception):
    pass


def _require_gdal():
    if not _HAS_GDAL:
        raise RasterUtilsError(
            "GDAL non disponibile nell'ambiente Python di QGIS. / "
            "GDAL is not available in the QGIS Python environment."
        )


def read_band(path_or_ds, band=1):
    """Return (array, geotransform, projection, xsize, ysize) for one band,
    with the source's nodata value (if any) mapped to NaN."""
    _require_gdal()
    ds = gdal.Open(path_or_ds) if isinstance(path_or_ds, str) else path_or_ds
    if ds is None:
        raise RasterUtilsError(
            "Impossibile aprire %s / Cannot open %s"
            % (path_or_ds, path_or_ds)
        )
    bnd = ds.GetRasterBand(band)
    arr = bnd.ReadAsArray().astype(np.float64)
    nodata = bnd.GetNoDataValue()
    if nodata is not None:
        arr[arr == nodata] = np.nan
    return arr, ds.GetGeoTransform(), ds.GetProjection(), ds.RasterXSize, ds.RasterYSize


def warp_to_reference(src_path, ref_geo, ref_proj, ref_xsize, ref_ysize,
                      resample_alg="bilinear"):
    """Resample ``src_path`` onto the reference grid, returned as an
    in-memory GDAL dataset (caller reads whichever bands it needs)."""
    _require_gdal()
    ref_ulx, px_w, _, ref_uly, _, px_h = ref_geo
    out_bounds = (
        ref_ulx,
        ref_uly + ref_ysize * px_h,
        ref_ulx + ref_xsize * px_w,
        ref_uly,
    )
    warped = gdal.Warp(
        "", src_path, format="MEM",
        dstSRS=ref_proj,
        outputBounds=out_bounds,
        width=ref_xsize, height=ref_ysize,
        resampleAlg=resample_alg,
    )
    if warped is None:
        raise RasterUtilsError(
            "Riproiezione fallita per %s / Reprojection failed for %s"
            % (src_path, src_path)
        )
    return warped


def read_and_align_stack(paths, band=1):
    """
    Read ``band`` from every file in ``paths`` into one (n, rows, cols)
    float64 array, warping every raster after the first onto the first
    one's grid (SNAP geocodes each pair independently, so per-pair extents
    can differ by a pixel or two even with identical processing
    parameters).

    Returns (stack, geotransform, projection) of the reference (first) file.
    """
    _require_gdal()
    if not paths:
        raise RasterUtilsError("Nessun raster da leggere. / No rasters to read.")

    ref_arr, ref_geo, ref_proj, xsize, ysize = read_band(paths[0], band)
    arrays = [ref_arr]
    for p in paths[1:]:
        warped = warp_to_reference(p, ref_geo, ref_proj, xsize, ysize)
        bnd = warped.GetRasterBand(band)
        arr = bnd.ReadAsArray().astype(np.float64)
        nodata = bnd.GetNoDataValue()
        if nodata is not None:
            arr[arr == nodata] = np.nan
        arrays.append(arr)
    return np.stack(arrays, axis=0), ref_geo, ref_proj


def write_raster(path, array, geotransform, projection, nodata=-9999.0):
    """Write a single-band float32 GeoTIFF, mapping NaN pixels to ``nodata``."""
    _require_gdal()
    rows, cols = array.shape
    driver = gdal.GetDriverByName("GTiff")
    ds = driver.Create(path, cols, rows, 1, gdal.GDT_Float32)
    ds.SetGeoTransform(geotransform)
    ds.SetProjection(projection)
    band = ds.GetRasterBand(1)
    out = np.where(np.isnan(array), nodata, array).astype(np.float32)
    band.WriteArray(out)
    band.SetNoDataValue(float(nodata))
    band.FlushCache()
    ds = None


def write_raster_multiband(path, arrays, geotransform, projection, nodata=-9999.0):
    """Write a multi-band float32 GeoTIFF from a list of same-shape arrays."""
    _require_gdal()
    rows, cols = arrays[0].shape
    driver = gdal.GetDriverByName("GTiff")
    ds = driver.Create(path, cols, rows, len(arrays), gdal.GDT_Float32)
    ds.SetGeoTransform(geotransform)
    ds.SetProjection(projection)
    for i, array in enumerate(arrays, start=1):
        band = ds.GetRasterBand(i)
        out = np.where(np.isnan(array), nodata, array).astype(np.float32)
        band.WriteArray(out)
        band.SetNoDataValue(float(nodata))
    ds.FlushCache()
    ds = None
