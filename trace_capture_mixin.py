# -*- coding: utf-8 -*-
"""Trace capture mixin composition for GeoSurvey Studio plugin."""

from .trace_capture_core_mixin import TraceCaptureCoreMixin
from .trace_capture_snapping_mixin import TraceCaptureSnappingMixin
from .trace_capture_postprocess_mixin import TraceCapturePostprocessMixin
from .trace_editing_mixin import TraceEditingMixin
from .trace_labeling_mixin import TraceLabelingMixin
from .trace_storage_mixin import TraceStorageMixin


class TraceCaptureMixin(
    TraceCapturePostprocessMixin,
    TraceCaptureSnappingMixin,
    TraceCaptureCoreMixin,
    TraceStorageMixin,
    TraceLabelingMixin,
    TraceEditingMixin,
):
    """Facade mixin that composes trace capture responsibilities."""

    pass
