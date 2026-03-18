import os
import shutil
from time import perf_counter

from qgis.PyQt.QtCore import QEventLoop, Qt
from qgis.PyQt.QtWidgets import QProgressDialog
from qgis.core import QgsApplication, QgsTask

from .project_catalog import (
    link_surfer_grid_into_project,
    load_catalog,
    normalize_copy_into_project,
    save_catalog,
    utc_now_iso,
)
from .radargram_metadata import find_worldfile


class CallbackTask(QgsTask):
    """Small QgsTask base class with a main-thread completion callback."""

    def __init__(self, description):
        super().__init__(description, QgsTask.CanCancel)
        self._completion_callback = None
        self.cancelled = False
        self.error_message = ""

    def set_completion_callback(self, callback):
        setattr(self, "_completion_callback", callback)

    def _notify_completion(self, ok):
        callback = getattr(self, "_completion_callback", None)
        if callable(callback):
            try:
                callback(self, bool(ok))
            except Exception:
                pass


class TimesliceImportTask(CallbackTask):
    """
    Background task: copy selected time-slices into project and auto-link z-grids.

    Input records format:
      {
        "source_path": str,
        "meta": dict,
        "warnings": list[str],
        "assigned_crs": str|None,
      }
    """

    def __init__(self, project_root, records, description="GeoSurvey Studio: Importing time-slices"):
        super().__init__(description)
        self.project_root = project_root
        self.records = list(records or [])
        self.imported_records = []
        self.imported_paths = []
        self.failed = []
        self.linked_grids = 0

    def run(self):
        total = len(self.records)
        if total <= 0:
            self.setProgress(100.0)
            return True

        for idx, rec in enumerate(self.records, start=1):
            if self.isCanceled():
                self.cancelled = True
                break

            source_path = (rec.get("source_path") or "").strip()
            source_name = os.path.basename(source_path) if source_path else f"record_{idx}"
            if not source_path:
                self.failed.append(f"{source_name}: missing source path")
                self.setProgress(float(idx) * 100.0 / float(total))
                continue

            try:
                project_path, normalized_name = normalize_copy_into_project(
                    self.project_root, "timeslices_2d", source_path
                )
                src_meta = rec.get("meta") if isinstance(rec.get("meta"), dict) else {}
                record = {
                    "id": f"timeslice_{utc_now_iso()}_{normalized_name}",
                    "name": os.path.splitext(normalized_name)[0],
                    "normalized_name": normalized_name,
                    "source_path": source_path,
                    "project_path": project_path,
                    "imported_at": utc_now_iso(),
                    "crs": src_meta.get("crs"),
                    "width": src_meta.get("width"),
                    "height": src_meta.get("height"),
                    "band_count": src_meta.get("band_count"),
                    "extent": src_meta.get("extent"),
                }

                link_info = link_surfer_grid_into_project(
                    self.project_root,
                    reference_raster_path=project_path,
                    source_raster_path=source_path,
                )
                if link_info:
                    record.update(link_info)
                    if record.get("z_grid_project_path"):
                        self.linked_grids += 1

                assigned_authid = (rec.get("assigned_crs") or "").strip()
                if assigned_authid:
                    record["assigned_crs"] = assigned_authid
                    if not record.get("crs"):
                        record["crs"] = assigned_authid

                warn_list = list(rec.get("warnings") or [])
                if warn_list:
                    record["georef_warnings"] = warn_list

                self.imported_records.append(record)
                self.imported_paths.append(project_path)
            except Exception as e:
                self.failed.append(f"{source_name}: {e}")

            self.setProgress(float(idx) * 100.0 / float(total))

        if self.cancelled:
            return False
        self.setProgress(100.0)
        return True

    def finished(self, result):
        self._notify_completion(bool(result) and not self.cancelled)


