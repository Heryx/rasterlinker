#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Benchmark IDW modes (fast vs quality) for OGPR slicer on synthetic data.

Run from plugin root:
  python scripts/benchmark_ogpr_idw.py

Optional JSON report:
  python scripts/benchmark_ogpr_idw.py --json-out benchmark_idw.json
"""

from __future__ import annotations

import argparse
import importlib
import json
import pathlib
import statistics
import sys
from time import perf_counter

import numpy as np


def _setup_imports():
    this_file = pathlib.Path(__file__).resolve()
    plugin_dir = this_file.parents[1]
    plugin_parent = plugin_dir.parent
    if str(plugin_parent) not in sys.path:
        sys.path.insert(0, str(plugin_parent))
    module = importlib.import_module("rasterlinker_feature_interpolation.gpr_ogpr_slicer")
    return module


class _FakeChannel:
    def __init__(self, data, easting, northing, altitude):
        self.data = np.asarray(data, dtype=np.float32)
        self.easting = np.asarray(easting, dtype=np.float64)
        self.northing = np.asarray(northing, dtype=np.float64)
        self.altitude = np.asarray(altitude, dtype=np.float64)
        self.distances = None


class _FakeProfile:
    def __init__(
        self,
        path,
        channels,
        easting,
        northing,
        altitude,
        depth_max_m=2.0,
        dt_ns=0.4,
        sampling_step_m=0.05,
        epsg=32633,
    ):
        self.path = str(path)
        self._channels = [
            _FakeChannel(ch, easting=easting, northing=northing, altitude=altitude)
            for ch in channels
        ]
        self.n_channels = int(len(self._channels))
        self.n_samples = int(self._channels[0].data.shape[0]) if self._channels else 0
        self.n_slices = int(self._channels[0].data.shape[1]) if self._channels else 0
        self.depth_max_m = float(depth_max_m)
        self.dt_ns = float(dt_ns)
        self.sampling_step_m = float(sampling_step_m)
        self.epsg = int(epsg)

    def channel(self, idx):
        return self._channels[int(idx)]


def _make_profiles(
    n_profiles: int,
    n_channels: int,
    n_samples: int,
    n_traces: int,
    seed: int = 1234,
):
    rng = np.random.default_rng(int(seed))
    profiles = []
    for p in range(int(n_profiles)):
        x = 443570.0 + np.arange(n_traces, dtype=np.float64) * 0.05
        y = np.full(n_traces, 4548956.0 + p * 0.60, dtype=np.float64)
        zsurf = np.full(n_traces, 120.0 + p * 0.02, dtype=np.float64)

        z = np.linspace(0.0, 1.0, n_samples, dtype=np.float64)[:, None]
        t = np.linspace(0.0, 1.0, n_traces, dtype=np.float64)[None, :]

        channels = []
        for c in range(int(n_channels)):
            base = (
                1400.0 * np.sin(21.0 * z + 0.35 * p + 0.25 * c)
                + 720.0 * np.cos(7.0 * t + 0.22 * c)
                + 110.0 * np.sin(9.0 * z * t * (c + 1.0))
            )
            noise = rng.normal(0.0, 40.0, size=(n_samples, n_traces))
            channels.append((base + noise).astype(np.float32))

        profiles.append(
            _FakeProfile(
                path=f"bench_profile_{p}.ogpr",
                channels=channels,
                easting=x,
                northing=y,
                altitude=zsurf,
                depth_max_m=2.5,
                dt_ns=0.35,
                sampling_step_m=0.05,
                epsg=32633,
            )
        )
    return profiles


def _safe_float(dct, key, default=0.0):
    try:
        v = float(dct.get(key, default))
    except Exception:
        v = float(default)
    if not np.isfinite(v):
        return float(default)
    return float(v)


def _run_once(compute_ogpr_slice_grids, profiles, args, mode: str) -> dict:
    t0 = perf_counter()
    grids, meta = compute_ogpr_slice_grids(
        profiles=profiles,
        channel=-1,
        combine_method="mean",
        resolution=float(args.resolution),
        z_step=float(args.z_step),
        z_min=0.0,
        z_max=float(args.z_max),
        radius=float(args.radius),
        extraction_mode="las_like",
        use_processing=bool(args.use_processing),
        idw_mode=str(mode),
        auto_radius=False,
        min_points=int(args.min_points),
        fill_nodata=bool(args.fill_nodata),
        smooth_sigma=float(args.smooth_sigma),
        emit_diagnostics=False,
        parallel_profiles=bool(args.parallel_profiles),
        profile_workers=int(args.profile_workers),
    )
    wall_s = max(0.0, perf_counter() - t0)
    meta = dict(meta or {})
    timing = dict(meta.get("timing_s") or {})
    counts = dict(meta.get("timing_counts") or {})
    return {
        "mode": str(mode),
        "wall_s": float(wall_s),
        "meta_total_s": _safe_float(timing, "total", wall_s),
        "preprocess_s": _safe_float(timing, "preprocess", 0.0),
        "grid_setup_s": _safe_float(timing, "grid_setup", 0.0),
        "slice_loop_s": _safe_float(timing, "slice_loop", 0.0),
        "idw_s": _safe_float(timing, "interpolation", 0.0),
        "idw_avg_s_per_slice": _safe_float(timing, "interpolation_avg_per_slice", 0.0),
        "fill_mean_pct": _safe_float(meta, "fill_pct_mean", 0.0),
        "fill_min_pct": _safe_float(meta, "fill_pct_min", 0.0),
        "n_slices": int(len(grids or [])),
        "slices_requested": int(counts.get("slices_requested", 0) or 0),
        "slices_computed": int(counts.get("slices_computed", len(grids or [])) or 0),
        "parallel_profiles": bool(meta.get("parallel_profiles", False)),
        "profile_workers_used": int(meta.get("profile_workers_used", 1) or 1),
        "meta": meta,
    }


def _aggregate(runs: list[dict]) -> dict:
    if not runs:
        return {}
    out = dict(runs[-1])
    for key in (
        "wall_s",
        "meta_total_s",
        "preprocess_s",
        "grid_setup_s",
        "slice_loop_s",
        "idw_s",
        "idw_avg_s_per_slice",
        "fill_mean_pct",
        "fill_min_pct",
    ):
        vals = [float(r.get(key, 0.0)) for r in runs]
        out[key] = float(statistics.mean(vals))
    out["repeats"] = int(len(runs))
    return out


def _fmt(x, nd=3):
    return f"{float(x):.{int(nd)}f}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark OGPR IDW fast vs quality.")
    parser.add_argument("--profiles", type=int, default=8, help="Synthetic profile count.")
    parser.add_argument("--channels", type=int, default=4, help="Channels per profile.")
    parser.add_argument("--samples", type=int, default=256, help="Samples per trace.")
    parser.add_argument("--traces", type=int, default=320, help="Traces per profile.")
    parser.add_argument("--resolution", type=float, default=0.10, help="Grid resolution (m).")
    parser.add_argument("--z-step", type=float, default=0.05, dest="z_step", help="Depth slice step (m).")
    parser.add_argument("--z-max", type=float, default=2.0, dest="z_max", help="Max depth (m).")
    parser.add_argument("--radius", type=float, default=0.35, help="IDW radius (m).")
    parser.add_argument("--min-points", type=int, default=1, dest="min_points", help="Min points per IDW cell.")
    parser.add_argument("--fill-nodata", action="store_true", help="Enable fill NoData post-IDW.")
    parser.add_argument("--smooth-sigma", type=float, default=0.0, dest="smooth_sigma", help="Gaussian smooth sigma.")
    parser.add_argument("--use-processing", action="store_true", help="Run preprocessing pipeline before slicing.")
    parser.add_argument("--parallel-profiles", action="store_true", default=True, help="Parallelize per-profile preprocessing.")
    parser.add_argument("--no-parallel-profiles", action="store_false", dest="parallel_profiles", help="Disable parallel profile preprocessing.")
    parser.add_argument("--profile-workers", type=int, default=0, help="Workers for profile preprocessing (0=auto).")
    parser.add_argument("--repeat", type=int, default=2, help="Repeat runs per mode and average.")
    parser.add_argument("--seed", type=int, default=1234, help="Random seed for synthetic data.")
    parser.add_argument("--json-out", type=str, default="", help="Optional path to write JSON report.")
    args = parser.parse_args()

    try:
        slicer = _setup_imports()
        compute_ogpr_slice_grids = slicer.compute_ogpr_slice_grids
    except Exception as exc:
        print(f"ERROR: cannot import plugin slicer module: {exc}")
        return 2

    profiles = _make_profiles(
        n_profiles=int(args.profiles),
        n_channels=int(args.channels),
        n_samples=int(args.samples),
        n_traces=int(args.traces),
        seed=int(args.seed),
    )

    report = {
        "dataset": {
            "profiles": int(args.profiles),
            "channels": int(args.channels),
            "samples": int(args.samples),
            "traces": int(args.traces),
            "resolution": float(args.resolution),
            "z_step": float(args.z_step),
            "z_max": float(args.z_max),
            "radius": float(args.radius),
            "repeat": int(args.repeat),
            "parallel_profiles": bool(args.parallel_profiles),
            "profile_workers": int(args.profile_workers),
        },
        "results": {},
    }

    modes = ("fast", "quality")
    for mode in modes:
        runs = []
        for _ in range(max(1, int(args.repeat))):
            runs.append(_run_once(compute_ogpr_slice_grids, profiles, args, mode=mode))
        report["results"][mode] = _aggregate(runs)

    fast = report["results"].get("fast", {})
    quality = report["results"].get("quality", {})

    print("\n=== OGPR IDW Benchmark ===")
    print(
        f"profiles={args.profiles} channels={args.channels} "
        f"samples={args.samples} traces={args.traces} "
        f"res={args.resolution:.3f}m z_step={args.z_step:.3f}m z_max={args.z_max:.3f}m"
    )
    print(f"repeat={args.repeat} parallel_profiles={args.parallel_profiles} workers={args.profile_workers}")
    print("")
    print("Mode      total(s)  idw(s)  idw_avg(s/slice)  fill_mean(%)  fill_min(%)  slices")
    for mode in modes:
        m = report["results"][mode]
        print(
            f"{mode:<8}  {_fmt(m.get('meta_total_s', 0.0)):>8}  {_fmt(m.get('idw_s', 0.0)):>6}  "
            f"{_fmt(m.get('idw_avg_s_per_slice', 0.0), 4):>15}  {_fmt(m.get('fill_mean_pct', 0.0), 2):>11}  "
            f"{_fmt(m.get('fill_min_pct', 0.0), 2):>10}  "
            f"{int(m.get('slices_computed', 0))}/{int(m.get('slices_requested', 0))}"
        )

    q = float(quality.get("meta_total_s", 0.0))
    f = float(fast.get("meta_total_s", 0.0))
    if q > 1e-9 and f > 0.0:
        speedup = q / f
        print(f"\nSpeed ratio quality/fast: {speedup:.2f}x")

    if args.json_out:
        out_path = pathlib.Path(args.json_out).expanduser().resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w", encoding="utf-8") as fobj:
            json.dump(report, fobj, indent=2, ensure_ascii=True)
        print(f"JSON report: {out_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
