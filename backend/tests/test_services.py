import json
import unittest
from pathlib import Path

from app.database import Base
from app.knowledge import extract_text
from app.main import app
from app.sales import parse_sales_csv
from app.settings_store import setting_value


ROOT = Path(__file__).resolve().parents[2]


class SettingRecord:
    def __init__(self, value):
        self.value = value


class SettingsTests(unittest.TestCase):
    def test_existing_setting_is_returned(self):
        class FakeSession:
            def get(self, model, key):
                return SettingRecord(900) if key == "chunk_size" else None

        self.assertEqual(setting_value(FakeSession(), "chunk_size"), 900)

    def test_missing_required_setting_raises(self):
        class FakeSession:
            def get(self, model, key):
                return None

        with self.assertRaisesRegex(ValueError, "chunk_size"):
            setting_value(FakeSession(), "chunk_size")

    def test_optional_setting_default_is_explicit(self):
        class FakeSession:
            def get(self, model, key):
                return None

        self.assertEqual(setting_value(FakeSession(), "optional_setting", []), [])


class FeatureRemovalTests(unittest.TestCase):
    def test_chatbot_and_agent_routes_are_not_exposed(self):
        paths = app.openapi()["paths"]
        self.assertTrue(paths)
        self.assertFalse(any(
            term in path.lower()
            for path in paths
            for term in ("chat", "agent", "approval", "task", "connector", "sent-item")
        ))

    def test_assistant_tables_are_not_in_active_schema(self):
        self.assertFalse(
            {"agents", "tasks", "task_steps", "events", "sent_items"} & set(Base.metadata.tables)
        )


class BusinessDataTests(unittest.TestCase):
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

    def test_settings_seed_is_valid_and_only_contains_workspace_settings(self):
        settings = json.loads((ROOT / "seed" / "settings.json").read_text(encoding="utf-8"))
        self.assertEqual([entry["key"] for entry in settings], ["chunk_size"])


if __name__ == "__main__":
    unittest.main()
