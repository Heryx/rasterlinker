# Repository Switch Checklist

Use this checklist when moving GeoSurvey Studio to a new canonical GitHub repository.

## 1. Update metadata links

Edit `metadata.txt` `[general]` section:

- `homepage=...`
- `tracker=...`
- `repository=...`
- `update_repository=...`

Recommended: keep all four consistent with the new canonical repository.

## 2. Update user-facing docs

Update repository URLs in:

- `README.md`
- wiki pages that reference GitHub links
- release templates/notes (if any)

## 3. Validate update checker behavior

`update_checker_mixin` resolves repository in this order:

1. `update_repository`
2. `repository`
3. `homepage`
4. `tracker`

Validation steps:

1. In QGIS, run **Check for Updates** action manually.
2. Confirm no parsing error appears in message bar.
3. Confirm owner/repo resolution points to the new repository.
4. Confirm latest tag/release lookup works.

## 4. Confirm release process

1. Push a test tag in the new repository (e.g. `vX.Y.Z-test` if appropriate).
2. Ensure GitHub Releases/Tags are visible publicly (or to intended users).
3. Re-run manual update check from plugin.

## 5. Post-switch cleanup

- Close/resolve migration issue in roadmap.
- Update milestone docs if needed.
- Keep one note in changelog indicating repository migration date.
