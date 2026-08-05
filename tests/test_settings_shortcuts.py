import unittest
from settings import duplicate_shortcuts, normalize_sequence


class ShortcutTests(unittest.TestCase):
    def test_duplicate_shortcuts_are_detected(self):
        duplicates = duplicate_shortcuts({"undo": "Ctrl+Z", "redo": "Ctrl+Z"})
        self.assertEqual(duplicates[0], ("Ctrl+Z", "undo", "redo"))

    def test_empty_shortcuts_are_ignored_for_conflicts(self):
        self.assertEqual(duplicate_shortcuts({"a": "", "b": ""}), [])

    def test_sequence_normalization(self):
        self.assertEqual(normalize_sequence("Ctrl+Z"), "Ctrl+Z")


if __name__ == "__main__":
    unittest.main()
