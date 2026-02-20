# -*- coding: utf-8 -*-
"""
Project-folder and catalog helpers for 2D/3D geophysics workflows.
"""

import json
import os
import re
import shutil
import zipfile
from datetime import datetime, timezone


PROJECT_FOLDERS = (
    "volumes_3d",
    "timeslices_2d",
    "radargrams",
    "vector_layers",
    "exports",
    "metadata",
)
CATALOG_VERSION = 4
# Legacy alias kept for backward compatibility with older code/sidecars.
CATALOG_SCHEMA_VERSION = CATALOG_VERSION
SURFER_GRID_EXTENSIONS = (".grd", ".gsag", ".gsbg")


def _default_catalog(project_root):
    now = utc_now_iso()
    return {
        "catalog_version": CATALOG_VERSION,
        "schema_version": CATALOG_VERSION,
        "project_root": project_root,
        "created_at": now,
        "updated_at": now,
        "models_3d": [],
        "radargrams": [],
        "timeslices": [],
        "vector_layers": [],
        "links": [],
        "raster_groups": [],
    }


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ensure_project_structure(project_root):
    """
    Create standard folders and return their absolute paths.
    """
    os.makedirs(project_root, exist_ok=True)
    paths = {}
    for folder in PROJECT_FOLDERS:
        abs_path = os.path.join(project_root, folder)
        os.makedirs(abs_path, exist_ok=True)
        paths[folder] = abs_path
    return paths


def catalog_path(project_root):
    return os.path.join(project_root, "metadata", "project_catalog.json")


def _safe_int(value, default=0):
    try:
        return int(value)
    except Exception:
        return int(default)


def _detect_catalog_version(data):
    if not isinstance(data, dict):
        return 0
    if "catalog_version" in data:
        return max(0, _safe_int(data.get("catalog_version"), 0))
    if "schema_version" in data:
        return max(0, _safe_int(data.get("schema_version"), 0))
    # Legacy fallback: existing dict without explicit version.
    return 1 if data else 0


def _write_catalog_file(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=True)
    return path


def _migrate_catalog_v0_to_v1(project_root, data):
    data = dict(data or {})
    now = utc_now_iso()
    data.setdefault("project_root", project_root)
    data.setdefault("created_at", now)
    data.setdefault("updated_at", now)
    data.setdefault("models_3d", [])
    data.setdefault("radargrams", [])
    data.setdefault("timeslices", [])
    data.setdefault("links", [])
    data.setdefault("raster_groups", [])
    data["catalog_version"] = 1
    data["schema_version"] = 1
    return data


def _migrate_catalog_v1_to_v2(project_root, data):
    data = dict(data or {})
    data.setdefault("raster_groups", [])
    if not data["raster_groups"]:
        data["raster_groups"] = [
            {
                "id": "grp_imported",
                "name": "Imported",
                "radargram_ids": [r.get("id") for r in data.get("radargrams", []) if isinstance(r, dict) and r.get("id")],
                "timeslice_ids": [t.get("id") for t in data.get("timeslices", []) if isinstance(t, dict) and t.get("id")],
                "created_at": utc_now_iso(),
            }
        ]
    data["catalog_version"] = 2
    data["schema_version"] = 2
    return data


def _migrate_catalog_v2_to_v3(project_root, data):
    data = dict(data or {})
    # Formalize catalog_version while keeping schema_version compatibility key.
    data["catalog_version"] = 3
    data["schema_version"] = 3
    return data


def _migrate_catalog_v3_to_v4(project_root, data):
    data = dict(data or {})
    data.setdefault("vector_layers", [])
    data["catalog_version"] = 4
    data["schema_version"] = 4
    return data


_CATALOG_MIGRATIONS = {
    0: _migrate_catalog_v0_to_v1,
    1: _migrate_catalog_v1_to_v2,
    2: _migrate_catalog_v2_to_v3,
    3: _migrate_catalog_v3_to_v4,
}


