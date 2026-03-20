# -*- coding: utf-8 -*-
"""
Depth extraction and validation helpers for timeslice raster file names.
"""

import os
import re


class TimesliceDepthParser:
    """
    Extract depth values from timeslice raster filenames.

    Strategy:
      1. Find all integer/float tokens in the filename.
      2. Select one token using positional index.
      3. Validate coherence inside a slice group.
      4. Allow manual overrides for anomalous files.
    """

    def __init__(self, unit="m"):
        self.unit = str(unit or "m")
        self.manual_overrides = {}  # {basename: float}

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    def extract_numbers(self, filename):
        """
        Return all numeric tokens found in filename basename, preserving order.
        """
        name = os.path.splitext(os.path.basename(str(filename or "")))[0]
        raw = re.findall(r"\d+[.,]\d+|\d+", name)
        result = []
        for token in raw:
            try:
                result.append(float(token.replace(",", ".")))
            except ValueError:
                pass
        return result

    def parse_depth(self, filename, token_index=0):
        """
        Extract a depth value from a single filename.

        Returns:
            (depth: float | None, status: str)

        Status values:
            "ok"
            "not_found"
            "index_out_of_range"
            "manual"
        """
        basename = os.path.basename(str(filename or ""))

        if basename in self.manual_overrides:
            return self.manual_overrides[basename], "manual"

        numbers = self.extract_numbers(filename)
        if not numbers:
            return None, "not_found"

        try:
            index = int(token_index)
        except Exception:
            index = 0
        if index < 0 or index >= len(numbers):
            return None, "index_out_of_range"

        return numbers[index], "ok"

    # ------------------------------------------------------------------
    # Batch parsing + coherence validation
    # ------------------------------------------------------------------

    def parse_batch(self, file_paths, token_index=0, delta_tolerance=0.3):
        """
        Parse a group of files and flag anomalies.

        Returns:
            list[dict]:
              {
                "file": str,
                "depth": float | None,
                "status": "ok" | "not_found" | "index_out_of_range" | "manual" | "incoherent",
              }
        """
        results = []
        for fp in list(file_paths or []):
            depth, status = self.parse_depth(fp, token_index=token_index)
            results.append({"file": fp, "depth": depth, "status": status})

        self._validate_coherence(results, delta_tolerance)
        return results

    def _validate_coherence(self, results, tolerance):
        """
        Validate that adjacent depth deltas are roughly consistent.
        Mutates `results` in place and marks outliers as "incoherent".
        """
        valid = [
            r
            for r in list(results or [])
            if r.get("depth") is not None and str(r.get("status") or "") in ("ok", "manual")
        ]
        if len(valid) < 3:
            return

        sorted_valid = sorted(valid, key=lambda r: float(r.get("depth")))
        depths = [float(r.get("depth")) for r in sorted_valid]
        deltas = [depths[i + 1] - depths[i] for i in range(len(depths) - 1)]
        if not deltas:
            return
        avg_delta = sum(deltas) / float(len(deltas))
        if avg_delta == 0:
            return

        try:
            tol = abs(float(avg_delta)) * abs(float(tolerance))
        except Exception:
            tol = abs(float(avg_delta)) * 0.3

        for idx, rec in enumerate(sorted_valid):
            if idx == 0:
                continue
            delta = depths[idx] - depths[idx - 1]
            if abs(delta - avg_delta) > tol and str(rec.get("status") or "") == "ok":
                rec["status"] = "incoherent"

    # ------------------------------------------------------------------
    # Manual overrides
    # ------------------------------------------------------------------

    def set_manual_override(self, filename, depth_value):
        self.manual_overrides[os.path.basename(str(filename or ""))] = float(depth_value)

    def clear_manual_override(self, filename):
        self.manual_overrides.pop(os.path.basename(str(filename or "")), None)

    # ------------------------------------------------------------------
    # UI preview
    # ------------------------------------------------------------------

    def preview(self, filename, token_index=0):
        numbers = self.extract_numbers(filename)
        depth, status = self.parse_depth(filename, token_index=token_index)
        return {
            "numbers": numbers,
            "selected": depth,
            "token_index": token_index,
            "unit": self.unit,
            "status": status,
        }
