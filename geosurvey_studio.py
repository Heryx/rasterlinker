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
from .grid_workflow_mixin import GridWorkflowMixin
from .ui_layout_mixin import UiLayoutMixin
from .app_runtime_mixin import AppRuntimeMixin
from .update_checker_mixin import UpdateCheckerMixin

from .resources import *
import os.path


class GeoSurveyStudioPlugin(
    UpdateCheckerMixin,
    AppRuntimeMixin,
    CatalogGroupMixin,
    CatalogToolsMixin,
    GridWorkflowMixin,
    UiLayoutMixin,
    TraceInfoMixin,
    TraceCaptureMixin,
    TraceBuild3DMixin,
    TraceToolsMixin,
):
    """QGIS Plugin Implementation."""

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

        icon_path = ':/plugins/geosurvey_studio/icon.png'
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
        if self.trace_toolbar is not None:
            try:
                self.iface.mainWindow().removeToolBar(self.trace_toolbar)
            except Exception as e:
                QgsMessageLog.logMessage(str(e), "GeoSurvey Studio", Qgis.Warning)
            self.trace_toolbar.deleteLater()
            self.trace_toolbar = None
            self.trace_toolbar_actions = {}
        if self.trace_info_dock is not None:
            try:
                self.iface.removeDockWidget(self.trace_info_dock)
            except Exception as e:
                QgsMessageLog.logMessage(str(e), "GeoSurvey Studio", Qgis.Warning)
            self.trace_info_dock.deleteLater()
            self.trace_info_dock = None
            self.trace_info_table = None
            self.trace_info_model = None
            self.trace_info_filter_edit = None
            self.trace_info_filter_field_combo = None
            self.trace_info_mode_combo = None
            self.trace_info_sort_field_combo = None
            self.trace_info_sort_order_combo = None
            self.trace_info_depth_pick_combo = None
            self.trace_info_depth_pick_btn = None
            self.trace_depth_pick_mode = "off"
            self.trace_info_stack = None
            self.trace_info_form_list = None
            self.trace_info_form_fields = {}
            self.trace_info_vertex_table = None
            self.trace_info_source_layer_id = None
            self.trace_info_form_preview_combo = None
            self.trace_info_form_preview_key = "timeslice"
            self.trace_info_view_table_btn = None
            self.trace_info_view_form_btn = None
            self.trace_info_query_btn = None
            self.trace_info_query_panel = None
            self.trace_info_interpretation_prompt_action = None
            self.trace_info_discard_outside_raster_action = None
            self.trace_info_help_btn = None
            self.trace_info_help_panel = None
            self.trace_info_selection_guard = False
            self.trace_info_is_docked = False
        if self.trace_canvas_click_filter is not None:
            try:
                canvas = self.iface.mapCanvas()
                if canvas is not None:
                    if canvas.viewport() is not None:
                        canvas.viewport().removeEventFilter(self.trace_canvas_click_filter)
                    canvas.removeEventFilter(self.trace_canvas_click_filter)
            except Exception as e:
                QgsMessageLog.logMessage(str(e), "GeoSurvey Studio", Qgis.Warning)
            self.trace_canvas_click_filter = None
            self.trace_canvas_click_capture_enabled = False
            self.trace_pending_vertex_clicks = []
            self.trace_canvas_wheel_modifier = "alt"
        self.trace_interpretation_prompted_keys = set()
        self.trace_interpretation_prompted_trace_ids = set()
        self.trace_draw_session_state = "idle"
        self.trace_postprocess_inflight = set()
        self.trace_postprocess_done = set()
        self.trace_postprocess_done_trace_ids = set()
        if self.orientation_helper_dialog is not None:
            try:
                self.orientation_helper_dialog.hide()
            except Exception as e:
                QgsMessageLog.logMessage(str(e), "GeoSurvey Studio", Qgis.Warning)
            try:
                self.orientation_helper_dialog.deleteLater()
            except Exception as e:
                QgsMessageLog.logMessage(str(e), "GeoSurvey Studio", Qgis.Warning)
            self.orientation_helper_dialog = None
            self.orientation_helper_status_label = None
            self.orientation_helper_edits = {}
            self._orientation_helper_syncing = False
        self.trace_z_grid_cache = {}
