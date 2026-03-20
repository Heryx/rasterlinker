# Issue Drafts: Drawing Refactor Backlog

These issue drafts are ready to be created on GitHub.

Suggested labels:

- `area:drawing`
- `type:enhancement`
- `priority:high` / `priority:medium`
- `milestone:v1.2.x` (or your chosen milestone)

---

## Issue 1 - Drawing Session Controller (state + idempotency)

**Title**

`Drawing: introduce session controller and idempotent postprocess pipeline`

**Body**

Implement a clear draw session controller for 2D traces with deterministic states (`idle`, `drawing_active`, `saving`, `postprocess`) and idempotent feature postprocess keyed by `layer_id:fid`.

### Scope

- Centralize draw ON/OFF behavior.
- Ensure save/commit does not retrigger feature-level UI side-effects.
- Guard feature postprocess against duplicate signal emissions.

### Acceptance Criteria

- Toggling draw action reliably enters/exits drawing mode.
- Saving edits never retriggers interpretation popup for the same feature.
- Duplicate signal emissions do not duplicate metadata side-effects.

### Priority

`low`

---

## Issue 2 - Optional Interpretation Prompt (default OFF)

**Title**

`Drawing: make interpretation popup optional with clean default and persistence`

**Body**

Keep interpretation popup as an optional guided mode, but with a non-intrusive default.

### Scope

- Setting: `Prompt interpretation form after draw`.
- Persist setting across sessions.
- Recommended default OFF for production workflow.

### Acceptance Criteria

- With prompt OFF: no popup on draw finish or save.
- With prompt ON: one popup per newly added feature only.
- Setting survives plugin restart.

### Priority

`high`

---

## Issue 3 - Canvas Slice Navigation (modifier + mouse wheel)

**Title**

`Drawing UX: add modifier+wheel time-slice navigation while digitizing`

**Body**

Allow switching active time-slice directly from canvas during drawing, avoiding dial interaction.

### Scope

- While drawing only: `modifier + wheel` moves slice up/down.
- Plain wheel remains QGIS zoom.
- Configurable modifier key (`Alt` default, optional Shift/Ctrl).
- Optional lightweight HUD feedback.

### Acceptance Criteria

- User can change slice without moving pointer to panel controls.
- No conflict with normal zoom when modifier is not pressed.
- Active slice metadata used by capture updates accordingly.

### Priority

`high`

---

## Issue 4 - Remove Automatic Vertex Point Layer Creation

**Title**

`Drawing: stop automatic vertex point layer creation during line capture`

**Body**

Make the line layer + `vertex_depths` JSON the primary source of truth, and remove automatic creation of derived vertex point layers during drawing.

### Scope

- No auto-create point layer on feature add.
- Keep line metadata complete for build3D and analysis.

### Acceptance Criteria

- New line drawing does not create vertex point layer unless explicitly requested.
- Build 3D works with line metadata only.
- Existing projects with point layer remain compatible.

### Priority

`high`

---

## Issue 5 - Explicit Vertex Layer Actions

**Title**

`Drawing panel: add explicit Generate/Refresh Vertex Points actions`

**Body**

Provide explicit user actions to create/update derived vertex point layers only when needed.

### Scope

- Button(s):
  - `Generate Vertex Points`
  - `Refresh Vertex Points`
- Build relation parent(line) -> child(points).
- Configure labels according to depth mode.

### Acceptance Criteria

- User can generate vertex layer on demand.
- Refresh action updates existing layer from current line data.
- Relation and labels are configured correctly.

### Priority

`high`

---

## Issue 6 - Depth Mode Semantics and Label Consistency

**Title**

`Depth mode: enforce single-selection semantics for derived labels and values`

**Body**

Depth mode (`None/Min/Mid/Max`) should clearly control derived point label/value behavior and nothing else.

### Scope

- Keep mode single-choice.
- `None` hides labels.
- `Min/Mid/Max` recomputes derived display values.

### Acceptance Criteria

- Changing depth mode updates labels deterministically.
- No label shown when mode is `None`.
- Behavior is consistent on reopened projects.

### Priority

`medium`

---

## Issue 7 - 2D/3D Draw Panel Simplification

**Title**

`UI: simplify Draw Panel actions and separate core draw from interpretation workflows`

**Body**

Reduce cognitive load by separating immediate draw actions from optional interpretation workflows.

### Scope

- Review and reorder toolbar actions.
- Keep essential actions prominent.
- Move advanced/optional controls into options/query area.

### Acceptance Criteria

- Panel shows a clearer draw-first workflow.
- Fewer accidental interruptions during capture.
- Users can still access interpretation tools when needed.

### Priority

`medium`

---

## Issue 8 - Draw Pipeline Tests

**Title**

`Tests: add automated coverage for draw session, prompt guard, and optional vertex generation`

**Body**

Add tests to prevent regressions in the refactored drawing workflow.

### Scope

- Prompt appears once per feature key.
- Save does not retrigger prompt.
- Vertex layer generation is explicit only.
- Depth mode state persistence.

### Acceptance Criteria

- Test suite includes draw workflow cases.
- Failing behavior reproduced by tests before fix and prevented after fix.

### Priority

`medium`

---

## Issue 9 - User Documentation Update (Drawing)

**Title**

`Docs: update user workflow for new drawing model and optional vertex layer`

**Body**

Update docs/wiki to reflect the refactored draw workflow and expected behavior.

### Scope

- Draw session flow.
- Optional interpretation prompt.
- On-demand vertex layer generation.
- Depth mode behavior.

### Acceptance Criteria

- Wiki/docs provide end-to-end drawing instructions.
- Includes quick troubleshooting for common confusion points.

### Priority

`medium`

---

## Suggested Implementation Order

1. Issue 1
2. Issue 2
3. Issue 3
4. Issue 4
5. Issue 5
6. Issue 6
7. Issue 7
8. Issue 8
9. Issue 9
10. Issue 10

---

## Issue 10 - Vertex Metadata Only On Raster Hit

**Title**

`Drawing: assign time-slice/depth metadata only when vertex intersects raster pixels`

**Body**

Current behavior assigns metadata from the active time-slice even when the user draws outside the raster coverage.
This causes incorrect `time-slice` and depth attribution for vertices that do not touch image pixels.

### Scope

- During vertex metadata capture, validate that the vertex point lies on valid raster coverage (pixel hit).
- If no raster hit is found, do not assign time-slice/depth metadata for that vertex.
- Keep geometry creation unaffected: users can still draw outside image bounds.
- Surface a clear status mode for non-hit vertices (e.g. `no_raster_hit`) in metadata.
- Ensure depth aggregation (`depth_list`, `depth_from`, `depth_to`, `vertex_depths`) excludes non-hit vertices.

### Acceptance Criteria

- Vertices outside raster footprint are stored without false time-slice/depth values.
- Vertices inside raster footprint keep current behavior.
- Line-level depth/time-slice fields are computed only from vertices with valid hits.
- UI/trace panel can distinguish valid-depth vertices from non-hit vertices.

### Priority

`high`
