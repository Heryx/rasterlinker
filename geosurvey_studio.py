# -*- coding: utf-8 -*-
"""GeoSurvey Studio plugin entrypoint and GUI lifecycle."""

import os.path

from qgis.PyQt.QtCore import QSettings, QCoreApplication
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction, QMessageBox
from qgis.core import QgsMessageLog, Qgis
from .trace_tools_mixin import TraceToolsMixin
from .trace_info_mixin import TraceInfoMixin
from .trace_capture_mixin import TraceCaptureMixin
from .trace_build3d_mixin import TraceBuild3DMixin
from .catalog_group_mixin import CatalogGroupMixin
from .catalog_tools_mixin import CatalogToolsMixin
from .gpr_volume_mixin import GprVolumeMixin
from .gpr_ogpr_volume_mixin import GprOgprVolumeMixin
from .grid_workflow_mixin import GridWorkflowMixin
from .ui_layout_mixin import UiLayoutMixin
from .app_runtime_mixin import AppRuntimeMixin
from .update_checker_mixin import UpdateCheckerMixin

try:
    from .gpr_profile_viewer import GprProfileViewer
    _HAS_PROFILE_VIEWER = True
except Exception:
    _HAS_PROFILE_VIEWER = False

try:
    from .resources import *  # noqa: F401,F403
    _HAS_QT_RESOURCES = True
except Exception:
    _HAS_QT_RESOURCES = False