def _apply_catalog_migrations(project_root, data):
    migrated = dict(data or {})
    source_version = _detect_catalog_version(migrated)
    applied = []
    current = source_version

    # Keep forward compatibility: do not downgrade unknown future versions.
    if current > CATALOG_VERSION:
        return migrated, source_version, current, applied

    while current < CATALOG_VERSION:
        prev = current
        fn = _CATALOG_MIGRATIONS.get(current)
        if fn is None:
            break
        migrated = fn(project_root, migrated)
        next_version = _detect_catalog_version(migrated)
        if next_version <= prev:
            # Safety net for malformed migration functions.
            next_version = prev + 1
            migrated["catalog_version"] = next_version
            migrated["schema_version"] = next_version
        current = next_version
        applied.append(current)

    if current < CATALOG_VERSION:
        migrated["catalog_version"] = CATALOG_VERSION
        migrated["schema_version"] = CATALOG_VERSION
        current = CATALOG_VERSION
        applied.append(current)

    return migrated, source_version, current, applied


def load_catalog(project_root):
    path = catalog_path(project_root)
    if not os.path.exists(path):
        return _default_catalog(project_root)
    with open(path, "r", encoding="utf-8") as f:
        loaded = json.load(f)

    normalized, info = ensure_catalog_schema(project_root, loaded, return_info=True)
    if info.get("changed"):
        _write_catalog_file(path, normalized)
    return normalized


def save_catalog(project_root, data):
    data = ensure_catalog_schema(project_root, data)
    data["updated_at"] = utc_now_iso()
    path = catalog_path(project_root)
    return _write_catalog_file(path, data)


def register_model_3d(project_root, model_record):
    data = load_catalog(project_root)
    data["models_3d"].append(normalize_model_record(model_record))
    return save_catalog(project_root, data)


def register_radargram(project_root, radargram_record):
    data = load_catalog(project_root)
    data["radargrams"].append(normalize_radargram_record(radargram_record))
    return save_catalog(project_root, data)


def register_timeslice(project_root, timeslice_record):
    data = load_catalog(project_root)
    data["timeslices"].append(normalize_timeslice_record(timeslice_record))
    return save_catalog(project_root, data)


def register_timeslices_batch(project_root, timeslice_records):
    records = [r for r in (timeslice_records or []) if isinstance(r, dict)]
    if not records:
        return
    data = load_catalog(project_root)
    data.setdefault("timeslices", [])
    for rec in records:
        data["timeslices"].append(normalize_timeslice_record(rec))
    return save_catalog(project_root, data)


def register_vector_layer(project_root, vector_record):
    """
    Add or update a vector layer record in catalog.
    Upsert policy:
      1) same id -> update
      2) same (project_path, layer_name) -> update
      3) otherwise append
    """
    rec = normalize_vector_layer_record(vector_record)
    data = load_catalog(project_root)
    data.setdefault("vector_layers", [])
    layers = data.get("vector_layers", [])

    match = None
    rid = rec.get("id")
    if rid:
        match = next((r for r in layers if r.get("id") == rid), None)
    if match is None:
        pth = os.path.abspath((rec.get("project_path") or "").strip()) if rec.get("project_path") else ""
        lname = (rec.get("layer_name") or "").strip().lower()
        if pth and lname:
            match = next(
                (
                    r for r in layers
                    if os.path.abspath((r.get("project_path") or "").strip()) == pth
                    and (r.get("layer_name") or "").strip().lower() == lname
                ),
                None,
            )

    rec["updated_at"] = utc_now_iso()
    if match is None:
        layers.append(rec)
    else:
        created_at = match.get("created_at") or rec.get("created_at") or utc_now_iso()
        match.update(rec)
        match["created_at"] = created_at
        match["updated_at"] = rec.get("updated_at")

    save_catalog(project_root, data)
    return rec


def register_link(project_root, link_record):
    data = load_catalog(project_root)
    data["links"].append(normalize_link_record(link_record))
    return save_catalog(project_root, data)


