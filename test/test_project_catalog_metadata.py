# coding=utf-8
"""Unit tests for catalog and metadata helpers."""

import json
import os
import tempfile
import unittest

from project_catalog import (
    CATALOG_VERSION,
    add_timeslice_to_default_group,
    assign_timeslices_to_group,
    catalog_path,
    create_raster_group,
    ensure_project_structure,
    export_project_package,
    import_project_package,
    link_surfer_grid_into_project,
    load_catalog,
    register_timeslices_batch,
    remove_timeslices_from_group,
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


if __name__ == "__main__":
    unittest.main()
