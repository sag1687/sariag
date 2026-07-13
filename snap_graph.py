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
snap_graph.py — ESA SNAP GPT graph generation and orchestration for a
single Sentinel-1 TOPS coregistration + interferogram + unwrapping pair.

SARIAG does not reimplement InSAR coregistration, interferogram formation
or phase unwrapping: those are hard, failure-prone algorithms that SNAP
(coregistration/interferometry) and SNAPHU (statistical-cost phase
unwrapping, Chen & Zebker) already implement correctly and that the user
already has installed. This module only builds the ``gpt`` XML graphs and
drives the ``gpt``/``snaphu`` command-line tools as subprocesses, mirroring
the manual SNAP GUI workflow (Split -> Orbit -> Back-Geocoding ->
Interferogram -> Deburst -> TopoPhaseRemoval -> Goldstein filter ->
Multilook -> SNAPHU export/unwrap/import -> Terrain-Correction).

Every path embedded in the generated XML is escaped with
``xml.sax.saxutils.escape``.
"""

import os
import re
import subprocess  # nosec B404 - esegue solo gpt/snaphu risolti da whitelist
from xml.sax.saxutils import escape as _x  # nosec B406 - solo escaping output

from . import install_helpers

_GPT_PROGRESS_RE = re.compile(r"(\d{1,3})\s*%")


class SnapError(Exception):
    """Raised when a gpt or snaphu subprocess step fails."""


# ---------------------------------------------------------------------------
# subprocess runner shared by every step
# ---------------------------------------------------------------------------


def _run(cmd, cwd=None, log_callback=None, progress_callback=None):
    if log_callback:
        log_callback("$ " + " ".join(cmd))
    # Comandi in forma lista, senza shell: niente injection possibile
    proc = subprocess.Popen(  # nosec B603
        cmd,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        universal_newlines=True,
        bufsize=1,
    )
    lines = []
    for line in proc.stdout:
        line = line.rstrip("\n")
        lines.append(line)
        if log_callback:
            log_callback(line)
        if progress_callback:
            m = _GPT_PROGRESS_RE.search(line)
            if m:
                progress_callback(min(int(m.group(1)), 100))
    proc.wait()
    if proc.returncode != 0:
        tail = "\n".join(lines[-40:])
        raise SnapError(
            "Comando fallito (exit %d): %s\n%s / "
            "Command failed (exit %d): %s\n%s"
            % (
                proc.returncode,
                " ".join(cmd),
                tail,
                proc.returncode,
                " ".join(cmd),
                tail,
            )
        )
    return lines


# ---------------------------------------------------------------------------
# Step 1 — coregistration + interferogram + filtering (single GPT graph)
# ---------------------------------------------------------------------------

_COREG_IFG_GRAPH = """<graph id="sariag_coreg_ifg">
  <version>1.0</version>
  <node id="Read-M">
    <operator>Read</operator>
    <sources/>
    <parameters><file>{master}</file></parameters>
  </node>
  <node id="Split-M">
    <operator>TOPSAR-Split</operator>
    <sources><sourceProduct refid="Read-M"/></sources>
    <parameters>
      <subswath>{subswath}</subswath>
      <selectedPolarisations>{polarisation}</selectedPolarisations>
      <firstBurstIndex>{first_burst}</firstBurstIndex>
      <lastBurstIndex>{last_burst}</lastBurstIndex>
    </parameters>
  </node>
  <node id="Orbit-M">
    <operator>Apply-Orbit-File</operator>
    <sources><sourceProduct refid="Split-M"/></sources>
    <parameters>
      <orbitType>Sentinel Precise (Auto Download)</orbitType>
      <polyDegree>3</polyDegree>
      <continueOnFail>true</continueOnFail>
    </parameters>
  </node>
  <node id="Read-S">
    <operator>Read</operator>
    <sources/>
    <parameters><file>{slave}</file></parameters>
  </node>
  <node id="Split-S">
    <operator>TOPSAR-Split</operator>
    <sources><sourceProduct refid="Read-S"/></sources>
    <parameters>
      <subswath>{subswath}</subswath>
      <selectedPolarisations>{polarisation}</selectedPolarisations>
      <firstBurstIndex>{first_burst}</firstBurstIndex>
      <lastBurstIndex>{last_burst}</lastBurstIndex>
    </parameters>
  </node>
  <node id="Orbit-S">
    <operator>Apply-Orbit-File</operator>
    <sources><sourceProduct refid="Split-S"/></sources>
    <parameters>
      <orbitType>Sentinel Precise (Auto Download)</orbitType>
      <polyDegree>3</polyDegree>
      <continueOnFail>true</continueOnFail>
    </parameters>
  </node>
  <node id="BackGeocoding">
    <operator>Back-Geocoding</operator>
    <sources>
      <sourceProduct refid="Orbit-M"/>
      <sourceProduct.1 refid="Orbit-S"/>
    </sources>
    <parameters>
      <demName>{dem_name}</demName>
      <demResamplingMethod>BILINEAR_INTERPOLATION</demResamplingMethod>
      <resamplingType>BILINEAR_INTERPOLATION</resamplingType>
      <maskOutAreaWithoutElevation>true</maskOutAreaWithoutElevation>
    </parameters>
  </node>
  <node id="ESD">
    <operator>Enhanced-Spectral-Diversity</operator>
    <sources><sourceProduct refid="BackGeocoding"/></sources>
    <parameters/>
  </node>
  <node id="Interferogram">
    <operator>Interferogram</operator>
    <sources><sourceProduct refid="ESD"/></sources>
    <parameters>
      <subtractFlatEarthPhase>true</subtractFlatEarthPhase>
      <srpPolynomialDegree>5</srpPolynomialDegree>
      <orbitDegree>3</orbitDegree>
      <includeCoherence>true</includeCoherence>
      <cohWinAz>3</cohWinAz>
      <cohWinRg>10</cohWinRg>
      <subtractTopographicPhase>true</subtractTopographicPhase>
      <demName>{dem_name}</demName>
      <tileExtensionPercent>100</tileExtensionPercent>
    </parameters>
  </node>
  <node id="Deburst">
    <operator>TOPSAR-Deburst</operator>
    <sources><sourceProduct refid="Interferogram"/></sources>
    <parameters>
      <selectedPolarisations>{polarisation}</selectedPolarisations>
    </parameters>
  </node>
  <node id="Goldstein">
    <operator>GoldsteinPhaseFiltering</operator>
    <sources><sourceProduct refid="Deburst"/></sources>
    <parameters>
      <alpha>1.0</alpha>
      <FFTSizeString>64</FFTSizeString>
      <windowSizeString>3</windowSizeString>
      <useCoherenceMask>false</useCoherenceMask>
      <coherenceThreshold>0.2</coherenceThreshold>
    </parameters>
  </node>
  <node id="Multilook">
    <operator>Multilook</operator>
    <sources><sourceProduct refid="Goldstein"/></sources>
    <parameters>
      <nRgLooks>{rg_looks}</nRgLooks>
      <nAzLooks>{az_looks}</nAzLooks>
      <outputIntensity>false</outputIntensity>
      <grSquarePixel>true</grSquarePixel>
    </parameters>
  </node>
  <node id="Write">
    <operator>Write</operator>
    <sources><sourceProduct refid="Multilook"/></sources>
    <parameters>
      <file>{out_dim}</file>
      <formatName>BEAM-DIMAP</formatName>
    </parameters>
  </node>
