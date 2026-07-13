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
decompose.py — Combine ascending + descending line-of-sight (LOS)
displacement into Vertical and East-West displacement maps.

A single SAR track only measures the 1-D projection of the true 3-D
displacement onto its line of sight, so one track alone cannot separate
"the ground went up" from "the ground went east". Combining an ascending
and a descending track (right-looking Sentinel-1, looking east and west
respectively) gives two independent LOS equations, enough to solve for two
of the three displacement components. The along-track (North-South)
component is dropped — this is the standard simplification used across
the InSAR literature (e.g. Wright et al., 2004) because near-polar orbits
make LOS almost insensitive to North-South motion regardless of how many
tracks are combined, so the 2x2 system below is well-posed while a 3x3
one would not be:

    d_asc  =  dU * cos(theta_a) - dE * sin(theta_a)
    d_desc =  dU * cos(theta_d) + dE * sin(theta_d)

solved per pixel by Cramer's rule (theta = incidence angle from vertical,
positive dE = east, positive dU = up):

    det = sin(theta_a + theta_d)
    dU  = (d_asc * sin(theta_d) + d_desc * sin(theta_a)) / det
    dE  = (cos(theta_a) * d_desc - cos(theta_d) * d_asc) / det

Sign convention caveat: whether SNAP's PhaseToDisplacement reports
"positive LOS = toward the sensor" or the opposite depends on the SNAP
version. Validate the output against a location with known motion (e.g. a
known-subsiding area should show negative dU) and flip ``los_sign`` below
if it comes out inverted.
"""

import numpy as np

from .raster_utils import (
    RasterUtilsError,
    read_band,
    warp_to_reference,
    write_raster,
)


class DecomposeError(Exception):
    pass


def solve_vertical_eastwest(
    los_asc, los_desc, inc_asc_deg, inc_desc_deg, los_sign=1.0
):
    """
    Per-pixel closed-form solution of the 2x2 asc/desc LOS system.

    Parameters
    ----------
    los_asc, los_desc : ndarray, same shape, meters, on the same grid.
    inc_asc_deg, inc_desc_deg : float or ndarray broadcastable to
        los_asc.shape — incidence angle from vertical, in degrees.
    los_sign : +1.0 or -1.0
        Flip if the vertical result comes out sign-inverted against a
        known reference (see module docstring).

    Returns
    -------
    (vertical, east_west) : ndarray, ndarray — meters, same shape as input.
    """
    if los_asc.shape != los_desc.shape:
        raise DecomposeError(
            "I raster ascendente e discendente non hanno la stessa "
            "dimensione: riproiettarli sulla stessa griglia prima della "
            "scomposizione. / The ascending and descending rasters do "
            "not share the same shape: resample them onto a common grid "
            "before decomposition."
        )

    theta_a = np.radians(inc_asc_deg)
    theta_d = np.radians(inc_desc_deg)

    det = np.sin(theta_a + theta_d)
    with np.errstate(divide="ignore", invalid="ignore"):
        vertical = (
            los_sign
            * (los_asc * np.sin(theta_d) + los_desc * np.sin(theta_a))
            / det
        )
        east_west = (
            los_sign
            * (np.cos(theta_a) * los_desc - np.cos(theta_d) * los_asc)
            / det
        )

    singular = np.abs(det) < 1e-6
    vertical = np.where(singular, np.nan, vertical)
    east_west = np.where(singular, np.nan, east_west)
    return vertical, east_west


# ---------------------------------------------------------------------------
# Raster-level orchestration
# ---------------------------------------------------------------------------


def decompose_asc_desc(
    asc_tif,
    desc_tif,
    out_vertical_tif,
    out_eastwest_tif,
    los_band=1,
    incidence_band=2,
    los_sign=1.0,
    inc_asc_deg=None,
    inc_desc_deg=None,
):
    """
    Load ascending/descending LOS-displacement GeoTIFFs (as produced by
    :func:`snap_graph.run_snaphu_import_geocode`, whose Terrain-Correction
    step is configured to also export an incidence-angle band), resample
    the descending raster onto the ascending grid, solve the 2x2 system
    per pixel and write Vertical/East-West GeoTIFFs.

    If ``inc_asc_deg`` / ``inc_desc_deg`` are given (float, degrees) they
    override the per-pixel incidence-angle band — use this when a product
    was exported without ``saveIncidenceAngleFromEllipsoid``.
    """
    try:
        los_a, geo, proj, xsize, ysize = read_band(asc_tif, los_band)

        desc_ds = warp_to_reference(desc_tif, geo, proj, xsize, ysize)
        los_d = (
            desc_ds.GetRasterBand(los_band).ReadAsArray().astype(np.float64)
        )
        nodata_d = desc_ds.GetRasterBand(los_band).GetNoDataValue()
        if nodata_d is not None:
            los_d[los_d == nodata_d] = np.nan

        if inc_asc_deg is None:
            inc_a, _, _, _, _ = read_band(asc_tif, incidence_band)
        else:
            inc_a = float(inc_asc_deg)

        if inc_desc_deg is None:
            inc_d = (
                desc_ds.GetRasterBand(incidence_band)
                .ReadAsArray()
                .astype(np.float64)
            )
            nodata_id = desc_ds.GetRasterBand(incidence_band).GetNoDataValue()
            if nodata_id is not None:
                inc_d[inc_d == nodata_id] = np.nan
        else:
            inc_d = float(inc_desc_deg)
    except RasterUtilsError as exc:
        raise DecomposeError(str(exc)) from exc

    vertical, east_west = solve_vertical_eastwest(
        los_a, los_d, inc_a, inc_d, los_sign=los_sign
    )

    write_raster(out_vertical_tif, vertical, geo, proj)
    write_raster(out_eastwest_tif, east_west, geo, proj)
    return out_vertical_tif, out_eastwest_tif
