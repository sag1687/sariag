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
pipeline.py — End-to-end SARIAG pipeline: search+download Sentinel-1 SLC
for one ascending and one descending stack, process every consecutive pair
through SNAP+SNAPHU, invert each stack's small-baseline network into a LOS
velocity map, then decompose the two LOS velocities into Vertical and
East-West displacement-rate maps.

Kept UI-free (plain callables for logging/progress/cancellation) so it can
be driven from a QThread worker in :mod:`dialog` without importing PyQt.
"""

import datetime
import os

from . import core_cdse
from . import decompose
from . import raster_utils
from . import snap_graph
from . import timeseries


class PipelineError(Exception):
    pass


class PipelineCancelled(PipelineError):
    pass


def _noop_log(_msg):
    pass


def _noop_progress(_stage, _pct):
    pass


def _parse_date(iso_str):
    """CDSE ContentDate/Start, e.g. '2023-05-14T05:12:34.123456Z' -> date."""
    return datetime.date.fromisoformat(iso_str[:10])


def _safe_zip_filename(product_name):
    if product_name.upper().endswith(".SAFE"):
        return product_name[: -len(".SAFE")] + ".zip"
    if product_name.lower().endswith(".zip"):
        return product_name
    return product_name + ".zip"


def _pick_group(groups, relative_orbit=None):
    if relative_orbit is not None:
        for (_direction, ro), items in groups.items():
            if ro == relative_orbit:
                return items
        raise PipelineError(
            "Nessuna scena trovata per relativeOrbitNumber=%s. / "
            "No scenes found for relativeOrbitNumber=%s."
            % (relative_orbit, relative_orbit)
        )
    if not groups:
        raise PipelineError(
            "Nessun risultato dalla ricerca CDSE per l'area/periodo "
            "indicati. / No results from the CDSE search for the given "
            "area/period."
        )
    return next(iter(groups.values()))  # largest group, groups is presorted


def _check_cancel(cancel_check):
    if cancel_check and cancel_check():
        raise PipelineCancelled("Elaborazione annullata dall'utente. / "
                                "Processing cancelled by the user.")


def _get_valid_token(params, log):
    """
    Return a valid CDSE access token, refreshing or fully re-authenticating
    as needed.

    A SNAP+SNAPHU pass over one stack can take hours, far longer than a
    CDSE access token (~10 min) or even its refresh token (~60 min), so
    the token captured once at the start of :func:`run_pipeline` cannot be
    assumed valid by the time the second (descending) stack starts its own
    search — this is checked/renewed at the top of every stack, not just
    before each download.
    """
    try:
        token = core_cdse.ensure_valid_token(params["_token"])
    except core_cdse.CdseError:
        log("Sessione CDSE scaduta, nuova autenticazione... / "
            "CDSE session expired, re-authenticating...")
        token = core_cdse.authenticate(
            params["cdse_username"], params["cdse_password"]
        )
    params["_token"] = token
    return token


def _process_direction(direction, params, log, progress, cancel_check):
    """Search, download and process one orbit-direction stack into a
    2-band (LOS velocity, incidence angle) GeoTIFF."""
    work_dir = params["work_dir"]
    token = _get_valid_token(params, log)

    log("[%s] Ricerca su CDSE... / [%s] Searching CDSE..." % (direction, direction))
    results = core_cdse.search_s1_slc(
        token["access_token"], params["bbox"],
        params["date_from"], params["date_to"],
        orbit_direction=direction,
        relative_orbit=params.get("relative_orbit_%s" % direction.lower()),
    )
    groups = core_cdse.group_by_relative_orbit(results)
    scenes = _pick_group(
        groups, params.get("relative_orbit_%s" % direction.lower())
    )
    if len(scenes) < 2:
        raise PipelineError(
            "Scene %s insufficienti per formare almeno un interferogramma "
            "(trovate %d, minimo 2). / Not enough %s scenes to form even "
            "one interferogram (found %d, need at least 2)."
            % (direction, len(scenes), direction, len(scenes))
        )
    log("[%s] %d scene trovate (relativeOrbit=%s). / "
        "[%s] %d scenes found (relativeOrbit=%s)."
        % (direction, len(scenes), scenes[0]["relative_orbit"],
           direction, len(scenes), scenes[0]["relative_orbit"]))

    dl_dir = os.path.join(work_dir, direction.lower(), "safe")
    os.makedirs(dl_dir, exist_ok=True)
    local_paths, dates = [], []
    for scene in scenes:
        _check_cancel(cancel_check)
        zip_path = os.path.join(dl_dir, _safe_zip_filename(scene["name"]))
        if not os.path.exists(zip_path):
            log("Download %s..." % scene["name"])
            token = _get_valid_token(params, log)

            def _dl_progress(done, total, _name=scene["name"]):
                pct = int(100 * done / total) if total else 0
                progress("download_%s_%s" % (direction, _name), pct)

            core_cdse.download_product(
                token["access_token"], scene["id"], zip_path,
                progress_callback=_dl_progress,
            )
        local_paths.append(zip_path)
        dates.append(_parse_date(scene["sensing_start"]))

    # Simplest small-baseline network: chain of consecutive-date pairs.
    proc_dir = os.path.join(work_dir, direction.lower(), "proc")
    pair_products, pair_dates = [], []
    for k in range(len(dates) - 1):
        _check_cancel(cancel_check)
        i, j = k, k + 1
        pair_name = "pair_%s_%s" % (dates[i].isoformat(), dates[j].isoformat())
        log("[%s] Interferogramma %s..." % (direction, pair_name))
        out_tif = snap_graph.process_pair(
            params["gpt_exe"], params["snaphu_exe"],
            local_paths[i], local_paths[j], proc_dir, pair_name,
            subswath=params["subswath"], polarisation=params["polarisation"],
            first_burst=params["first_burst"], last_burst=params["last_burst"],
            dem_name=params["dem_name"],
            rg_looks=params["rg_looks"], az_looks=params["az_looks"],
            pixel_spacing=params["pixel_spacing"],
            log_callback=log,
            progress_callback=lambda stage, pct, _pn=pair_name: progress(
                "%s_%s_%s" % (direction, _pn, stage), pct
            ),
        )
        pair_products.append(out_tif)
        pair_dates.append((dates[i], dates[j]))

    unique_dates = sorted(set(dates))
    log("[%s] Inversione della serie temporale (%d date, %d interferogrammi)... / "
        "[%s] Time series inversion (%d dates, %d interferograms)..."
        % (direction, len(unique_dates), len(pair_products),
           direction, len(unique_dates), len(pair_products)))
    stack, geo, proj = timeseries.read_raster_stack(pair_products, band=1)
    cumulative = timeseries.invert_stack(stack, unique_dates, pair_dates)
    velocity = timeseries.velocity_from_cumulative(cumulative, unique_dates)

    # Incidence angle is ~constant across pairs of the same track/subswath;
    # the first pair's band is on exactly the reference grid (geo, proj)
    # used above, so no extra warp is needed here.
    inc_angle, _, _, _, _ = raster_utils.read_band(pair_products[0], band=2)

    out_velocity_tif = os.path.join(
        work_dir, "%s_velocity_los.tif" % direction.lower()
    )
    raster_utils.write_raster_multiband(
        out_velocity_tif, [velocity, inc_angle], geo, proj
    )
    return out_velocity_tif


def run_pipeline(params, log=None, progress=None, cancel_check=None):
    """
    Run the full SARIAG pipeline.

    Parameters
    ----------
    params : dict — see dialog.py for the exact keys (bbox, date_from,
        date_to, cdse_username, cdse_password, gpt_exe, snaphu_exe,
        work_dir, subswath, polarisation, first_burst, last_burst,
        dem_name, rg_looks, az_looks, pixel_spacing, los_sign,
        relative_orbit_ascending, relative_orbit_descending — the last two
        optional, restrict each stack to a single relative orbit).
    log(msg) : str -> None, optional progress log sink.
    progress(stage, percent) : (str, int) -> None, optional.
    cancel_check() -> bool, optional, polled between processing stages.

    Returns
    -------
    dict: vertical, eastwest, asc_velocity, desc_velocity — output paths.
    """
    log = log or _noop_log
    progress = progress or _noop_progress
    params = dict(params)
    os.makedirs(params["work_dir"], exist_ok=True)

    log("Autenticazione su Copernicus Data Space Ecosystem... / "
        "Authenticating with Copernicus Data Space Ecosystem...")
    params["_token"] = core_cdse.authenticate(
        params["cdse_username"], params["cdse_password"]
    )

    asc_velocity_tif = _process_direction(
        core_cdse.ORBIT_ASCENDING, params, log, progress, cancel_check
    )
    _check_cancel(cancel_check)
    desc_velocity_tif = _process_direction(
        core_cdse.ORBIT_DESCENDING, params, log, progress, cancel_check
    )
    _check_cancel(cancel_check)

    out_vert = os.path.join(params["work_dir"], "vertical_velocity.tif")
    out_ew = os.path.join(params["work_dir"], "eastwest_velocity.tif")
    log("Scomposizione LOS ascendente+discendente in spostamento "
        "Verticale/Est-Ovest... / Decomposing ascending+descending LOS "
        "into Vertical/East-West displacement...")
    decompose.decompose_asc_desc(
        asc_velocity_tif, desc_velocity_tif, out_vert, out_ew,
        los_band=1, incidence_band=2,
        los_sign=params.get("los_sign", 1.0),
    )
    log("Completato. / Done.")

    return {
        "vertical": out_vert,
        "eastwest": out_ew,
        "asc_velocity": asc_velocity_tif,
        "desc_velocity": desc_velocity_tif,
    }
