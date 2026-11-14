import unittest
from pathlib import Path

from app.knowledge import extract_text
from app.models import Task
from app.sales import parse_sales_csv
from app.state import InvalidTransition, transition_task


ROOT = Path(__file__).resolve().parents[2]


class RecordingSession:
    def __init__(self):
        self.added = []

    def add(self, value):
        self.added.append(value)


class IngestionTests(unittest.TestCase):
    def test_seed_csv_is_parseable(self):
        rows, headers = parse_sales_csv((ROOT / "seed" / "sales.csv").read_bytes())
        self.assertEqual(len(rows), 4)
        self.assertIn("revenue", headers)

    def test_empty_csv_is_rejected(self):
        with self.assertRaises(ValueError):
            parse_sales_csv(b"")

    def test_seed_markdown_is_read_as_text(self):
        text = extract_text(
            "knowledge.md",
            "text/markdown",
            (ROOT / "seed" / "knowledge.md").read_bytes(),
        )
        self.assertTrue(text.startswith("# Sample"))


class StateMachineTests(unittest.TestCase):
    def test_valid_transition_is_persisted_and_logged(self):
        db = RecordingSession()
        task = Task(id="task", status="planned")

        transition_task(db, task, "running", "Task execution started.")

        self.assertEqual(task.status, "running")
        self.assertEqual(len(db.added), 1)
        self.assertEqual(db.added[0].details, {"from": "planned", "to": "running"})

    def test_invalid_transition_is_rejected(self):
        db = RecordingSession()
        task = Task(id="task", status="done")

        with self.assertRaises(InvalidTransition):
            transition_task(db, task, "running", "Invalid transition.")

        self.assertEqual(task.status, "done")
        self.assertEqual(db.added, [])


if __name__ == "__main__":
    unittest.main()
