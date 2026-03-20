# -*- coding: utf-8 -*-

from PyQt5 import QtCore, QtGui, QtWidgets


class Ui_Dialog(object):
    """Single-source UI base for the main GeoSurvey Studio dock dialog.

    Notes:
    - Keep object names stable because runtime mixins bind to these names.
    - This file is now maintained manually to avoid geometry drift caused by
      repeated runtime re-parenting on top of legacy absolute positions.
    """

    def setupUi(self, Dialog):
        Dialog.setObjectName("Dialog")
        Dialog.resize(920, 720)
        Dialog.setMinimumSize(QtCore.QSize(620, 520))

        self.dialogLayout = QtWidgets.QVBoxLayout(Dialog)
        self.dialogLayout.setContentsMargins(2, 2, 2, 2)
        self.dialogLayout.setSpacing(4)
        self.dialogLayout.setObjectName("dialogLayout")

        self.layoutWidget = QtWidgets.QWidget(Dialog)
        self.layoutWidget.setObjectName("layoutWidget")
        self.dialogLayout.addWidget(self.layoutWidget, 1)

        self.gridLayout_3 = QtWidgets.QGridLayout(self.layoutWidget)
        self.gridLayout_3.setContentsMargins(0, 0, 0, 0)
        self.gridLayout_3.setHorizontalSpacing(10)
        self.gridLayout_3.setVerticalSpacing(8)
        self.gridLayout_3.setObjectName("gridLayout_3")

        # Left column ---------------------------------------------------------
        self.verticalLayout_3 = QtWidgets.QVBoxLayout()
        self.verticalLayout_3.setContentsMargins(0, 0, 0, 0)
        self.verticalLayout_3.setSpacing(6)
        self.verticalLayout_3.setObjectName("verticalLayout_3")

        self.labelSelezionaRaster = QtWidgets.QLabel(self.layoutWidget)
        font = QtGui.QFont()
        font.setBold(True)
        font.setWeight(75)
        self.labelSelezionaRaster.setFont(font)
        self.labelSelezionaRaster.setObjectName("labelSelezionaRaster")
        self.verticalLayout_3.addWidget(self.labelSelezionaRaster)

        self.rasterListWidget = QtWidgets.QListWidget(self.layoutWidget)
        self.rasterListWidget.setSelectionMode(QtWidgets.QAbstractItemView.MultiSelection)
        self.rasterListWidget.setObjectName("rasterListWidget")
        self.verticalLayout_3.addWidget(self.rasterListWidget, 1)

        self.labelseleziona = QtWidgets.QLabel(self.layoutWidget)
        self.labelseleziona.setFont(font)
        self.labelseleziona.setObjectName("labelseleziona")
        self.verticalLayout_3.addWidget(self.labelseleziona)

        self.groupListWidget = QtWidgets.QListWidget(self.layoutWidget)
        self.groupListWidget.setSelectionMode(QtWidgets.QAbstractItemView.MultiSelection)
        self.groupListWidget.setSelectionRectVisible(False)
        self.groupListWidget.setObjectName("groupListWidget")
        self.verticalLayout_3.addWidget(self.groupListWidget, 1)

        self.dial2 = QtWidgets.QDial(self.layoutWidget)
        self.dial2.setNotchesVisible(True)
        self.dial2.setNotchTarget(3.0)
        self.dial2.setObjectName("dial2")
        self.verticalLayout_3.addWidget(self.dial2, 0, QtCore.Qt.AlignHCenter)

        self.Dial = QtWidgets.QSlider(self.layoutWidget)
        self.Dial.setOrientation(QtCore.Qt.Horizontal)
        self.Dial.setObjectName("Dial")
        self.verticalLayout_3.addWidget(self.Dial)

        # Runtime drawing-options host (filled by grid_options_ui at runtime).
        self.widget = QtWidgets.QWidget(self.layoutWidget)
        self.widget.setObjectName("widget")
        self.horizontalLayout_2 = QtWidgets.QHBoxLayout(self.widget)
        self.horizontalLayout_2.setContentsMargins(0, 0, 0, 0)
        self.horizontalLayout_2.setSpacing(0)
        self.horizontalLayout_2.setObjectName("horizontalLayout_2")
        self.verticalLayout_3.addWidget(self.widget)

        self.gridLayout_3.addLayout(self.verticalLayout_3, 0, 0, 1, 1)

        self.line = QtWidgets.QFrame(self.layoutWidget)
        self.line.setFrameShape(QtWidgets.QFrame.VLine)
        self.line.setFrameShadow(QtWidgets.QFrame.Sunken)
        self.line.setObjectName("line")
        self.gridLayout_3.addWidget(self.line, 0, 1, 1, 1)

        # Right column host ---------------------------------------------------
        self.toolsHostWidget = QtWidgets.QWidget(self.layoutWidget)
        self.toolsHostWidget.setObjectName("toolsHostWidget")
        self.gridLayout = QtWidgets.QGridLayout(self.toolsHostWidget)
        self.gridLayout.setContentsMargins(0, 0, 0, 0)
        self.gridLayout.setHorizontalSpacing(8)
        self.gridLayout.setVerticalSpacing(6)
        self.gridLayout.setObjectName("gridLayout")

        self.zoomSelectedGroupsButton = QtWidgets.QPushButton(self.toolsHostWidget)
        self.zoomSelectedGroupsButton.setObjectName("zoomSelectedGroupsButton")
        self.gridLayout.addWidget(self.zoomSelectedGroupsButton, 0, 0, 1, 1)

        self.createGroupButton = QtWidgets.QPushButton(self.toolsHostWidget)
        self.createGroupButton.setObjectName("createGroupButton")
        self.gridLayout.addWidget(self.createGroupButton, 0, 1, 1, 1)

        self.groupNameEdit = QtWidgets.QLineEdit(self.toolsHostWidget)
        self.groupNameEdit.setObjectName("groupNameEdit")
        self.gridLayout.addWidget(self.groupNameEdit, 1, 0, 1, 2)

        self.gridLayout_3.addWidget(self.toolsHostWidget, 0, 2, 1, 1)

        # Hidden legacy controls (re-parented by runtime into bottom panel) ---
        self.legacyControls = QtWidgets.QWidget(Dialog)
        self.legacyControls.setObjectName("legacyControls")
        self.legacyControls.setVisible(False)
        self.legacyLayout = QtWidgets.QGridLayout(self.legacyControls)
        self.legacyLayout.setContentsMargins(0, 0, 0, 0)
        self.legacyLayout.setHorizontalSpacing(8)
        self.legacyLayout.setVerticalSpacing(4)
        self.legacyLayout.setObjectName("legacyLayout")

        self.selectGridPointsButton = QtWidgets.QPushButton(self.legacyControls)
        self.selectGridPointsButton.setEnabled(False)
        self.selectGridPointsButton.setObjectName("selectGridPointsButton")
        self.legacyLayout.addWidget(self.selectGridPointsButton, 0, 0, 1, 1)

        self.createGridButton = QtWidgets.QPushButton(self.legacyControls)
        self.createGridButton.setEnabled(False)
        self.createGridButton.setObjectName("createGridButton")
        self.legacyLayout.addWidget(self.createGridButton, 0, 1, 1, 1)

        self.lineEditX0Y0 = QtWidgets.QLineEdit(self.legacyControls)
        self.lineEditX0Y0.setEnabled(False)
        self.lineEditX0Y0.setObjectName("lineEditX0Y0")
        self.legacyLayout.addWidget(self.lineEditX0Y0, 2, 0, 1, 1)
        self.lineEditX1Y0 = QtWidgets.QLineEdit(self.legacyControls)
        self.lineEditX1Y0.setEnabled(False)
        self.lineEditX1Y0.setObjectName("lineEditX1Y0")
        self.legacyLayout.addWidget(self.lineEditX1Y0, 2, 1, 1, 1)
        self.lineEditY0 = QtWidgets.QLineEdit(self.legacyControls)
        self.lineEditY0.setEnabled(False)
        self.lineEditY0.setObjectName("lineEditY0")
        self.legacyLayout.addWidget(self.lineEditY0, 4, 0, 1, 1)
        self.lineEditX0Y1 = QtWidgets.QLineEdit(self.legacyControls)
        self.lineEditX0Y1.setEnabled(False)
        self.lineEditX0Y1.setObjectName("lineEditX0Y1")
        self.legacyLayout.addWidget(self.lineEditX0Y1, 4, 1, 1, 1)

        self.lineEditAreaNames = QtWidgets.QLineEdit(self.legacyControls)
        self.lineEditAreaNames.setEnabled(False)
        self.lineEditAreaNames.setObjectName("lineEditAreaNames")
        self.legacyLayout.addWidget(self.lineEditAreaNames, 6, 0, 1, 1)
        self.lineEditDistanceX = QtWidgets.QLineEdit(self.legacyControls)
        self.lineEditDistanceX.setEnabled(False)
        self.lineEditDistanceX.setObjectName("lineEditDistanceX")
        self.legacyLayout.addWidget(self.lineEditDistanceX, 6, 1, 1, 1)
        self.lineEditDistanceY = QtWidgets.QLineEdit(self.legacyControls)
        self.lineEditDistanceY.setEnabled(False)
        self.lineEditDistanceY.setObjectName("lineEditDistanceY")
        self.legacyLayout.addWidget(self.lineEditDistanceY, 6, 2, 1, 1)

        self.gridLayout_3.setColumnStretch(0, 5)
        self.gridLayout_3.setColumnStretch(1, 0)
        self.gridLayout_3.setColumnStretch(2, 4)
        # Keep top content compact; free vertical space is handled by runtime filler row.
        self.gridLayout_3.setRowStretch(0, 0)

        self.retranslateUi(Dialog)
        QtCore.QMetaObject.connectSlotsByName(Dialog)

    def retranslateUi(self, Dialog):
        _translate = QtCore.QCoreApplication.translate
        Dialog.setWindowTitle(_translate("Dialog", "GeoSurvey Studio"))
        self.labelSelezionaRaster.setText(_translate("Dialog", "Raster list"))
        self.labelseleziona.setText(_translate("Dialog", "Group list"))
        self.zoomSelectedGroupsButton.setText(_translate("Dialog", "Zoom Groups"))
        self.createGroupButton.setText(_translate("Dialog", "Create Group"))
        self.selectGridPointsButton.setText(_translate("Dialog", "Set Orientation"))
        self.createGridButton.setText(_translate("Dialog", "Draw Polygon"))
