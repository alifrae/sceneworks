from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from supervisor.journal import OperationJournal, ensure_token


class OperationJournalTests(unittest.TestCase):
    def test_accept_is_durable_across_instances(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "supervisor.db"
            journal = OperationJournal(path)
            operation_id = journal.accept(
                actor="user_ui",
                action="restart",
                component="api",
            )

            reopened = OperationJournal(path)
            row = reopened.get(operation_id)

            self.assertIsNotNone(row)
            assert row is not None
            self.assertEqual(row["state"], "ACCEPTED")
            self.assertEqual(row["actor"], "user_ui")
            self.assertEqual(row["action"], "restart")
            self.assertEqual(row["component"], "api")

    def test_detail_redacts_secret_assignments_and_is_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            journal = OperationJournal(Path(tmp) / "supervisor.db", max_detail_chars=80)
            operation_id = journal.accept(
                actor="auto",
                action="restart",
                component="mcp_tunnel",
            )
            journal.mark_running(operation_id)
            journal.finish(
                operation_id,
                result="FAILED",
                detail=(
                    "CONTROL_PLANE_API_KEY=very-secret-value "
                    "token=another-secret-value "
                    + "x" * 200
                ),
            )

            row = journal.get(operation_id)
            assert row is not None
            detail = row["detail"] or ""
            self.assertNotIn("very-secret-value", detail)
            self.assertNotIn("another-secret-value", detail)
            self.assertLessEqual(len(detail), 80)
            self.assertEqual(row["state"], "FAILED")

    def test_ensure_token_reuses_existing_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            first = ensure_token(data_dir)
            second = ensure_token(data_dir)

            self.assertEqual(first, second)
            self.assertGreaterEqual(len(first), 32)
            self.assertEqual((data_dir / "token").read_text(encoding="utf-8").strip(), first)


if __name__ == "__main__":
    unittest.main()
