import os

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QApplication,
    QFileDialog,
    QInputDialog,
    QListWidgetItem,
    QMessageBox,
    QProgressDialog,
)
from qgis.core import (
    QgsCoordinateTransform,
    QgsLayerTreeLayer,
    QgsProject,
    QgsRasterLayer,
    QgsRectangle,
)

from .project_catalog import (
    assign_timeslices_to_group,
    create_raster_group,
    load_catalog,
    register_timeslices_batch,
    remove_timeslices_from_group,
)
from .background_tasks import (
    TimesliceImportTask,
    run_task_with_progress_dialog,
    start_task_with_progress_dialog,
)


class CatalogGroupMixin:
    def populate_group_list(self):
        """Populate group list using only groups currently present in QGIS dock."""
        try:
            if self.dlg is None:
                return
            self.dlg.groupListWidget.clear()
            self.dlg.rasterListWidget.clear()

            project_root = self._require_project_root(notify=False)
            if not project_root:
                return

            by_name = self._catalog_groups_by_name(project_root)
            for group_name in self._visible_plugin_group_names():
                group = by_name.get(group_name)
                if not group or not group.get("timeslice_ids"):
                    continue
                item = QListWidgetItem(group_name)
                item.setData(Qt.UserRole, group.get("id"))
                self.dlg.groupListWidget.addItem(item)
        except Exception as e:
            QMessageBox.critical(self.dlg, "Error", f"Error while loading plugin groups: {e}")

    def _format_timeslice_depth_range(self, rec):
        if not isinstance(rec, dict):
            return ""
        depth_from = rec.get("depth_from")
        depth_to = rec.get("depth_to")
        unit = (rec.get("unit") or "m").strip() or "m"
        if depth_from is None and depth_to is None:
            return ""

        def _fmt(v):
            try:
                txt = f"{float(v):.3f}"
                txt = txt.rstrip("0").rstrip(".")
                return txt
            except Exception:
                return str(v)

        if depth_from is None:
            return f"to {_fmt(depth_to)} {unit}"
        if depth_to is None:
            return f"from {_fmt(depth_from)} {unit}"
        return f"{_fmt(depth_from)}-{_fmt(depth_to)} {unit}"

    def populate_raster_list_from_selected_groups(self):
        """Populate raster list from selected plugin groups and catalog-imported time-slices only."""
        try:
            self.dlg.rasterListWidget.clear()
            project_root = self._require_project_root()
            if not project_root:
                return

            catalog = load_catalog(project_root)
            groups_by_id = {g.get("id"): g for g in catalog.get("raster_groups", [])}
            timeslices_by_id = {t.get("id"): t for t in catalog.get("timeslices", [])}

            selected_group_items = self.dlg.groupListWidget.selectedItems()
            if not selected_group_items:
                return

            seen_timeslice_ids = set()
            for group_item in selected_group_items:
                group_id = group_item.data(Qt.UserRole)
                group = groups_by_id.get(group_id)
                if not group:
                    continue
                group_label = group.get("name", "Group")
                for timeslice_id in group.get("timeslice_ids", []):
                    if timeslice_id in seen_timeslice_ids:
                        continue
                    rec = timeslices_by_id.get(timeslice_id)
                    if not rec:
                        continue
                    project_path = rec.get("project_path")
                    if not project_path:
                        continue
                    seen_timeslice_ids.add(timeslice_id)
                    base_name = rec.get("normalized_name") or rec.get("name") or timeslice_id
                    depth_txt = self._format_timeslice_depth_range(rec)
                    has_z_grid = bool((rec.get("z_grid_project_path") or "").strip())
                    display = f"[{group_label}] {base_name}"
                    if depth_txt:
                        display = f"{display} (Depth: {depth_txt})"
                    item = QListWidgetItem(display)
                    tooltip_lines = []
                    if depth_txt:
                        tooltip_lines.append(f"Depth range: {depth_txt}")
                    if has_z_grid:
                        tooltip_lines.append("Z source: linked Surfer grid")
                    if tooltip_lines:
                        item.setToolTip("\n".join(tooltip_lines))
                    item.setData(
                        Qt.UserRole,
                        {
                            "timeslice_id": timeslice_id,
                            "project_path": project_path,
                            "group_id": group_id,
                            "group_name": group_label,
                            "depth_range": depth_txt,
                            "z_source": rec.get("z_source"),
                            "z_grid_project_path": rec.get("z_grid_project_path"),
                        },
                    )
                    self.dlg.rasterListWidget.addItem(item)
            self._update_navigation_controls()
        except Exception as e:
            QMessageBox.critical(self.dlg, "Error", f"Error while loading rasters: {e}")

    def populate_raster_list(self, group_path=None):
        self.populate_raster_list_from_selected_groups()

    def on_group_selected(self, item):
        if not item:
            self._set_name_raster_label(None)
            return
        self.populate_raster_list_from_selected_groups()
        lines = self._build_name_lines_for_selected_groups()
        if lines:
            self._render_name_raster_lines(lines)
        else:
            first_item = self.dlg.rasterListWidget.item(0)
            self._set_name_raster_label(first_item.text() if first_item else None)
        self.load_raster(show_message=False)

    def on_group_selection_changed(self):
        if self.dlg is None:
            return
        selected_items = self.dlg.groupListWidget.selectedItems()
        if not selected_items:
            self.dlg.rasterListWidget.clear()
            self._set_name_raster_label(None)
            self._update_navigation_controls(0)
            return
        self.populate_raster_list_from_selected_groups()
        self._update_navigation_controls()
        lines = self._build_name_lines_for_selected_groups()
        self._render_name_raster_lines(lines)
        self.load_raster(show_message=False)

    def _update_navigation_controls(self, value=None):
        if self.dlg is None:
            return

        total = self.dlg.rasterListWidget.count()
        max_idx = max(0, total - 1)
        current = self.dlg.Dial.value() if value is None else int(value)
        if current < 0:
            current = 0
        if current > max_idx:
            current = max_idx

        controls = [self.dlg.Dial, self.dlg.dial2]
        for ctrl in controls:
            ctrl.blockSignals(True)
            ctrl.setMinimum(0)
            ctrl.setMaximum(max_idx)
            ctrl.setSingleStep(1)
            if hasattr(ctrl, "setPageStep"):
                ctrl.setPageStep(1)
            ctrl.setEnabled(total > 0)
            ctrl.setValue(current)
            ctrl.blockSignals(False)

    def update_visibility_with_dial(self, value):
        selected_group_items = self.dlg.groupListWidget.selectedItems()
        if not selected_group_items:
            QMessageBox.warning(self.dlg, "Error", "Select at least one group before using the dial.")
            return

        self._update_navigation_controls(value)
        value = self.dlg.Dial.value()

        parts_for_label = []
        for group_item in selected_group_items:
            group_name = group_item.text().strip()
            group = self._get_or_create_plugin_qgis_group(group_name)
            raster_nodes = [
                child for child in group.children()
                if isinstance(child, QgsLayerTreeLayer) and isinstance(child.layer(), QgsRasterLayer)
            ]
            if not raster_nodes:
                continue

            index = min(value, len(raster_nodes) - 1)
            for i, node in enumerate(raster_nodes):
                node.setItemVisibilityChecked(i == index)

            visible_raster_name = raster_nodes[index].layer().name()
            parts_for_label.append(f"[{group_name}] {visible_raster_name}")

        self._render_name_raster_lines(parts_for_label)

    def zoom_to_selected_groups(self):
        selected_group_items = self.dlg.groupListWidget.selectedItems()
        if not selected_group_items:
            QMessageBox.warning(self.dlg, "Error", "Select at least one group.")
            return

        project = QgsProject.instance()
        project_crs = project.crs()
        combined_extent = None
        found_any_layer = False

        for group_item in selected_group_items:
            group_name = group_item.text().strip()
            group = self._get_or_create_plugin_qgis_group(group_name)
            for child in group.children():
                if not isinstance(child, QgsLayerTreeLayer):
                    continue
                layer = child.layer()
                if layer is None:
                    continue

                extent = layer.extent()
                if extent is None or extent.isNull() or extent.isEmpty():
                    continue
                if layer.crs() != project_crs:
                    try:
                        tr = QgsCoordinateTransform(layer.crs(), project_crs, project)
                        extent = tr.transformBoundingBox(extent)
                    except Exception:
                        continue
                found_any_layer = True
                if combined_extent is None:
                    combined_extent = QgsRectangle(extent)
                else:
                    combined_extent.combineExtentWith(extent)

        if not found_any_layer or combined_extent is None or combined_extent.isNull() or combined_extent.isEmpty():
            QMessageBox.warning(self.dlg, "Error", "No valid layers found in selected groups.")
            return
        canvas = self.iface.mapCanvas()
        canvas.setExtent(combined_extent)
        canvas.refresh()

    def _inspect_timeslice(self, file_path):
        layer = QgsRasterLayer(file_path, os.path.basename(file_path))
        if not layer.isValid():
            return {"is_valid_raster": False}
        extent = layer.extent()
        return {
            "is_valid_raster": True,
            "crs": layer.crs().authid() if layer.crs().isValid() else None,
            "width": layer.width(),
            "height": layer.height(),
            "band_count": layer.bandCount(),
            "extent": {
                "xmin": extent.xMinimum(),
                "xmax": extent.xMaximum(),
                "ymin": extent.yMinimum(),
                "ymax": extent.yMaximum(),
            },
        }

    def _timeslice_georef_warnings(self, meta):
        warnings = []
        extent = meta.get("extent") or {}
        xmin = extent.get("xmin")
        xmax = extent.get("xmax")
        ymin = extent.get("ymin")
        ymax = extent.get("ymax")
        crs_authid = (meta.get("crs") or "").strip()
        project_crs = QgsProject.instance().crs()

        if not crs_authid:
            warnings.append("Missing CRS in raster metadata.")
        elif project_crs.isValid() and crs_authid != project_crs.authid():
            warnings.append(f"CRS mismatch: raster {crs_authid}, project {project_crs.authid()}.")

        if None in (xmin, xmax, ymin, ymax):
            return warnings

        x_span = float(xmax) - float(xmin)
        y_span = float(ymax) - float(ymin)
        if x_span <= 0 or y_span <= 0:
            warnings.append("Invalid extent (non-positive width/height).")
            return warnings

        max_abs = max(abs(float(xmin)), abs(float(xmax)), abs(float(ymin)), abs(float(ymax)))
        if max_abs < 1.0:
            warnings.append("Extent is very close to origin (0,0).")
        if max_abs > 1e8:
            warnings.append("Extent coordinates are unusually large.")
        return warnings

    def _ensure_preferred_import_crs(self):
        preferred = self._get_preferred_import_crs()
        if preferred is not None and preferred.isValid():
            return preferred

        epsg_text, ok = QInputDialog.getText(
            self.dlg,
            "Set Import CRS",
            "Enter EPSG code (example: 32633) or AUTHID (example: EPSG:32633):",
            text="EPSG:32633",
        )
        if not ok or not epsg_text.strip():
            return None

        try:
            from qgis.core import QgsCoordinateReferenceSystem
            raw = epsg_text.strip().upper()
            authid = raw if raw.startswith("EPSG:") else f"EPSG:{raw}"
            selected = QgsCoordinateReferenceSystem(authid)
        except Exception:
            selected = None

        if selected is None or not selected.isValid():
            QMessageBox.warning(self.dlg, "Set Import CRS", "Invalid CRS selection.")
            return None
        self.settings.setValue(self.settings_key_default_import_crs, selected.authid())
        return selected

    def _select_group_item_by_id(self, group_id):
        if self.dlg is None or not group_id:
            return False
        widget = self.dlg.groupListWidget
        widget.blockSignals(True)
        widget.clearSelection()
        found = False
        for idx in range(widget.count()):
            item = widget.item(idx)
            if item.data(Qt.UserRole) == group_id:
                item.setSelected(True)
                widget.setCurrentItem(item)
                found = True
                break
        widget.blockSignals(False)
        if found:
            self.on_group_selection_changed()
        return found

    def _quick_import_timeslices_for_group(self, project_root, group, on_finished=None):
        def _finish(result):
            if callable(on_finished):
                try:
                    on_finished(result)
                except Exception:
                    pass
                return None
            return result

        file_paths, _ = QFileDialog.getOpenFileNames(
            self.dlg,
            "Select Time-slice raster files",
            "",
            "Rasters (*.tif *.tiff *.png *.jpg *.jpeg *.asc *.img);;All files (*.*)",
        )
        if not file_paths:
            return _finish({"cancelled": False, "imported": 0, "skipped": 0})

        records = []
        invalid = []
        scan_total = len(file_paths)
        scan_progress = QProgressDialog("Analyzing selected images...", "Cancel", 0, scan_total, self.dlg)
        scan_progress.setWindowTitle("Create Group - Analyze Images")
        scan_progress.setWindowModality(Qt.WindowModal)
        scan_progress.setMinimumDuration(0)
        scan_progress.setAutoClose(True)
        scan_progress.setAutoReset(True)
        scan_progress.setValue(0)
        for idx, src_path in enumerate(file_paths, start=1):
            if scan_progress.wasCanceled():
                scan_progress.close()
                return _finish({"cancelled": True, "imported": 0, "skipped": 0})
            scan_progress.setLabelText(f"Analyzing {idx}/{scan_total}: {os.path.basename(src_path)}")
            QApplication.processEvents()
            meta = self._inspect_timeslice(src_path)
            if not meta.get("is_valid_raster"):
                invalid.append(os.path.basename(src_path))
                scan_progress.setValue(idx)
                QApplication.processEvents()
                continue
            records.append(
                {
                    "source_path": src_path,
                    "meta": meta,
                    "warnings": self._timeslice_georef_warnings(meta),
                    "assigned_crs": None,
                }
            )
            scan_progress.setValue(idx)
            QApplication.processEvents()
        scan_progress.close()

        skipped = 0
        if invalid:
            skipped += len(invalid)
            preview = "\n".join(invalid[:10])
            extra = len(invalid) - min(len(invalid), 10)
            if extra > 0:
                preview += f"\n... and {extra} more."
            QMessageBox.warning(
                self.dlg,
                "Invalid raster files",
                "Some selected files are not valid rasters and will be skipped:\n\n" + preview,
            )

        if not records:
            return _finish({"cancelled": False, "imported": 0, "skipped": skipped})

        missing_crs = [r for r in records if not (r.get("meta", {}).get("crs") or "").strip()]
        if missing_crs:
            msg = QMessageBox(self.dlg)
            msg.setIcon(QMessageBox.Warning)
            msg.setWindowTitle("Missing CRS")
            msg.setText(f"{len(missing_crs)} image(s) have no CRS.")
            msg.setInformativeText("Choose how to proceed:")
            assign_btn = msg.addButton("Assign Default CRS", QMessageBox.AcceptRole)
            keep_btn = msg.addButton("Keep Without CRS", QMessageBox.ActionRole)
            skip_btn = msg.addButton("Skip Missing CRS", QMessageBox.DestructiveRole)
            cancel_btn = msg.addButton(QMessageBox.Cancel)
            msg.setDefaultButton(assign_btn)
            msg.exec_()
            clicked = msg.clickedButton()
            if clicked == cancel_btn:
                return _finish({"cancelled": True, "imported": 0, "skipped": skipped})
            if clicked == skip_btn:
                records = [r for r in records if (r.get("meta", {}).get("crs") or "").strip()]
                skipped += len(missing_crs)
            elif clicked == assign_btn:
                preferred = self._ensure_preferred_import_crs()
                if preferred is None or not preferred.isValid():
                    return _finish({"cancelled": True, "imported": 0, "skipped": skipped})
                authid = preferred.authid()
                for rec in missing_crs:
                    rec["assigned_crs"] = authid
                    rec_meta = rec.get("meta") or {}
                    rec_meta["crs"] = authid
                    rec["warnings"] = self._timeslice_georef_warnings(rec_meta)
            elif clicked == keep_btn:
                pass

        if not records:
            return _finish({"cancelled": False, "imported": 0, "skipped": skipped})

        crs_values = sorted({(r.get("meta", {}).get("crs") or "").strip() for r in records if (r.get("meta", {}).get("crs") or "").strip()})
        if len(crs_values) > 1:
            preferred = self._get_preferred_import_crs()
            target_authid = preferred.authid() if preferred and preferred.isValid() else crs_values[0]
            msg = QMessageBox(self.dlg)
            msg.setIcon(QMessageBox.Warning)
            msg.setWindowTitle("Mixed CRS detected")
            msg.setText("Selected images have different CRS values.")
            msg.setInformativeText(
                "Detected CRS:\n- "
                + "\n- ".join(crs_values)
                + f"\n\nChoose how to proceed (target: {target_authid})."
            )
            import_all_btn = msg.addButton("Import All", QMessageBox.AcceptRole)
            keep_target_btn = msg.addButton(f"Keep Only {target_authid}", QMessageBox.ActionRole)
            cancel_btn = msg.addButton(QMessageBox.Cancel)
            msg.setDefaultButton(import_all_btn)
            msg.exec_()
            clicked = msg.clickedButton()
            if clicked == cancel_btn:
                return _finish({"cancelled": True, "imported": 0, "skipped": skipped})
            if clicked == keep_target_btn:
                before = len(records)
                records = [r for r in records if (r.get("meta", {}).get("crs") or "").strip() == target_authid]
                skipped += max(0, before - len(records))

        if not records:
            return _finish({"cancelled": False, "imported": 0, "skipped": skipped})

        with_warnings = [r for r in records if r.get("warnings")]
        if with_warnings:
            lines = []
            for rec in with_warnings[:12]:
                name = os.path.basename(rec.get("source_path") or "")
                lines.append(f"{name}: {'; '.join(rec.get('warnings') or [])}")
            extra = len(with_warnings) - len(lines)
            if extra > 0:
                lines.append(f"... and {extra} more.")
            msg = QMessageBox(self.dlg)
            msg.setIcon(QMessageBox.Warning)
            msg.setWindowTitle("Georeference warnings")
            msg.setText("Potential georeference issues were detected.")
            msg.setInformativeText("\n".join(lines))
            import_all_btn = msg.addButton("Import All", QMessageBox.AcceptRole)
            skip_warned_btn = msg.addButton("Skip Problematic", QMessageBox.ActionRole)
            cancel_btn = msg.addButton(QMessageBox.Cancel)
            msg.setDefaultButton(import_all_btn)
            msg.exec_()
            clicked = msg.clickedButton()
            if clicked == cancel_btn:
                return _finish({"cancelled": True, "imported": 0, "skipped": skipped})
            if clicked == skip_warned_btn:
                before = len(records)
                records = [r for r in records if not r.get("warnings")]
                skipped += max(0, before - len(records))

        if not records:
            return _finish({"cancelled": False, "imported": 0, "skipped": skipped})

        import_task = TimesliceImportTask(
            project_root,
            records,
            description="RasterLinker: Importing selected images",
        )
        def _finalize(done_task):
            cancelled = bool(done_task.cancelled)
            linked_grids = int(done_task.linked_grids)
            failed = list(done_task.failed)
            imported_records = list(done_task.imported_records)
            imported_ids = []
            imported_count = 0

            if imported_records:
                try:
                    register_timeslices_batch(project_root, imported_records)
                    imported_ids = [rec.get("id") for rec in imported_records if rec.get("id")]
                    imported_count = len(imported_ids)
                except Exception as e:
                    failed.append(f"catalog write: {e}")
                    imported_ids = []
                    imported_count = 0

            if imported_ids:
                assign_timeslices_to_group(project_root, group.get("id"), imported_ids)
                try:
                    remove_timeslices_from_group(project_root, "grp_imported", imported_ids)
                except Exception:
                    pass

            local_skipped = skipped
            if failed:
                preview = "\n".join(failed[:10])
                extra = len(failed) - min(len(failed), 10)
                if extra > 0:
                    preview += f"\n... and {extra} more."
                QMessageBox.warning(
                    self.dlg,
                    "Import warning",
                    "Some files could not be imported:\n\n" + preview,
                )
                local_skipped += len(failed)

            return {
                "cancelled": cancelled,
                "imported": imported_count,
                "skipped": local_skipped,
                "linked_grids": linked_grids,
            }

        if callable(on_finished):
            if getattr(self, "_group_import_active", False):
                QMessageBox.warning(self.dlg, "Import running", "A group import is already running.")
                return _finish({"cancelled": True, "imported": 0, "skipped": skipped})
            self._group_import_active = True
            if hasattr(self.dlg, "createGroupButton"):
                self.dlg.createGroupButton.setEnabled(False)

            def _on_task_done(done_task, _ok):
                try:
                    result = _finalize(done_task)
                    _finish(result)
                finally:
                    self._group_import_active = False
                    if hasattr(self.dlg, "createGroupButton"):
                        self.dlg.createGroupButton.setEnabled(True)

            start_task_with_progress_dialog(
                import_task,
                self.dlg,
                "Importing selected images...",
                "Create Group - Import Images",
                on_finished=_on_task_done,
            )
            self.iface.messageBar().pushInfo(
                "RasterLinker",
                "Group image import started in background.",
            )
            return None

        run_task_with_progress_dialog(
            import_task,
            self.dlg,
            "Importing selected images...",
            "Create Group - Import Images",
        )
        return _finish(_finalize(import_task))

    def create_group(self):
        group_name = self.dlg.groupNameEdit.text().strip()
        if not group_name:
            QMessageBox.warning(self.dlg, "Error", "Group name cannot be empty.")
            return
        project_root = self._require_project_root()
        if not project_root:
            return
        try:
            group, created = create_raster_group(project_root, group_name)

            def _on_result(result):
                imported = int((result or {}).get("imported", 0))
                skipped = int((result or {}).get("skipped", 0))
                linked_grids = int((result or {}).get("linked_grids", 0))
                cancelled = bool((result or {}).get("cancelled", False))

                if imported > 0:
                    self._get_or_create_plugin_qgis_group(group_name)
                self.populate_group_list()
                if imported > 0:
                    self._select_group_item_by_id(group.get("id"))
                    self.load_raster(show_message=False)
                    status = "cancelled after partial import" if cancelled else "completed"
                    self.iface.messageBar().pushInfo(
                        "RasterLinker",
                        (
                            f"Group '{group_name}' import {status}: "
                            f"imported {imported}, skipped {skipped}, linked z-grids {linked_grids}."
                        ),
                    )
                    return

                if cancelled:
                    title = "Group created" if created else "Import cancelled"
                    QMessageBox.information(
                        self.dlg,
                        title,
                        (
                            f"Group '{group_name}' is available in the project catalog.\n"
                            "No images were imported."
                        ),
                    )
                    return

                if created:
                    QMessageBox.information(
                        self.dlg,
                        "Group created",
                        (
                            f"Group '{group_name}' was created in the project catalog.\n"
                            "It will appear in the plugin list after you import images into it."
                        ),
                    )
                else:
                    QMessageBox.information(
                        self.dlg,
                        "Information",
                        f"Group '{group_name}' is already present. No images imported.",
                    )

            self._quick_import_timeslices_for_group(project_root, group, on_finished=_on_result)
        except Exception as e:
            QMessageBox.critical(self.dlg, "Error", f"Error while creating group or importing images: {e}")

    def add_group_with_button(self, group_name):
        self.populate_group_list()

    def load_raster(self, show_message=True):
        """Load imported time-slices from selected plugin groups into QGIS layer tree."""
        try:
            project_root = self._require_project_root()
            if not project_root:
                return
            selected_groups = self.dlg.groupListWidget.selectedItems()
            if not selected_groups:
                if show_message:
                    QMessageBox.warning(self.dlg, "Error", "Select at least one plugin group.")
                return

            catalog = load_catalog(project_root)
            groups_by_id = {g.get("id"): g for g in catalog.get("raster_groups", [])}
            timeslices_by_id = {t.get("id"): t for t in catalog.get("timeslices", [])}
            existing_sources = {layer.source() for layer in QgsProject.instance().mapLayers().values()}
            target_crs = self._get_preferred_import_crs()
            loaded_count = 0

            for group_item in selected_groups:
                group_id = group_item.data(Qt.UserRole)
                group = groups_by_id.get(group_id)
                if not group:
                    continue
                group_name = group.get("name", "Group")
                style_path = (group.get("style_qml_path") or "").strip()
                qgis_group = self._get_or_create_plugin_qgis_group(group_name)

                for timeslice_id in group.get("timeslice_ids", []):
                    rec = timeslices_by_id.get(timeslice_id)
                    if not rec:
                        continue
                    path = rec.get("project_path")
                    if not path or not os.path.exists(path) or path in existing_sources:
                        continue
                    layer_name = rec.get("normalized_name") or os.path.basename(path)
                    raster_layer = QgsRasterLayer(path, layer_name)
                    if not raster_layer.isValid():
                        continue
                    assigned_authid = (rec.get("assigned_crs") or "").strip()
                    if assigned_authid:
                        try:
                            from qgis.core import QgsCoordinateReferenceSystem
                            assigned_crs = QgsCoordinateReferenceSystem(assigned_authid)
                            if assigned_crs.isValid():
                                raster_layer.setCrs(assigned_crs)
                        except Exception:
                            pass
                    elif not raster_layer.crs().isValid() and target_crs.isValid():
                        raster_layer.setCrs(target_crs)
                    if style_path and os.path.exists(style_path):
                        try:
                            raster_layer.loadNamedStyle(style_path)
                        except Exception:
                            pass
                    QgsProject.instance().addMapLayer(raster_layer, False)
                    qgis_group.addLayer(raster_layer)
                    existing_sources.add(path)
                    loaded_count += 1

            self.populate_raster_list_from_selected_groups()
            if show_message:
                QMessageBox.information(self.dlg, "Success", f"Time-slice caricate dal catalogo plugin: {loaded_count}")
        except Exception as e:
            if show_message:
                QMessageBox.critical(self.dlg, "Error", f"Error while loading rasters: {e}")

    def get_selected_group(self):
        selected_group_item = self.dlg.groupListWidget.currentItem()
        if not selected_group_item:
            QMessageBox.warning(self.dlg, "Error", "Select a group.")
            return None
        return selected_group_item.data(Qt.UserRole)

    def move_rasters(self):
        """Assign selected imported time-slices to currently selected plugin group."""
        selected_raster_items = self.dlg.rasterListWidget.selectedItems()
        selected_group_item = self.dlg.groupListWidget.currentItem()
        if not selected_raster_items or not selected_group_item:
            QMessageBox.warning(self.dlg, "Error", "Select rasters and target group.")
            return
        project_root = self._require_project_root()
        if not project_root:
            return

        target_group_id = selected_group_item.data(Qt.UserRole)
        timeslice_ids = []
        for item in selected_raster_items:
            payload = item.data(Qt.UserRole) or {}
            tid = payload.get("timeslice_id")
            if tid:
                timeslice_ids.append(tid)
        if not timeslice_ids:
            QMessageBox.warning(self.dlg, "Error", "No valid raster selected.")
            return
        try:
            assign_timeslices_to_group(project_root, target_group_id, timeslice_ids)
            self.populate_raster_list_from_selected_groups()
            QMessageBox.information(self.dlg, "Success", f"Assigned {len(timeslice_ids)} time-slices to group.")
        except Exception as e:
            QMessageBox.critical(self.dlg, "Error", f"Error while assigning raster(s): {e}")

