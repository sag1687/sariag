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
core_cdse.py — Copernicus Data Space Ecosystem (CDSE) authentication,
Sentinel-1 SLC catalog search and product download.

InSAR needs the phase information carried only by Sentinel-1 SLC (Single
Look Complex) products. Those are not published as STAC/COG assets by the
usual open catalogs (Planetary Computer, Earth Search, ...) — they are only
distributed as full SAFE archives by ESA/Copernicus, so SARIAG talks to CDSE
directly through its OData catalog and token API rather than reusing the
STAC Browser plugin's search backend.

Reference: https://documentation.dataspace.copernicus.eu/APIs/OData.html
"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request

_TIMEOUT = 30
_DOWNLOAD_TIMEOUT = 120
_ALLOWED_SCHEMES = ("http", "https")

_AUTH_ENDPOINT = (
    "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/"
    "protocol/openid-connect/token"
)
_CATALOG_URL = "https://catalogue.dataspace.copernicus.eu/odata/v1/Products"
_ZIPPER_URL = "https://zipper.dataspace.copernicus.eu/odata/v1/Products"
_CLIENT_ID = "cdse-public"

_HEADERS = {
    "User-Agent": "QGIS-SARIAG/0.1 (+https://sinocloud.it)",
    "Accept": "application/json",
}

ORBIT_ASCENDING = "ASCENDING"
ORBIT_DESCENDING = "DESCENDING"


class CdseError(Exception):
    """Raised for authentication, search or download failures against CDSE."""


def _check_url_scheme(url):
    """Reject non-HTTP(S) URLs to avoid file:// or custom schemes."""
    scheme = urllib.parse.urlparse(url).scheme.lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise ValueError("URL scheme non consentito: %r" % scheme)


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


def authenticate(username, password):
    """
    Exchange CDSE username/password for an OAuth2 token pair.

    Returns a dict: access_token, refresh_token, expires_at (epoch seconds),
    refresh_expires_at (epoch seconds).
    """
    return _token_request(
        {
            "grant_type": "password",
            "username": username,
            "password": password,
            "client_id": _CLIENT_ID,
        }
    )


def refresh_token(refresh_token_value):
    """Exchange a still-valid refresh token for a new access token."""
    return _token_request(
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token_value,
            "client_id": _CLIENT_ID,
        }
    )


