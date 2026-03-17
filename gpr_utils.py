"""Utility checks for 3D/GPR import dependencies."""

import importlib
import os
import re
import subprocess
import sys


RUNTIME_PYTHON_DEPENDENCIES = (
    {"name": "matplotlib", "import_name": "matplotlib", "pip_spec": "matplotlib"},
    {"name": "scipy", "import_name": "scipy", "pip_spec": "scipy"},
    {"name": "laspy", "import_name": "laspy", "pip_spec": "laspy[lazrs]"},
    {"name": "netCDF4", "import_name": "netCDF4", "pip_spec": "netCDF4"},
    {"name": "pyvista", "import_name": "pyvista", "pip_spec": "pyvista"},
    {"name": "pyvistaqt", "import_name": "pyvistaqt", "pip_spec": "pyvistaqt"},
)


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
            "Apri 'OSGeo4W Shell' dal menu Start e digita:\n"
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

    for candidate in candidates:
        if os.path.isfile(candidate):
            return candidate
    return sys.executable


def _try_pip_install(package_spec: str) -> tuple:
    """
    Try to install a package. Returns (success: bool, error_msg: str).

    Strategy:
      1. pip programmatic API - no subprocess, works inside QGIS Python.
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
            [
                python_exe,
                "-m",
                "pip",
                "install",
                package_spec,
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
        return False, "timeout durante l'installazione"
    except Exception as e:
        return False, str(e)


def check_python_dependency(import_name: str, pip_spec: str, auto_install: bool = False) -> dict:
    """Check an importable Python dependency and optionally install it."""
    try:
        module = importlib.import_module(import_name)
        return {
            "ok": True,
            "version": str(getattr(module, "__version__", "sconosciuta")),
            "import_name": import_name,
            "pip_spec": pip_spec,
        }
    except ImportError as e:
        if not auto_install:
            return {
                "ok": False,
                "import_name": import_name,
                "pip_spec": pip_spec,
                "error": f"{import_name} non installato ({e})",
            }

    success, err_msg = _try_pip_install(pip_spec)
    if not success:
        return {
            "ok": False,
            "import_name": import_name,
            "pip_spec": pip_spec,
            "error": (
                f"{import_name} non installato e auto-install fallito:\n{err_msg}\n\n"
                + _install_hint(pip_spec)
            ),
        }

    try:
        module = importlib.import_module(import_name)
        return {
            "ok": True,
            "version": str(getattr(module, "__version__", "sconosciuta")),
            "just_installed": True,
            "import_name": import_name,
            "pip_spec": pip_spec,
        }
    except ImportError as e:
        return {
            "ok": False,
            "import_name": import_name,
            "pip_spec": pip_spec,
            "error": (
                f"{import_name} installato ma non importabile in questa sessione ({e}).\n"
                "Riavvia QGIS e riprova."
            ),
        }


def check_laspy(auto_install: bool = False) -> dict:
    """Check if laspy is importable."""
    return check_python_dependency("laspy", "laspy[lazrs]", auto_install=auto_install)


def check_netcdf4(auto_install: bool = False) -> dict:
    """Check if netCDF4 is importable."""
    return check_python_dependency("netCDF4", "netCDF4", auto_install=auto_install)


def check_runtime_dependencies(auto_install: bool = False) -> dict:
    """Check all known runtime dependencies and optionally install Python ones."""
    python_results = []
    for spec in RUNTIME_PYTHON_DEPENDENCIES:
        result = check_python_dependency(
            spec["import_name"],
            spec["pip_spec"],
            auto_install=auto_install,
        )
        result["name"] = spec["name"]
        python_results.append(result)

    missing_python = [res for res in python_results if not res.get("ok")]
    pdal_result = check_pdal()

    return {
        "python": python_results,
        "missing_python": missing_python,
        "pdal": pdal_result,
        "has_failures": bool(missing_python) or not pdal_result.get("ok"),
    }


def format_runtime_dependency_report(report: dict) -> str:
    """Build a multiline human-readable dependency status report."""
    lines = []

    for item in report.get("python", []):
        name = item.get("name") or item.get("import_name") or "unknown"
        if item.get("ok"):
            suffix = " (appena installato - riavvia QGIS)" if item.get("just_installed") else ""
            lines.append(f"OK {name} {item.get('version', '')}{suffix}".rstrip())
        else:
            lines.append(f"MISSING {name}: {item.get('error', '')}")

    pdal = report.get("pdal", {})
    if pdal.get("ok"):
        lines.append(f"OK PDAL {pdal.get('version', '')}".rstrip())
    else:
        lines.append(f"MISSING PDAL: {pdal.get('error', '')}")

    return "\n".join(lines)


def check_environment(auto_install: bool = False) -> str:
    """Backward-compatible environment report helper."""
    return format_runtime_dependency_report(
        check_runtime_dependencies(auto_install=auto_install)
    )