class LasLazImportTask(CallbackTask):
    """
    Background task: copy LAS/LAZ files into project folder.
    Metadata registration and map loading are handled in UI-thread callback.
    """

    def __init__(self, project_root, file_paths, description="GeoSurvey Studio: Importing LAS/LAZ"):
        super().__init__(description)
        self.project_root = project_root
        self.file_paths = list(file_paths or [])
        self.imported_files = []
        self.failed = []

    def run(self):
        total = len(self.file_paths)
        if total <= 0:
            self.setProgress(100.0)
            return True

        target_dir = os.path.normcase(os.path.abspath(os.path.join(self.project_root, "volumes_3d")))
        for idx, source_path in enumerate(self.file_paths, start=1):
            if self.isCanceled():
                self.cancelled = True
                break

            source_name = os.path.basename(source_path) if source_path else f"record_{idx}"
            if not source_path:
                self.failed.append(f"{source_name}: missing source path")
                self.setProgress(float(idx) * 100.0 / float(total))
                continue

            try:
                source_abs = os.path.abspath(source_path)
                source_dir = os.path.normcase(os.path.dirname(source_abs))
                already_in_project = source_dir == target_dir
                if already_in_project:
                    project_path = source_abs
                    normalized_name = os.path.basename(source_abs)
                else:
                    project_path, normalized_name = normalize_copy_into_project(
                        self.project_root, "volumes_3d", source_path
                    )
                self.imported_files.append(
                    {
                        "source_path": source_path,
                        "project_path": project_path,
                        "normalized_name": normalized_name,
                        "imported_at": utc_now_iso(),
                        "already_in_project": bool(already_in_project),
                    }
                )
            except Exception as e:
                self.failed.append(f"{source_name}: {e}")

            self.setProgress(float(idx) * 100.0 / float(total))

        if self.cancelled:
            return False
        self.setProgress(100.0)
        return True

    def finished(self, result):
        self._notify_completion(bool(result) and not self.cancelled)


class RadargramImportTask(CallbackTask):
    """
    Background task: copy radargram files into project folder + optional worldfile copy.
    Metadata registration and georef classification are handled in UI-thread callback.
    """

    def __init__(self, project_root, file_paths, description="GeoSurvey Studio: Importing radargrams"):
        super().__init__(description)
        self.project_root = project_root
        self.file_paths = list(file_paths or [])
        self.imported_files = []
        self.failed = []

    def _copy_worldfile_if_present(self, source_path, project_path):
        source_wf = find_worldfile(source_path)
        if not source_wf:
            return None
        _, wf_ext = os.path.splitext(source_wf)
        target_wf = os.path.splitext(project_path)[0] + wf_ext.lower()
        shutil.copy2(source_wf, target_wf)
        return target_wf

    def run(self):
        total = len(self.file_paths)
        if total <= 0:
            self.setProgress(100.0)
            return True

        for idx, source_path in enumerate(self.file_paths, start=1):
            if self.isCanceled():
                self.cancelled = True
                break

            source_name = os.path.basename(source_path) if source_path else f"record_{idx}"
            if not source_path:
                self.failed.append(f"{source_name}: missing source path")
                self.setProgress(float(idx) * 100.0 / float(total))
                continue

            try:
                project_path, normalized_name = normalize_copy_into_project(
                    self.project_root, "radargrams", source_path
                )
                copied_worldfile = self._copy_worldfile_if_present(source_path, project_path)
                self.imported_files.append(
                    {
                        "source_path": source_path,
                        "project_path": project_path,
                        "normalized_name": normalized_name,
                        "worldfile_path": copied_worldfile,
                        "imported_at": utc_now_iso(),
                    }
                )
            except Exception as e:
                self.failed.append(f"{source_name}: {e}")

            self.setProgress(float(idx) * 100.0 / float(total))

        if self.cancelled:
            return False
        self.setProgress(100.0)
        return True

    def finished(self, result):
        self._notify_completion(bool(result) and not self.cancelled)


