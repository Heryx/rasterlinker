"""Utility checks for 3D/GPR import dependencies."""

import re
import subprocess


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


def check_laspy() -> dict:
    """Check if laspy is importable and return its version."""
    try:
        import laspy  # type: ignore
    except ImportError:
        return {"ok": False, "error": "laspy non installato"}
    return {"ok": True, "version": str(getattr(laspy, "__version__", "sconosciuta"))}


def check_environment() -> str:
    """Return a multiline status report for PDAL and laspy checks."""
    pdal = check_pdal()
    laspy = check_laspy()

    if pdal.get("ok"):
        pdal_line = f"✅ PDAL {pdal.get('version')} trovato"
    else:
        pdal_line = f"❌ PDAL {pdal.get('error')}"

    if laspy.get("ok"):
        laspy_line = f"✅ laspy {laspy.get('version')} trovato"
    else:
        laspy_line = f"❌ {laspy.get('error')}"

    return f"{pdal_line}\n{laspy_line}"
