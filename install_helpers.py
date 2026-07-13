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
install_helpers.py — Best-effort auto-detection of an existing ESA SNAP /
SNAPHU install, plus a self-contained SNAPHU build helper. Works on both
Linux and Windows, SARIAG's two supported platforms.

ESA SNAP and SNAPHU are both free. SNAP is a large (multi-GB) interactive
desktop installer, so SARIAG does not attempt to silently run it — instead
it can locate an already-installed ``gpt`` and points the user at the
official download page. SNAPHU, by contrast, is a small C program that
normally builds in seconds from its public source repository, so on Linux
SARIAG can fetch and build it itself on request; on Windows it instead
explains what toolchain to install first, since ``gcc``/``make`` are not
part of a stock Windows install the way they are on most Linux distros.
"""

import os
import platform
import shlex
import shutil
import subprocess  # nosec B404 - esegue solo git/make/gpt/snaphu noti

IS_WINDOWS = platform.system() == "Windows"

SNAP_DOWNLOAD_PAGE = "https://step.esa.int/main/download/snap-download/"
SNAPHU_REPO_URL = "https://github.com/gina-alaska/snaphu.git"
SNAPHU_INSTALL_DIR = os.path.expanduser(
    os.path.join("~", ".local", "share", "SARIAG", "snaphu")
    if not IS_WINDOWS
    else os.path.join("~", "AppData", "Local", "SARIAG", "snaphu")
)


class InstallError(Exception):
    pass


def _exe(name):
    return name + ".exe" if IS_WINDOWS else name


def _windows_program_dirs():
    dirs = []
    for var in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
        val = os.environ.get(var)
        if val and val not in dirs:
            dirs.append(val)
    return dirs


def _common_gpt_paths():
    paths = [
        os.path.join("~", "esa-snap", "bin", _exe("gpt")),
        os.path.join("~", "snap", "bin", _exe("gpt")),
    ]
    if IS_WINDOWS:
        for base in _windows_program_dirs():
            paths.append(os.path.join(base, "esa-snap", "bin", "gpt.exe"))
            paths.append(os.path.join(base, "snap", "bin", "gpt.exe"))
    else:
        paths += [
            "/opt/esa-snap/bin/gpt",
            "/opt/snap/bin/gpt",
            "/usr/local/esa-snap/bin/gpt",
            "/usr/local/snap/bin/gpt",
            os.path.join("~", "Applications", "esa-snap", "bin", "gpt"),
            "/Applications/esa-snap/bin/gpt",
        ]
    return tuple(paths)


def _common_snaphu_paths():
    paths = [
        os.path.join(SNAPHU_INSTALL_DIR, "snaphu-src", "bin", _exe("snaphu")),
    ]
    if IS_WINDOWS:
        for base in _windows_program_dirs():
            paths.append(os.path.join(base, "snaphu", "snaphu.exe"))
    else:
        paths += ["/usr/bin/snaphu", "/usr/local/bin/snaphu"]
    return tuple(paths)


def _first_existing_executable(candidates):
    for raw in candidates:
        path = os.path.expanduser(raw)
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return None


def find_gpt():
    """Return the path to ``gpt`` if found on PATH or in a common SNAP
    install location, else None. ``shutil.which`` already resolves the
    ``.exe`` suffix on Windows via ``PATHEXT``, so only the manually
    listed candidate paths need it explicitly."""
    return shutil.which("gpt") or _first_existing_executable(
        _common_gpt_paths()
    )


def find_snaphu():
    """Return the path to ``snaphu`` if found on PATH, in a common
    location, or in SARIAG's own build directory, else None."""
    return shutil.which("snaphu") or _first_existing_executable(
        _common_snaphu_paths()
    )


def _run(cmd, log_callback, cwd=None):
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
    proc.wait()
    if proc.returncode != 0:
        tail = "\n".join(lines[-30:])
        raise InstallError(
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


def install_snaphu(log_callback=None, dest_dir=SNAPHU_INSTALL_DIR):
    """
    Clone and build SNAPHU locally under ``dest_dir`` (no admin/root rights,
    fully contained — delete the folder to uninstall). Requires ``git`` and
    a C toolchain (``gcc``/``make``) already present on the system.

    On Windows this normally means installing MSYS2 (msys2.org) and, from
    its shell, ``pacman -S git make mingw-w64-x86_64-gcc`` first — Windows
    has no C toolchain out of the box the way most Linux distros do, so
    SARIAG explains this instead of guessing a silent workaround. WSL
    (running the Linux build inside it) is the other common option.

    Returns the path to the built ``snaphu`` executable.
    """
    missing = [t for t in ("git", "make", "gcc") if shutil.which(t) is None]
    if missing:
        if IS_WINDOWS:
            raise InstallError(
                "Strumenti di compilazione mancanti: %s. Installa MSYS2 "
                "(msys2.org) e, dalla sua shell, esegui: pacman -S git "
                "make mingw-w64-x86_64-gcc — poi riprova (oppure usa WSL). "
                "/ Missing build tools: %s. Install MSYS2 (msys2.org) and, "
                "from its shell, run: pacman -S git make "
                "mingw-w64-x86_64-gcc — then retry (or use WSL instead)."
                % (", ".join(missing), ", ".join(missing))
            )
        raise InstallError(
            "Strumenti di compilazione mancanti: %s. Su Debian/Ubuntu: "
            "sudo apt install build-essential git. / "
            "Missing build tools: %s. On Debian/Ubuntu: "
            "sudo apt install build-essential git."
            % (", ".join(missing), ", ".join(missing))
        )

    os.makedirs(dest_dir, exist_ok=True)
    src_dir = os.path.join(dest_dir, "snaphu-src")
    if not os.path.isdir(os.path.join(src_dir, ".git")):
        if log_callback:
            log_callback(
                "Scaricamento sorgenti SNAPHU da GitHub... / "
                "Downloading SNAPHU sources from GitHub..."
            )
        _run(
            ["git", "clone", "--depth", "1", SNAPHU_REPO_URL, src_dir],
            log_callback,
        )
    else:
        if log_callback:
            log_callback(
                "Sorgenti già presenti, aggiornamento... / "
                "Sources already present, updating..."
            )
        _run(["git", "-C", src_dir, "pull"], log_callback)

    if log_callback:
        log_callback("Compilazione (make)... / Building (make)...")
    _run(["make"], log_callback, cwd=os.path.join(src_dir, "src"))

    built = os.path.join(src_dir, "bin", _exe("snaphu"))
    if not os.path.isfile(built):
        raise InstallError(
            "Compilazione terminata ma %s non esiste. / "
            "Build finished but %s does not exist." % (built, built)
        )
    return built


def parse_command_line(line):
    """Split a recommended command line (as found in SNAP's snaphu.conf
    comment) into argv, respecting quoted paths — needed on both
    platforms, but especially Windows where paths routinely contain
    spaces (e.g. ``C:\\Program Files\\...``)."""
    return shlex.split(line, posix=not IS_WINDOWS)
