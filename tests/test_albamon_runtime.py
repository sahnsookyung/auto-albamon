import tempfile
import unittest
from pathlib import Path

from tools.albamon_runtime import (
    AlreadyRunningError,
    atomic_write_text,
    exclusive_file_lock,
)


class AlbamonRuntimeTests(unittest.TestCase):
    def test_atomic_write_replaces_complete_file_without_temp_leak(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "report.json"
            path.write_text("old", encoding="utf-8")

            atomic_write_text(path, "new")

            self.assertEqual("new", path.read_text(encoding="utf-8"))
            self.assertEqual([], list(root.glob(".report.json.*.tmp")))

    def test_exclusive_lock_rejects_an_overlapping_process_path(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "refresh.lock"

            with exclusive_file_lock(path):
                with self.assertRaises(AlreadyRunningError):
                    with exclusive_file_lock(path):
                        self.fail("overlapping lock unexpectedly succeeded")


if __name__ == "__main__":
    unittest.main()
