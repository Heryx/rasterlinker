# -*- coding: utf-8 -*-
import unittest
import os
import sys
from pathlib import Path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
import project_catalog as pc

class TestPinnedAndUnassignedBehavior(unittest.TestCase):
    def _make_tmp(self, name='tmp_proj_for_tests'):
        tmp = os.path.abspath(os.path.join(os.getcwd(), name))
        os.makedirs(tmp, exist_ok=True)
        return tmp

    def _cleanup_tmp(self, tmp):
        try:
            p = pc.catalog_path(tmp)
            if os.path.exists(p):
                os.remove(p)
        except Exception:
            pass
        try:
            meta_dir = os.path.join(tmp, 'metadata')
            if os.path.isdir(meta_dir):
                os.rmdir(meta_dir)
        except Exception:
            pass
        try:
            os.rmdir(tmp)
        except Exception:
            pass

    def test_pinned_flag_persisted_on_update(self):
        tmp = self._make_tmp('tmp_proj_pinned')
        try:
            # start with fresh catalog
            cat = pc._default_catalog(tmp)
            pc._write_catalog_file(pc.catalog_path(tmp), cat)

            # create a group
            grp, created = pc.create_raster_group(tmp, 'MyGroup')
            self.assertTrue(created)
            self.assertFalse(grp.get('pinned'))

            # update group to pinned
            grp_id = grp.get('id')
            pc.update_raster_group(tmp, grp_id, {'pinned': True})

            # reload catalog and verify
            data = pc.load_catalog(tmp)
            found = next((g for g in data.get('raster_groups', []) if g.get('id') == grp_id), None)
            self.assertIsNotNone(found)
            self.assertTrue(bool(found.get('pinned')))
        finally:
            self._cleanup_tmp(tmp)

    def test_auto_assign_no_crs_flow(self):
        tmp = self._make_tmp('tmp_proj_unassigned')
        try:
            # fresh catalog
            cat = pc._default_catalog(tmp)
            pc._write_catalog_file(pc.catalog_path(tmp), cat)

            # register a timeslice without CRS
            rec = {
                'id': 'ts_test_1',
                'normalized_name': 'ts1.tif',
                'project_path': os.path.join(tmp, 'timeslices_2d', 'ts1.tif'),
                'crs': None,
            }
            # ensure timeslices folder exists for path
            ts_dir = os.path.dirname(rec['project_path'])
            os.makedirs(ts_dir, exist_ok=True)
            # create an empty placeholder file
            Path(rec['project_path']).write_text('')

            pc.register_timeslices_batch(tmp, [rec])
            # add to default imported group to simulate initial import behavior
            pc.add_timeslice_to_default_group(tmp, rec['id'])

            # auto-assign to grp_no_crs
            pc.ensure_system_group(tmp, 'grp_no_crs', 'No_CRS')
            pc.assign_timeslices_to_group(tmp, 'grp_no_crs', [rec['id']])
            # remove from imported
            pc.remove_timeslices_from_group(tmp, 'grp_imported', [rec['id']])

            data = pc.load_catalog(tmp)
            g_no = next((g for g in data.get('raster_groups', []) if g.get('id') == 'grp_no_crs'), None)
            self.assertIsNotNone(g_no)
            self.assertIn(rec['id'], g_no.get('timeslice_ids', []))

            g_imp = next((g for g in data.get('raster_groups', []) if g.get('id') == 'grp_imported'), None)
            # imported group should not contain the id
            if g_imp:
                self.assertNotIn(rec['id'], g_imp.get('timeslice_ids', []))
        finally:
            # cleanup placeholder file and directories
            try:
                if os.path.exists(rec.get('project_path')):
                    os.remove(rec.get('project_path'))
            except Exception:
                pass
            self._cleanup_tmp(tmp)

if __name__ == '__main__':
    unittest.main()