def _token_request(form):
    _check_url_scheme(_AUTH_ENDPOINT)
    data = urllib.parse.urlencode(form).encode("ascii")
    req = urllib.request.Request(
        _AUTH_ENDPOINT,
        data=data,
        headers={
            **_HEADERS,
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    now = time.time()
    try:
        with urllib.request.urlopen(
            req, timeout=_TIMEOUT
        ) as resp:  # nosec B310
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise CdseError(
            "Autenticazione CDSE fallita (HTTP %d): %s / "
            "CDSE authentication failed (HTTP %d): %s"
            % (exc.code, detail, exc.code, detail)
        ) from exc
    except urllib.error.URLError as exc:
        raise CdseError(
            "Impossibile contattare CDSE: %s / Cannot reach CDSE: %s"
            % (exc.reason, exc.reason)
        ) from exc

    return {
        "access_token": payload["access_token"],
        "refresh_token": payload.get("refresh_token", ""),
        "expires_at": now + float(payload.get("expires_in", 600)),
        "refresh_expires_at": now
        + float(payload.get("refresh_expires_in", 3600)),
    }


def ensure_valid_token(session, margin=60):
    """
    Given a token dict as returned by :func:`authenticate`, refresh it in
    place if it is about to expire. Returns the (possibly refreshed) dict.
    """
    if session.get("expires_at", 0) - margin > time.time():
        return session
    if session.get("refresh_expires_at", 0) - margin <= time.time():
        raise CdseError(
            "Sessione CDSE scaduta, effettuare nuovamente il login. / "
            "CDSE session expired, please log in again."
        )
    refreshed = refresh_token(session["refresh_token"])
    return refreshed


# ---------------------------------------------------------------------------
# Catalog search
# ---------------------------------------------------------------------------


def _bbox_to_polygon_wkt(bbox):
    """bbox = [west, south, east, north] -> CSC geography WKT polygon."""
    west, south, east, north = bbox
    coords = ("{w} {s},{e} {s},{e} {n},{w} {n},{w} {s}").format(
        w=west, s=south, e=east, n=north
    )
    return "SRID=4326;POLYGON(({0}))".format(coords)


def _odata_datetime(value, end_of_day=False):
    """
    Accept 'YYYY-MM-DD' or a full ISO string, return an OData literal.

    A bare date used as the upper bound of a range must mean "through the
    end of that day" (``end_of_day=True``), otherwise a filter built with
    ``ContentDate/Start lt 2023-12-31`` would silently exclude the whole
    of December 31st (its own midnight instant is the only moment that
    satisfies "less than midnight of the 31st").
    """
    if len(value) <= 10:
        return value + ("T23:59:59.999Z" if end_of_day else "T00:00:00.000Z")
    return value


def search_s1_slc(
    access_token,
    bbox,
    date_from,
    date_to,
    orbit_direction=None,
    relative_orbit=None,
    top=100,
):
    """
    Search Sentinel-1 IW SLC products intersecting ``bbox`` in the given
    date range.

    Parameters
    ----------
    access_token : str
    bbox : [west, south, east, north] in EPSG:4326
    date_from, date_to : str  ('YYYY-MM-DD' or full ISO datetime)
    orbit_direction : 'ASCENDING' | 'DESCENDING' | None
    relative_orbit : int | None
        Restrict to a single relative orbit number, so every scene in the
        result shares the same acquisition geometry (required to build a
        coherent InSAR stack).
    top : int   max results (CDSE caps a single page at 1000)

    Returns
    -------
    list[dict] sorted by acquisition date, each with:
        id, name, sensing_start, orbit_direction, relative_orbit, size_bytes
    """
    filters = [
        "Collection/Name eq 'SENTINEL-1'",
        "contains(Name,'IW_SLC')",
        "OData.CSC.Intersects(area=geography'%s')"
        % _bbox_to_polygon_wkt(bbox),
        "ContentDate/Start gt %s" % _odata_datetime(date_from),
        "ContentDate/Start lt %s" % _odata_datetime(date_to, end_of_day=True),
    ]
    if orbit_direction:
        filters.append(
            "Attributes/OData.CSC.StringAttribute/any(att:att/Name eq "
            "'orbitDirection' and att/OData.CSC.StringAttribute/Value eq "
            "'%s')" % orbit_direction
        )
    if relative_orbit is not None:
        filters.append(
            "Attributes/OData.CSC.IntegerAttribute/any(att:att/Name eq "
            "'relativeOrbitNumber' and att/OData.CSC.IntegerAttribute/Value "
            "eq %d)" % int(relative_orbit)
        )

    params = {
        "$filter": " and ".join(filters),
        "$orderby": "ContentDate/Start asc",
        "$top": str(min(int(top), 1000)),
        "$expand": "Attributes",
    }
    url = (
        _CATALOG_URL
        + "?"
        + urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
    )
    _check_url_scheme(url)
    req = urllib.request.Request(
        url, headers={**_HEADERS, "Authorization": "Bearer " + access_token}
    )
    try:
        with urllib.request.urlopen(
            req, timeout=_TIMEOUT
        ) as resp:  # nosec B310
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise CdseError(
            "Ricerca CDSE fallita (HTTP %d): %s / "
            "CDSE search failed (HTTP %d): %s"
            % (exc.code, detail, exc.code, detail)
        ) from exc

    results = []
    for item in payload.get("value", []):
        attrs = {
            a.get("Name"): a.get("Value") for a in item.get("Attributes", [])
        }
        results.append(
            {
                "id": item.get("Id"),
                "name": item.get("Name"),
                "sensing_start": item.get("ContentDate", {}).get("Start"),
                "orbit_direction": attrs.get("orbitDirection"),
                "relative_orbit": attrs.get("relativeOrbitNumber"),
                "size_bytes": item.get("ContentLength"),
            }
        )
    results.sort(key=lambda r: r["sensing_start"] or "")
    return results


def group_by_relative_orbit(products):
    """
    Group search results by (orbit_direction, relative_orbit).

    A usable InSAR stack must come from a single group — mixing relative
    orbits mixes incompatible acquisition geometries. Returns a dict keyed
    by (orbit_direction, relative_orbit) -> list[dict], largest group first.
    """
    groups = {}
    for p in products:
        key = (p["orbit_direction"], p["relative_orbit"])
        groups.setdefault(key, []).append(p)
    return dict(
        sorted(groups.items(), key=lambda kv: len(kv[1]), reverse=True)
    )


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------


def download_product(
    access_token, product_id, dest_path, progress_callback=None
):
    """
    Download a product's full SAFE archive (zip) to ``dest_path``.

    progress_callback(bytes_done, bytes_total) is called every 1 MB.
    bytes_total may be 0 if Content-Length is unavailable before the
    storage redirect resolves.
    """
    url = "%s(%s)/$value" % (_ZIPPER_URL, product_id)
    _check_url_scheme(url)
    req = urllib.request.Request(
        url, headers={**_HEADERS, "Authorization": "Bearer " + access_token}
    )
    chunk_size = 1024 * 1024  # 1 MB

    try:
        with urllib.request.urlopen(
            req, timeout=_DOWNLOAD_TIMEOUT
        ) as resp:  # nosec B310
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            with open(dest_path, "wb") as out_f:
                while True:
                    chunk = resp.read(chunk_size)
                    if not chunk:
                        break
                    out_f.write(chunk)
                    done += len(chunk)
                    if progress_callback:
                        progress_callback(done, total)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise CdseError(
            "Download CDSE fallito (HTTP %d): %s / "
            "CDSE download failed (HTTP %d): %s"
            % (exc.code, detail, exc.code, detail)
        ) from exc
    return dest_path