</graph>
"""


def build_coreg_ifg_graph(
    master_path,
    slave_path,
    out_dim_path,
    subswath="IW1",
    polarisation="VV",
    first_burst=1,
    last_burst=9,
    dem_name="SRTM 1Sec HGT",
    rg_looks=4,
    az_looks=1,
):
    """Build the coregistration + interferogram + Goldstein-filter graph
    XML."""
    return _COREG_IFG_GRAPH.format(
        master=_x(master_path),
        slave=_x(slave_path),
        subswath=_x(subswath),
        polarisation=_x(polarisation),
        first_burst=int(first_burst),
        last_burst=int(last_burst),
        dem_name=_x(dem_name),
        out_dim=_x(out_dim_path),
        rg_looks=int(rg_looks),
        az_looks=int(az_looks),
    )


def run_coreg_ifg(
    gpt_exe,
    master_path,
    slave_path,
    out_dim_path,
    subswath="IW1",
    polarisation="VV",
    first_burst=1,
    last_burst=9,
    dem_name="SRTM 1Sec HGT",
    rg_looks=4,
    az_looks=1,
    log_callback=None,
    progress_callback=None,
):
    """Write the coreg/interferogram graph to disk next to the output
    and run it."""
    graph_xml = build_coreg_ifg_graph(
        master_path,
        slave_path,
        out_dim_path,
        subswath,
        polarisation,
        first_burst,
        last_burst,
        dem_name,
        rg_looks,
        az_looks,
    )
    graph_path = os.path.splitext(out_dim_path)[0] + "_graph.xml"
    with open(graph_path, "w", encoding="utf-8") as f:
        f.write(graph_xml)
    _run(
        [gpt_exe, graph_path, "-x"],
        log_callback=log_callback,
        progress_callback=progress_callback,
    )
    return out_dim_path


# ---------------------------------------------------------------------------
# Step 2 — SNAPHU export (GPT operator that prepares files for the
# external snaphu binary) + snaphu itself + SNAPHU import/geocode graph
# ---------------------------------------------------------------------------

_SNAPHU_EXPORT_GRAPH = """<graph id="sariag_snaphu_export">
  <version>1.0</version>
  <node id="Read">
    <operator>Read</operator>
    <sources/>
    <parameters><file>{in_dim}</file></parameters>
  </node>
  <node id="SnaphuExport">
    <operator>SnaphuExport</operator>
    <sources><sourceProduct refid="Read"/></sources>
    <parameters>
      <targetFolder>{export_dir}</targetFolder>
      <statCostMode>DEFO</statCostMode>
      <initMethod>MCF</initMethod>
      <numberOfTileRows>1</numberOfTileRows>
      <numberOfTileCols>1</numberOfTileCols>
      <numberOfProcessors>4</numberOfProcessors>
    </parameters>
  </node>
