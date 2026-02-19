import os
import shutil

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
        self._completion_callback = callback

    def _notify_completion(self, ok):
        if callable(self._completion_callback):
            try:
                self._completion_callback(self, bool(ok))
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

    def __init__(self, project_root, records, description="RasterLinker: Importing time-slices"):
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

    def __init__(self, project_root, file_paths, description="RasterLinker: Importing LAS/LAZ"):
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
                    self.project_root, "volumes_3d", source_path
                )
                self.imported_files.append(
                    {
                        "source_path": source_path,
                        "project_path": project_path,
                        "normalized_name": normalized_name,
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


class RadargramImportTask(CallbackTask):
    """
    Background task: copy radargram files into project folder + optional worldfile copy.
    Metadata registration and georef classification are handled in UI-thread callback.
    """

    def __init__(self, project_root, file_paths, description="RasterLinker: Importing radargrams"):
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


class CatalogCleanupTask(CallbackTask):
    """
    Background task for catalog cleanup:
    removes missing model/radargram files from catalog.
    """

    def __init__(self, project_root, description="RasterLinker: Cleaning catalog"):
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
