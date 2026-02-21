# GeoSurvey Studio Docs

This folder is the project knowledge base for development and release tracking.
It can be used as:

- local documentation in the repository
- source content for a future GitHub Wiki

## Contents

- `docs/CHANGELOG_DEV.md`
  - chronological list of technical changes grouped by commit
- `docs/MILESTONE_1_1_X.md`
  - scope and execution checklist for patch-line 1.1.x
- `docs/WORKFLOW_AND_VERSIONING.md`
  - practical rules for commits, version bumps, milestones, and releases
- `docs/REPOSITORY_SWITCH_CHECKLIST.md`
  - step-by-step procedure for changing canonical GitHub repository links safely
- `docs/RELEASE_NOTES_v1.1.0.md`
  - ready-to-paste release text (full and short) + issue closing comment template

## Maintenance Rule

For each functional change:

1. commit the code change
2. append a short entry in `docs/CHANGELOG_DEV.md`
3. update milestone checklist if relevant

This keeps implementation and documentation aligned.
