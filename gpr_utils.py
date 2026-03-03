"""Utility checks for 3D/GPR import dependencies."""

import os
import re
import subprocess
import sys


def check_pdal() -> dict:
    """Check PDAL availability and version from CLI."""
    try:
        result = subprocess.run(
            ["pdal", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except FileNotFoundError:
        return {"ok": False, "error": "pdal non trovato nel PATH"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "pdal timeout"}
    except Exception as e:
        return {"ok": False, "error": str(e)}

    if result.returncode != 0:
        err = (result.stderr or result.stdout or "").strip()
        return {"ok": False, "error": err or f"pdal exit code {result.returncode}"}

    stdout = (result.stdout or "").strip()
    match = re.search(r"\d+(?:\.\d+)+", stdout)
    version = match.group(0) if match else (stdout.splitlines()[0].strip() if stdout else "sconosciuta")
    return {"ok": True, "version": version}


def _install_hint(package: str) -> str:
    """Return OS-appropriate manual install instructions."""
    import platform
    if platform.system() == "Windows":
        return (
            f"Apri 'OSGeo4W Shell' dal menu Start e digita:\n"
            f"  pip install {package}\n"
        )
    return f"Installa nel Python di QGIS:\n  {sys.executable} -m pip install {package}"


def _find_python_for_pip() -> str:
    """
    On OSGeo4W, sys.executable is qgis-bin.exe, not python.exe.
    Search exec_prefix and exe dir for a real Python interpreter.
    """
    import platform
    exe_dir = os.path.dirname(os.path.abspath(sys.executable))
    exec_prefix = os.path.abspath(sys.exec_prefix)

    if platform.system() == "Windows":
        candidates = [
            os.path.join(exec_prefix, "python.exe"),
            os.path.join(exec_prefix, "python3.exe"),
            os.path.join(exe_dir, "python3.exe"),
            os.path.join(exe_dir, "python.exe"),
        ]
    else:
        candidates = [
            os.path.join(exec_prefix, "bin", "python3"),
            os.path.join(exec_prefix, "bin", "python"),
            os.path.join(exe_dir, "python3"),
            os.path.join(exe_dir, "python"),
        ]

    for c in candidates:
        if os.path.isfile(c):
            return c
    return sys.executable


def _try_pip_install(package_spec: str) -> tuple:
    """
    Try to install a package. Returns (success: bool, error_msg: str).

    Strategy:
      1. pip programmatic API  – no subprocess, works inside QGIS Python.
      2. subprocess with real python.exe (not qgis-bin.exe on OSGeo4W).
    """
    # Method 1: pip programmatic (preferred in QGIS plugins)
    try:
        from pip._internal.cli.main import main as _pip_main
        ret = _pip_main(["install", package_spec, "--quiet", "--no-warn-script-location"])
        if ret == 0:
            return True, ""
        return False, f"pip exit code {ret}"
    except Exception:
        pass

    try:
        import pip as _pip_mod
        if hasattr(_pip_mod, "main"):
            ret = _pip_mod.main(["install", package_spec, "--quiet"])
            if ret == 0:
                return True, ""
            return False, f"pip exit code {ret}"
    except Exception:
        pass

    # Method 2: subprocess with real python.exe
    python_exe = _find_python_for_pip()
    try:
        proc = subprocess.run(
            [python_exe, "-m", "pip", "install", package_spec,
             "--quiet", "--no-warn-script-location"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if proc.returncode == 0:
            return True, ""
        err = (proc.stderr or proc.stdout or "").strip()
        return False, f"pip exit {proc.returncode}: {err}"
    except subprocess.TimeoutExpired:
        return False, "timeout durante l'installazione"
    except Exception as e:
        return False, str(e)


def _check_and_install(import_name: str, pip_spec: str) -> dict:
    """Generic check + auto-install pattern for optional dependencies."""
    import importlib

    # Fast path: already importable
    try:
        m = importlib.import_module(import_name)
        return {"ok": True, "version": str(getattr(m, "__version__", "sconosciuta"))}
    except ImportError:
        pass

    success, err_msg = _try_pip_install(pip_spec)
    if not success:
        return {
            "ok": False,
            "error": (
                f"{import_name} non installato e auto-install fallito:\n{err_msg}\n\n"
                + _install_hint(pip_spec)
            ),
        }

    try:
        m = importlib.import_module(import_name)
        return {
            "ok": True,
            "version": str(getattr(m, "__version__", "sconosciuta")),
            "just_installed": True,
        }
    except ImportError as e:
        return {
            "ok": False,
            "error": (
                f"{import_name} installato ma non importabile in questa sessione ({e}).\n"
                "Riavvia QGIS e riprova."
            ),
        }


def check_laspy() -> dict:
    """Check if laspy is importable; auto-install if missing."""
    return _check_and_install("laspy", "laspy[lazrs]")


def check_netcdf4() -> dict:
    """Check if netCDF4 is importable; auto-install if missing."""
    return _check_and_install("netCDF4", "netCDF4")


def check_environment() -> str:
    """Return a multiline status report for all GPR dependencies."""
    pdal = check_pdal()
    laspy = check_laspy()
    nc4 = check_netcdf4()

    def line(name, result):
        if result.get("ok"):
            suffix = " (appena installato — riavvia QGIS)" if result.get("just_installed") else ""
            return f"\u2705 {name} {result.get('version','')}{suffix}"
        return f"\u274c {name}: {result.get('error', '')}"

    return "\n".join([
        line("PDAL", pdal),
        line("laspy", laspy),
        line("netCDF4", nc4),
    ])