def ensure_catalog_schema(project_root, data, return_info=False):
    raw_input = dict(data or {}) if isinstance(data, dict) else {}
    migrated, raw_version, final_version, applied_migrations = _apply_catalog_migrations(project_root, raw_input)
    data = dict(migrated or {})
    if not isinstance(data, dict):
        data = {}

    default = _default_catalog(project_root)
    for key, value in default.items():
        if key not in data:
            data[key] = value if not isinstance(value, list) else []

    detected_version = _detect_catalog_version(data)
    if detected_version < CATALOG_VERSION:
        detected_version = CATALOG_VERSION
    data["catalog_version"] = detected_version
    data["schema_version"] = detected_version
    data["project_root"] = project_root
    data["models_3d"] = [normalize_model_record(v) for v in data.get("models_3d", []) if isinstance(v, dict)]
    data["radargrams"] = [normalize_radargram_record(v) for v in data.get("radargrams", []) if isinstance(v, dict)]
    data["timeslices"] = [normalize_timeslice_record(v) for v in data.get("timeslices", []) if isinstance(v, dict)]
    data["vector_layers"] = [normalize_vector_layer_record(v) for v in data.get("vector_layers", []) if isinstance(v, dict)]
    data["links"] = [normalize_link_record(v) for v in data.get("links", []) if isinstance(v, dict)]
    data["raster_groups"] = [normalize_raster_group_record(v) for v in data.get("raster_groups", []) if isinstance(v, dict)]
    if not data["raster_groups"]:
        data["raster_groups"].append(
            normalize_raster_group_record(
                {
                    "id": "grp_imported",
                    "name": "Imported",
                    "radargram_ids": [r.get("id") for r in data.get("radargrams", []) if r.get("id")],
                    "timeslice_ids": [],
                }
            )
        )
    else:
        known_radargrams = {r.get("id") for r in data.get("radargrams", []) if r.get("id")}
        known_timeslices = {t.get("id") for t in data.get("timeslices", []) if t.get("id")}
        for group in data["raster_groups"]:
            group["radargram_ids"] = [rid for rid in group.get("radargram_ids", []) if rid in known_radargrams]
            group["timeslice_ids"] = [tid for tid in group.get("timeslice_ids", []) if tid in known_timeslices]

    info = {
        "raw_version": raw_version,
        "final_version": data.get("catalog_version"),
        "applied_migrations": applied_migrations,
        "changed": bool(
            applied_migrations
            or raw_input != data
            or final_version != data.get("catalog_version")
        ),
    }
    if return_info:
        return data, info
    return data


def normalize_model_record(rec):
    rec = dict(rec or {})
    rec.setdefault("id", f"model_{utc_now_iso()}")
    rec.setdefault("normalized_name", rec.get("file_name", ""))
    rec.setdefault("source_path", "")
    rec.setdefault("project_path", "")
    rec.setdefault("imported_at", utc_now_iso())
    rec.setdefault("crs", None)
    return rec


def normalize_radargram_record(rec):
    rec = dict(rec or {})
    rec.setdefault("id", f"radargram_{utc_now_iso()}")
    rec.setdefault("normalized_name", rec.get("file_name", ""))
    rec.setdefault("source_path", "")
    rec.setdefault("project_path", "")
    rec.setdefault("imported_at", utc_now_iso())
    rec.setdefault("import_mode", "catalog_only")
    rec.setdefault("georef_level", "none")
    rec.setdefault("line_id", None)
    rec.setdefault("timeslice_id", None)
    rec.setdefault("trace_count", None)
    rec.setdefault("trace_spacing", None)
    rec.setdefault("sample_interval", None)
    rec.setdefault("time_zero", None)
    rec.setdefault("velocity", None)
    rec.setdefault("crs", rec.get("crs", None))
    rec.setdefault("notes", "")
    return rec


def normalize_timeslice_record(rec):
    rec = dict(rec or {})
    rec.setdefault("id", f"timeslice_{utc_now_iso()}")
    rec.setdefault("name", rec.get("normalized_name", ""))
    rec.setdefault("project_path", rec.get("path", ""))
    rec.setdefault("depth_from", None)
    rec.setdefault("depth_to", None)
    rec.setdefault("unit", "m")
    rec.setdefault("crs", None)
    rec.setdefault("z_source", "none")
    rec.setdefault("z_grid_source_path", None)
    rec.setdefault("z_grid_project_path", None)
    rec.setdefault("z_grid_band", 1)
    rec.setdefault("z_grid_linked_at", None)
    rec.setdefault("imported_at", utc_now_iso())
    return rec


