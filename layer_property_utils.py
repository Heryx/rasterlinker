# -*- coding: utf-8 -*-
"""Helpers for plugin layer customProperty namespace migration."""

NEW_NAMESPACE = "geosurvey_studio"
LEGACY_NAMESPACE = "rasterlinker"
_MISSING = object()


def _read_custom_property(layer, key, default=_MISSING):
    if layer is None or not key:
        return default
    try:
        return layer.customProperty(str(key), default)
    except Exception:
        return default


def _write_custom_property(layer, key, value):
    if layer is None or not key:
        return
    try:
        layer.setCustomProperty(str(key), value)
    except Exception:
        pass


def namespaced_property_key(name):
    clean = str(name or "").strip().strip("/")
    if not clean:
        return ""
    return f"{NEW_NAMESPACE}/{clean}"


def _legacy_property_keys(name):
    clean = str(name or "").strip().strip("/")
    if not clean:
        return []
    keys = [f"{LEGACY_NAMESPACE}/{clean}"]
    if "/" not in clean:
        keys.append(f"{LEGACY_NAMESPACE}_{clean}")
    return keys


def set_layer_property(layer, name, value):
    key = namespaced_property_key(name)
    if not key:
        return ""
    _write_custom_property(layer, key, value)
    return key


def get_layer_property(layer, name, default=None, legacy_keys=None, migrate=True):
    key = namespaced_property_key(name)
    if not key:
        return default

    value = _read_custom_property(layer, key, _MISSING)
    if value is not _MISSING and value is not None and not (isinstance(value, str) and value == ""):
        return value

    candidates = []
    for item in (legacy_keys or _legacy_property_keys(name)):
        k = str(item or "").strip()
        if k and k not in candidates:
            candidates.append(k)

    for old_key in candidates:
        old_val = _read_custom_property(layer, old_key, _MISSING)
        if old_val is _MISSING or old_val is None or (isinstance(old_val, str) and old_val == ""):
            continue
        if migrate:
            _write_custom_property(layer, key, old_val)
        return old_val

    return default
