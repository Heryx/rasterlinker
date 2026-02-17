# -*- coding: utf-8 -*-
"""
Project-folder and catalog helpers for 2D/3D geophysics workflows.
"""

import json
import os
import re
import shutil
from datetime import datetime, timezone


PROJECT_FOLDERS = (
    "volumes_3d",
    "timeslices_2d",
    "radargrams",
    "exports",
    "metadata",
)


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


def load_catalog(project_root):
    path = catalog_path(project_root)
    if not os.path.exists(path):
        return {
            "project_root": project_root,
            "created_at": utc_now_iso(),
            "updated_at": utc_now_iso(),
            "models_3d": [],
            "radargrams": [],
            "timeslices": [],
            "links": [],
        }
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_catalog(project_root, data):
    data["updated_at"] = utc_now_iso()
    path = catalog_path(project_root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=True)
    return path


def register_model_3d(project_root, model_record):
    data = load_catalog(project_root)
    data["models_3d"].append(model_record)
    return save_catalog(project_root, data)


def register_radargram(project_root, radargram_record):
    data = load_catalog(project_root)
    data["radargrams"].append(radargram_record)
    return save_catalog(project_root, data)


def _slugify_filename(name):
    base, ext = os.path.splitext(name)
    base = base.strip().replace(" ", "_")
    base = re.sub(r"[^A-Za-z0-9_\-\.]+", "_", base)
    base = re.sub(r"_+", "_", base).strip("_")
    if not base:
        base = "file"
    return f"{base}{ext.lower()}"


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
