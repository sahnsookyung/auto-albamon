import threading
import time
import unittest

from tools.dashboard_operations import OperationCapacityError, OperationRegistry


class DashboardOperationTests(unittest.TestCase):
    def test_single_flight_reuses_running_operation(self):
        registry = OperationRegistry(max_workers=1)
        release = threading.Event()

        def task(progress):
            progress(20, "waiting", None)
            release.wait(timeout=2)
            return {"ok": True}

        try:
            first = registry.start(kind="search", key="same", task=task)
            second = registry.start(kind="search", key="same", task=task)
            self.assertEqual(first["operation_id"], second["operation_id"])
            release.set()
            for _ in range(100):
                completed = registry.get(first["operation_id"])
                if completed and completed["status"] == "succeeded":
                    break
                time.sleep(0.01)
            self.assertEqual("succeeded", completed["status"])
            self.assertEqual({"ok": True}, completed["result"])
        finally:
            release.set()
            registry.shutdown()

    def test_cancel_is_observed_at_progress_checkpoint(self):
        registry = OperationRegistry(max_workers=1)
        entered = threading.Event()
        release = threading.Event()

        def task(progress):
            entered.set()
            release.wait(timeout=2)
            progress(50, "checkpoint", None)
            return {"ok": True}

        try:
            operation = registry.start(kind="search", key="cancel", task=task)
            self.assertTrue(entered.wait(timeout=1))
            registry.cancel(operation["operation_id"])
            release.set()
            for _ in range(100):
                completed = registry.get(operation["operation_id"])
                if completed and completed["status"] == "cancelled":
                    break
                time.sleep(0.01)
            self.assertEqual("cancelled", completed["status"])
            self.assertNotIn("result", completed)
        finally:
            release.set()
            registry.shutdown()

    def test_capacity_rejects_new_keys_but_preserves_single_flight(self):
        registry = OperationRegistry(max_workers=1, max_history=1)
        entered = threading.Event()
        release = threading.Event()

        def task(progress):
            entered.set()
            release.wait(timeout=2)
            return {"ok": True}

        try:
            first = registry.start(kind="search", key="same", task=task)
            self.assertTrue(entered.wait(timeout=1))
            duplicate = registry.start(kind="search", key="same", task=task)
            self.assertEqual(first["operation_id"], duplicate["operation_id"])
            with self.assertRaisesRegex(OperationCapacityError, "Too many"):
                registry.start(kind="search", key="different", task=task)
        finally:
            release.set()
            registry.shutdown()


if __name__ == "__main__":
    unittest.main()
