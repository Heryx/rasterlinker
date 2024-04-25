# -*- coding: utf-8 -*-
"""
/***************************************************************************
 GPRDialog
                                 A QGIS plugin
 GPR
 Generated by Plugin Builder: http://g-sherman.github.io/Qgis-Plugin-Builder/
                             -------------------
        begin                : 2024-04-23
        git sha              : $Format:%H$
        copyright            : (C) 2024 by Giuseppe
        email                : guarino.archeo@gmail.com
 ***************************************************************************/

/***************************************************************************
 *                                                                         *
 *   This program is free software; you can redistribute it and/or modify  *
 *   it under the terms of the GNU General Public License as published by  *
 *   the Free Software Foundation; either version 2 of the License, or     *
 *   (at your option) any later version.                                   *
 *                                                                         *
 ***************************************************************************/
"""

import os

from qgis.core import QgsProject, QgsRasterLayer, QgsLayerTreeGroup, QgsLayerTreeLayer
from qgis.PyQt import QtWidgets, QtCore
from qgis.PyQt.QtWidgets import QDialogButtonBox, QFileDialog
from qgis.PyQt import uic
from PyQt5.QtGui import QStandardItem, QStandardItemModel

# This loads your .ui file so that PyQt can populate your plugin with the elements from Qt Designer
FORM_CLASS, _ = uic.loadUiType(os.path.join(
    os.path.dirname(__file__), 'gpr_linker_dialog_base.ui'))