def normalize_vector_layer_record(rec):
    rec = dict(rec or {})
    rec.setdefault("id", f"vector_{utc_now_iso()}")
    rec.setdefault("name", rec.get("layer_name") or "")
    rec.setdefault("layer_name", rec.get("name") or "")
    rec.setdefault("project_path", rec.get("path", ""))
    rec.setdefault("source_path", rec.get("source_path", ""))
    rec.setdefault("geometry_type", "unknown")
    rec.setdefault("is_3d", False)
    rec.setdefault("crs", None)
    rec.setdefault("storage_mode", "gpkg" if str(rec.get("project_path") or "").lower().endswith(".gpkg") else "memory")
    rec.setdefault("source_kind", "generic")
    rec.setdefault("created_at", utc_now_iso())
    rec.setdefault("updated_at", utc_now_iso())
    rec.setdefault("notes", "")
    return rec


def normalize_link_record(rec):
    rec = dict(rec or {})
    rec.setdefault("id", f"link_{utc_now_iso()}")
    rec.setdefault("radargram_id", None)
    rec.setdefault("timeslice_id", None)
    rec.setdefault("line_id", None)
    rec.setdefault("trace_from", None)
    rec.setdefault("trace_to", None)
    rec.setdefault("confidence", 1.0)
    rec.setdefault("notes", "")
    rec.setdefault("created_at", utc_now_iso())
    return rec


def normalize_raster_group_record(rec):
    rec = dict(rec or {})
    rec.setdefault("id", f"group_{utc_now_iso()}")
    rec.setdefault("name", "Group")
    rec.setdefault("radargram_ids", [])
    rec.setdefault("timeslice_ids", [])
    rec.setdefault("style_qml_path", "")
    rec["radargram_ids"] = [v for v in rec.get("radargram_ids", []) if v]
    rec["timeslice_ids"] = [v for v in rec.get("timeslice_ids", []) if v]
    rec.setdefault("created_at", utc_now_iso())
    return rec


def create_raster_group(project_root, group_name):
    data = load_catalog(project_root)
    existing = [g for g in data.get("raster_groups", []) if (g.get("name") or "").strip().lower() == group_name.strip().lower()]
    if existing:
        return existing[0], False

    group = normalize_raster_group_record(
        {
            "id": f"group_{utc_now_iso()}",
            "name": group_name.strip(),
            "radargram_ids": [],
            "timeslice_ids": [],
            "created_at": utc_now_iso(),
        }
    )
    data["raster_groups"].append(group)
    save_catalog(project_root, data)
    return group, True


def assign_radargrams_to_group(project_root, group_id, radargram_ids):
    data = load_catalog(project_root)
    radargram_ids = [rid for rid in radargram_ids if rid]

    group = next((g for g in data.get("raster_groups", []) if g.get("id") == group_id), None)
    if group is None:
        raise ValueError(f"Raster group not found: {group_id}")

    merged = list(dict.fromkeys(group.get("radargram_ids", []) + radargram_ids))
    group["radargram_ids"] = merged
    save_catalog(project_root, data)
    return group


def add_radargram_to_default_group(project_root, radargram_id):
    if not radargram_id:
        return
    data = load_catalog(project_root)
    default_group = next((g for g in data.get("raster_groups", []) if g.get("id") == "grp_imported"), None)
    if default_group is None:
        default_group = normalize_raster_group_record(
            {
                "id": "grp_imported",
                "name": "Imported",
                "radargram_ids": [],
                "timeslice_ids": [],
                "created_at": utc_now_iso(),
            }
        )
        data.setdefault("raster_groups", []).append(default_group)
    if radargram_id not in default_group["radargram_ids"]:
        default_group["radargram_ids"].append(radargram_id)
        save_catalog(project_root, data)


