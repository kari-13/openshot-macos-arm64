"""
 @file
 @brief Unit tests for local path normalization helpers
"""

import os
import sys
import unittest


PATH = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if PATH not in sys.path:
    sys.path.append(PATH)

from classes.path_utils import comparable_local_path, normalized_local_path


class PathUtilsTests(unittest.TestCase):
    def test_windows_drive_paths_are_treated_as_local_paths(self):
        path = r"C:\Projects\example_assets\title\intro.svg"

        self.assertEqual(normalized_local_path(path), os.path.normpath(os.path.abspath(path)))
        self.assertEqual(comparable_local_path(path), os.path.normcase(os.path.normpath(os.path.abspath(path))))

    def test_content_uris_remain_opaque(self):
        path = "content://documents/tree/project.osp"

        self.assertEqual(normalized_local_path(path), path)
        self.assertEqual(comparable_local_path(path), path)
