# Workflow and Versioning

## Commit Strategy

- Commit each logical change separately.
- Prefer small, reversible commits.
- Keep one topic per commit when possible.

## Version Strategy

- Do **not** bump plugin version on every commit.
- Bump version only for stable release-ready sets.

Semantic approach:

- `PATCH` (`1.1.0 -> 1.1.1`): bugfixes/hardening, no breaking changes.
- `MINOR` (`1.1.x -> 1.2.0`): new features, backward compatible.
- `MAJOR` (`1.x -> 2.0.0`): breaking changes/migrations.

## Milestones in this Repo

- `v1.1.0`: already used for current stabilization tasks.
- `v1.1.x`: patch-line backlog where final patch number is decided later.
- `v1.2.0`: Atlas/layout roadmap.

## Documentation Rule

After each implementation commit:

1. update `docs/CHANGELOG_DEV.md`
2. update milestone checklist if status changed
3. keep issue labels/milestones consistent with current scope

## Repository Migration

When switching to a new canonical GitHub repository, follow:

- `docs/REPOSITORY_SWITCH_CHECKLIST.md`