def assign_timeslices_to_group(project_root, group_id, timeslice_ids):
    data = load_catalog(project_root)
    timeslice_ids = [tid for tid in timeslice_ids if tid]
    group = next((g for g in data.get("raster_groups", []) if g.get("id") == group_id), None)
    if group is None:
        raise ValueError(f"Raster group not found: {group_id}")
    merged = list(dict.fromkeys(group.get("timeslice_ids", []) + timeslice_ids))
    group["timeslice_ids"] = merged
    save_catalog(project_root, data)
    return group


def remove_timeslices_from_group(project_root, group_id, timeslice_ids):
    data = load_catalog(project_root)
    timeslice_ids = set(tid for tid in timeslice_ids if tid)
    group = next((g for g in data.get("raster_groups", []) if g.get("id") == group_id), None)
    if group is None:
        raise ValueError(f"Raster group not found: {group_id}")
    group["timeslice_ids"] = [tid for tid in group.get("timeslice_ids", []) if tid not in timeslice_ids]
    save_catalog(project_root, data)
    return group


def add_timeslice_to_default_group(project_root, timeslice_id):
    if not timeslice_id:
        return
    data = load_catalog(project_root)
    default_group = next((g for g in data.get("raster_groups", []) if g.get("id") == "grp_imported"), None)
    if default_group is None:
        default_group = normalize_raster_group_record(
            {
                "id": "grp_imported",
                "name": "Imported",
                "radargram_ids": [],
                "timeslice_ids": [],
                "created_at": utc_now_iso(),
            }
        )
        data.setdefault("raster_groups", []).append(default_group)
    if timeslice_id not in default_group["timeslice_ids"]:
        default_group["timeslice_ids"].append(timeslice_id)
        save_catalog(project_root, data)


def update_raster_group(project_root, group_id, updates):
    data = load_catalog(project_root)
    group = next((g for g in data.get("raster_groups", []) if g.get("id") == group_id), None)
    if group is None:
        raise ValueError(f"Raster group not found: {group_id}")
    group.update(dict(updates or {}))
    save_catalog(project_root, data)
    return group


def validate_catalog(project_root, catalog_data=None):
    data = ensure_catalog_schema(project_root, catalog_data or load_catalog(project_root))
    errors = []
    warnings = []

    radargram_ids = set()
    timeslice_ids = set()
    vector_ids = set()
    vector_keys = set()

    for model in data.get("models_3d", []):
        if not model.get("project_path"):
            errors.append("Model missing project_path.")
        elif not os.path.exists(model.get("project_path")):
            errors.append(f"Missing model file: {model.get('project_path')}")
        if not model.get("crs"):
            warnings.append(f"Model without CRS: {model.get('normalized_name') or model.get('id')}")

    for rg in data.get("radargrams", []):
        rid = rg.get("id")
        if rid:
            if rid in radargram_ids:
                errors.append(f"Duplicate radargram id: {rid}")
            radargram_ids.add(rid)
        else:
            errors.append("Radargram missing id.")
        if not rg.get("project_path"):
            errors.append(f"Radargram without project_path: {rid}")
        elif not os.path.exists(rg.get("project_path")):
            errors.append(f"Missing radargram file: {rg.get('project_path')}")
        if rg.get("import_mode") == "mapped" and rg.get("georef_level") == "none":
            warnings.append(f"Radargram marked mapped but georef_level is none: {rid}")

    for ts in data.get("timeslices", []):
        tid = ts.get("id")
        if tid:
            if tid in timeslice_ids:
                errors.append(f"Duplicate timeslice id: {tid}")
            timeslice_ids.add(tid)
        else:
            errors.append("Timeslice missing id.")
        pth = ts.get("project_path")
        if pth and not os.path.exists(pth):
            warnings.append(f"Missing timeslice file: {pth}")
        z_grid_path = ts.get("z_grid_project_path")
        if z_grid_path and not os.path.exists(z_grid_path):
            warnings.append(f"Missing linked z-grid file: {z_grid_path}")

    for vl in data.get("vector_layers", []):
        vid = vl.get("id")
        if vid:
            if vid in vector_ids:
                errors.append(f"Duplicate vector layer id: {vid}")
            vector_ids.add(vid)
        else:
            errors.append("Vector layer missing id.")

        pth = (vl.get("project_path") or "").strip()
        lname = (vl.get("layer_name") or vl.get("name") or "").strip()
        key = (os.path.abspath(pth).lower(), lname.lower()) if pth and lname else None
        if key is not None:
            if key in vector_keys:
                warnings.append(f"Duplicate vector catalog entry for layer '{lname}' at path: {pth}")
            vector_keys.add(key)

        if not pth:
            warnings.append(f"Vector layer without project_path: {vid or lname or 'unknown'}")
            continue
        if not os.path.exists(pth):
            warnings.append(f"Missing vector layer file: {pth}")

    for ln in data.get("links", []):
        link_id = ln.get("id")
        rid = ln.get("radargram_id")
        tid = ln.get("timeslice_id")
        lid = ln.get("line_id")

        if not rid:
            errors.append(f"Link without radargram_id: {link_id}")
        elif rid not in radargram_ids:
            warnings.append(f"Link references unknown radargram_id: {rid}")

        if not tid and not lid:
            warnings.append(f"Link without timeslice_id and line_id: {link_id}")
        if tid and tid not in timeslice_ids:
            warnings.append(f"Link references unknown timeslice_id: {tid}")

    return {"errors": errors, "warnings": warnings, "catalog": data}