</graph>
"""


def run_snaphu_export(
    gpt_exe,
    in_dim_path,
    export_dir,
    log_callback=None,
    progress_callback=None,
):
    os.makedirs(export_dir, exist_ok=True)
    graph_xml = _SNAPHU_EXPORT_GRAPH.format(
        in_dim=_x(in_dim_path),
        export_dir=_x(export_dir),
    )
    graph_path = os.path.join(export_dir, "snaphu_export_graph.xml")
    with open(graph_path, "w", encoding="utf-8") as f:
        f.write(graph_xml)
    _run(
        [gpt_exe, graph_path, "-x"],
        log_callback=log_callback,
        progress_callback=progress_callback,
    )

    conf_path = None
    for root, _dirs, files in os.walk(export_dir):
        if "snaphu.conf" in files:
            conf_path = os.path.join(root, "snaphu.conf")
            break
    if conf_path is None:
        raise SnapError(
            "SnaphuExport non ha prodotto snaphu.conf in %s. / "
            "SnaphuExport did not produce snaphu.conf in %s."
            % (export_dir, export_dir)
        )
    return conf_path


# Matches "snaphu" or "snaphu.exe" (Windows) followed by its arguments.
_SNAPHU_CMD_RE = re.compile(r"snaphu(?:\.exe)?\s+-f\s+\S+\s+.+", re.IGNORECASE)


def run_snaphu_unwrap(snaphu_exe, conf_path, log_callback=None):
    """
    Run the external snaphu binary using the command line SNAP recommends
    in the header comment of the snaphu.conf it generated.
    """
    work_dir = os.path.dirname(conf_path)
    recommended = None
    with open(conf_path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            m = _SNAPHU_CMD_RE.search(line)
            if m:
                recommended = m.group(0)
                break
    if recommended is None:
        raise SnapError(
            "Impossibile trovare il comando snaphu consigliato in %s: "
            "verificare manualmente la versione di SNAP installata. / "
            "Could not find the recommended snaphu command in %s: "
            "please check the installed SNAP version manually."
            % (conf_path, conf_path)
        )
    # parts[0] is "snaphu"/"snaphu.exe" from the comment; replace it with
    # the configured executable path, keep the rest of the arguments
    # (-f snaphu.conf ...). shlex (Windows-aware) handles quoted paths
    # with spaces, e.g. "C:\Program Files\...".
    parts = install_helpers.parse_command_line(recommended)
    cmd = [snaphu_exe] + parts[1:]
    _run(cmd, cwd=work_dir, log_callback=log_callback)
    return work_dir


_SNAPHU_IMPORT_GEOCODE_GRAPH = """<graph id="sariag_snaphu_import_geocode">
  <version>1.0</version>
  <node id="Read-Filtered">
    <operator>Read</operator>
    <sources/>
    <parameters><file>{in_dim}</file></parameters>
  </node>
  <node id="Read-Unwrapped">
    <operator>Read</operator>
    <sources/>
    <parameters><file>{unwrapped_hdr}</file></parameters>
  </node>
  <node id="SnaphuImport">
    <operator>SnaphuImport</operator>
    <sources>
      <sourceProduct refid="Read-Filtered"/>
      <sourceProduct.1 refid="Read-Unwrapped"/>
    </sources>
    <parameters>
      <doNotKeepWrapped>false</doNotKeepWrapped>
    </parameters>
  </node>
  <node id="PhaseToDisplacement">
    <operator>PhaseToDisplacement</operator>
    <sources><sourceProduct refid="SnaphuImport"/></sources>
    <parameters/>
  </node>
  <node id="TerrainCorrection">
    <operator>Terrain-Correction</operator>
    <sources><sourceProduct refid="PhaseToDisplacement"/></sources>
    <parameters>
      <demName>{dem_name}</demName>
      <imgResamplingMethod>BILINEAR_INTERPOLATION</imgResamplingMethod>
      <pixelSpacingInMeter>{pixel_spacing}</pixelSpacingInMeter>
      <mapProjection>WGS84(DD)</mapProjection>
      <saveSelectedSourceBand>true</saveSelectedSourceBand>
      <saveIncidenceAngleFromEllipsoid>true</saveIncidenceAngleFromEllipsoid>
      <nodataValueAtSea>false</nodataValueAtSea>
    </parameters>
  </node>
  <node id="Write">
    <operator>Write</operator>
    <sources><sourceProduct refid="TerrainCorrection"/></sources>
    <parameters>
      <file>{out_tif}</file>
      <formatName>GeoTIFF</formatName>
    </parameters>
  </node>
