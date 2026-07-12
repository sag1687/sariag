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
timeseries.py — Small-baseline (SBAS-style) line-of-sight displacement
time series inversion.

Each interferometric pair (i, j) with i earlier than j gives an observed
LOS displacement equal to the *cumulative* displacement between the two
acquisition dates. Given a network of such pairs (not necessarily every
date connected to every other, and not necessarily a single connected
chain), the per-epoch incremental displacement is recovered with the
least-squares network inversion of Berardino et al. (2002): build one
design row per interferogram (a run of 1s between its two dates in the
list of N-1 unknown increments), then solve with the Moore-Penrose
pseudo-inverse so that gaps/disconnected sub-networks still yield the
minimum-norm solution instead of failing outright.

Operates on a stack of already-unwrapped, already-geocoded LOS
displacement rasters (one GeoTIFF per interferometric pair, all on the
same grid — this module does not resample) produced by
:mod:`snap_graph`.
"""

import numpy as np

from .raster_utils import (
    RasterUtilsError,
    read_and_align_stack,
    write_raster as _write_raster,
)


class TimeseriesError(Exception):
    pass


def build_design_matrix(dates, pairs):
    """
    dates : sorted list of unique acquisition dates (any sortable type,
            typically ``datetime.date`` or ISO strings)
    pairs : list of (date_i, date_j) tuples, date_i earlier than date_j,
            one per interferogram, in the same order as the raster stack.

    Returns
    -------
    A : ndarray (n_pairs, n_dates - 1)
        Design matrix mapping incremental per-epoch displacement to the
        cumulative displacement observed by each interferogram.
    """
    index = {d: k for k, d in enumerate(dates)}
    n_dates = len(dates)
    n_pairs = len(pairs)
    A = np.zeros((n_pairs, n_dates - 1), dtype=np.float64)
    for row, (d_i, d_j) in enumerate(pairs):
        if d_i not in index or d_j not in index:
            raise TimeseriesError(
                "Coppia interferometrica con data non presente nella "
                "lista delle acquisizioni: %s / %s. / "
                "Interferometric pair with a date missing from the "
                "acquisition list: %s / %s." % (d_i, d_j, d_i, d_j)
            )
        i, j = index[d_i], index[d_j]
        if i >= j:
            raise TimeseriesError(
                "La coppia (%s, %s) non è ordinata cronologicamente. / "
                "Pair (%s, %s) is not chronologically ordered."
                % (d_i, d_j, d_i, d_j)
            )
        A[row, i:j] = 1.0
    return A


def invert_stack(stack, dates, pairs):
    """
    Invert a stack of unwrapped LOS displacement rasters into a per-date
    cumulative displacement stack via least-squares (SVD pseudo-inverse).

    Parameters
    ----------
    stack : ndarray (n_pairs, rows, cols)
        LOS displacement (meters) for each interferogram, NaN where masked.
    dates : sorted list of unique acquisition dates, length n_dates.
    pairs : list of (date_i, date_j), length n_pairs, matching stack order.

    Returns
    -------
    cumulative : ndarray (n_dates, rows, cols)
        Cumulative LOS displacement per date, relative to ``dates[0]``
        (all zeros on the first date by construction).
    """
    if stack.shape[0] != len(pairs):
        raise TimeseriesError(
            "Il numero di layer nello stack (%d) non corrisponde al "
            "numero di coppie (%d). / The number of layers in the stack "
            "(%d) does not match the number of pairs (%d)."
            % (stack.shape[0], len(pairs), stack.shape[0], len(pairs))
        )

    A = build_design_matrix(dates, pairs)
    n_pairs, rows, cols = stack.shape
    n_dates = len(dates)

    obs = stack.reshape(n_pairs, rows * cols)
    valid_cols = ~np.any(np.isnan(obs), axis=0)

    increments = np.zeros((n_dates - 1, rows * cols), dtype=np.float64)
    if np.any(valid_cols):
        A_pinv = np.linalg.pinv(A)  # (n_dates-1, n_pairs), computed once
        increments[:, valid_cols] = A_pinv @ obs[:, valid_cols]
    increments[:, ~valid_cols] = np.nan

    cumulative = np.zeros((n_dates, rows * cols), dtype=np.float64)
    cumulative[1:, :] = np.cumsum(increments, axis=0)
    cumulative[:, ~valid_cols] = np.nan
    return cumulative.reshape(n_dates, rows, cols)


def velocity_from_cumulative(cumulative, dates):
    """
    Linear-fit mean LOS velocity (m/year) per pixel from a cumulative
    displacement stack, robust to unevenly spaced acquisition dates.
    """
    t_years = np.array(
        [(d - dates[0]).days / 365.25 for d in dates], dtype=np.float64
    )
    n_dates, rows, cols = cumulative.shape
    y = cumulative.reshape(n_dates, rows * cols)
    valid_cols = ~np.any(np.isnan(y), axis=0)

    velocity = np.full(rows * cols, np.nan, dtype=np.float64)
    if np.any(valid_cols):
        design = np.vstack([t_years, np.ones_like(t_years)]).T
        coeffs, *_ = np.linalg.lstsq(design, y[:, valid_cols], rcond=None)
        velocity[valid_cols] = coeffs[0, :]
    return velocity.reshape(rows, cols)


# ---------------------------------------------------------------------------
# GDAL I/O — thin re-exports of raster_utils, kept here so callers that
# only deal with time series don't need to import raster_utils directly.
# ---------------------------------------------------------------------------

def read_raster_stack(paths, band=1):
    """Read+align ``band`` from each GeoTIFF in ``paths`` into one
    (n, rows, cols) float64 array. See
    :func:`raster_utils.read_and_align_stack`."""
    try:
        return read_and_align_stack(paths, band=band)
    except RasterUtilsError as exc:
        raise TimeseriesError(str(exc)) from exc


def write_raster(path, array, geotransform, projection, nodata=-9999.0):
    """Write a single-band float32 GeoTIFF. See
    :func:`raster_utils.write_raster`."""
    try:
        return _write_raster(path, array, geotransform, projection, nodata)
    except RasterUtilsError as exc:
        raise TimeseriesError(str(exc)) from exc