def save_radargram_sidecar(project_root, radargram_record):
    """
    Save or update a radargram sidecar metadata JSON used for line/timeslice linking.
    """
    metadata_dir = os.path.join(project_root, "metadata", "radargram_sidecars")
    os.makedirs(metadata_dir, exist_ok=True)

    rid = radargram_record.get("id", f"radargram_{utc_now_iso()}").replace(":", "_")
    sidecar_path = os.path.join(metadata_dir, f"{rid}.json")

    payload = {
        "catalog_version": CATALOG_VERSION,
        "schema_version": CATALOG_VERSION,
        "id": radargram_record.get("id"),
        "normalized_name": radargram_record.get("normalized_name"),
        "project_path": radargram_record.get("project_path"),
        "source_path": radargram_record.get("source_path"),
        "imported_at": radargram_record.get("imported_at"),
        "import_mode": radargram_record.get("import_mode", "catalog_only"),
        "georef_level": radargram_record.get("georef_level", "none"),
        "line_id": radargram_record.get("line_id"),
        "timeslice_id": radargram_record.get("timeslice_id"),
        "start_xy": radargram_record.get("start_xy"),
        "end_xy": radargram_record.get("end_xy"),
        "trace_count": radargram_record.get("trace_count"),
        "trace_spacing": radargram_record.get("trace_spacing"),
        "sample_interval": radargram_record.get("sample_interval"),
        "time_zero": radargram_record.get("time_zero"),
        "velocity": radargram_record.get("velocity"),
        "notes": radargram_record.get("notes", ""),
        "auto_detected": {
            "width": radargram_record.get("width"),
            "height": radargram_record.get("height"),
            "rows": radargram_record.get("rows"),
            "cols": radargram_record.get("cols"),
            "shape": radargram_record.get("shape"),
            "crs": radargram_record.get("crs"),
        },
    }

    with open(sidecar_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=True)
    return sidecar_path


def _slugify_filename(name):
    base, ext = os.path.splitext(name)
    base = base.strip().replace(" ", "_")
    base = re.sub(r"[^A-Za-z0-9_\-\.]+", "_", base)
    base = re.sub(r"_+", "_", base).strip("_")
    if not base:
        base = "file"
    return f"{base}{ext.lower()}"


def sanitize_filename(name):
    return _slugify_filename(name)


def _unique_destination_path(directory, filename):
    os.makedirs(directory, exist_ok=True)
    candidate = os.path.join(directory, filename)
    if not os.path.exists(candidate):
        return candidate

    stem, ext = os.path.splitext(filename)
    i = 1
    while True:
        candidate = os.path.join(directory, f"{stem}_{i:03d}{ext}")
        if not os.path.exists(candidate):
            return candidate
        i += 1


