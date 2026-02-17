# -*- coding: utf-8 -*-
"""
Best-effort metadata extraction for radargram files.
"""

import csv
import os

from PyQt5.QtGui import QImageReader


IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}
WORLDFILE_EXTENSIONS = {
    ".png": [".pgw", ".pngw", ".wld"],
    ".jpg": [".jgw", ".jpgw", ".wld"],
    ".jpeg": [".jgw", ".jpegw", ".wld"],
    ".tif": [".tfw", ".tifw", ".wld"],
    ".tiff": [".tfw", ".tiffw", ".wld"],
    ".bmp": [".bpw", ".bmpw", ".wld"],
}


def _inspect_image(file_path):
    reader = QImageReader(file_path)
    size = reader.size()
    if size.isValid():
        return {"width": size.width(), "height": size.height(), "source_type": "image"}
    return {}


def _inspect_npy(file_path):
    try:
        import numpy as np  # Optional dependency
    except Exception:
        return {}

    try:
        arr = np.load(file_path, mmap_mode="r")
        shape = tuple(int(v) for v in arr.shape)
        meta = {"shape": shape, "ndim": int(arr.ndim), "source_type": "npy"}
        if arr.ndim >= 2:
            meta["rows"] = int(shape[0])
            meta["cols"] = int(shape[1])
        return meta
    except Exception:
        return {}


def _inspect_text_matrix(file_path):
    """
    Infer row/col size from first lines of csv/txt matrix-like files.
    """
    rows = 0
    max_cols = 0
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            sample = "".join([next(f, "") for _ in range(20)])
        if not sample:
            return {}
        delimiter = "," if "," in sample else None
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            reader = csv.reader(f, delimiter=delimiter) if delimiter else csv.reader(f, delimiter=" ")
            for row in reader:
                if not row:
                    continue
                if delimiter is None:
                    row = [v for v in row if v.strip()]
                rows += 1
                max_cols = max(max_cols, len(row))
                if rows >= 50000:
                    break
        if rows > 0 and max_cols > 0:
            return {"rows": rows, "cols": max_cols, "source_type": "text-matrix"}
    except Exception:
        return {}
    return {}


def inspect_radargram(file_path):
    """
    Return generic metadata for radargram files with best-effort dimensions.
    """
    ext = os.path.splitext(file_path)[1].lower()
    meta = {
        "path": file_path,
        "file_name": os.path.basename(file_path),
        "extension": ext,
        "size_bytes": os.path.getsize(file_path),
    }

    if ext in IMAGE_EXTENSIONS:
        meta.update(_inspect_image(file_path))
    elif ext == ".npy":
        meta.update(_inspect_npy(file_path))
    elif ext in {".csv", ".txt", ".asc"}:
        meta.update(_inspect_text_matrix(file_path))

    return meta


def find_worldfile(file_path):
    base, ext = os.path.splitext(file_path)
    ext = ext.lower()
    for wf_ext in WORLDFILE_EXTENSIONS.get(ext, []):
        candidate = base + wf_ext
        if os.path.exists(candidate):
            return candidate
    return None