class OgprSliceBuildTask(CallbackTask):
    """Background task: compute OGPR slice grids and write GeoTIFF files."""

    def __init__(
        self,
        profiles,
        params,
        extra_slice_params,
        output_dir,
        epsg=None,
        description="GeoSurvey Studio: Building OGPR timeslices",
    ):
        super().__init__(description)
        self.profiles = list(profiles or [])
        self.params = dict(params or {})
        self.extra_slice_params = dict(extra_slice_params or {})
        self.output_dir = output_dir
        self.epsg = epsg

        self.meta = {}
        self.grids = []
        self.slices = []
        self.no_grids = False

    def run(self):
        try:
            from .gpr_ogpr_slicer import compute_ogpr_slice_grids, write_grids_to_tifs
        except Exception as e:
            self.error_message = str(e)
            return False

        if not self.profiles:
            self.error_message = "Nessun profilo OGPR disponibile."
            return False

        task_start = perf_counter()
        compute_s = 0.0
        write_s = 0.0

        self.setProgress(5.0)
        if self.isCanceled():
            self.cancelled = True
            return False

        try:
            t0 = perf_counter()
            grids, meta = compute_ogpr_slice_grids(
                profiles=self.profiles,
                channel=self.params["channel"],
                combine_method=self.params["combine_method"],
                resolution=self.params["resolution"],
                z_step=self.params["z_step"],
                z_min=self.params["z_min"],
                z_max=self.params["z_max"],
                radius=self.params["radius"],
                pipeline_params=self.extra_slice_params.get("pipeline_params"),
                normalize_channels=bool(self.extra_slice_params.get("normalize_channels", False)),
                extraction_mode=str(self.extra_slice_params.get("extraction_mode", "las_like") or "las_like"),
                use_processing=bool(self.extra_slice_params.get("use_processing", False)),
                amplitude_sigma=self.extra_slice_params.get("amplitude_sigma"),
                use_anisotropic_idw=bool(self.extra_slice_params.get("use_anisotropic_idw", False)),
                idw_mode=str(self.extra_slice_params.get("idw_mode", "quality") or "quality"),
                auto_radius=bool(self.extra_slice_params.get("auto_radius", True)),
                min_points=int(self.extra_slice_params.get("min_points", 1) or 1),
                fill_nodata=bool(self.extra_slice_params.get("fill_nodata", True)),
                smooth_sigma=float(self.extra_slice_params.get("smooth_sigma", 0.8) or 0.0),
                depth_radius_factor=float(self.extra_slice_params.get("depth_radius_factor", 0.6) or 0.0),
                balance_profiles=bool(self.extra_slice_params.get("balance_profiles", True)),
                pre_slice_bg_removal=bool(self.extra_slice_params.get("pre_slice_bg_removal", False)),
                pre_slice_bg_mode=str(self.extra_slice_params.get("pre_slice_bg_mode", "line_by_line") or "line_by_line"),
                pre_slice_bg_window=int(self.extra_slice_params.get("pre_slice_bg_window", 0) or 0),
                pre_slice_bg_sample_start=int(self.extra_slice_params.get("pre_slice_bg_sample_start", 0) or 0),
                pre_slice_bg_sample_end=int(self.extra_slice_params.get("pre_slice_bg_sample_end", 0) or 0),
                stack_n=int(self.extra_slice_params.get("stack_n", 1) or 1),
                stack_kernel=str(self.extra_slice_params.get("stack_kernel", "boxcar") or "boxcar"),
                flip_traces_mode=str(self.extra_slice_params.get("flip_traces_mode", "none") or "none"),
                topographic_correction=bool(self.extra_slice_params.get("topographic_correction", False)),
                topo_reference_mode=str(self.extra_slice_params.get("topo_reference_mode", "median") or "median"),
                topo_reference_elevation=self.extra_slice_params.get("topo_reference_elevation"),
                parallel_profiles=bool(self.extra_slice_params.get("parallel_profiles", True)),
                profile_workers=int(self.extra_slice_params.get("profile_workers", 0) or 0),
            )
            compute_s = max(0.0, perf_counter() - t0)
        except Exception as e:
            self.error_message = str(e)
            return False

        self.meta = meta or {}
        self.grids = list(grids or [])
        self.setProgress(70.0)

        if self.isCanceled():
            self.cancelled = True
            return False

        if not self.grids:
            self.no_grids = True
            total_s = max(0.0, perf_counter() - task_start)
            self.meta["task_timing_s"] = {
                "total": float(round(total_s, 6)),
                "compute": float(round(compute_s, 6)),
                "write": 0.0,
            }
            self.setProgress(100.0)
            return True

        epsg_to_write = self.epsg
        try:
            if bool(self.meta.get("using_synthetic_coords", False)):
                # Avoid writing misleading projected CRS when geometry is synthetic/local.
                epsg_to_write = None
                self.meta["georef_warning"] = (
                    "Coordinate OGPR non plausibili: timeslice scritte con coordinate sintetiche locali "
                    "(CRS non assegnato)."
                )
            elif int(self.meta.get("profiles_geo_skipped", 0) or 0) > 0:
                skipped = int(self.meta.get("profiles_geo_skipped", 0) or 0)
                total = int(self.meta.get("profiles_total", 0) or 0)
                self.meta["georef_warning"] = (
                    f"Esclusi {skipped}/{max(total, 1)} profili con coordinate non valide "
                    "per mantenere georeferenziazione coerente."
                )
        except Exception:
            pass
        self.meta["epsg_written"] = int(epsg_to_write) if epsg_to_write else None

        try:
            t0 = perf_counter()
            self.slices = write_grids_to_tifs(
                self.grids,
                self.meta,
                self.output_dir,
                epsg=epsg_to_write,
            )
            write_s = max(0.0, perf_counter() - t0)
        except Exception as e:
            self.error_message = str(e)
            return False

        if self.isCanceled():
            self.cancelled = True
            return False
        total_s = max(0.0, perf_counter() - task_start)
        self.meta["task_timing_s"] = {
            "total": float(round(total_s, 6)),
            "compute": float(round(compute_s, 6)),
            "write": float(round(write_s, 6)),
        }
        self.setProgress(100.0)
        return True

    def finished(self, result):
        self._notify_completion(bool(result) and not self.cancelled)


