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
map_tool.py — Area-of-interest drawing tools for SARIAG.

An InSAR stack needs one AOI shared by every Sentinel-1 scene in the
stack, so a single rectangle (or a small buffered point) is enough —
there is no line tool here, unlike STAC Browser. Both tools finally
emit ``bboxDrawn(west, south, east, north)`` in EPSG:4326.
"""

from qgis.gui import QgsMapTool, QgsRubberBand
from qgis.core import (
    QgsPointXY,
    QgsRectangle,
    QgsWkbTypes,
    QgsCoordinateTransform,
    QgsCoordinateReferenceSystem,
    QgsProject,
)
from qgis.PyQt.QtCore import pyqtSignal, Qt
from qgis.PyQt.QtGui import QColor

from .qt_compat import ensure_qt_compat, QtCompat

ensure_qt_compat(Qt)

_CRS_4326 = QgsCoordinateReferenceSystem("EPSG:4326")

# Half-size (in degrees) of the bbox generated around a single clicked point
# (~2 km at the equator).
POINT_BUFFER_DEG = 0.02


def transform_point_to_4326(point):
    """Transform a single :class:`QgsPointXY` from the project CRS to 4326."""
    project_crs = QgsProject.instance().crs()
    if project_crs == _CRS_4326 or project_crs.authid() == "EPSG:4326":
        return point.x(), point.y()
    try:
        xform = QgsCoordinateTransform(
            project_crs,
            _CRS_4326,
            QgsProject.instance().transformContext(),
        )
    except TypeError:
        xform = QgsCoordinateTransform(
            project_crs, _CRS_4326, QgsProject.instance()
        )
    out = xform.transform(point)
    return out.x(), out.y()


class DrawBboxTool(QgsMapTool):
    """Click-and-drag rectangle tool emitting ``bboxDrawn`` in EPSG:4326."""

    bboxDrawn = pyqtSignal(float, float, float, float)  # W, S, E, N (4326)

    def __init__(self, canvas):
        super().__init__(canvas)
        self.canvas = canvas
        self._start_point = None
        self._drawing = False

        self._rb = QgsRubberBand(
            self.canvas, QgsWkbTypes.GeometryType.PolygonGeometry
        )
        self._rb.setColor(QColor(91, 155, 213, 50))
        self._rb.setWidth(2)
        try:
            self._rb.setFillColor(QColor(91, 155, 213, 30))
            self._rb.setStrokeColor(QColor(91, 155, 213, 200))
        except AttributeError:
            pass

    def canvasPressEvent(self, e):
        if e.button() == QtCompat.MouseButton.LeftButton:
            self._start_point = self.toMapCoordinates(e.pos())
            self._drawing = True
            self._rb.reset(QgsWkbTypes.GeometryType.PolygonGeometry)

    def canvasMoveEvent(self, e):
        if self._drawing and self._start_point is not None:
            current = self.toMapCoordinates(e.pos())
            self._update_rubber_band(self._start_point, current)

    def canvasReleaseEvent(self, e):
        if (
            e.button() == QtCompat.MouseButton.LeftButton
            and self._drawing
            and self._start_point is not None
        ):
            end_point = self.toMapCoordinates(e.pos())
            self._drawing = False
            self._rb.reset(QgsWkbTypes.GeometryType.PolygonGeometry)

            x1, y1 = self._start_point.x(), self._start_point.y()
            x2, y2 = end_point.x(), end_point.y()

            if abs(x2 - x1) < 1e-10 or abs(y2 - y1) < 1e-10:
                self._start_point = None
                return

            rect = QgsRectangle(
                min(x1, x2),
                min(y1, y2),
                max(x1, x2),
                max(y1, y2),
            )
            west, south, east, north = self._rect_to_4326(rect)
            self._start_point = None
            self.bboxDrawn.emit(west, south, east, north)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self.reset()

    def _update_rubber_band(self, p1, p2):
        x1, y1 = p1.x(), p1.y()
        x2, y2 = p2.x(), p2.y()
        self._rb.reset(QgsWkbTypes.GeometryType.PolygonGeometry)
        points = [
            QgsPointXY(x1, y1),
            QgsPointXY(x2, y1),
            QgsPointXY(x2, y2),
            QgsPointXY(x1, y2),
            QgsPointXY(x1, y1),
        ]
        for pt in points:
            self._rb.addPoint(pt, True)
        self._rb.show()

    def _rect_to_4326(self, rect):
        ll = transform_point_to_4326(
            QgsPointXY(rect.xMinimum(), rect.yMinimum())
        )
        ur = transform_point_to_4326(
            QgsPointXY(rect.xMaximum(), rect.yMaximum())
        )
        west = min(ll[0], ur[0])
        south = min(ll[1], ur[1])
        east = max(ll[0], ur[0])
        north = max(ll[1], ur[1])
        return west, south, east, north

    def reset(self):
        self._start_point = None
        self._drawing = False
        self._rb.reset(QgsWkbTypes.GeometryType.PolygonGeometry)

    def deactivate(self):
        self.reset()
        super().deactivate()


class DrawPointTool(QgsMapTool):
    """Single-click tool; emits a small bbox buffered around the point."""

    bboxDrawn = pyqtSignal(float, float, float, float)

    def __init__(self, canvas, buffer_deg=POINT_BUFFER_DEG):
        super().__init__(canvas)
        self.canvas = canvas
        self.buffer_deg = buffer_deg

        self._rb = QgsRubberBand(
            self.canvas, QgsWkbTypes.GeometryType.PointGeometry
        )
        self._rb.setColor(QColor(91, 155, 213, 200))
        self._rb.setWidth(3)
        try:
            self._rb.setIconSize(12)
        except AttributeError:
            pass

    def canvasReleaseEvent(self, e):
        if e.button() != QtCompat.MouseButton.LeftButton:
            return
        map_pt = self.toMapCoordinates(e.pos())
        self._rb.reset(QgsWkbTypes.GeometryType.PointGeometry)
        self._rb.addPoint(map_pt, True)
        self._rb.show()

        lon, lat = transform_point_to_4326(map_pt)
        b = self.buffer_deg
        self.bboxDrawn.emit(lon - b, lat - b, lon + b, lat + b)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self.reset()

    def reset(self):
        self._rb.reset(QgsWkbTypes.GeometryType.PointGeometry)

    def deactivate(self):
        self.reset()
        super().deactivate()
