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
dialog.py — Main dialog for the SARIAG QGIS plugin.

Five tabs: Area & date range (AOI + dates + relative orbit), Credentials &
paths (CDSE login + gpt/snaphu executables + work folder), Advanced
parameters (SNAP processing parameters), Run (run/cancel, log, progress,
load results) and Info. The whole UI is bilingual (Italian/English) with a
top-right toggle button, mirroring STAC Browser's ``self.lang`` /
``_update_ui_lang`` pattern. The actual work runs in
:class:`PipelineWorker` (a QThread) so the UI stays responsive during what
is normally a multi-hour SNAP processing run.
"""

from qgis.PyQt.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QFormLayout,
    QPushButton,
    QLabel,
    QComboBox,
    QSpinBox,
    QDoubleSpinBox,
    QWidget,
    QTabWidget,
    QProgressBar,
    QGroupBox,
    QPlainTextEdit,
    QFileDialog,
    QDateEdit,
    QMessageBox,
    QLineEdit,
    QTextBrowser,
)
from qgis.PyQt.QtCore import Qt, QThread, pyqtSignal, QDate, QTimer, QUrl
from qgis.PyQt.QtGui import QDesktopServices

try:
    from qgis.core import (
        QgsProject,
        QgsRasterLayer,
        QgsMessageLog,
        Qgis,
        QgsRasterShader,
        QgsColorRampShader,
        QgsSingleBandPseudoColorRenderer,
    )

    _HAS_QGIS = True
except ImportError:
    _HAS_QGIS = False
    QgsMessageLog = None
    Qgis = None

try:
    from qgis.core import QgsSettings
except ImportError:
    QgsSettings = None

try:
    from qgis.utils import iface as _iface
except ImportError:
    _iface = None

from .qt_compat import ensure_qt_compat
from . import core_cdse
from . import install_helpers
from . import pipeline
from . import plugin_hub

ensure_qt_compat(Qt)

_SETTINGS_BASE = "GeoFusion/SARIAG"


def _t(lang, it, en):
    """Return the Italian or English string based on lang ('it' or 'en')."""
    return en if lang == "en" else it


# Muted slate-blue dark theme, softer than STAC Browser's neon "Ocean
# Depth" — near-white text on dark gray-blue panels, closer to a modern
# terminal/IDE dark theme than a neon accent.
OCEAN_STYLE = """
QDialog {
    background-color: #141a22;
    color: #f2f5f8;
    font-family: 'Segoe UI', 'Inter', 'Roboto', Tahoma, Geneva,
                 Verdana, sans-serif;
    font-size: 13px;
}
QWidget { background-color: #141a22; color: #f2f5f8; }
QLabel { color: #c3ccd6; font-size: 13px; }
QGroupBox {
    border: 1px solid #2c3a48;
    border-radius: 8px;
    margin-top: 10px;
    padding: 12px 10px;
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
                stop:0 #1b2430,stop:1 #141a22);
    color: #f2f5f8;
    font-weight: 600;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 6px;
    color: #5b9bd5;
    font-size: 12px;
}
QPushButton {
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
                stop:0 #3f6f9e,stop:1 #2c4f70);
    color: #f2f5f8;
    border: 1px solid #5b9bd5;
    border-radius: 6px;
    padding: 7px 14px;
    font-weight: 700;
    font-size: 12px;
}
QPushButton:hover {
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
                stop:0 #4d84b8,stop:1 #3f6f9e);
    color: #ffffff;
}
QPushButton:pressed { background: #2c4f70; }
QPushButton:disabled {
    background: #1b2430; color: #6b7785; border-color: #2c3a48;
}
QPushButton#btnLang {
    background: #1b2430;
    color: #c3ccd6;
    border: 1px solid #2c3a48;
    padding: 4px 10px;
    font-size: 11px;
    font-weight: 700;
    border-radius: 4px;
}
QPushButton#btnLang:hover { background: #22303e; color: #5b9bd5; }
QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit, QDateEdit {
    padding: 5px 8px;
    border: 1px solid #2c3a48;
    border-radius: 5px;
    background: #1b2430;
    color: #f2f5f8;
    selection-background-color: #2c4f70;
}
QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus,
QLineEdit:focus, QDateEdit:focus {
    border-color: #5b9bd5;
}
QComboBox::drop-down { border: none; padding-right: 6px; }
QTabWidget::pane {
    border: 1px solid #2c3a48;
    border-radius: 6px;
    top: -1px;
    background: #141a22;
}
QTabBar::tab {
    background: #1b2430;
    border: 1px solid #2c3a48;
    padding: 7px 14px;
    margin-right: 2px;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    color: #8a97a5;
    font-size: 12px;
}
QTabBar::tab:selected {
    background: #141a22;
    border-bottom-color: #141a22;
    font-weight: bold;
    color: #5b9bd5;
}
QTabBar::tab:hover:!selected { background: #22303e; color: #c3ccd6; }
QProgressBar {
    border: 1px solid #2c3a48;
    border-radius: 4px;
    height: 6px;
    background: #1b2430;
    color: transparent;
}
QProgressBar::chunk {
    background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                stop:0 #3f6f9e,stop:1 #5b9bd5);
    border-radius: 3px;
}
QPlainTextEdit, QTextBrowser {
    background: #1b2430;
    border: 1px solid #2c3a48;
    border-radius: 5px;
    color: #c3ccd6;
    font-size: 12px;
}
QScrollBar:vertical, QScrollBar:horizontal {
    background: #1b2430;
    border: none;
    width: 8px; height: 8px;
}
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {
    background: #2c3a48;
    border-radius: 4px;
    min-height: 20px;
}
QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover {
    background: #5b9bd5;
}
QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; }
QFrame { background: transparent; }
"""

# ---------------------------------------------------------------------------
# Info tab HTML — bilingual inline (both languages shown together, like
# STAC Browser's own Info tab), so it does not need to be rebuilt on
# language toggle.
# ---------------------------------------------------------------------------

INFO_HTML = """
<html>
<head>
<style>
  body { background:#141a22; color:#f2f5f8;
         font-family:'Segoe UI',Arial,sans-serif;
         font-size:12px; margin:16px; line-height:1.6; }
  h2   { color:#5b9bd5; font-size:15px; margin:0 0 12px;
         border-bottom:1px solid #2c3a48; padding-bottom:6px; }
  h3   { color:#8ab4e0; font-size:13px; margin:16px 0 6px; }
  h4   { color:#f59e0b; font-size:12px; margin:12px 0 4px; }
  p, li { margin:3px 0; color:#c3ccd6; }
  a    { color:#5b9bd5; }
  code { background:#22303e; padding:1px 4px; border-radius:3px;
         font-family:monospace; }
  table { border-collapse:collapse; width:100%; margin:8px 0; }
  th   { background:#22303e; color:#c3ccd6; padding:6px 10px; text-align:left;
         border-bottom:2px solid #2c3a48; font-size:11px; }
  td   { padding:5px 10px; border-bottom:1px solid #22303e; color:#f2f5f8; }
  tr:nth-child(even) td { background:rgba(27,36,48,0.5); }
  .badge-warn { background:rgba(239,68,68,0.15); color:#ef4444;
                padding:1px 6px; border-radius:8px; font-size:11px;
                font-weight:600; }
  .badge-ok   { background:rgba(34,197,94,0.15); color:#22c55e;
                padding:1px 6px; border-radius:8px; font-size:11px;
                font-weight:600; }
  .section-sep { border:none; border-top:1px solid #22303e; margin:16px 0; }
</style>
</head>
<body>

<h2>SARIAG &mdash; INFORMAZIONI / INFORMATION</h2>

<h3>IL PLUGIN È UN ORCHESTRATORE / THE PLUGIN IS AN ORCHESTRATOR</h3>
<p><b>IT:</b> SARIAG <b>non ospita né rivende dati o software</b>. Cerca e
scarica le scene Sentinel-1 SLC dal Copernicus Data Space Ecosystem con le
tue credenziali, poi guida <b>ESA SNAP</b> (<code>gpt</code>) e
<b>SNAPHU</b> &mdash; già installati da te &mdash; nell'elaborazione
InSAR. La coregistrazione e soprattutto l'<i>unwrapping di fase</i> sono
algoritmi delicati che SNAP e SNAPHU implementano correttamente da anni:
SARIAG li orchestra invece di reimplementarli.</p>
<p><b>EN:</b> SARIAG <b>does not host or resell data or software</b>. It
searches and downloads Sentinel-1 SLC scenes from the Copernicus Data
Space Ecosystem using your own credentials, then drives <b>ESA SNAP</b>
(<code>gpt</code>) and <b>SNAPHU</b> &mdash; installed by you &mdash;
through InSAR processing. Coregistration and especially <i>phase
unwrapping</i> are delicate algorithms that SNAP and SNAPHU have
implemented correctly for years: SARIAG orchestrates them instead of
reimplementing them.</p>

<h3>FONTI, LICENZE E ACCOUNT / SOURCES, LICENSES AND ACCOUNTS</h3>
<table>
  <tr><th>Cosa / What</th><th>Licenza / License</th><th>Account</th></tr>
  <tr><td>Scene Sentinel-1 SLC / Sentinel-1 SLC scenes</td>
      <td><span class="badge-ok">CC BY 4.0</span> (Copernicus/ESA)</td>
      <td>Gratuito / Free &mdash;
      <a href="https://dataspace.copernicus.eu/">dataspace.copernicus.eu</a>
      </td></tr>
  <tr><td>ESA SNAP (<code>gpt</code>)</td>
      <td>Gratuito / Free (ESA)</td>
      <td>Nessuno / None</td></tr>
  <tr><td>SNAPHU (unwrapping)</td>
      <td>Gratuito / Free (Stanford / GINA-ASF)</td>
      <td>Nessuno / None</td></tr>
</table>
<p><b>IT:</b> i dati Sentinel-1 restano di propriet&agrave; di
Copernicus/ESA e richiedono <b>attribuzione</b>. SNAP e SNAPHU restano dei
rispettivi autori: leggi le loro licenze prima dell'uso.</p>
<p><b>EN:</b> Sentinel-1 data remains the property of Copernicus/ESA and
requires <b>attribution</b>. SNAP and SNAPHU remain their respective
authors': read their licenses before use.</p>

<h3>COME FUNZIONA / HOW IT WORKS</h3>
<p><b>IT:</b> per ogni orbita (ascendente e discendente): ricerca
automatica CDSE &rarr; download SLC &rarr; per ogni coppia consecutiva,
coregistrazione TOPS + interferogramma + filtro Goldstein
(<code>gpt</code>) &rarr; unwrapping (<code>snaphu</code>) &rarr; import e
geocodifica (<code>gpt</code>) &rarr; inversione della rete a baseline
corta (SBAS, pseudo-inversa) in una velocit&agrave; LOS. Le due
velocit&agrave; LOS (ascendente + discendente) vengono infine scomposte
per Cramer in <b>Verticale</b> ed <b>Est-Ovest</b>.</p>
<p><b>EN:</b> for each orbit (ascending and descending): automatic CDSE
search &rarr; SLC download &rarr; for every consecutive pair, TOPS
coregistration + interferogram + Goldstein filter (<code>gpt</code>)
&rarr; unwrapping (<code>snaphu</code>) &rarr; import and geocoding
(<code>gpt</code>) &rarr; small-baseline network inversion (SBAS,
pseudo-inverse) into a LOS velocity. The two LOS velocities
(ascending + descending) are finally decomposed via Cramer's rule into
<b>Vertical</b> and <b>East-West</b>.</p>

<h3>LIMITI NOTI / KNOWN LIMITATIONS</h3>
<p><b>IT:</b></p>
<ul>
<li><b>Segno verticale</b>: se invertito rispetto a un riferimento noto,
    cambialo nella scheda Parametri avanzati (dipende dalla versione di
    SNAP).</li>
<li><b>Componente Nord-Sud</b>: non calcolabile &mdash; limite fisico
    delle orbite quasi polari, non una scelta implementativa.</li>
<li><b>Rete SBAS</b>: v0.1 usa la catena di coppie consecutive più
    semplice.</li>
<li><b>Barra di progresso</b>: mostra la fase corrente, non
    l'avanzamento complessivo (che può durare ore).</li>
</ul>
<p><b>EN:</b></p>
<ul>
<li><b>Vertical sign</b>: if inverted against a known reference, flip it
    in the Advanced Parameters tab (depends on the installed SNAP
    version).</li>
<li><b>North-South component</b>: not computable &mdash; a physical
    limit of near-polar orbits, not an implementation choice.</li>
<li><b>SBAS network</b>: v0.1 uses the simplest consecutive-pair
    chain.</li>
<li><b>Progress bar</b>: shows the current stage, not the whole run's
    progress (which can take hours).</li>
</ul>

<h3>REQUISITI / REQUIREMENTS</h3>
<p><b>IT:</b> QGIS 3.16+ &middot; ESA SNAP con S1TBX &middot; SNAPHU &middot;
account gratuito Copernicus Data Space Ecosystem &middot; spazio disco
abbondante (~4&ndash;5 GB per scena SLC) &middot; <span
class="badge-warn">nessuna GPU richiesta</span>, contano CPU e RAM.</p>
<p><b>EN:</b> QGIS 3.16+ &middot; ESA SNAP with S1TBX &middot; SNAPHU
&middot; free Copernicus Data Space Ecosystem account &middot; plenty of
disk space (~4&ndash;5 GB per SLC scene) &middot; <span
class="badge-warn">no GPU required</span>, CPU and RAM matter instead.</p>

<hr class="section-sep">

<h3>AUTORE / AUTHOR</h3>
<p>Dott. Sarino Alfonso Grande &mdash;
<a href="mailto:sino.grande@gmail.com">sino.grande@gmail.com</a>
&mdash; <a href="https://sinocloud.it">sinocloud.it</a> &mdash;
<a href="https://github.com/sag1687">github.com/sag1687</a></p>
<p style="color:#8a97a5;font-size:11px;">SARIAG &middot; GPL-2.0 &middot;
fratello di / sibling of STAC Browser.</p>

</body>
</html>
"""


def _log_warning(message):
    if QgsMessageLog is not None:
        QgsMessageLog.logMessage(message, "SARIAG", Qgis.Warning)


# ---------------------------------------------------------------------------
# Background workers
# ---------------------------------------------------------------------------


class PipelineWorker(QThread):
    logMessage = pyqtSignal(str)
    progressChanged = pyqtSignal(str, int)
    finishedOk = pyqtSignal(dict)
    finishedError = pyqtSignal(str)

    def __init__(self, params, parent=None):
        super().__init__(parent)
        self.params = params
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        try:
            result = pipeline.run_pipeline(
                self.params,
                log=lambda msg: self.logMessage.emit(msg),
                progress=lambda stage, pct: self.progressChanged.emit(
                    stage, pct
                ),
                cancel_check=lambda: self._cancel,
            )
            self.finishedOk.emit(result)
        except (
            Exception
        ) as exc:  # noqa: BLE001 — surfaced to the UI, not swallowed
            self.finishedError.emit(str(exc))


class AvailabilityWorker(QThread):
    """
    Search-only worker (no download, no SNAP): mirrors STAC Browser's
    "automatic search" — as soon as the AOI/dates/credentials are usable,
    check what CDSE actually has for both orbit directions so the user can
    pick a relative orbit from real data instead of guessing a number.
    """

    resultReady = pyqtSignal(
        dict, dict, dict
    )  # groups_asc, groups_desc, token
    errorOccurred = pyqtSignal(str)

    def __init__(
        self,
        username,
        password,
        bbox,
        date_from,
        date_to,
        token=None,
        parent=None,
    ):
        super().__init__(parent)
        self.username = username
        self.password = password
        self.bbox = bbox
        self.date_from = date_from
        self.date_to = date_to
        self.token = token

    def run(self):
        try:
            if self.token is not None:
                try:
                    token = core_cdse.ensure_valid_token(self.token)
                except core_cdse.CdseError:
                    token = core_cdse.authenticate(
                        self.username, self.password
                    )
            else:
                token = core_cdse.authenticate(self.username, self.password)

            asc = core_cdse.search_s1_slc(
                token["access_token"],
                self.bbox,
                self.date_from,
                self.date_to,
                orbit_direction=core_cdse.ORBIT_ASCENDING,
            )
            desc = core_cdse.search_s1_slc(
                token["access_token"],
                self.bbox,
                self.date_from,
                self.date_to,
                orbit_direction=core_cdse.ORBIT_DESCENDING,
            )
            groups_asc = core_cdse.group_by_relative_orbit(asc)
            groups_desc = core_cdse.group_by_relative_orbit(desc)
            self.resultReady.emit(groups_asc, groups_desc, token)
        except (
            Exception
        ) as exc:  # noqa: BLE001 — surfaced to the UI, not swallowed
            self.errorOccurred.emit(str(exc))


class SnaphuInstallWorker(QThread):
    logMessage = pyqtSignal(str)
    finishedOk = pyqtSignal(str)
    finishedError = pyqtSignal(str)

    def run(self):
        try:
            path = install_helpers.install_snaphu(
                log_callback=lambda msg: self.logMessage.emit(msg)
            )
            self.finishedOk.emit(path)
        except (
            Exception
        ) as exc:  # noqa: BLE001 — surfaced to the UI, not swallowed
            self.finishedError.emit(str(exc))


# ---------------------------------------------------------------------------
# Dialog
# ---------------------------------------------------------------------------


class SariagDialog(QDialog):

    drawRequested = pyqtSignal(str)

    def __init__(self, parent=None, lang="it"):
        super().__init__(parent)
        self.lang = lang
        self.resize(780, 660)
        self.setStyleSheet(OCEAN_STYLE)
        self.worker = None
        self._results = None
        self._availability_worker = None
        self._availability_token = None
        self._snaphu_worker = None
        self._availability_text = None  # (it, en) — last availability status

        # Retranslation registries, filled while building each tab and
        # consumed by _update_ui_lang() (see the bottom of this file).
        self._i18n_widgets = []  # (widget, setter_name, it, en)
        self._i18n_tabs = []  # (tabs_widget, index, it, en)
        self._i18n_items = []  # (combo, index, it, en)

        self._build_ui()
        self._update_ui_lang()
        self._load_settings()

    # ------------------------------------------------------------------
    # Retranslation helpers
    # ------------------------------------------------------------------

    def _tr(self, widget, setter, it, en):
        """Register a widget for retranslation; text is applied by the
        initial :meth:`_update_ui_lang` call, not here."""
        self._i18n_widgets.append((widget, setter, it, en))
        return widget

    def _mklabel(self, it, en):
        lbl = QLabel()
        self._tr(lbl, "setText", it, en)
        return lbl

    def _mkbutton(self, it, en):
        btn = QPushButton()
        self._tr(btn, "setText", it, en)
        return btn

    def _mkgroup(self, it, en):
        grp = QGroupBox()
        self._tr(grp, "setTitle", it, en)
        return grp

    def _tr_tab(self, tabs, index, it, en):
        self._i18n_tabs.append((tabs, index, it, en))

    def _tr_item(self, combo, index, it, en):
        self._i18n_items.append((combo, index, it, en))

    def _update_ui_lang(self):
        lang = self.lang
        self.setWindowTitle(
            _t(
                lang,
                "SARIAG — Serie temporali Sentinel-1 (InSAR)",
                "SARIAG — Sentinel-1 time series (InSAR)",
            )
        )
        self.btn_lang.setText(plugin_hub.lang_button_label(lang))
        if hasattr(self, "family_widget"):
            self.family_widget.set_lang(lang)
        for widget, setter, it, en in self._i18n_widgets:
            getattr(widget, setter)(_t(lang, it, en))
        for tabs, index, it, en in self._i18n_tabs:
            tabs.setTabText(index, _t(lang, it, en))
        for combo, index, it, en in self._i18n_items:
            if index < combo.count():
                combo.setItemText(index, _t(lang, it, en))
        if self._availability_text is not None:
            it, en = self._availability_text
            self.lbl_availability.setText(_t(lang, it, en))

    def _toggle_lang(self):
        self.lang = "en" if self.lang == "it" else "it"
        self._update_ui_lang()

    def _set_availability_text(self, it, en):
        self._availability_text = (it, en)
        self.lbl_availability.setText(_t(self.lang, it, en))

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        layout = QVBoxLayout(self)

        top_bar = QHBoxLayout()
        self.lbl_title = QLabel()
        self._tr(self.lbl_title, "setText", "🛰️ SARIAG", "🛰️ SARIAG")
        self.lbl_title.setStyleSheet(
            "color:#5b9bd5; font-size:15px; font-weight:700;"
        )
        top_bar.addWidget(self.lbl_title)
        top_bar.addStretch(1)
        self.btn_lang = QPushButton(plugin_hub.LANG_LABEL_EN)
        self.btn_lang.setObjectName("btnLang")
        self.btn_lang.clicked.connect(self._toggle_lang)
        top_bar.addWidget(self.btn_lang)
        layout.addLayout(top_bar)

        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)

        self.TAB_AREA = self.tabs.addTab(self._build_area_tab(), "")
        self._tr_tab(
            self.tabs, self.TAB_AREA, "Area && periodo", "Area && dates"
        )

        self.TAB_CREDENTIALS = self.tabs.addTab(
            self._build_credentials_tab(), ""
        )
        self._tr_tab(
            self.tabs,
            self.TAB_CREDENTIALS,
            "Credenziali && percorsi",
            "Credentials && paths",
        )

        self.TAB_PARAMS = self.tabs.addTab(self._build_params_tab(), "")
        self._tr_tab(
            self.tabs,
            self.TAB_PARAMS,
            "Parametri avanzati",
            "Advanced parameters",
        )

        self.TAB_RUN = self.tabs.addTab(self._build_run_tab(), "")
        self._tr_tab(self.tabs, self.TAB_RUN, "Elabora", "Run")

        self.TAB_INFO = self.tabs.addTab(self._build_info_tab(), "ℹ Info")

        # Debounced "automatic search" (mirrors STAC Browser): fires a
        # search-only CDSE query shortly after the AOI/dates/credentials
        # settle, instead of requiring an explicit search action.
        self._availability_timer = QTimer(self)
        self._availability_timer.setSingleShot(True)
        self._availability_timer.timeout.connect(self._run_availability_search)
        for widget, signal_name in (
            (self.sb_west, "valueChanged"),
            (self.sb_south, "valueChanged"),
            (self.sb_east, "valueChanged"),
            (self.sb_north, "valueChanged"),
            (self.de_from, "dateChanged"),
            (self.de_to, "dateChanged"),
            (self.ed_user, "textChanged"),
            (self.ed_pwd, "textChanged"),
        ):
            getattr(widget, signal_name).connect(
                self._schedule_availability_search
            )

    def _make_coord_spin(self, lo, hi, default):
        sb = QDoubleSpinBox()
        sb.setRange(lo, hi)
        sb.setDecimals(6)
        sb.setValue(default)
        return sb

    def _build_area_tab(self):
        w = QWidget()
        v = QVBoxLayout(w)

        bbox_group = self._mkgroup(
            "Area di interesse (AOI)", "Area of interest (AOI)"
        )
        form = QFormLayout(bbox_group)
        self.sb_west = self._make_coord_spin(-180, 180, -9.0)
        self.sb_south = self._make_coord_spin(-90, 90, 38.0)
        self.sb_east = self._make_coord_spin(-180, 180, -8.8)
        self.sb_north = self._make_coord_spin(-90, 90, 38.2)
        form.addRow("West:", self.sb_west)
        form.addRow("South:", self.sb_south)
        form.addRow("East:", self.sb_east)
        form.addRow("North:", self.sb_north)
        btn_draw = self._mkbutton("Disegna sulla mappa", "Draw on map")
        btn_draw.clicked.connect(lambda: self.drawRequested.emit("bbox"))
        form.addRow(btn_draw)
        v.addWidget(bbox_group)

        date_group = self._mkgroup("Periodo", "Date range")
        form2 = QFormLayout(date_group)
        self.de_from = QDateEdit(QDate.currentDate().addMonths(-12))
        self.de_from.setCalendarPopup(True)
        self.de_to = QDateEdit(QDate.currentDate())
        self.de_to.setCalendarPopup(True)
        form2.addRow(self._mklabel("Da:", "From:"), self.de_from)
        form2.addRow(self._mklabel("A:", "To:"), self.de_to)
        v.addWidget(date_group)

        orbit_group = self._mkgroup(
            "Disponibilità dati (ricerca automatica CDSE)",
            "Data availability (automatic CDSE search)",
        )
        form3 = QFormLayout(orbit_group)
        self.cb_ro_asc = QComboBox()
        self.cb_ro_asc.addItem("", None)
        self._tr_item(
            self.cb_ro_asc,
            0,
            "Automatico (in attesa di ricerca...)",
            "Automatic (waiting for search...)",
        )
        self.cb_ro_desc = QComboBox()
        self.cb_ro_desc.addItem("", None)
        self._tr_item(
            self.cb_ro_desc,
            0,
            "Automatico (in attesa di ricerca...)",
            "Automatic (waiting for search...)",
        )
        form3.addRow(
            self._mklabel("Orbita ascendente:", "Ascending orbit:"),
            self.cb_ro_asc,
        )
        form3.addRow(
            self._mklabel("Orbita discendente:", "Descending orbit:"),
            self.cb_ro_desc,
        )

        self.lbl_availability = QLabel()
        self.lbl_availability.setWordWrap(True)
        self._set_availability_text(
            "Disegna l'area, imposta il periodo e le credenziali CDSE per "
            "vedere subito le scene disponibili.",
            "Draw the area, set the date range and CDSE credentials to "
            "see available scenes right away.",
        )
        form3.addRow(self.lbl_availability)

        btn_refresh_availability = self._mkbutton(
            "Aggiorna ora", "Refresh now"
        )
        btn_refresh_availability.clicked.connect(self._run_availability_search)
        form3.addRow(btn_refresh_availability)

        v.addWidget(orbit_group)

        v.addStretch(1)
        return w

    def _build_credentials_tab(self):
        w = QWidget()
        v = QVBoxLayout(w)

        cred_group = self._mkgroup(
            "Copernicus Data Space Ecosystem (account gratuito)",
            "Copernicus Data Space Ecosystem (free account)",
        )
        form = QFormLayout(cred_group)
        self.ed_user = QLineEdit()
        self.ed_pwd = QLineEdit()
        try:
            self.ed_pwd.setEchoMode(QLineEdit.EchoMode.Password)
        except AttributeError:
            self.ed_pwd.setEchoMode(QLineEdit.Password)
        form.addRow(self._mklabel("Utente:", "Username:"), self.ed_user)
        form.addRow("Password:", self.ed_pwd)
        self.lbl_hint = QLabel()
        self._tr(
            self.lbl_hint,
            "setText",
            '<a href="https://dataspace.copernicus.eu/">'
            "Registrati su dataspace.copernicus.eu</a>",
            '<a href="https://dataspace.copernicus.eu/">'
            "Register at dataspace.copernicus.eu</a>",
        )
        self.lbl_hint.setOpenExternalLinks(True)
        form.addRow(self.lbl_hint)
        v.addWidget(cred_group)

        paths_group = self._mkgroup(
            "Strumenti locali, gratuiti (Linux/Windows)",
            "Local tools, free (Linux/Windows)",
        )
        form2 = QFormLayout(paths_group)

        self.ed_gpt = QLineEdit()
        btn_gpt_browse = QPushButton("...")
        btn_gpt_browse.clicked.connect(
            lambda: self._browse_file(self.ed_gpt, "gpt (SNAP)")
        )
        btn_gpt_detect = self._mkbutton("Rileva", "Detect")
        btn_gpt_detect.clicked.connect(self._detect_gpt)
        btn_gpt_download = self._mkbutton("Scarica SNAP", "Download SNAP")
        btn_gpt_download.clicked.connect(self._open_snap_download)
        row_gpt = QHBoxLayout()
        row_gpt.addWidget(self.ed_gpt)
        row_gpt.addWidget(btn_gpt_browse)
        row_gpt.addWidget(btn_gpt_detect)
        row_gpt.addWidget(btn_gpt_download)
        form2.addRow("SNAP gpt:", row_gpt)

        self.ed_snaphu = QLineEdit()
        btn_snaphu_browse = QPushButton("...")
        btn_snaphu_browse.clicked.connect(
            lambda: self._browse_file(self.ed_snaphu, "snaphu")
        )
        btn_snaphu_detect = self._mkbutton("Rileva", "Detect")
        btn_snaphu_detect.clicked.connect(self._detect_snaphu)
        self.btn_snaphu_install = self._mkbutton("Installa", "Install")
        self.btn_snaphu_install.clicked.connect(self._install_snaphu)
        row_snaphu = QHBoxLayout()
        row_snaphu.addWidget(self.ed_snaphu)
        row_snaphu.addWidget(btn_snaphu_browse)
        row_snaphu.addWidget(btn_snaphu_detect)
        row_snaphu.addWidget(self.btn_snaphu_install)
        form2.addRow("SNAPHU:", row_snaphu)

        install_note = QLabel()
        self._tr(
            install_note,
            "setText",
            "SNAP e SNAPHU sono entrambi gratuiti. 'Rileva' cerca "
            "un'installazione già presente sul sistema; 'Installa' scarica "
            "e compila SNAPHU in locale (richiede git + un compilatore C, "
            "vedi README per Windows). SNAP va installato manualmente una "
            "sola volta (pulsante 'Scarica SNAP'), è un programma desktop "
            "di alcuni GB.",
            "SNAP and SNAPHU are both free. 'Detect' looks for an install "
            "already on the system; 'Install' downloads and builds "
            "SNAPHU locally (needs git + a C compiler, see the README for "
            "Windows). SNAP must be installed manually once ('Download "
            "SNAP' button), it's a multi-GB desktop application.",
        )
        install_note.setWordWrap(True)
        form2.addRow(install_note)

        self.install_log = QPlainTextEdit()
        self.install_log.setReadOnly(True)
        self.install_log.setMaximumHeight(90)
        form2.addRow(self.install_log)

        self.ed_workdir = QLineEdit()
        btn_workdir = QPushButton("...")
        btn_workdir.clicked.connect(self._browse_workdir)
        row_workdir = QHBoxLayout()
        row_workdir.addWidget(self.ed_workdir)
        row_workdir.addWidget(btn_workdir)
        form2.addRow(
            self._mklabel("Cartella di lavoro:", "Work folder:"), row_workdir
        )

        v.addWidget(paths_group)

        btn_row = QHBoxLayout()
        btn_save = self._mkbutton("Salva", "Save")
        btn_save.clicked.connect(self._save_settings)
        btn_row.addWidget(btn_save)
        btn_row.addStretch(1)
        v.addLayout(btn_row)

        v.addStretch(1)
        return w

    def _browse_file(self, line_edit, hint):
        path, _filter = QFileDialog.getOpenFileName(
            self,
            _t(self.lang, "Seleziona %s", "Select %s") % hint,
            line_edit.text(),
        )
        if path:
            line_edit.setText(path)

    def _browse_workdir(self):
        path = QFileDialog.getExistingDirectory(
            self,
            _t(self.lang, "Cartella di lavoro", "Work folder"),
            self.ed_workdir.text(),
        )
        if path:
            self.ed_workdir.setText(path)

    # ------------------------------------------------------------------
    # gpt / SNAPHU auto-detect & auto-install
    # ------------------------------------------------------------------

    def _detect_gpt(self):
        found = install_helpers.find_gpt()
        if found:
            self.ed_gpt.setText(found)
        else:
            QMessageBox.information(
                self,
                "SARIAG",
                _t(
                    self.lang,
                    "gpt non trovato automaticamente: installa SNAP e "
                    "indica il percorso manualmente.",
                    "gpt not found automatically: install SNAP and set "
                    "the path manually.",
                ),
            )

    def _detect_snaphu(self):
        found = install_helpers.find_snaphu()
        if found:
            self.ed_snaphu.setText(found)
        else:
            QMessageBox.information(
                self,
                "SARIAG",
                _t(
                    self.lang,
                    "snaphu non trovato automaticamente: prova 'Installa' "
                    "o indica il percorso manualmente.",
                    "snaphu not found automatically: try 'Install' or set "
                    "the path manually.",
                ),
            )

    def _install_snaphu(self):
        self.btn_snaphu_install.setEnabled(False)
        self.install_log.clear()
        self._snaphu_worker = SnaphuInstallWorker(self)
        self._snaphu_worker.logMessage.connect(
            self.install_log.appendPlainText
        )
        self._snaphu_worker.finishedOk.connect(self._on_snaphu_installed)
        self._snaphu_worker.finishedError.connect(
            self._on_snaphu_install_error
        )
        self._snaphu_worker.finished.connect(self._snaphu_worker.deleteLater)
        self._snaphu_worker.start()

    def _on_snaphu_installed(self, path):
        self.ed_snaphu.setText(path)
        self.btn_snaphu_install.setEnabled(True)
        self.install_log.appendPlainText(
            _t(self.lang, "Installato in %s", "Installed at %s") % path
        )

    def _on_snaphu_install_error(self, message):
        self.btn_snaphu_install.setEnabled(True)
        QMessageBox.critical(self, "SARIAG", message)

    def _open_snap_download(self):
        QDesktopServices.openUrl(QUrl(install_helpers.SNAP_DOWNLOAD_PAGE))

    def _build_params_tab(self):
        w = QWidget()
        form = QFormLayout(w)

        self.cb_subswath = QComboBox()
        self.cb_subswath.addItems(["IW1", "IW2", "IW3"])
        form.addRow("Sub-swath:", self.cb_subswath)

        self.cb_pol = QComboBox()
        self.cb_pol.addItems(["VV", "VH", "HH", "HV"])
        form.addRow(
            self._mklabel("Polarizzazione:", "Polarisation:"), self.cb_pol
        )

        self.sb_first_burst = QSpinBox()
        self.sb_first_burst.setRange(1, 20)
        self.sb_first_burst.setValue(1)
        self.sb_last_burst = QSpinBox()
        self.sb_last_burst.setRange(1, 20)
        self.sb_last_burst.setValue(9)
        form.addRow(
            self._mklabel("Primo burst:", "First burst:"), self.sb_first_burst
        )
        form.addRow(
            self._mklabel("Ultimo burst:", "Last burst:"), self.sb_last_burst
        )

        self.cb_dem = QComboBox()
        self.cb_dem.addItems(
            ["SRTM 1Sec HGT", "SRTM 3Sec", "Copernicus 30m Global DEM"]
        )
        form.addRow("DEM:", self.cb_dem)

        self.sb_rg_looks = QSpinBox()
        self.sb_rg_looks.setRange(1, 20)
        self.sb_rg_looks.setValue(4)
        self.sb_az_looks = QSpinBox()
        self.sb_az_looks.setRange(1, 20)
        self.sb_az_looks.setValue(1)
        form.addRow("Range looks:", self.sb_rg_looks)
        form.addRow("Azimuth looks:", self.sb_az_looks)

        self.sb_pixel_spacing = QDoubleSpinBox()
        self.sb_pixel_spacing.setRange(5.0, 200.0)
        self.sb_pixel_spacing.setValue(20.0)
        form.addRow("Pixel spacing output (m):", self.sb_pixel_spacing)

        self.cb_los_sign = QComboBox()
        self.cb_los_sign.addItems(["", ""])
        self._tr_item(self.cb_los_sign, 0, "+1 (predefinito)", "+1 (default)")
        self._tr_item(self.cb_los_sign, 1, "-1 (invertito)", "-1 (inverted)")
        form.addRow(
            self._mklabel("Segno verticale:", "Vertical sign:"),
            self.cb_los_sign,
        )

        note = QLabel()
        self._tr(
            note,
            "setText",
            "Se lo spostamento verticale risulta invertito rispetto a un "
            "riferimento noto (es. area in subsidenza nota), cambiare "
            "questo segno.",
            "If the vertical result comes out inverted against a known "
            "reference (e.g. a known-subsiding area), flip this sign.",
        )
        note.setWordWrap(True)
        form.addRow(note)

        return w

    def _build_run_tab(self):
        w = QWidget()
        v = QVBoxLayout(w)

        btn_row = QHBoxLayout()
        self.btn_run = self._mkbutton("Avvia elaborazione", "Run")
        self.btn_run.clicked.connect(self._start_pipeline)
        self.btn_cancel = self._mkbutton("Annulla", "Cancel")
        self.btn_cancel.clicked.connect(self._cancel_pipeline)
        self.btn_cancel.setEnabled(False)
        btn_row.addWidget(self.btn_run)
        btn_row.addWidget(self.btn_cancel)
        btn_row.addStretch(1)
        v.addLayout(btn_row)

        self.progress_bar = QProgressBar()
        v.addWidget(self.progress_bar)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        v.addWidget(self.log_view, 1)

        self.btn_load = self._mkbutton(
            "Carica risultati in QGIS", "Load results into QGIS"
        )
        self.btn_load.clicked.connect(self._load_results)
        self.btn_load.setEnabled(False)
        v.addWidget(self.btn_load)

        return w

    def _build_info_tab(self):
        w = QWidget()
        v = QVBoxLayout(w)
        browser = QTextBrowser()
        browser.setOpenExternalLinks(True)
        browser.setHtml(INFO_HTML)
        v.addWidget(browser, 1)
        self.family_widget = plugin_hub.make_family_widget(
            "sariag", lang=self.lang
        )
        v.addWidget(self.family_widget)
        return w

    # ------------------------------------------------------------------
    # AOI drawing hookup (called by plugin.py after a rectangle is drawn)
    # ------------------------------------------------------------------

    def set_bbox(self, west, south, east, north):
        self.sb_west.setValue(west)
        self.sb_south.setValue(south)
        self.sb_east.setValue(east)
        self.sb_north.setValue(north)
        self.show()
        self.raise_()
        self.activateWindow()

    # ------------------------------------------------------------------
    # Settings persistence (QgsSettings, mirrors STAC Browser's pattern)
    # ------------------------------------------------------------------

    def _load_settings(self):
        if QgsSettings is not None:
            s = QgsSettings()
            self.ed_user.setText(
                s.value(_SETTINGS_BASE + "/cdse_username", "") or ""
            )
            self.ed_pwd.setText(
                s.value(_SETTINGS_BASE + "/cdse_password", "") or ""
            )
            self.ed_gpt.setText(s.value(_SETTINGS_BASE + "/gpt_exe", "") or "")
            self.ed_snaphu.setText(
                s.value(_SETTINGS_BASE + "/snaphu_exe", "") or ""
            )
            self.ed_workdir.setText(
                s.value(_SETTINGS_BASE + "/work_dir", "") or ""
            )
            saved_lang = s.value(_SETTINGS_BASE + "/lang", "") or ""
            if saved_lang in ("it", "en") and saved_lang != self.lang:
                self.lang = saved_lang
                self._update_ui_lang()

        # Nothing saved yet (or SNAP/SNAPHU were installed afterwards) —
        # try a silent, read-only auto-detect before asking the user.
        if not self.ed_gpt.text().strip():
            found = install_helpers.find_gpt()
            if found:
                self.ed_gpt.setText(found)
        if not self.ed_snaphu.text().strip():
            found = install_helpers.find_snaphu()
            if found:
                self.ed_snaphu.setText(found)

    def _save_settings(self):
        if QgsSettings is None:
            QMessageBox.information(
                self,
                "SARIAG",
                _t(
                    self.lang,
                    "QgsSettings non disponibile.",
                    "QgsSettings unavailable.",
                ),
            )
            return
        s = QgsSettings()
        s.setValue(
            _SETTINGS_BASE + "/cdse_username", self.ed_user.text().strip()
        )
        s.setValue(_SETTINGS_BASE + "/cdse_password", self.ed_pwd.text())
        s.setValue(_SETTINGS_BASE + "/gpt_exe", self.ed_gpt.text().strip())
        s.setValue(
            _SETTINGS_BASE + "/snaphu_exe", self.ed_snaphu.text().strip()
        )
        s.setValue(
            _SETTINGS_BASE + "/work_dir", self.ed_workdir.text().strip()
        )
        s.setValue(_SETTINGS_BASE + "/lang", self.lang)
        QMessageBox.information(
            self,
            "SARIAG",
            _t(self.lang, "Impostazioni salvate.", "Settings saved."),
        )

    # ------------------------------------------------------------------
    # Pipeline execution
    # ------------------------------------------------------------------

    def _collect_params(self):
        L = self.lang
        bbox = [
            self.sb_west.value(),
            self.sb_south.value(),
            self.sb_east.value(),
            self.sb_north.value(),
        ]
        if bbox[0] >= bbox[2] or bbox[1] >= bbox[3]:
            raise ValueError(
                _t(
                    L,
                    "Area non valida: verificare West<East e South<North.",
                    "Invalid area: check West<East and South<North.",
                )
            )
        if self.de_from.date() >= self.de_to.date():
            raise ValueError(
                _t(
                    L,
                    "Periodo non valido: la data iniziale deve precedere la "
                    "finale.",
                    "Invalid date range: the start date must be before the "
                    "end date.",
                )
            )
        if not self.ed_user.text().strip() or not self.ed_pwd.text():
            raise ValueError(
                _t(
                    L,
                    "Inserire le credenziali CDSE nella scheda Credenziali.",
                    "Enter CDSE credentials in the Credentials tab.",
                )
            )
        if not self.ed_gpt.text().strip() or not self.ed_snaphu.text().strip():
            raise ValueError(
                _t(
                    L,
                    "Indicare i percorsi di gpt e snaphu nella scheda "
                    "Credenziali.",
                    "Set the gpt and snaphu paths in the Credentials tab.",
                )
            )
        if not self.ed_workdir.text().strip():
            raise ValueError(
                _t(L, "Indicare una cartella di lavoro.", "Set a work folder.")
            )
        if self.sb_first_burst.value() > self.sb_last_burst.value():
            raise ValueError(
                _t(
                    L,
                    "Il primo burst deve precedere l'ultimo.",
                    "First burst must not be after last burst.",
                )
            )

        params = {
            "bbox": bbox,
            "date_from": self.de_from.date().toString("yyyy-MM-dd"),
            "date_to": self.de_to.date().toString("yyyy-MM-dd"),
            "cdse_username": self.ed_user.text().strip(),
            "cdse_password": self.ed_pwd.text(),
            "gpt_exe": self.ed_gpt.text().strip(),
            "snaphu_exe": self.ed_snaphu.text().strip(),
            "work_dir": self.ed_workdir.text().strip(),
            "subswath": self.cb_subswath.currentText(),
            "polarisation": self.cb_pol.currentText(),
            "first_burst": self.sb_first_burst.value(),
            "last_burst": self.sb_last_burst.value(),
            "dem_name": self.cb_dem.currentText(),
            "rg_looks": self.sb_rg_looks.value(),
            "az_looks": self.sb_az_looks.value(),
            "pixel_spacing": self.sb_pixel_spacing.value(),
            "los_sign": -1.0 if self.cb_los_sign.currentIndex() == 1 else 1.0,
        }
        ro_asc = self.cb_ro_asc.currentData()
        if ro_asc is not None:
            params["relative_orbit_ascending"] = ro_asc
        ro_desc = self.cb_ro_desc.currentData()
        if ro_desc is not None:
            params["relative_orbit_descending"] = ro_desc
        return params

    # ------------------------------------------------------------------
    # Automatic CDSE availability search (debounced), mirrors STAC
    # Browser's "automatic search": no explicit search button needed once
    # the AOI, dates and CDSE credentials are filled in.
    # ------------------------------------------------------------------

    def _schedule_availability_search(self, *_args):
        self._availability_timer.start(900)

    def _run_availability_search(self):
        west, south = self.sb_west.value(), self.sb_south.value()
        east, north = self.sb_east.value(), self.sb_north.value()
        if west >= east or south >= north:
            return
        if self.de_from.date() >= self.de_to.date():
            return
        username = self.ed_user.text().strip()
        password = self.ed_pwd.text()
        if not username or not password:
            self._set_availability_text(
                "Inserisci le credenziali CDSE per vedere le scene "
                "disponibili.",
                "Enter your CDSE credentials to see available scenes.",
            )
            return
        if (
            self._availability_worker is not None
            and self._availability_worker.isRunning()
        ):
            # Field still changing while a search is in flight — the
            # debounce timer will fire again once things settle.
            return

        self._set_availability_text(
            "Ricerca disponibilità in corso...",
            "Checking availability...",
        )
        self._availability_worker = AvailabilityWorker(
            username,
            password,
            [west, south, east, north],
            self.de_from.date().toString("yyyy-MM-dd"),
            self.de_to.date().toString("yyyy-MM-dd"),
            token=self._availability_token,
            parent=self,
        )
        self._availability_worker.resultReady.connect(
            self._on_availability_result
        )
        self._availability_worker.errorOccurred.connect(
            self._on_availability_error
        )
        self._availability_worker.finished.connect(
            self._availability_worker.deleteLater
        )
        self._availability_worker.start()

    def _on_availability_result(self, groups_asc, groups_desc, token):
        self._availability_token = token
        n_asc = sum(len(v) for v in groups_asc.values())
        n_desc = sum(len(v) for v in groups_desc.values())
        self._populate_orbit_combo(self.cb_ro_asc, groups_asc)
        self._populate_orbit_combo(self.cb_ro_desc, groups_desc)
        self._set_availability_text(
            "Trovate %d scene ascendenti e %d discendenti." % (n_asc, n_desc),
            "Found %d ascending and %d descending scenes." % (n_asc, n_desc),
        )

    def _on_availability_error(self, message):
        self._set_availability_text(
            "Ricerca disponibilità non riuscita: %s" % message,
            "Availability search failed: %s" % message,
        )

    def _populate_orbit_combo(self, combo, groups):
        previous = combo.currentData()
        combo.blockSignals(True)
        combo.clear()
        combo.addItem(
            _t(
                self.lang,
                "Automatico (orbita più popolata)",
                "Automatic (largest stack)",
            ),
            None,
        )
        restore_index = 0
        for (_direction, relative_orbit), items in groups.items():
            first = min(p["sensing_start"] for p in items)[:10]
            last = max(p["sensing_start"] for p in items)[:10]
            combo.addItem(
                _t(
                    self.lang,
                    "Orbita %s — %d scene (%s → %s)",
                    "Orbit %s — %d scenes (%s → %s)",
                )
                % (relative_orbit, len(items), first, last),
                relative_orbit,
            )
            if relative_orbit == previous:
                restore_index = combo.count() - 1
        combo.setCurrentIndex(restore_index)
        combo.blockSignals(False)

    def _start_pipeline(self):
        try:
            params = self._collect_params()
        except ValueError as exc:
            QMessageBox.warning(self, "SARIAG", str(exc))
            return

        self.log_view.clear()
        self.progress_bar.setValue(0)
        self.btn_run.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.btn_load.setEnabled(False)
        self._results = None

        self.worker = PipelineWorker(params, self)
        self.worker.logMessage.connect(self._on_log)
        self.worker.progressChanged.connect(self._on_progress)
        self.worker.finishedOk.connect(self._on_finished_ok)
        self.worker.finishedError.connect(self._on_finished_error)
        self.worker.finished.connect(self.worker.deleteLater)
        self.worker.start()

    def _cancel_pipeline(self):
        if self.worker:
            self.worker.cancel()
            self._on_log(
                _t(
                    self.lang,
                    "Annullamento richiesto (effettivo tra una fase e "
                    "l'altra)...",
                    "Cancellation requested (takes effect between stages)...",
                )
            )

    def _on_log(self, msg):
        self.log_view.appendPlainText(msg)

    def _on_progress(self, stage, pct):
        # Percent is relative to the current pipeline stage, not to the
        # whole multi-hour run — an overall ETA is not attempted here.
        self.progress_bar.setValue(pct)
        self.progress_bar.setFormat("%s: %%p%%" % stage)

    def _on_finished_ok(self, result):
        self._results = result
        self.btn_run.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        self.btn_load.setEnabled(True)
        self._on_log(
            _t(self.lang, "Elaborazione completata.", "Processing complete.")
        )
        self._load_results()

    def _on_finished_error(self, message):
        self.btn_run.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        self._on_log(_t(self.lang, "ERRORE: ", "ERROR: ") + message)
        QMessageBox.critical(self, "SARIAG", message)

    # ------------------------------------------------------------------
    # Load results into QGIS
    # ------------------------------------------------------------------

    def _load_results(self):
        if not _HAS_QGIS or not self._results:
            return
        project = QgsProject.instance()

        vert_layer = QgsRasterLayer(
            self._results["vertical"],
            _t(
                self.lang,
                "SARIAG — Spostamento Verticale",
                "SARIAG — Vertical Displacement",
            ),
        )
        ew_layer = QgsRasterLayer(
            self._results["eastwest"],
            _t(
                self.lang,
                "SARIAG — Spostamento Est-Ovest",
                "SARIAG — East-West Displacement",
            ),
        )
        for layer in (vert_layer, ew_layer):
            if layer.isValid():
                self._apply_diverging_style(layer)
                project.addMapLayer(layer)
            else:
                self._on_log(
                    _t(self.lang, "Layer non valido: %s", "Invalid layer: %s")
                    % layer.source()
                )
        if _iface:
            _iface.mapCanvas().refresh()

    def _apply_diverging_style(self, layer):
        """Blue (subsidence/west) - white (0) - red (uplift/east) stretch
        symmetric around zero, bounded by the layer's actual min/max."""
        try:
            provider = layer.dataProvider()
            stats = provider.bandStatistics(1)
            bound = (
                max(abs(stats.minimumValue), abs(stats.maximumValue)) or 1.0
            )

            shader = QgsColorRampShader()
            ramp_type = getattr(
                getattr(QgsColorRampShader, "Type", QgsColorRampShader),
                "Interpolated",
                None,
            )
            if ramp_type is not None:
                shader.setColorRampType(ramp_type)
            shader.setColorRampItemList(
                [
                    QgsColorRampShader.ColorRampItem(
                        -bound, _qcolor(30, 60, 200), "-%.3f m" % bound
                    ),
                    QgsColorRampShader.ColorRampItem(
                        0.0, _qcolor(245, 245, 245), "0"
                    ),
                    QgsColorRampShader.ColorRampItem(
                        bound, _qcolor(200, 30, 30), "+%.3f m" % bound
                    ),
                ]
            )
            raster_shader = QgsRasterShader()
            raster_shader.setRasterShaderFunction(shader)
            renderer = QgsSingleBandPseudoColorRenderer(
                provider, 1, raster_shader
            )
            layer.setRenderer(renderer)
        except Exception as exc:  # noqa: BLE001 — styling is best-effort
            _log_warning("Impossibile applicare lo stile diverging: %s" % exc)


def _qcolor(r, g, b):
    from qgis.PyQt.QtGui import QColor

    return QColor(r, g, b)
