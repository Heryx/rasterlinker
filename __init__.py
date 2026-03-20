# -*- coding: utf-8 -*-
"""QGIS plugin bootstrap for GeoSurvey Studio."""


# noinspection PyPep8Naming
def classFactory(iface):  # pylint: disable=invalid-name
    """Load GeoSurvey Studio plugin class from file geosurvey_studio.

    :param iface: A QGIS interface instance.
    :type iface: QgsInterface
    """
    #
    from .geosurvey_studio import GeoSurveyStudioPlugin
    return GeoSurveyStudioPlugin(iface)
