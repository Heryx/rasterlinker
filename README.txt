GeoSurvey Studio - QGIS Plugin
==============================

GeoSurvey Studio is a QGIS plugin for geophysical/time-slice workflows:
- grouped raster management
- grid drawing/orientation tools
- 2D/3D trace workflow
- project manager with catalog and import tools

Main files
----------
- geosurvey_studio.py
- geosurvey_studio_dialog.py
- geosurvey_studio_dialog_base.ui
- metadata.txt
- resources.qrc / resources.py

Development quick start
-----------------------
1. Install/copy the plugin folder into your QGIS profile plugins directory.
2. Rebuild resources when icon/resource paths change:
   python-qgis -m PyQt5.pyrcc_main -o resources.py resources.qrc
3. Reload plugin from QGIS and test key workflows.
4. Run available tests from this repository (where supported by your runtime).

Notes
-----
- Plugin internal name: GeoSurvey Studio
- Current metadata version is defined in metadata.txt
- Source code license: GPL-2.0-or-later