class CatalogCleanupTask(CallbackTask):
    """
    Background task for catalog cleanup:
    removes missing model/radargram files from catalog.
    """

    def __init__(self, project_root, description="GeoSurvey Studio: Cleaning catalog"):
        super().__init__(description)
        self.project_root = project_root
        self.removed_models = 0
        self.removed_radargrams = 0

    def run(self):
        try:
            catalog = load_catalog(self.project_root)
            self.setProgress(10.0)

            before_models = len(catalog.get("models_3d", []))
            before_radargrams = len(catalog.get("radargrams", []))
            models = list(catalog.get("models_3d", []))
            radargrams = list(catalog.get("radargrams", []))
            total = max(1, len(models) + len(radargrams))
            processed = 0

            filtered_models = []
            for rec in models:
                if self.isCanceled():
                    self.cancelled = True
                    return False
                pth = rec.get("project_path")
                if pth and os.path.exists(pth):
                    filtered_models.append(rec)
                processed += 1
                self.setProgress(10.0 + (80.0 * float(processed) / float(total)))

            filtered_radargrams = []
            for rec in radargrams:
                if self.isCanceled():
                    self.cancelled = True
                    return False
                pth = rec.get("project_path")
                if pth and os.path.exists(pth):
                    filtered_radargrams.append(rec)
                processed += 1
                self.setProgress(10.0 + (80.0 * float(processed) / float(total)))

            catalog["models_3d"] = filtered_models
            catalog["radargrams"] = filtered_radargrams
            save_catalog(self.project_root, catalog)

            self.removed_models = before_models - len(filtered_models)
            self.removed_radargrams = before_radargrams - len(filtered_radargrams)
            self.setProgress(100.0)
            return True
        except Exception as e:
            self.error_message = str(e)
            return False

    def finished(self, result):
        self._notify_completion(bool(result) and not self.cancelled)


def run_task_with_progress_dialog(task, parent, label_text, window_title):
    """
    Run a QgsTask with a modal progress dialog and cancel support.
    Returns True on successful completion, False on cancellation/failure.
    """
    manager = QgsApplication.taskManager()
    if manager is None:
        ok = bool(task.run())
        task.finished(ok)
        return ok

    done = {"ok": False}
    loop = QEventLoop()

    progress = QProgressDialog(label_text, "Cancel", 0, 100, parent)
    progress.setWindowTitle(window_title)
    progress.setWindowModality(Qt.WindowModal)
    progress.setMinimumDuration(0)
    progress.setAutoClose(True)
    progress.setAutoReset(True)
    progress.setValue(0)

    def _on_progress(value):
        try:
            ivalue = int(round(float(value)))
        except Exception:
            ivalue = 0
        ivalue = max(0, min(100, ivalue))
        progress.setValue(ivalue)

    def _on_done(_task, ok):
        done["ok"] = bool(ok)
        try:
            if done["ok"]:
                progress.setValue(100)
            progress.close()
        except Exception:
            pass
        loop.quit()

    task.set_completion_callback(_on_done)
    progress.canceled.connect(task.cancel)
    if hasattr(task, "progressChanged"):
        task.progressChanged.connect(_on_progress)

    manager.addTask(task)
    loop.exec_()
    progress.close()
    return bool(done["ok"])


def start_task_with_progress_dialog(task, parent, label_text, window_title, on_finished=None):
    """
    Start a QgsTask with a modeless progress dialog and return immediately.
    on_finished receives (task, ok) on main thread.
    """
    manager = QgsApplication.taskManager()
    if manager is None:
        ok = bool(task.run())
        task.finished(ok)
        if callable(on_finished):
            try:
                on_finished(task, ok)
            except Exception:
                pass
        return None

    progress = QProgressDialog(label_text, "Cancel", 0, 100, parent)
    progress.setWindowTitle(window_title)
    progress.setWindowModality(Qt.NonModal)
    progress.setMinimumDuration(0)
    progress.setAutoClose(True)
    progress.setAutoReset(True)
    progress.setValue(0)
    progress.show()

    def _on_progress(value):
        try:
            ivalue = int(round(float(value)))
        except Exception:
            ivalue = 0
        ivalue = max(0, min(100, ivalue))
        progress.setValue(ivalue)

    def _on_done(_task, ok):
        try:
            if ok:
                progress.setValue(100)
            progress.close()
        except Exception:
            pass
        if callable(on_finished):
            try:
                on_finished(_task, bool(ok))
            except Exception:
                pass

    task.set_completion_callback(_on_done)
    progress.canceled.connect(task.cancel)
    if hasattr(task, "progressChanged"):
        task.progressChanged.connect(_on_progress)
    manager.addTask(task)
    return progress
