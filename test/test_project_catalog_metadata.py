# coding=utf-8
"""Unit tests for catalog and metadata helpers."""

import json
import os
import tempfile
import unittest
import zipfile

from project_catalog import (
    CATALOG_VERSION,
    add_timeslice_to_default_group,
    assign_timeslices_to_group,
    catalog_path,
    create_raster_group,
    ensure_project_structure,
    export_project_package,
    export_project_package_portable,
    import_project_package,
    inspect_catalog_compatibility,
    inspect_package_import_conflicts,
    link_surfer_grid_into_project,
    load_catalog,
    load_catalog_with_info,
    register_timeslices_batch,
    register_vector_layer,
    remove_timeslices_from_group,
    save_catalog,
    validate_catalog,
)

try:
    from radargram_metadata import find_worldfile, inspect_radargram
    HAS_RADARGRAM_METADATA = True
except Exception:
    HAS_RADARGRAM_METADATA = False
    find_worldfile = None
    inspect_radargram = None


class ProjectCatalogMetadataTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project_root = self.tmp.name
        ensure_project_structure(self.project_root)

    def tearDown(self):
        self.tmp.cleanup()

    def _write_json(self, path, payload):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

    def _touch(self, path, content=""):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

    def test_load_catalog_migrates_legacy_and_persists_version(self):
        path = catalog_path(self.project_root)
        legacy = {
            "schema_version": 1,
            "project_root": self.project_root,
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
            "models_3d": [],
            "radargrams": [],
            "timeslices": [],
            "links": [],
        }
        self._write_json(path, legacy)

        data = load_catalog(self.project_root)
        self.assertEqual(data.get("catalog_version"), CATALOG_VERSION)
        self.assertEqual(data.get("schema_version"), CATALOG_VERSION)
        self.assertTrue(any(g.get("id") == "grp_imported" for g in data.get("raster_groups", [])))

        with open(path, "r", encoding="utf-8") as f:
            persisted = json.load(f)
        self.assertEqual(persisted.get("catalog_version"), CATALOG_VERSION)

    def test_inspect_catalog_compatibility_detects_migration_need(self):
        path = catalog_path(self.project_root)
        legacy = {
            "schema_version": 1,
            "project_root": self.project_root,
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
            "models_3d": [],
            "radargrams": [],
            "timeslices": [],
            "links": [],
        }
        self._write_json(path, legacy)

        report = inspect_catalog_compatibility(self.project_root, plugin_version="1.1.1")
        self.assertEqual(report.get("status"), "needs_migration")
        self.assertEqual(report.get("raw_catalog_version"), 1)
        self.assertGreaterEqual(len(report.get("applied_migrations") or []), 1)

    def test_load_catalog_with_info_creates_backup_and_stamps_plugin_version(self):
        path = catalog_path(self.project_root)
        legacy_v3 = {
            "catalog_version": 3,
            "schema_version": 3,
            "project_root": self.project_root,
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
            "models_3d": [],
            "radargrams": [],
            "timeslices": [],
            "links": [],
            "raster_groups": [],
        }
        self._write_json(path, legacy_v3)

        data, info = load_catalog_with_info(self.project_root, plugin_version="1.1.1")
        self.assertEqual(data.get("catalog_version"), CATALOG_VERSION)
        self.assertEqual(data.get("created_with_plugin"), "1.1.1")
        self.assertEqual(data.get("last_opened_with_plugin"), "1.1.1")
        self.assertTrue(info.get("applied_migrations"))
        backup_path = info.get("backup_path")
        self.assertTrue(backup_path and os.path.isfile(backup_path))

    def test_load_catalog_migrates_v3_to_v4_vector_layers(self):
        path = catalog_path(self.project_root)
        legacy_v3 = {
            "catalog_version": 3,
            "schema_version": 3,
            "project_root": self.project_root,
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
            "models_3d": [],
            "radargrams": [],
            "timeslices": [],
            "links": [],
            "raster_groups": [],
        }
        self._write_json(path, legacy_v3)
        data = load_catalog(self.project_root)
        self.assertEqual(data.get("catalog_version"), CATALOG_VERSION)
        self.assertIsInstance(data.get("vector_layers"), list)

    def test_create_group_is_case_insensitive_unique(self):
        group_1, created_1 = create_raster_group(self.project_root, "TimeSlices")
        group_2, created_2 = create_raster_group(self.project_root, "timeslices")
        self.assertTrue(created_1)
        self.assertFalse(created_2)
        self.assertEqual(group_1.get("id"), group_2.get("id"))

    def test_assign_and_remove_timeslices_from_group(self):
        register_timeslices_batch(
            self.project_root,
            [
                {"id": "ts_a", "project_path": os.path.join(self.project_root, "timeslices_2d", "a.tif")},
                {"id": "ts_b", "project_path": os.path.join(self.project_root, "timeslices_2d", "b.tif")},
            ],
        )
        group, _created = create_raster_group(self.project_root, "AreaA")
        gid = group.get("id")

        assign_timeslices_to_group(self.project_root, gid, ["ts_a", "ts_b"])
        add_timeslice_to_default_group(self.project_root, "ts_a")
        add_timeslice_to_default_group(self.project_root, "ts_b")
        remove_timeslices_from_group(self.project_root, gid, ["ts_b"])

        data = load_catalog(self.project_root)
        grp = next(g for g in data.get("raster_groups", []) if g.get("id") == gid)
        self.assertEqual(grp.get("timeslice_ids"), ["ts_a"])

    def test_validate_catalog_reports_duplicate_timeslice_ids(self):
        data = load_catalog(self.project_root)
        data["timeslices"] = [
            {"id": "ts_dup", "project_path": os.path.join(self.project_root, "timeslices_2d", "missing_1.tif")},
            {"id": "ts_dup", "project_path": os.path.join(self.project_root, "timeslices_2d", "missing_2.tif")},
        ]
        report = validate_catalog(self.project_root, data)
        self.assertTrue(any("Duplicate timeslice id: ts_dup" in msg for msg in report.get("errors", [])))
        self.assertTrue(any("Missing timeslice file:" in msg for msg in report.get("warnings", [])))

    def test_register_vector_layer_upsert_and_validate(self):
        gpkg_path = os.path.join(self.project_root, "vector_layers", "trace2d.gpkg")
        self._touch(gpkg_path, "")

        register_vector_layer(
            self.project_root,
            {
                "id": "vector_trace2d",
                "layer_name": "Trace2D",
                "project_path": gpkg_path,
                "geometry_type": "line",
                "is_3d": False,
                "source_kind": "trace2d",
            },
        )
        register_vector_layer(
            self.project_root,
            {
                "layer_name": "Trace2D",
                "project_path": gpkg_path,
                "geometry_type": "line",
                "is_3d": True,
                "source_kind": "trace3d",
            },
        )

        data = load_catalog(self.project_root)
        vectors = data.get("vector_layers", [])
        self.assertEqual(len(vectors), 1)
        self.assertEqual(vectors[0].get("source_kind"), "trace3d")
        self.assertTrue(vectors[0].get("is_3d"))

        broken = load_catalog(self.project_root)
        broken["vector_layers"] = [
            {
                "id": "vec_dup",
                "layer_name": "A",
                "project_path": os.path.join(self.project_root, "vector_layers", "missing_a.gpkg"),
            },
            {
                "id": "vec_dup",
                "layer_name": "A",
                "project_path": os.path.join(self.project_root, "vector_layers", "missing_a.gpkg"),
            },
        ]
        report = validate_catalog(self.project_root, broken)
        self.assertTrue(any("Duplicate vector layer id: vec_dup" in msg for msg in report.get("errors", [])))
        self.assertTrue(any("Missing vector layer file:" in msg for msg in report.get("warnings", [])))

    def test_link_surfer_grid_into_project(self):
        ref = os.path.join(self.project_root, "timeslices_2d", "slice_01.tif")
        grd = os.path.join(self.project_root, "timeslices_2d", "slice_01.grd")
        self._touch(ref, "raster")
        self._touch(grd, "surfer-grid")

        link = link_surfer_grid_into_project(
            self.project_root,
            reference_raster_path=ref,
            source_raster_path=ref,
        )
        self.assertEqual(link.get("z_source"), "surfer_grid")
        self.assertTrue(os.path.exists(link.get("z_grid_project_path", "")))
        self.assertTrue(link.get("z_grid_project_path", "").lower().endswith(".grd"))

    @unittest.skipUnless(HAS_RADARGRAM_METADATA, "radargram_metadata dependencies are not available")
    def test_radargram_worldfile_and_text_metadata(self):
        png = os.path.join(self.project_root, "radargrams", "line_01.png")
        pgw = os.path.join(self.project_root, "radargrams", "line_01.pgw")
        csv_path = os.path.join(self.project_root, "radargrams", "line_02.csv")
        self._touch(png, "img")
        self._touch(pgw, "1\n0\n0\n-1\n0\n0\n")
        self._touch(csv_path, "1,2,3\n4,5,6\n")

        wf = find_worldfile(png)
        self.assertEqual(os.path.normcase(wf), os.path.normcase(pgw))

        meta = inspect_radargram(csv_path)
        self.assertEqual(meta.get("source_type"), "text-matrix")
        self.assertEqual(meta.get("rows"), 2)
        self.assertEqual(meta.get("cols"), 3)

    def test_export_import_package_roundtrip(self):
        file_in_project = os.path.join(self.project_root, "timeslices_2d", "slice_a.tif")
        self._touch(file_in_project, "abc")

        out_zip = os.path.join(self.project_root, "exports", "package_test.zip")
        zip_path = export_project_package(self.project_root, out_zip)
        self.assertTrue(os.path.isfile(zip_path))

        target_root = os.path.join(self.project_root, "import_target")
        import_project_package(zip_path, target_root)
        imported_file = os.path.join(target_root, "timeslices_2d", "slice_a.tif")
        self.assertTrue(os.path.isfile(imported_file))

    def test_export_portable_package_reports_external_and_missing(self):
        in_project = os.path.join(self.project_root, "timeslices_2d", "slice_inside.tif")
        self._touch(in_project, "inside")

        ext_tmp = tempfile.TemporaryDirectory()
        try:
            external_path = os.path.join(ext_tmp.name, "slice_external.tif")
            self._touch(external_path, "external")
            missing_path = os.path.join(self.project_root, "timeslices_2d", "missing_slice.tif")

            data = load_catalog(self.project_root)
            data["timeslices"] = [
                {"id": "ts_inside", "project_path": in_project},
                {"id": "ts_external", "project_path": external_path},
                {"id": "ts_missing", "project_path": missing_path},
            ]
            save_catalog(self.project_root, data)

            out_zip = os.path.join(self.project_root, "exports", "portable_test.zip")
            report = export_project_package_portable(self.project_root, out_zip)
            self.assertTrue(os.path.isfile(report.get("zip_path", "")))
            self.assertIn(os.path.normcase(os.path.abspath(external_path)), [os.path.normcase(p) for p in report.get("external_files", [])])
            self.assertIn(os.path.normcase(os.path.abspath(missing_path)), [os.path.normcase(p) for p in report.get("missing_files", [])])

            with zipfile.ZipFile(report.get("zip_path"), "r") as zf:
                names = set(zf.namelist())
            self.assertIn("metadata/project_catalog.json", names)
            self.assertIn("timeslices_2d/slice_inside.tif", names)
            self.assertNotIn("timeslices_2d/slice_external.tif", names)
        finally:
            ext_tmp.cleanup()

    def test_import_conflict_report_and_overwrite_stats(self):
        src_file = os.path.join(self.project_root, "timeslices_2d", "slice_a.tif")
        self._touch(src_file, "src")
        out_zip = os.path.join(self.project_root, "exports", "conflict_test.zip")
        zip_path = export_project_package(self.project_root, out_zip)

        target_root = os.path.join(self.project_root, "import_target_conflict")
        preexisting = os.path.join(target_root, "timeslices_2d", "slice_a.tif")
        self._touch(preexisting, "old")

        preview = inspect_package_import_conflicts(zip_path, target_root)
        self.assertGreaterEqual(preview.get("total_entries", 0), 1)
        self.assertGreaterEqual(preview.get("conflict_count", 0), 1)

        report = import_project_package(zip_path, target_root, return_report=True)
        self.assertGreaterEqual(report.get("total_entries", 0), 1)
        self.assertGreaterEqual(report.get("overwritten_entries", 0), 1)
        self.assertTrue(os.path.isfile(os.path.join(target_root, "timeslices_2d", "slice_a.tif")))


if __name__ == "__main__":
    unittest.main()
