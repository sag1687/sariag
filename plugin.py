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
plugin.py — Entry point for the SARIAG QGIS plugin.
"""

import os

from qgis.PyQt.QtWidgets import QAction
from qgis.PyQt.QtGui import QIcon
from qgis.core import Qgis

from .dialog import SariagDialog
from .map_tool import DrawBboxTool, DrawPointTool


class SariagPlugin:

    def __init__(self, iface):
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)
        self.action = None
        self.dialog = None
        self.map_tool = None

    # ------------------------------------------------------------------
    # initGui / unload
    # ------------------------------------------------------------------

    def initGui(self):
        icon_path = os.path.join(self.plugin_dir, "icon.svg")
        self.action = QAction(
            QIcon(icon_path),
            "SARIAG",
            self.iface.mainWindow(),
        )
        self.action.triggered.connect(self.run)
        self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToMenu("&GeoFusion Tools", self.action)

    def unload(self):
        if self.action:
            self.iface.removeToolBarIcon(self.action)
            self.iface.removePluginMenu("&GeoFusion Tools", self.action)
        if self.map_tool:
            self.iface.mapCanvas().unsetMapTool(self.map_tool)
            self.map_tool = None
        if self.dialog:
            if (
                self.dialog.worker is not None
                and self.dialog.worker.isRunning()
            ):
                self.dialog.worker.cancel()
                self.dialog.worker.wait(5000)
            self.dialog.close()
            self.dialog = None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _push(self, title, message, level_name="Info"):
        level = getattr(Qgis, level_name, None)
        if level is None:
            ml = getattr(Qgis, "MessageLevel", None)
            level = getattr(ml, level_name, 0) if ml else 0
        try:
            self.iface.messageBar().pushMessage(title, message, level)
        except TypeError:
            self.iface.messageBar().pushMessage(title, message, level=level)

    # ------------------------------------------------------------------
    # run
    # ------------------------------------------------------------------

    def run(self):
        """Open (or show) the SARIAG dialog."""
        if self.dialog is None:
            self.dialog = SariagDialog(self.iface.mainWindow())
            self.dialog.drawRequested.connect(self._activate_draw_tool)

        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()

    # ------------------------------------------------------------------
    # Map tool activation
    # ------------------------------------------------------------------

    _DRAW_HINTS = {
        "bbox": (
            "Clicca e trascina per disegnare l'area di interesse (AOI). "
            "Esc per annullare. / "
            "Click and drag to draw the area of interest (AOI). "
            "Esc to cancel."
        ),
        "point": (
            "Clicca un punto sulla mappa. Esc per annullare. / "
            "Click a point on the map. Esc to cancel."
        ),
    }

    def _activate_draw_tool(self, mode="bbox"):
        """Activate the requested drawing tool and hide the dialog."""
        if self.map_tool is not None:
            self.iface.mapCanvas().unsetMapTool(self.map_tool)
            self.map_tool = None

        if mode == "point":
            self.map_tool = DrawPointTool(self.iface.mapCanvas())
        else:
            self.map_tool = DrawBboxTool(self.iface.mapCanvas())
        self.map_tool.bboxDrawn.connect(self._on_bbox_drawn)

        # Hide dialog so the user can draw on the canvas
        if self.dialog:
            self.dialog.hide()

        self.iface.mapCanvas().setMapTool(self.map_tool)
        self._push("SARIAG", self._DRAW_HINTS.get(mode, ""), "Info")

    # ------------------------------------------------------------------
    # Bbox drawn callback
    # ------------------------------------------------------------------

    def _on_bbox_drawn(self, west, south, east, north):
        """Called when the user finishes drawing the AOI on the map."""
        self.iface.mapCanvas().unsetMapTool(self.map_tool)

        if self.dialog is None:
            self.dialog = SariagDialog(self.iface.mainWindow())
            self.dialog.drawRequested.connect(self._activate_draw_tool)

        self.dialog.set_bbox(west, south, east, north)