def normalize_copy_into_project(project_root, subfolder, source_path):
    """
    Copy source file to a canonical project subfolder with sanitized unique name.
    Returns (project_path, normalized_name).
    """
    if not os.path.isfile(source_path):
        raise FileNotFoundError(source_path)
    source_name = os.path.basename(source_path)
    normalized_name = _slugify_filename(source_name)
    destination_dir = os.path.join(project_root, subfolder)
    destination_path = _unique_destination_path(destination_dir, normalized_name)
    shutil.copy2(source_path, destination_path)
    return destination_path, os.path.basename(destination_path)


def find_matching_surfer_grid(reference_raster_path, extra_search_dirs=None):
    """
    Find a Surfer grid file with the same basename (stem) as the reference raster.
    """
    if not reference_raster_path:
        return None
    ref_name = os.path.basename(reference_raster_path)
    ref_stem = os.path.splitext(ref_name)[0].strip().lower()
    if not ref_stem:
        return None

    search_dirs = [os.path.dirname(reference_raster_path)]
    for d in (extra_search_dirs or []):
        if d and d not in search_dirs:
            search_dirs.append(d)

    for directory in search_dirs:
        if not directory or not os.path.isdir(directory):
            continue
        try:
            names = sorted(os.listdir(directory))
        except Exception:
            continue
        for name in names:
            cand_path = os.path.join(directory, name)
            if not os.path.isfile(cand_path):
                continue
            stem, ext = os.path.splitext(name)
            if stem.strip().lower() != ref_stem:
                continue
            if ext.lower() in SURFER_GRID_EXTENSIONS:
                return cand_path
    return None


def link_surfer_grid_into_project(project_root, reference_raster_path, source_raster_path=None):
    """
    Try to auto-link a matching Surfer grid to a time-slice.
    Returns a dict with z-grid linkage fields (empty dict if not found).
    """
    if not project_root or not reference_raster_path:
        return {}

    search_dirs = []
    if source_raster_path:
        search_dirs.append(os.path.dirname(source_raster_path))
    search_dirs.append(os.path.dirname(reference_raster_path))
    search_dirs.append(os.path.join(project_root, "timeslices_2d"))
    search_dirs.append(os.path.join(project_root, "timeslices_2d", "z_grids"))

    candidate = find_matching_surfer_grid(source_raster_path or reference_raster_path, search_dirs)
    if not candidate:
        return {}

    target_subfolder = os.path.join("timeslices_2d", "z_grids")
    target_dir = os.path.abspath(os.path.join(project_root, target_subfolder))
    os.makedirs(target_dir, exist_ok=True)
    candidate_abs = os.path.abspath(candidate)
    copied = False

    if os.path.normcase(os.path.dirname(candidate_abs)) == os.path.normcase(target_dir):
        project_grid_path = candidate_abs
    else:
        project_grid_path, _normalized = normalize_copy_into_project(project_root, target_subfolder, candidate_abs)
        copied = True

    return {
        "z_source": "surfer_grid",
        "z_grid_source_path": candidate_abs,
        "z_grid_project_path": project_grid_path,
        "z_grid_band": 1,
        "z_grid_linked_at": utc_now_iso(),
        "z_grid_copied": copied,
    }


def export_project_package(project_root, output_zip_path):
    """
    Export entire project folder as zip package.
    """
    if not os.path.isdir(project_root):
        raise FileNotFoundError(project_root)
    if not output_zip_path.lower().endswith(".zip"):
        output_zip_path += ".zip"

    with zipfile.ZipFile(output_zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _, files in os.walk(project_root):
            for fn in files:
                abs_path = os.path.join(root, fn)
                rel_path = os.path.relpath(abs_path, project_root)
                zf.write(abs_path, rel_path)
    return output_zip_path


def import_project_package(zip_path, target_project_root):
    """
    Import project package zip into target folder.
    """
    if not os.path.isfile(zip_path):
        raise FileNotFoundError(zip_path)
    ensure_project_structure(target_project_root)
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(target_project_root)
    return target_project_root
