"""
 @file
 @brief Targeted unit tests for project-file thumbnail helper logic.
"""

import importlib
import os
import sys
import unittest
from unittest.mock import patch


PATH = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
THUMBNAIL_PATH = os.path.join(PATH, "images", "thumb.png")
if PATH not in sys.path:
    sys.path.append(PATH)


class FilesModelHelperTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.files_model_module = importlib.import_module("windows.models.files_model")

    def test_icon_from_thumbnail_source_prefers_freshly_loaded_pixmap(self):
        def load_pixmap(_, path):
            return path == THUMBNAIL_PATH

        pixmap = type(
            "PixmapStub",
            (),
            {
                "load": load_pixmap,
                "isNull": lambda self: False,
            },
        )()

        with patch.object(self.files_model_module, "QPixmap", return_value=pixmap), \
                patch.object(self.files_model_module, "QIcon", side_effect=lambda arg: ("icon", arg)) as qicon:
            result = self.files_model_module.FilesModel._icon_from_thumbnail_source(THUMBNAIL_PATH)

        self.assertEqual(result, ("icon", pixmap))
        qicon.assert_called_once_with(pixmap)

    def test_icon_from_thumbnail_source_falls_back_to_path_when_pixmap_load_fails(self):
        pixmap = type(
            "PixmapStub",
            (),
            {
                "load": lambda self, path: False,
                "isNull": lambda self: True,
            },
        )()

        with patch.object(self.files_model_module, "QPixmap", return_value=pixmap), \
                patch.object(self.files_model_module, "QIcon", side_effect=lambda arg: ("icon", arg)) as qicon:
            result = self.files_model_module.FilesModel._icon_from_thumbnail_source(THUMBNAIL_PATH)

        self.assertEqual(result, ("icon", THUMBNAIL_PATH))
        qicon.assert_called_once_with(THUMBNAIL_PATH)


if __name__ == "__main__":
    unittest.main()