class GPRDialog(QtWidgets.QDialog, FORM_CLASS):
    def __init__(self, parent=None):
        """Constructor."""
        super(GPRDialog, self).__init__(parent)
        # Set up the user interface from Designer through FORM_CLASS.
        # After self.setupUi() you can access any designer object by doing
        # self.<objectname>, and you can use autoconnect slots - see
        # http://qt-project.org/doc/qt-4.8/designer-using-a-ui-file.html
        # #widgets-and-dialogs-with-auto-connect

        self.setupUi(self)

        # Initialize the standard item model for the list view
        self.list_model = QStandardItemModel()
        self.listView.setModel(self.list_model)  # Set the model for the list view
        
        # Connect the "Sfoglia" button
        self.Sfoglia.clicked.connect(self.browse_rasters)

        # Find the dial in the dialog layout
        self.dial = self.findChild(QtWidgets.QDial, "dial")
        self.dial.setEnabled(False)  # Disable the dial initially

        # Connect the valueChanged signal of the dial to the toggle_raster_visibility method
        self.dial.valueChanged.connect(self.toggle_raster_visibility)

        # Connect the signal for the checkbox state change
        self.listView.clicked.connect(self.toggle_group_visibility)

    def populate_group_checkbox_list(self):
        """Populate the QListView with checkboxes for each group in the TOC."""
        root = QgsProject.instance().layerTreeRoot()
        self.list_model.clear()  # Clear the model

        for child in root.children():
            if isinstance(child, QgsLayerTreeGroup):
                item = QStandardItem(child.name())
                item.setCheckable(True)  # Make the item checkable
                self.list_model.appendRow(item)

    def browse_rasters(self):
        """Open the file dialog to select raster files."""
        files, _ = QFileDialog.getOpenFileNames(self, "Select raster files", "/", "Raster files (*.tif *.tiff)")
        if files:
            print("Selected raster files:", files)
            # Perform desired operations with the selected raster files
            for file in files:
                layer = QgsRasterLayer(file, os.path.basename(file))
                if layer.isValid():
                    QgsProject.instance().addMapLayer(layer)
                else:
                    print(f"Unable to load raster file: {file}")

            # Update the group list after loading the raster files
            self.populate_group_checkbox_list()

            # Set the range of the dial based on the number of raster layers in the group
            self.update_dial_range()

    def import_rasters(self):
        """Open the file dialog to select raster files and create a new group."""
        files, _ = QFileDialog.getOpenFileNames(self, "Select raster files", "/", "Raster files (*.tif *.tiff)")
        if files:
            # Ask the user to input the group name
            group_name, ok = QtWidgets.QInputDialog.getText(self, "Enter Group Name", "Enter the name for the new group:")
            if ok and group_name:
                # Create the new group
                root = QgsProject.instance().layerTreeRoot()
                group = root.addGroup(group_name)
                if group:
                    # Load raster files into the new group
                    for file in files:
                        layer = QgsRasterLayer(file, os.path.basename(file))
                        if layer.isValid():
                            QgsProject.instance().addMapLayer(layer, False)
                            group.insertChildNode(0, QgsLayerTreeLayer(layer))
                        else:
                            print(f"Unable to load raster file: {file}")
                else:
                    QMessageBox.warning(self, "Error", "Failed to create the group.")
            else:
                QMessageBox.warning(self, "Error", "Group name not provided.")

    def load_rasters_into_group(self, raster_files, group_name):
        """Load raster files into the specified group."""
        group = QgsProject.instance().layerTreeRoot().findGroup(group_name)
        if group:
            for file in raster_files:
                layer = QgsRasterLayer(file, os.path.basename(file))
                if layer.isValid():
                    QgsProject.instance().addMapLayer(layer, False)
                    group.insertChildNode(0, QgsLayerTreeLayer(layer))
                else:
                    print(f"Unable to load raster file: {file}")
        else:
            print(f"No group found with name: {group_name}")

    def populate_group_list(self):
        """Populate the QListView with items for each group in the TOC."""
        model = QtWidgets.QStandardItemModel()
        root = QgsProject.instance().layerTreeRoot()

        for child in root.children():
            if isinstance(child, QgsLayerTreeGroup):
                item = QtWidgets.QStandardItem(child.name())
                model.appendRow(item)

        self.listView.setModel(model)

    def toggle_group_visibility(self, index):
        """Toggle group visibility based on the checkbox state."""
        item = self.list_model.itemFromIndex(index)
        if item is not None:
            if item.checkState() == QtCore.Qt.Checked:
                # Enable the dial when at least one group is checked
                self.dial.setEnabled(True)
            else:
                # Disable the dial if no group is checked
                self.dial.setEnabled(False)

    def toggle_raster_visibility(self, value):
        """Toggle raster visibility based on the dial value."""
        selected_index = self.listView.selectedIndexes()
        if selected_index:
            group_name = selected_index[0].data()
            group = QgsProject.instance().layerTreeRoot().findGroup(group_name)
            if group:
                layer_nodes = [child for child in group.children() if isinstance(child, QgsLayerTreeLayer)]
                if layer_nodes:
                    # Disable the previous raster
                    previous_index = value - 1 if value > 0 else len(layer_nodes) - 1
                    if previous_index < len(layer_nodes):
                        previous_layer_node = layer_nodes[previous_index]
                        previous_layer_node.setItemVisibilityChecked(False)

                    # Enable the current raster
                    current_index = value
                    if current_index < len(layer_nodes):
                        current_layer_node = layer_nodes[current_index]
                        current_layer_node.setItemVisibilityChecked(True)
                else:
                    print("No raster layers in the selected group.")
            else:
                print(f"No group found with name: {group_name}")
        else:
            print("No group selected.")

    def update_dial_range(self):
        """Update the range of the dial based on the number of raster layers in the selected group."""
        selected_index = self.listView.selectedIndexes()
        if selected_index:
            group_name = selected_index[0].data()
            group = QgsProject.instance().layerTreeRoot().findGroup(group_name)
            if group:
                layer_nodes = [child for child in group.children() if isinstance(child, QgsLayerTreeLayer)]
                if layer_nodes:
                    self.dial.setRange(0, len(layer_nodes) - 1)
                else:
                    print("No raster layers in the selected group.")
            else:
                print(f"No group found with name: {group_name}")
        else:
            print("No group selected.")
