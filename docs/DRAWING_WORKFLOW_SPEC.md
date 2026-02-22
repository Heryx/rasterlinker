# Drawing Workflow Specification (Draft)

## Status

- Draft for review
- Scope target: next minor line (recommended `v1.2.x`)
- Owner: GeoSurvey Studio plugin team

## 1) Problem Statement

Current 2D/3D drawing behavior mixes QGIS native editing and plugin side-effects in ways that are hard to predict:

- Metadata prompts can appear at the wrong moment (e.g., save/commit cycle).
- Vertex point layer may be created automatically even when not desired.
- Switching time-slices during drawing is slow (requires moving pointer to dial).
- Too many operations happen at once (draw, metadata, labels, child layers, form UX).

The result is a workflow that feels fragile and cognitively heavy.

## 2) Product Goals

1. Make drawing **canvas-first** and low-friction.
2. Keep metadata capture **deterministic** and idempotent.
3. Avoid automatic side-effects unless explicitly requested.
4. Preserve compatibility with current projects and layers.
5. Prepare clean foundations for later 3D and atlas/reporting integration.

## 3) Non-Goals (for this block)

- Full rewrite of QGIS attribute table behavior.
- Replacing QGIS editing subsystem.
- Atlas/layout implementation (tracked separately in `docs/MILESTONE_1_2_0.md`).

## 4) Target UX Model

### 4.1 Core Principle

- `Draw 2D Line` should only control capture/edit session.
- Metadata enrichment should be automatic and silent.
- Interpretation fields should be edited on-demand (panel/form), not forced by popups.

### 4.2 Drawing Session

When `Draw 2D Line` is ON:

- Enter edit session on active trace layer.
- Enable draw tool.
- Enable time-slice quick navigation from canvas (modifier + mouse wheel).
- Show lightweight canvas HUD with active slice info.

When `Draw 2D Line` is OFF:

- Exit draw tool.
- Keep layer editable state consistent with toggle semantics.
- No additional prompts.

### 4.3 Interpretation Prompt

- Optional feature only.
- Default recommendation: OFF.
- If ON: prompt appears once per newly added feature only.
- Prompt must never be retriggered by save/commit refresh events.

### 4.4 Vertex Point Layer

- Never auto-created during line drawing.
- Created/updated only by explicit action:
  - `Generate Vertex Points`
  - `Refresh Vertex Points`
- Line layer remains the source of truth; point layer is a derived view.

## 5) Functional Requirements

### FR-1 Draw Session Control

- Single source for draw state (`idle`, `drawing`).
- Toggle action updates both plugin UI and QGIS tool state.
- Save action does not alter draw semantics.

### FR-2 Time-slice Canvas Navigation

- In drawing mode only, `modifier + wheel` cycles slices.
- Plain wheel remains standard QGIS zoom behavior.
- Modifier key configurable (default `Alt`).
- Optional fallbacks: `Shift` or `Ctrl`.

### FR-3 Metadata Auto-Enrichment

On feature add:

- Auto-fill `trace_id`, `ts_id`, `ts_name`, `group_name`.
- Compute and save depth range/list fields.
- Save per-vertex depth payload in `vertex_depths` JSON.

No popup mandatory for these fields.

### FR-4 Interpretation Fields

- `notes`, `interpretation`, `comment` editable from panel/form only.
- Optional popup controlled by user setting.
- If popup disabled, no interruption in drawing flow.

### FR-5 Vertex Layer Generation (Explicit)

- New tool button(s) in 2D/3D Draw Panel:
  - `Generate Vertex Points` (if missing)
  - `Refresh Vertex Points` (if exists)
- Must support:
  - label setup according to depth mode
  - relation line (parent) -> points (child)
- Must not be required for Build 3D calculations.

### FR-6 Depth Mode and Labels

- Single depth mode selection (`None`, `Min`, `Mid`, `Max`).
- If `None`: point labels hidden.
- If `Min/Mid/Max`: labels shown, values recomputed from line metadata.
- Depth mode affects derived labeling and derived point depth values.

## 6) Data Model

### 6.1 Line Layer (Primary)

Primary storage remains in trace line layer fields:

- `trace_id`
- `ts_id`
- `ts_name`
- `group_name`
- `depth_list`
- `depth_from`
- `depth_to`
- `depth_unit`
- `z_source`
- `z_grid_path`
- `z_mode`
- `z_value`
- `vertex_depths` (JSON)
- `notes`
- `interpretation`
- `comment`

### 6.2 Vertex Point Layer (Derived, Optional)

Derived from line geometry + `vertex_depths`:

- `trace_fid`
- `trace_id`
- `vertex_idx`
- `depth_val`
- `depth_min`
- `depth_max`
- `depth_lbl`
- `depth_unit`
- `trace_layer_id`

## 7) Event and State Rules

### 7.1 Recommended State Machine

- `idle`
- `drawing_active`
- `saving` (transient)
- `postprocess` (transient, no UI block)

Rules:

- Prompt logic only allowed in `postprocess` on `featureAdded`.
- Save/commit must not re-enter interpretation prompt path for same feature key.

### 7.2 Idempotency

Each feature processing should be idempotent for key:

- `layer_id:fid`

Repeated geometry/attribute signals must not duplicate side-effects.

## 8) UI Changes

### 8.1 2D/3D Draw Panel

- Keep tools compact and clear.
- Add explicit buttons for vertex layer generation/update.
- Keep interpretation popup option in query/options menu.
- Add visual draw status indicator (small badge: `Drawing ON/OFF`).

### 8.2 Canvas HUD (minimal)

During drawing:

- active group
- active slice index / total
- active depth text
- depth mode

Must be non-blocking and auto-hide when drawing stops.

## 9) Compatibility and Migration

- Existing projects must still load.
- If optional vertex point layers already exist, keep using them.
- New behavior should not force recreation of layers.
- Legacy keys/properties should continue with fallback.

## 10) Validation and QA

### 10.1 Manual QA Scenarios

1. Draw with popup OFF: no interruptions.
2. Draw with popup ON: exactly one prompt per new feature.
3. Save edits after drawing: no extra prompt.
4. Use modifier+wheel during drawing: slice changes without leaving canvas.
5. Build 3D without point layer: works from line metadata.
6. Generate point layer manually: labels/relation created correctly.
7. Switch depth mode: point labels update or hide accordingly.

### 10.2 Automated Tests (minimum)

- Prompt guard idempotency by feature key.
- Depth mode state persistence.
- Vertex layer generation command behavior.
- Build 3D path independent of point layer presence.

## 11) Release Plan

Suggested incremental rollout:

1. Session and prompt hardening
2. Canvas slice navigation
3. Explicit vertex layer generation
4. Label/depth mode polishing
5. Documentation/user guide updates

## 12) Open Decisions

1. Default modifier key for slice wheel (`Alt` recommended).
2. Keep popup default ON or OFF (recommended OFF).
3. Whether to allow optional auto-refresh of existing point layer after each draw.
4. Final naming of explicit point tools:
   - `Generate Vertex Points`
   - `Refresh Vertex Points`

