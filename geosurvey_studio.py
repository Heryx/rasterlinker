# -*- coding: utf-8 -*-
"""GeoSurvey Studio plugin entrypoint and GUI lifecycle."""

from qgis.PyQt.QtCore import QSettings, QCoreApplication
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction
from qgis.core import QgsMessageLog, Qgis
from .trace_tools_mixin import TraceToolsMixin
from .trace_info_mixin import TraceInfoMixin
from .trace_capture_mixin import TraceCaptureMixin
from .trace_build3d_mixin import TraceBuild3DMixin
from .catalog_group_mixin import CatalogGroupMixin
from .catalog_tools_mixin import CatalogToolsMixin
from .gpr_volume_mixin import GprVolumeMixin
from .grid_workflow_mixin import GridWorkflowMixin
from .ui_layout_mixin import UiLayoutMixin
from .app_runtime_mixin import AppRuntimeMixin
from .update_checker_mixin import UpdateCheckerMixin

import os.path

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
    5. GridWorkflowMixin
    6. UiLayoutMixin
    7. TraceInfoMixin
    8. TraceCaptureMixin
    9. TraceBuild3DMixin
    10. TraceToolsMixin

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
        self.plugin_layer_root_name = "GeoSurvey Studio"
        self.project_manager_dialog = None
        self.pending_vector_storage_mode = None

        # Mixin state initialisation – each mixin owns and resets its own attributes.
        self._init_grid_state()
        self._init_ui_layout()
        self._init_trace_tools()
        self._init_trace_info()
        self._init_trace_capture()
        self._init_update_checker()

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

        self.check_updates_action = self.add_action(
            icon_path,
            text=self.tr(u'Check for Updates'),
            callback=self.check_for_updates_manual,
            parent=self.iface.mainWindow(),
        )
        refresh_icon = self._qgis_theme_icon("mActionRefresh.svg", "mActionReload.svg")
        if refresh_icon is not None and not refresh_icon.isNull():
            self.check_updates_action.setIcon(refresh_icon)
        self.first_start = True

    def unload(self):
        """Unload the plugin."""
        if self.dlg is not None:
            self._save_ui_settings()
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
