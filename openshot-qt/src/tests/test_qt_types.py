"""
 @file
 @brief Unit tests for Qt compatibility helpers
"""

import os
import sys
import unittest


PATH = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if PATH not in sys.path:
    sys.path.append(PATH)

from classes import qt_types


class _OldIndex:
    def __init__(self):
        self.calls = []

    def row(self):
        return 7

    def sibling(self, row, column):
        self.calls.append((row, column))
        return ("sibling", row, column)


class _NewIndex(_OldIndex):
    def siblingAtColumn(self, column):
        self.calls.append(("at", column))
        return ("siblingAtColumn", column)


class _OldMetrics:
    def width(self, text):
        return len(text) * 10


class _NewMetrics(_OldMetrics):
    def horizontalAdvance(self, text):
        return len(text) * 12


class QtTypesTests(unittest.TestCase):
    def test_model_index_sibling_at_column_uses_new_api_when_available(self):
        index = _NewIndex()

        result = qt_types.model_index_sibling_at_column(index, 3)

        self.assertEqual(result, ("siblingAtColumn", 3))
        self.assertEqual(index.calls, [("at", 3)])

    def test_model_index_sibling_at_column_falls_back_to_old_api(self):
        index = _OldIndex()

        result = qt_types.model_index_sibling_at_column(index, 2)

        self.assertEqual(result, ("sibling", 7, 2))
        self.assertEqual(index.calls, [(7, 2)])

    def test_font_metrics_horizontal_advance_uses_new_api_when_available(self):
        metrics = _NewMetrics()

        self.assertEqual(qt_types.font_metrics_horizontal_advance(metrics, "abc"), 36)

    def test_font_metrics_horizontal_advance_falls_back_to_old_api(self):
        metrics = _OldMetrics()

        self.assertEqual(qt_types.font_metrics_horizontal_advance(metrics, "abc"), 30)
