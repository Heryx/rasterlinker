# -*- coding: utf-8 -*-
import unittest
import os
import sys
# Make package importable when running tests from the plugin folder
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
import project_catalog as pc

class TestGroupMigrationAndDefaults(unittest.TestCase):
    def test_normalize_raster_group_defaults(self):
        rec = {}
        norm = pc.normalize_raster_group_record(rec)
        self.assertIn('pinned', norm)
        self.assertFalse(norm.get('pinned'))
        self.assertIn('system', norm)
        self.assertFalse(norm.get('system'))

    def test_ensure_system_group_creates_grp_no_crs(self):
        # Use a temp folder
        tmp = os.path.abspath(os.path.join(os.getcwd(), 'tmp_test_project'))
        os.makedirs(tmp, exist_ok=True)
        try:
            # start with fresh catalog
            cat = pc._default_catalog(tmp)
            pc._write_catalog_file(pc.catalog_path(tmp), cat)
            grp = pc.ensure_system_group(tmp, 'grp_no_crs', 'No_CRS')
            self.assertIsNotNone(grp)
            self.assertEqual(grp.get('id'), 'grp_no_crs')
            self.assertTrue(bool(grp.get('system')))
        finally:
            # cleanup
            try:
                os.remove(pc.catalog_path(tmp))
            except Exception:
                pass
            try:
                os.rmdir(os.path.join(tmp, 'metadata'))
            except Exception:
                pass
            try:
                os.rmdir(tmp)
            except Exception:
                pass

    def test_migrate_v4_to_v5(self):
        # prepare a v4-style catalog with grp_imported named differently
        sample = {
            'catalog_version': 4,
            'schema_version': 4,
            'raster_groups': [
                {'id': 'grp_imported', 'name': 'Imported', 'radargram_ids': [], 'timeslice_ids': []}
            ],
            'timeslices': [],
            'radargrams': [],
            'models_3d': [],
            'links': [],
            'vector_layers': [],
        }
        migrated = pc._migrate_catalog_v4_to_v5('dummy', sample)
        # check catalog version
        self.assertEqual(migrated.get('catalog_version'), 5)
        self.assertEqual(migrated.get('schema_version'), 5)
        # grp_imported renamed to Unassigned
        found = next((g for g in migrated.get('raster_groups', []) if g.get('id') == 'grp_imported'), None)
        self.assertIsNotNone(found)
        self.assertEqual(found.get('name'), 'Unassigned')
        # grp_no_crs exists
        no_crs = next((g for g in migrated.get('raster_groups', []) if g.get('id') == 'grp_no_crs'), None)
        self.assertIsNotNone(no_crs)
        self.assertTrue(bool(no_crs.get('system')))

if __name__ == '__main__':
    unittest.main()
