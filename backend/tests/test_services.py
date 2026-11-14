import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy.dialects import postgresql

from app.knowledge import extract_text
from app.llm import (
    ModelRequestError,
    _chat_request,
    _chat_result,
    _embedding_request,
    _embedding_result,
    _provider_config,
    _request_headers,
    _request_url,
)
from app.main import list_events
from app.models import Task
from app.sales import parse_sales_csv
from app.settings_store import setting_value
from app.state import InvalidTransition, transition_task


ROOT = Path(__file__).resolve().parents[2]


class RecordingSession:
    def __init__(self):
        self.added = []

    def add(self, value):
        self.added.append(value)


class SettingRecord:
    def __init__(self, value):
        self.value = value


class SettingsTests(unittest.TestCase):
    def test_existing_setting_is_returned(self):
        class FakeSession:
            def get(self, model, key):
                return SettingRecord("configured") if key == "system_prompt" else None

        self.assertEqual(setting_value(FakeSession(), "system_prompt"), "configured")

    def test_missing_required_setting_raises(self):
        class FakeSession:
            def get(self, model, key):
                return None

        with self.assertRaisesRegex(ValueError, "system_prompt"):
            setting_value(FakeSession(), "system_prompt")

    def test_optional_setting_default_is_explicit(self):
        class FakeSession:
            def get(self, model, key):
                return None

        self.assertEqual(setting_value(FakeSession(), "example_instructions", []), [])


class GeminiProviderTests(unittest.TestCase):
    def test_gemini_provider_uses_its_server_settings(self):
        config = SimpleNamespace(
            llm_provider="gemini",
            gemini_api_key="server-secret",
            gemini_base_url="https://provider.invalid/v1beta/",
            openai_api_key="",
            openai_base_url="",
        )
        with patch("app.llm.get_config", return_value=config):
            provider, api_key, base_url = _provider_config()

        self.assertEqual((provider, api_key, base_url), ("gemini", "server-secret", "https://provider.invalid/v1beta"))

    def test_gemini_chat_and_embedding_requests_use_provider_contracts(self):
        self.assertEqual(
            _request_url("gemini", "https://provider.invalid/v1beta", "openai/chat/completions"),
            "https://provider.invalid/v1beta/openai/chat/completions",
        )
        self.assertEqual(
            _request_headers("gemini", "server-secret", "models/gemini-embedding-001:embedContent"),
            {"x-goog-api-key": "server-secret"},
        )
        endpoint, payload = _embedding_request("gemini", "gemini-embedding-001", "policy text")
        self.assertEqual(endpoint, "models/gemini-embedding-001:embedContent")
        self.assertEqual(payload["content"]["parts"][0]["text"], "policy text")
        endpoint, chat_payload = _chat_request(
            "gemini",
            "gemini-4-hydrogen",
            [
                {"role": "system", "content": "System guidance"},
                {"role": "user", "content": "Task"},
                {"role": "assistant", "content": "Prior answer"},
            ],
            512,
            True,
        )
        self.assertEqual(endpoint, "openai/chat/completions")
        self.assertEqual(chat_payload["messages"][0]["content"], "System guidance")
        self.assertEqual(chat_payload["messages"][2]["role"], "assistant")
        self.assertEqual(chat_payload["max_tokens"], 512)
        self.assertEqual(chat_payload["response_format"]["type"], "json_object")
        self.assertEqual(
            _chat_result({"choices": [{"message": {"content": "{\"ok\": true}"}}]}),
            "{\"ok\": true}",
        )
        self.assertEqual(
            _request_headers("gemini", "server-secret", "openai/chat/completions"),
            {"Authorization": "Bearer server-secret"},
        )

    def test_gemini_embedding_response_is_normalized(self):
        vector, tokens = _embedding_result(
            "gemini",
            {"embedding": {"values": [0.1, 0.2]}, "usageMetadata": {"promptTokenCount": 8}},
        )
        self.assertEqual(vector, [0.1, 0.2])
        self.assertEqual(tokens, 8)

    def test_unknown_provider_is_rejected(self):
        config = SimpleNamespace(llm_provider="unknown")
        with patch("app.llm.get_config", return_value=config):
            with self.assertRaises(ModelRequestError):
                _provider_config()


class ActivityScopeTests(unittest.TestCase):
    def test_activity_query_is_limited_to_the_browser_session(self):
        class EmptyResult:
            def all(self):
                return []

        class RecordingSession:
            statement = None

            def scalars(self, statement):
                self.statement = statement
                return EmptyResult()

        db = RecordingSession()
        self.assertEqual(list_events(x_session_id="current-session", db=db), [])
        compiled = db.statement.compile(dialect=postgresql.dialect())
        sql = str(compiled)
        self.assertIn("tasks.session_id", sql)
        self.assertIn("events.details", sql)
        self.assertIn("current-session", compiled.params.values())

    def test_activity_query_requires_a_session_identifier(self):
        class RecordingSession:
            def scalars(self, statement):
                raise AssertionError("A query must not run without a session identifier.")

        with self.assertRaises(HTTPException) as error:
            list_events(x_session_id=None, db=RecordingSession())
        self.assertEqual(error.exception.status_code, 400)


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
