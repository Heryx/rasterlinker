# Development Changelog

This file tracks implementation changes between formal releases.

## 2026-02-21 (branch: `dev/feature/ui-responsive-docking`)

- Package workflow hardening: Full vs Portable export modes, safer import with conflict preview and unsafe-entry skip reporting, and new package workflow tests.
- Refactor: extract Project Manager time-slice import/CRS/validation helpers into `project_manager_timeslice_import_mixin.py`.
- `c15a902` Extract package import/export actions from Project Manager into dedicated mixin.
- `a44ae11` Extract Trace Info UI state logic into dedicated `trace_info_state_mixin` module.
- `38c8169` Add repository-switch checklist for metadata and update-check migration.
- `19d5f7a` Rename remaining legacy dialog test file and make test skip safely outside QGIS runtime.
- `d6d4dc3` Migrate layer `customProperty` keys to `geosurvey_studio/*` with legacy fallback.
- `e281783` Improve update checker repo parsing and add `update_repository` metadata key.
- `d40a3bf` Remove legacy Plugin Builder/GPR headers from core docs and build files.
- `dac1ad4` Align test naming with GeoSurvey Studio and add safer dialog import fallback.
- `b8995af` Replace legacy README templates with GeoSurvey Studio documentation.

## Previous Stabilization Block (already merged in this branch)

- `73e8911` Align Qt resource prefix to `geosurvey_studio` and regenerate resources.
- `41c559f` Group plugin actions in a dedicated GeoSurvey Studio toolbar.
- `6cc08fe` Add GitHub-based update checker (manual + startup checks).
- `f2898ff` Bump plugin to `v1.0.1` and update metadata.
- `957ef23` Rename core modules to GeoSurvey Studio and stabilize draw panel docking.

## Notes

- This changelog is technical and commit-oriented.
- User-facing release notes should remain in GitHub Releases and metadata changelog.
