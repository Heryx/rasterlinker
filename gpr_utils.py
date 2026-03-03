"""Utility checks for 3D/GPR import dependencies."""

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
    """Return OS-appropriate install instruction for laspy."""
    import platform
    if platform.system() == "Windows":
        return (
            "Installa da OSGeo4W Shell:\n"
            "  python -m pip install laspy[lazrs]\n\n"
            "Oppure apri 'OSGeo4W Shell' dal menu Start e digita:\n"
            "  pip install laspy[lazrs]"
        )
    return (
        f"Installa nel Python di QGIS:\n"
        f"  {sys.executable} -m pip install laspy[lazrs]"
    )


def check_laspy() -> dict:
    """Check if laspy is importable; attempt pip auto-install if missing."""
    try:
        import laspy  # type: ignore
        return {"ok": True, "version": str(getattr(laspy, "__version__", "sconosciuta"))}
    except ImportError:
        pass

    # Auto-install using QGIS's own Python interpreter
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pip", "install", "laspy[lazrs]", "--quiet"],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except Exception as install_err:
        return {
            "ok": False,
            "error": (
                f"laspy non installato e auto-install fallito:\n{install_err}\n\n"
                + _laspy_install_hint()
            ),
        }

    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        return {
            "ok": False,
            "error": (
                f"laspy: auto-install fallito (pip exit {proc.returncode}):\n{err}\n\n"
                + _laspy_install_hint()
            ),
        }

    # Re-try import after successful install
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

    if pdal.get("ok"):
        pdal_line = f"\u2705 PDAL {pdal.get('version')} trovato"
    else:
        pdal_line = f"\u274c PDAL {pdal.get('error')}"

    if laspy.get("ok"):
        suffix = " (appena installato - riavvia QGIS)" if laspy.get("just_installed") else ""
        laspy_line = f"\u2705 laspy {laspy.get('version')} trovato{suffix}"
    else:
        laspy_line = f"\u274c {laspy.get('error')}"

    return f"{pdal_line}\n{laspy_line}"