</graph>
"""


def run_snaphu_import_geocode(
    gpt_exe,
    in_dim_path,
    unwrapped_hdr_path,
    out_tif_path,
    dem_name="SRTM 1Sec HGT",
    pixel_spacing=20.0,
    log_callback=None,
    progress_callback=None,
):
    """
    Import the unwrapped phase produced by snaphu back into SNAP, convert
    phase to line-of-sight displacement (meters), and geocode to a GeoTIFF
    (with an incidence-angle band, needed later for LOS decomposition).
    """
    graph_xml = _SNAPHU_IMPORT_GEOCODE_GRAPH.format(
        in_dim=_x(in_dim_path),
        unwrapped_hdr=_x(unwrapped_hdr_path),
        dem_name=_x(dem_name),
        pixel_spacing=float(pixel_spacing),
        out_tif=_x(out_tif_path),
    )
    graph_path = os.path.splitext(out_tif_path)[0] + "_import_graph.xml"
    with open(graph_path, "w", encoding="utf-8") as f:
        f.write(graph_xml)
    _run(
        [gpt_exe, graph_path, "-x"],
        log_callback=log_callback,
        progress_callback=progress_callback,
    )
    return out_tif_path


def find_unwrapped_phase_header(export_dir):
    """
    Locate the *.hdr ENVI header snaphu wrote for the unwrapped phase
    (named after the wrapped-phase image it unwrapped, in the SnaphuExport
    target folder) so it can be fed to :func:`run_snaphu_import_geocode`.
    """
    candidates = []
    for root, _dirs, files in os.walk(export_dir):
        for name in files:
            if name.lower().startswith("unwphase") and name.lower().endswith(
                ".hdr"
            ):
                candidates.append(os.path.join(root, name))
    if not candidates:
        raise SnapError(
            "Nessun file di fase unwrapped (UnwPhase*.hdr) trovato in %s "
            "dopo l'esecuzione di snaphu. / "
            "No unwrapped phase file (UnwPhase*.hdr) found in %s after "
            "running snaphu." % (export_dir, export_dir)
        )
    candidates.sort(key=os.path.getmtime, reverse=True)
    return candidates[0]


# ---------------------------------------------------------------------------
# Full single-pair pipeline
# ---------------------------------------------------------------------------


def process_pair(
    gpt_exe,
    snaphu_exe,
    master_path,
    slave_path,
    work_dir,
    pair_name,
    subswath="IW1",
    polarisation="VV",
    first_burst=1,
    last_burst=9,
    dem_name="SRTM 1Sec HGT",
    rg_looks=4,
    az_looks=1,
    pixel_spacing=20.0,
    log_callback=None,
    progress_callback=None,
):
    """
    Run the full SNAP + SNAPHU chain for one interferometric pair and
    return the path to the geocoded LOS-displacement GeoTIFF.

    progress_callback(stage_name, percent) if given; percent is the
    progress of the current stage, not of the whole pipeline.
    """

    def _stage(name, pct):
        if progress_callback:
            progress_callback(name, pct)

    os.makedirs(work_dir, exist_ok=True)
    dim_path = os.path.join(work_dir, pair_name + "_ifg.dim")
    export_dir = os.path.join(work_dir, pair_name + "_snaphu")
    out_tif = os.path.join(work_dir, pair_name + "_los_disp.tif")

    run_coreg_ifg(
        gpt_exe,
        master_path,
        slave_path,
        dim_path,
        subswath,
        polarisation,
        first_burst,
        last_burst,
        dem_name,
        rg_looks,
        az_looks,
        log_callback=log_callback,
        progress_callback=lambda p: _stage("coreg_ifg", p),
    )
    conf_path = run_snaphu_export(
        gpt_exe,
        dim_path,
        export_dir,
        log_callback=log_callback,
        progress_callback=lambda p: _stage("snaphu_export", p),
    )
    run_snaphu_unwrap(snaphu_exe, conf_path, log_callback=log_callback)
    unwrapped_hdr = find_unwrapped_phase_header(export_dir)
    run_snaphu_import_geocode(
        gpt_exe,
        dim_path,
        unwrapped_hdr,
        out_tif,
        dem_name,
        pixel_spacing,
        log_callback=log_callback,
        progress_callback=lambda p: _stage("import_geocode", p),
    )
    return out_tif
