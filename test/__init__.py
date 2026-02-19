# Import qgis libs when available (QGIS runtime).
# Keep tests importable even outside QGIS for pure-python unit tests.
try:  # pragma: no cover
    import qgis  # pylint: disable=W0611  # NOQA
except Exception:  # pragma: no cover
    qgis = None
