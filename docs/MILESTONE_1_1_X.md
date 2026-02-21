# Milestone `v1.1.x` Tracking

Patch-line objective: stability, compatibility cleanup, and workflow hardening.

## Open Issues

- [ ] #8 Migrate `customProperty` namespace to `geosurvey_studio/*` with backward compatibility
- [ ] #10 Rename remaining legacy dialog test filename
- [ ] #9 Repository switch plan for metadata/update-check links
- [ ] #11 Refactor large modules (`project_manager_dialog`, `trace_info_mixin`)
- [ ] #16 Package workflow hardening (Full vs Portable export + safer import)

## Recommended Execution Order

1. #8 (runtime compatibility baseline)
2. #10 (test naming cleanup)
3. #9 (documentation/process hardening)
4. #16 (package workflow hardening)
5. #11 (structural refactor after stable behavior)

## Exit Criteria for `v1.1.x`

- No legacy runtime key dependency without fallback.
- Tests aligned with current naming and passing in supported environments.
- Package import/export behavior documented and validated.
- No known regressions in core workflows:
  - Project Manager
  - Time-slice import/group visibility
  - Draw 2D/Build 3D panel
