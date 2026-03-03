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
    if match:
        version = match.group(0)
    elif stdout:
        version = stdout.splitlines()[0].strip()
    else:
        version = "sconosciuta"
    return {"ok": True, "version": version}


def _laspy_install_hint() -> str:
    """Return OS-appropriate manual install instructions for laspy."""
    import platform
    if platform.system() == "Windows":
        return (
            "Apri 'OSGeo4W Shell' dal menu Start e digita:\n"
            "  pip install laspy[lazrs]\n\n"
            "Oppure dalla OSGeo4W Shell:\n"
            "  python -m pip install laspy[lazrs]"
        )
    return (
        "Installa nel Python di QGIS:\n"
        f"  {sys.executable} -m pip install laspy[lazrs]"
    )


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
    return sys.executable  # last resort (might still be qgis-bin.exe)


def _try_pip_install_laspy() -> tuple:
    """
    Try to install laspy[lazrs]. Returns (success: bool, error_msg: str).

    Strategy:
      1. pip programmatic API  – no subprocess, works inside QGIS's Python.
      2. subprocess with real python.exe (not qgis-bin.exe on OSGeo4W).
    """
    # --- Method 1: pip programmatic (preferred in QGIS plugins) ----------
    try:
        from pip._internal.cli.main import main as _pip_main  # pip >= 18
        ret = _pip_main(
            ["install", "laspy[lazrs]", "--quiet", "--no-warn-script-location"]
        )
        if ret == 0:
            return True, ""
        return False, f"pip exit code {ret}"
    except Exception:
        pass

    try:
        import pip as _pip_mod  # old pip (<= 9)
        if hasattr(_pip_mod, "main"):
            ret = _pip_mod.main(["install", "laspy[lazrs]", "--quiet"])
            if ret == 0:
                return True, ""
            return False, f"pip exit code {ret}"
    except Exception:
        pass

    # --- Method 2: subprocess with real python.exe -----------------------
    python_exe = _find_python_for_pip()
    try:
        proc = subprocess.run(
            [
                python_exe,
                "-m",
                "pip",
                "install",
                "laspy[lazrs]",
                "--quiet",
                "--no-warn-script-location",
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if proc.returncode == 0:
            return True, ""
        err = (proc.stderr or proc.stdout or "").strip()
        return False, f"pip exit {proc.returncode}: {err}"
    except subprocess.TimeoutExpired:
        return False, "timeout durante l'installazione di laspy"
    except Exception as e:
        return False, str(e)


def check_laspy() -> dict:
    """
    Check if laspy is importable.
    If not, attempt auto-install via pip API (no subprocess race with QGIS).
    """
    # Fast path: already installed
    try:
        import laspy  # type: ignore
        return {
            "ok": True,
            "version": str(getattr(laspy, "__version__", "sconosciuta")),
        }
    except ImportError:
        pass

    # Auto-install attempt
    success, err_msg = _try_pip_install_laspy()
    if not success:
        return {
            "ok": False,
            "error": (
                f"laspy non installato e auto-install fallito:\n{err_msg}\n\n"
                + _laspy_install_hint()
            ),
        }

    # Re-import after install (importlib bypasses the cached ImportError)
    try:
        import importlib
        _laspy = importlib.import_module("laspy")
        return {
            "ok": True,
            "version": str(getattr(_laspy, "__version__", "sconosciuta")),
            "just_installed": True,
        }
    except ImportError as e:
        return {
            "ok": False,
            "error": (
                f"laspy installato ma non importabile in questa sessione ({e}).\n"
                "Riavvia QGIS e riprova."
            ),
        }


def check_environment() -> str:
    """Return a multiline status report for PDAL and laspy checks."""
    pdal = check_pdal()
    laspy = check_laspy()

    pdal_line = (
        f"\u2705 PDAL {pdal.get('version')} trovato"
        if pdal.get("ok")
        else f"\u274c PDAL {pdal.get('error')}"
    )

    if laspy.get("ok"):
        suffix = " (appena installato \u2014 riavvia QGIS)" if laspy.get("just_installed") else ""
        laspy_line = f"\u2705 laspy {laspy.get('version')} trovato{suffix}"
    else:
        laspy_line = f"\u274c {laspy.get('error')}"

    return f"{pdal_line}\n{laspy_line}"
