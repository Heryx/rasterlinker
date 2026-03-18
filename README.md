# GeoSurvey Studio — QGIS Plugin

GeoSurvey Studio is a QGIS plugin for the import, processing, visualization, and
project management of geophysical survey data, with native support for GPR
(Ground Penetrating Radar) datasets in OGPR, CSV, and LAS/point cloud formats.
It is designed for archaeological and geophysical survey workflows requiring
georeferenced depth-slice analysis and structured multi-profile management.

---

## Requirements

- QGIS 3.16 or later
- Python 3.9+
- Dependencies: `numpy`, `scipy`, `matplotlib`, `pyproj`, `laspy` (for LAS support), `netCDF4` (for NetCDF export)

---

## Installation

1. Download or clone this repository.
2. Copy the plugin folder to your QGIS plugins directory:
   - Windows: `%APPDATA%\QGIS\QGIS3\profiles\default\python\plugins\`
   - Linux/macOS: `~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/`
3. Enable the plugin from **Plugins → Manage and Install Plugins → Installed**.

---

## Main Features

### 1. GPR Data Import (OGPR, CSV, LAS)

GeoSurvey Studio supports importing GPR profiles from multiple formats:

- **OGPR format** (v1.0 int16 / v2.0 float32): native binary format with embedded
  JSON header, georeferenced sample geolocations (easting, northing, altitude,
  heading), and optional MD5 integrity check. The reader automatically detects
  byte order (little/big endian) and extra-doubles layout in the geolocations block.
- **CSV format**: tabular GPR amplitude data with associated coordinate columns.
- **LAS / Point Cloud format**: GPR amplitude encoded as point cloud attributes,
  compatible with LAS 1.2–1.4 files.

Multiple profiles can be imported simultaneously into a single project via the
**Project Manager** dialog. Each imported profile is georeferenced and registered
in the project catalog with its metadata (EPSG, frequency, velocity, polarization,
sampling step).

---

### 2. Radargram Profile Viewer

The interactive profile viewer (`GPR Profile Viewer`) allows full inspection of
individual GPR radargrams:

- Display of the 2D amplitude section (samples × traces) with adjustable colormap
  and contrast range
- Interactive crosshair cursor with real-time depth and distance readout
- Timeslice preset panel: save and recall depth-window configurations for fast
  multi-profile comparison
- Per-profile processing controls (dewow, background removal, AGC gain, manual gain)
- Horizontal and vertical measurement tools
- Support for multi-channel profiles (selectable channel display)

---

### 3. Timeslice Generation

GeoSurvey Studio generates georeferenced horizontal depth slices from multi-profile
GPR datasets. Three processing engines are available depending on the source format:

#### OGPR Slicer
The OGPR slicer (`gpr_ogpr_slicer.py`) builds a 2D interpolated raster from
the amplitude values of all loaded profiles at a given depth window:

- **Processing pipeline** applied per profile: dewow (mean subtraction),
  background removal (running mean or median), and gain (AGC or manual).
- **Inter-profile normalization**: global amplitude scaling based on the median
  of each profile, ensuring consistent brightness across profiles acquired at
  different times or with different antenna coupling.
- **IDW interpolation** (Inverse Distance Weighting): the processed amplitude
  values are projected onto a regular grid using IDW. Key parameters:
  - `resolution` (m): output raster cell size
  - `radius` (m): search radius for neighbouring trace points; can be set
    manually or estimated automatically (`auto_radius`) from the inter-profile
    spacing
  - `power`: IDW exponent (default 2.0)
  - `depth_radius_factor`: increases the search radius linearly with depth
    to compensate for lower trace density in deep slices
- **Output**: georeferenced GeoTIFF raster loaded directly into the QGIS project,
  clipped to the survey extent.

#### CSV Slicer
Processes amplitude slices from tabular CSV data with the same IDW engine.

#### LAS Slicer
Extracts horizontal slices from GPR point clouds stored as LAS files,
using spatial binning on the XY plane within the selected depth range.

---

### 4. 3D GPR Volume Viewer

The 3D viewer reconstructs the full GPR volume from the imported profiles and
displays it as an interactive 3D scene:

- Volumetric rendering of amplitude along X, Y, Z axes
- Adjustable clip planes for each axis
- Opacity and colormap controls
- Export of the current view as an image

---

### 5. Project Management and Catalog

The **Project Manager** dialog is the central hub for organizing survey data:

- Import and register multiple OGPR/CSV/LAS profiles into a structured catalog
- Assign profiles to named survey grids
- Manage timeslice groups (depth ranges → raster layers)
- Import pre-computed timeslice rasters and assign them to existing groups
- Detect and reassign unlinked or orphaned timeslice files
- Package the entire project (data + rasters + catalog) into a portable archive
- Project health check: validates that all registered files are present and
  readable, reports missing or corrupted entries

---

### 6. Import External Rasters as Timeslice Group

Pre-computed raster files (GeoTIFF or other QGIS-compatible formats) generated
outside GeoSurvey Studio can be imported directly as a named timeslice group
and immediately used with the dial/slider interface:

- Select a folder or a set of individual raster files
- Assign a group name and depth-label scheme (numeric order, filename, or
  custom depth values)
- The plugin validates CRS and extent consistency before loading; files with
  missing or mismatched CRS can be assigned the project CRS automatically
- All loaded rasters are added to the `GeoSurvey Studio / <group name>` layer
  group in the QGIS layer tree and are immediately accessible via the dial and
  slider controls

> **In development**: a dedicated button in the Timeslice Group Manager dialog
> for folder-based import with automatic depth ordering is planned for the next
> release.

---

### 7. Vector Trace Annotations

GeoSurvey Studio includes a complete vector annotation system for marking
archaeological or geophysical features directly on the map canvas:

- **Trace capture**: draw polyline traces on the QGIS map canvas with optional
  snapping to existing survey geometry
- **Trace editing**: move, reshape, split, and delete traces
- **Trace labeling**: assign classification labels, attributes, and notes to
  each trace
- **3D trace construction**: project 2D surface traces onto the GPR depth volume
  to generate 3D interpretive polylines
- **Trace info panel**: tabular view of all traces with filtering, sorting,
  and attribute editing
- All trace data is stored as standard QGIS vector layers (GeoPackage)

---

### 8. Grid Manager and Timeslice Groups

The Grid Manager allows defining the spatial extent and resolution of the output
raster grid:

- Draw or import a rectangular or polygonal survey area on the map canvas
- Set output resolution (m/pixel) and coordinate reference system
- Group timeslice rasters by depth range into named **Timeslice Groups**
- Control layer visibility across groups using the dial/slider interface:
  cycle through depth slices, zoom to group extent, display active slice name
- Validate slice coverage and detect depth gaps or duplicate assignments

---

### 9. Export

- **NetCDF export**: saves the full 3D GPR amplitude volume as a CF-compliant
  NetCDF file with georeferenced X, Y, Z axes
- **GeoTIFF**: each generated timeslice is saved as a single-band float32 GeoTIFF
  with embedded CRS and geotransform
- **Raster visibility export**: export the current slice view as a PNG image

---

### 10. Project Health and Validation

The **Project Health** dialog provides a diagnostic report of the current project:

- Checks that all registered OGPR/CSV/LAS source files exist on disk
- Verifies that all timeslice raster files are present and linked correctly
- Reports orphaned layers (present in QGIS but not in the catalog) and missing
  layers (in the catalog but absent from QGIS)
- Radargram validation: checks geometric consistency (profile direction, slice
  count, coordinate gap) and flags profiles with suspicious data

---

## Typical Workflow

1. **Open Project Manager** → create a new project or load an existing catalog.
2. **Import profiles** → select one or more `.ogpr`, `.csv`, or `.las` files;
   they are parsed, georeferenced, and registered in the catalog.
3. **Inspect profiles** → open the Profile Viewer on any imported profile to
   review signal quality and set processing parameters.
4. **Define the survey grid** → draw the output raster extent on the map canvas
   and set resolution.
5. **Generate timeslices** → configure depth windows, IDW parameters, and
   processing pipeline; run the slicer (background task with progress bar).
6. **Organize groups** → assign generated rasters to Timeslice Groups by depth
   range; use the dial/slider to browse slices interactively.
7. **Annotate features** → use the Trace tool to draw and label archaeological
   or geophysical anomalies on the map.
8. **Export** → save the volume as NetCDF or export individual slices as GeoTIFF.
9. **Run Health Check** → verify project integrity before archiving or sharing.

---

## OGPR Format — Technical Reference

The OGPR format is a binary container for georeferenced GPR profile data:

```
[magic: b'ogpr\r\n' or b'ogpr\n']
[MD5 checksum line]
[JSON header length line]
[JSON header: version, mainDescriptor, dataBlockDescriptors]
[Radar Volume block: n_slices × n_channels × n_samples  float32/int16]
[Sample Geolocations block: n_slices × n_channels × 8 float64]
```

The JSON header declares:
- `version.major` / `version.minor`: format version (1 = int16, 2 = float32)
- `mainDescriptor`: sample count, channel count, slice count, sampling step,
  sampling time (ns), propagation velocity (m/s), frequency (MHz), polarization
- `dataBlockDescriptors`: byte offsets and sizes for the Radar Volume and
  Sample Geolocations blocks
- Geolocations: 8 doubles per channel per slice —
  easting, northing, altitude, heading, pitch, roll, spare×2

The reader supports automatic endian detection for both the radar and geolocations
blocks, and handles non-standard layouts with extra doubles at the start or end
of the geolocations record.

---

## Development Docs

- Project docs index: [`docs/README.md`](docs/README.md)
- Technical dev changelog: [`docs/CHANGELOG_DEV.md`](docs/CHANGELOG_DEV.md)
- Milestone tracking (`v1.1.x`): [`docs/MILESTONE_1_1_X.md`](docs/MILESTONE_1_1_X.md)
- Workflow/versioning rules: [`docs/WORKFLOW_AND_VERSIONING.md`](docs/WORKFLOW_AND_VERSIONING.md)

---

## How to Cite

If GeoSurvey Studio supports your work, please cite it as:

> Guarino, G. (2026). *GeoSurvey Studio* (v1.1.0) [QGIS plugin]. GitHub.
> https://github.com/Heryx/rasterlinker

Suggested in-text citation: (Guarino, 2026)

- Plugin source code: GPL-2.0-or-later
- Documentation: CC BY 4.0 (recommended for reuse with attribution)
