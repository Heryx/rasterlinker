# GeoSurvey Studio v1.1.0 - Release Notes Draft

Use this page as copy/paste source for GitHub Release and issue closing comments.

## Option A - Full Release Notes

**Title**
`GeoSurvey Studio v1.1.0 - Stability, Packaging, and Refactor`

**Body**
```markdown
## GeoSurvey Studio v1.1.0

This release focuses on stability, compatibility cleanup, package workflow hardening, and internal refactor to improve maintainability.

## Highlights

- Migrated layer custom properties to `geosurvey_studio/*` with legacy fallback compatibility.
- Hardened project package workflow:
  - Full package export (entire project folder)
  - Portable package export (catalog-linked assets)
  - Import conflict preview before overwrite
  - Safe import with unsafe-entry skip reporting
- Refactored large modules into focused mixins:
  - Project Manager: package actions, timeslice import/CRS validation, import rollback/outcome handling, radargram validation
  - 2D/3D Draw Panel: trace info state and help/query panel logic
- Improved test suite:
  - Renamed legacy dialog test naming
  - Added package workflow tests (portable export, conflict/overwrite reporting)
- Added/updated technical docs for workflow, versioning, and repository switch procedures.

## Closed Issues

- #8 customProperty namespace migration
- #9 repository switch documentation
- #10 legacy test filename rename
- #11 large-module refactor
- #16 package workflow hardening

## Notes

- Backward compatibility is preserved for legacy custom property keys.
- Portable export intentionally excludes external files and reports missing/external assets.
- Core workflows validated:
  - Project Manager
  - Time-slice import/group visibility
  - 2D/3D Draw Panel
```

## Option B - Short "What's New"

```markdown
## What's new in v1.1.0

- Improved compatibility: migrated to `geosurvey_studio/*` custom properties with legacy fallback.
- Safer package workflow: Full/Portable export, overwrite conflict preview, and safe import handling.
- Internal refactor of Project Manager and 2D/3D Draw Panel for better stability and future maintenance.
- Extended tests and updated development docs for milestone tracking and repository-switch safety.
```

## Issue Closing Comment Template

```markdown
Completed in `v1.1.0`.

Implemented and validated in branch `dev/feature/ui-responsive-docking`.

Main related commits:
- 2344a28
- 22a919b
- d92027f
- 1fbaa20
- 1052c5d
```

