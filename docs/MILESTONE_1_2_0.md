# Milestone `v1.2.0` Tracking

Minor-line objective: Atlas/Layout integration for fast, repeatable survey reporting.

## Scope (Planned)

- [ ] Atlas bridge for GeoSurvey groups/time-slices (QGIS Layout Atlas integration)
- [ ] Coverage layer generator for batch pages (by group, time-slice, depth, trace layer)
- [ ] Layout preset workflow (template + parameter binding)
- [ ] Batch export profiles (PDF/PNG naming rules and output structure)
- [ ] Optional overlays in print context (2D traces, grid, selected vector layers)
- [ ] Pre-export validation panel (missing layout, missing layers, CRS mismatch, empty pages)

## Recommended Execution Order

1. Atlas bridge + coverage generation
2. Batch export profiles
3. Optional overlays and style controls
4. Validation panel
5. User documentation for print workflow

## Exit Criteria for `v1.2.0`

- User can generate a coverage set from GeoSurvey data without manual attribute preparation.
- User can run a full Atlas export in one action with deterministic naming.
- Validation catches common blocking errors before export starts.
- Core printing workflow is documented end-to-end.