class GeoSurveyStudioPlugin(
    UpdateCheckerMixin,
    AppRuntimeMixin,
    CatalogGroupMixin,
    CatalogToolsMixin,
    GprVolumeMixin,
    GprOgprVolumeMixin,
    GridWorkflowMixin,
    UiLayoutMixin,
    TraceInfoMixin,
    TraceCaptureMixin,
    TraceBuild3DMixin,
    TraceToolsMixin,
):
    """QGIS Plugin implementation with explicit mixin resolution order.

    MRO (left-to-right precedence among top-level mixins):
    1. UpdateCheckerMixin
    2. AppRuntimeMixin
    3. CatalogGroupMixin
    4. CatalogToolsMixin
    5. GprVolumeMixin
    6. GprOgprVolumeMixin
    7. GridWorkflowMixin
    8. UiLayoutMixin
    9. TraceInfoMixin
    10. TraceCaptureMixin
    11. TraceBuild3DMixin
    12. TraceToolsMixin

    Potential method-name conflicts to watch:
    - `_safe_float`: defined in `CatalogToolsMixin` and in the TraceCapture branch
      (`TraceCaptureSnappingMixin`). With the current MRO, `CatalogToolsMixin`
      wins. Current implementations are behaviorally equivalent.
    """

    def __init__(self, iface):
        """Constructor.

        Core plugin identity and QGIS wiring are set here.
        Domain-specific state is delegated to each mixin via _init_*() calls,
        so that every mixin owns and documents its own attributes.
        """
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)
        self.actions = []
        self.plugin_toolbar = None
        self.brand_name = "GeoSurvey Studio"
        self.menu = self.tr(u'&GeoSurvey Studio')
        self.first_start = None
        self.dlg = None
        self.dock_widget = None
        self.settings = QSettings()
        self.settings_group = "GeoSurveyStudio"
        self.settings_key_active_project = "GeoSurveyStudio/active_project_root"
        self.settings_key_default_import_crs = "GeoSurveyStudio/default_import_crs_authid"
        self.settings_key_deps_checked_version = "GeoSurveyStudio/deps_checked_version"
        self.plugin_layer_root_name = "GeoSurvey Studio"
        self.project_manager_dialog = None
        self.pending_vector_storage_mode = None
        self._deps_checked_this_session = False

        # GPR Profile Viewer (finestra indipendente)
        self._gpr_profile_viewer = None

        # Mixin state initialisation – each mixin owns and resets its own attributes.
        self._init_grid_state()
        self._init_ui_layout()
        self._init_trace_tools()
        self._init_trace_info()
        self._init_trace_capture()
        self._init_update_checker()
        # Stato volume GPR (inizializzazione sicura pre-dialogo)
        self._gpr_pc_layer = None
        self._gpr_z_min = 0.0
        self._gpr_z_step = 0.05
        self._gpr_n_slices = 0

    # Translation helper
    def tr(self, message):
        return QCoreApplication.translate('GeoSurveyStudio', message)

    # Add actions to the toolbar/menu
    def add_action(self, icon_path, text, callback, parent=None):
        icon = QIcon(icon_path)
        action = QAction(icon, text, parent)
        action.triggered.connect(callback)
        if self.plugin_toolbar is not None:
            self.plugin_toolbar.addAction(action)
        else:
            self.iface.addToolBarIcon(action)
        self.iface.addPluginToMenu(self.menu, action)
        self.actions.append(action)
        return action

    def initGui(self):
        """Initialize the GUI."""
        if self.plugin_toolbar is None:
            self.plugin_toolbar = self.iface.addToolBar(self.tr(u"GeoSurvey Studio"))
            self.plugin_toolbar.setObjectName("GeoSurveyStudioMainToolbar")
            self.plugin_toolbar.setToolTip("GeoSurvey Studio tools")

        if _HAS_QT_RESOURCES:
            icon_path = ':/plugins/geosurvey_studio/icon.png'
        else:
            icon_path = os.path.join(self.plugin_dir, "icon.png")
        self.add_action(icon_path, text=self.tr(u'GeoSurvey Studio'), callback=self.run, parent=self.iface.mainWindow())
        pm_action = self.add_action(
            icon_path,
            text=self.tr(u'GeoSurvey Studio Project Manager'),
            callback=self.open_project_manager,
            parent=self.iface.mainWindow(),
        )
        pm_icon_path = os.path.join(self.plugin_dir, "icon2.png")
        if os.path.exists(pm_icon_path):
            pm_action.setIcon(QIcon(pm_icon_path))
        self._ensure_trace_actions()
        self.trace_info_action = self.add_action(
            icon_path,
            text=self.tr(u'2D/3D Draw Panel'),
            callback=self.open_trace_info_tab,
            parent=self.iface.mainWindow(),
        )
        pencil_icon = self._qgis_theme_icon("mActionToggleEditing.svg", "mActionAddFeature.svg")
        if pencil_icon is not None and not pencil_icon.isNull():
            self.trace_info_action.setIcon(pencil_icon)

        # --- GPR Profile Viewer ---
        if _HAS_PROFILE_VIEWER:
            self.gpr_viewer_action = self.add_action(
                icon_path,
                text=self.tr(u'GPR Profile Viewer'),
                callback=self.open_gpr_profile_viewer,
                parent=self.iface.mainWindow(),
            )
            radar_icon = self._qgis_theme_icon("mIconRasterLayer.svg", "mIconRaster.svg")
            if radar_icon is not None and not radar_icon.isNull():
                self.gpr_viewer_action.setIcon(radar_icon)

        self.check_updates_action = self.add_action(
            icon_path,
            text=self.tr(u'Check for Updates'),
            callback=self.check_for_updates_manual,
            parent=self.iface.mainWindow(),
        )
        refresh_icon = self._qgis_theme_icon("mActionRefresh.svg", "mActionReload.svg")
        if refresh_icon is not None and not refresh_icon.isNull():
            self.check_updates_action.setIcon(refresh_icon)
        self.check_dependencies_action = self.add_action(
            icon_path,
            text=self.tr(u'Riesegui check dipendenze'),
            callback=self.run_dependency_check_manual,
            parent=self.iface.mainWindow(),
        )
        deps_icon = self._qgis_theme_icon("mActionOptions.svg", "mActionRefresh.svg")
        if deps_icon is not None and not deps_icon.isNull():
            self.check_dependencies_action.setIcon(deps_icon)
        self.first_start = True
        self._maybe_run_startup_dependency_check()

    def _plugin_version(self) -> str:
        """Read plugin version from metadata.txt."""
        metadata_path = os.path.join(self.plugin_dir, "metadata.txt")
        try:
            with open(metadata_path, "r", encoding="utf-8") as fh:
                for raw in fh:
                    line = raw.strip()
                    if line.lower().startswith("version="):
                        value = line.split("=", 1)[1].strip()
                        if value:
                            return value
        except Exception:
            pass
        return "unknown"

    def _maybe_run_startup_dependency_check(self):
        """Run dependency check once per plugin version and ask before install."""
        if self._deps_checked_this_session:
            return
        self._deps_checked_this_session = True

        current_version = self._plugin_version()
        checked_version = (
            self.settings.value(self.settings_key_deps_checked_version, "", type=str) or ""
        ).strip()
        if checked_version == current_version:
            return

        self._run_dependency_check_flow(manual=False)
        self.settings.setValue(self.settings_key_deps_checked_version, current_version)

    def run_dependency_check_manual(self):
        """User-triggered dependency check from plugin menu/toolbar."""
        self._run_dependency_check_flow(manual=True)

    def _run_dependency_check_flow(self, manual: bool = False):
        """Shared dependency check flow with optional install prompt."""
        try:
            from .gpr_utils import check_runtime_dependencies, format_runtime_dependency_report

            initial_report = check_runtime_dependencies(auto_install=False)
            missing_python = initial_report.get("missing_python", [])
            initial_text = format_runtime_dependency_report(initial_report)
            if not missing_python:
                if manual:
                    QMessageBox.information(
                        self.iface.mainWindow(),
                        "GeoSurvey Studio - Check dipendenze",
                        "Risultato controllo dipendenze:\n\n" + initial_text,
                    )
                elif not initial_report.get("pdal", {}).get("ok"):
                    QMessageBox.information(
                        self.iface.mainWindow(),
                        "GeoSurvey Studio - PDAL non trovato",
                        "Le librerie Python risultano disponibili, ma PDAL non e' nel PATH.\n"
                        "Le funzioni point-cloud che dipendono da PDAL potrebbero non funzionare.",
                    )
                return

            missing_lines = []
            for item in missing_python:
                name = item.get("name") or item.get("import_name") or "unknown"
                pip_spec = item.get("pip_spec") or name
                missing_lines.append(f"- {name} (pip: {pip_spec})")

            prompt_text = (
                "Sono state trovate librerie Python mancanti richieste dal plugin:\n\n"
                + "\n".join(missing_lines)
                + "\n\nVuoi procedere con l'installazione automatica adesso?"
            )

            answer = QMessageBox.question(
                self.iface.mainWindow(),
                "GeoSurvey Studio - Dipendenze mancanti",
                prompt_text,
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )

            if answer == QMessageBox.Yes:
                final_report = check_runtime_dependencies(auto_install=True)
                report_text = format_runtime_dependency_report(final_report)
                if final_report.get("missing_python"):
                    QMessageBox.warning(
                        self.iface.mainWindow(),
                        "GeoSurvey Studio - Installazione incompleta",
                        "Alcune librerie non sono state installate correttamente.\n\n"
                        + report_text,
                    )
                else:
                    QMessageBox.information(
                        self.iface.mainWindow(),
                        "GeoSurvey Studio - Dipendenze",
                        "Controllo dipendenze completato.\n"
                        "Se alcune librerie sono state appena installate, riavvia QGIS.\n\n"
                        + report_text,
                    )
            else:
                manual_hint = "\n".join(
                    f"pip install {item.get('pip_spec') or item.get('name')}"
                    for item in missing_python
                )
                QMessageBox.information(
                    self.iface.mainWindow(),
                    "GeoSurvey Studio - Installazione rimandata",
                    "Installazione automatica annullata.\n"
                    "Potrai installare manualmente da OSGeo4W Shell:\n\n"
                    + manual_hint,
                )
        except Exception as e:
            QgsMessageLog.logMessage(
                f"Dependency check startup error: {e}",
                "GeoSurvey Studio",
                level=Qgis.Warning,
            )

    def open_gpr_profile_viewer(self):
        """Apre (o porta in primo piano) il GPR Profile Viewer."""
        if not _HAS_PROFILE_VIEWER:
            from qgis.PyQt.QtWidgets import QMessageBox
            QMessageBox.warning(
                self.iface.mainWindow(),
                "GPR Profile Viewer",
                "matplotlib non trovato.\n"
                "Installa con: pip install matplotlib scipy"
            )
            return
        viewer_alive = False
        if self._gpr_profile_viewer is not None:
            try:
                self._gpr_profile_viewer.objectName()
                viewer_alive = True
            except RuntimeError:
                viewer_alive = False
            except Exception:
                viewer_alive = False
        if (self._gpr_profile_viewer is None) or (not viewer_alive):
            self._gpr_profile_viewer = GprProfileViewer(
                iface=self.iface,
                plugin=self,
                parent=self.iface.mainWindow(),
            )
            try:
                self._gpr_profile_viewer.destroyed.connect(lambda *_a: setattr(self, "_gpr_profile_viewer", None))
            except Exception:
                pass
        self._gpr_profile_viewer.show()
        self._gpr_profile_viewer.raise_()
        self._gpr_profile_viewer.activateWindow()

    def unload(self):
        """Unload the plugin."""
        if self.dlg is not None:
            self._save_ui_settings()
        # Chiudi viewer GPR se aperto
        if self._gpr_profile_viewer is not None:
            try:
                if hasattr(self._gpr_profile_viewer, "request_close"):
                    self._gpr_profile_viewer.request_close()
                else:
                    self._gpr_profile_viewer.close()
            except Exception:
                pass
            self._gpr_profile_viewer = None
        for action in self.actions:
            self.iface.removePluginMenu(self.tr(u'&GeoSurvey Studio'), action)
            if self.plugin_toolbar is None:
                self.iface.removeToolBarIcon(action)
            else:
                try:
                    self.plugin_toolbar.removeAction(action)
                except Exception as e:
                    QgsMessageLog.logMessage(str(e), "GeoSurvey Studio", Qgis.Warning)
        self.actions = []
        if self.plugin_toolbar is not None:
            try:
                self.iface.mainWindow().removeToolBar(self.plugin_toolbar)
            except Exception as e:
                QgsMessageLog.logMessage(str(e), "GeoSurvey Studio", Qgis.Warning)
            self.plugin_toolbar.deleteLater()
            self.plugin_toolbar = None
        if self.dock_widget is not None:
            self.iface.removeDockWidget(self.dock_widget)
            self.dock_widget.deleteLater()
            self.dock_widget = None
            self.dlg = None
        self._cleanup_trace_tools()
        self._cleanup_trace_info()
        self._cleanup_trace_capture()
        self._cleanup_grid_state()
