import asyncio
import hashlib
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from app.auth import create_session, get_session_user, hash_password, verify_password
from app.knowledge import _full_text_query, extract_text, ingest_document, retrieve
from app.llm import (
    ModelRequestError,
    _chat_request,
    _chat_result,
    _embedding_request,
    _embedding_result,
    _provider_config,
    _request_headers,
    _request_url,
    provider_supports_embeddings,
)
from app.main import (
    ApprovalInput,
    SettingInput,
    app,
    _set_auth_cookie,
    list_agents,
    list_events,
    review_task,
    retry_task,
    update_setting,
)
from app.models import AuthSession, KnowledgeChunk, Setting, Task, User, WorkspaceSetting
from app.orchestrator import _allowed_tools, create_plan, normalize_agent_tools
from app.sales import parse_sales_csv, sales_summary
from app.settings_store import setting_value
from app.state import InvalidTransition, transition_task
from app.status import StepStatus, TaskStatus


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

    def test_workspace_setting_overrides_global_default(self):
        class FakeSession:
            def get(self, model, key):
                if model is WorkspaceSetting:
                    return SettingRecord("private prompt")
                if model is Setting:
                    return SettingRecord("global prompt")
                return None

        self.assertEqual(
            setting_value(
                FakeSession(),
                "system_prompt",
                workspace_id="workspace-a",
            ),
            "private prompt",
        )


class AuthenticationTests(unittest.TestCase):
    def test_password_hash_is_not_reversible_and_verifies_only_the_password(self):
        stored = hash_password("a-long-password-for-tests")

        self.assertNotIn("a-long-password-for-tests", stored)
        self.assertTrue(verify_password("a-long-password-for-tests", stored))
        self.assertFalse(verify_password("another-password", stored))
        self.assertFalse(verify_password("a-long-password-for-tests", "invalid"))

    def test_persisted_session_key_is_a_hash_and_resolves_to_its_user(self):
        class CaptureSession:
            added = []

            def add(self, item):
                self.added.append(item)

        db = CaptureSession()
        token, session = create_session(cast(Session, db), "user-id", 14)
        user = SimpleNamespace(id="user-id")

        class LookupSession:
            def get(self, model, key):
                if model is AuthSession:
                    self.assert_hash = key
                    return session
                if model is User:
                    return user
                return None

        lookup = LookupSession()
        self.assertEqual(len(db.added), 1)
        self.assertNotEqual(session.token_hash, token)
        self.assertEqual(session.token_hash, hashlib.sha256(token.encode()).hexdigest())
        self.assertIs(get_session_user(cast(Session, lookup), token), user)
        self.assertEqual(lookup.assert_hash, session.token_hash)

    def test_expired_session_is_revoked(self):
        session = SimpleNamespace(
            expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)
        )

        class ExpiredSession:
            deleted = None
            committed = False

            def get(self, model, key):
                return session if model is AuthSession else None

            def delete(self, value):
                self.deleted = value

            def commit(self):
                self.committed = True

        db = ExpiredSession()
        self.assertIsNone(
            get_session_user(cast(Session, db), "expired-token")
        )
        self.assertIs(db.deleted, session)
        self.assertTrue(db.committed)

    def test_auth_cookie_is_http_only_secure_and_same_site(self):
        from fastapi.responses import JSONResponse

        config = SimpleNamespace(
            auth_cookie_name="workpilot_session",
            auth_cookie_secure=True,
            auth_cookie_samesite="lax",
            auth_session_days=14,
        )
        response = JSONResponse({})
        with patch("app.main.get_config", return_value=config):
            _set_auth_cookie(response, "opaque-token")

        cookie = response.headers["set-cookie"]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("Secure", cookie)
        self.assertIn("SameSite=lax", cookie)


class CurrencySettingTests(unittest.IsolatedAsyncioTestCase):
    async def test_billing_currency_requires_three_uppercase_letters(self):
        class FakeSetting:
            value = ""
            description = "currency"

        class FakeSession:
            record = FakeSetting()

            def get(self, model, key):
                return self.record

            def add(self, value):
                pass

            def commit(self):
                pass

        db = FakeSession()
        with self.assertRaises(HTTPException) as error:
            await update_setting(
                "billing_currency",
                SettingInput(value="abc"),
                workspace_id="workspace-id",
                db=cast(Session, db),
            )
        self.assertEqual(error.exception.status_code, 422)

        result = await update_setting(
            "billing_currency",
            SettingInput(value="ABC"),
            workspace_id="workspace-id",
            db=cast(Session, db),
        )
        self.assertEqual(result["value"], "ABC")


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

    def test_groq_provider_uses_its_server_settings(self):
        config = SimpleNamespace(
            llm_provider="groq",
            groq_api_key="server-secret",
            groq_base_url="https://api.groq.com/openai/v1/",
        )
        with patch("app.llm.get_config", return_value=config):
            provider, api_key, base_url = _provider_config()

        self.assertEqual((provider, api_key, base_url), ("groq", "server-secret", "https://api.groq.com/openai/v1"))
        self.assertEqual(
            _request_headers("groq", "server-secret", "chat/completions"),
            {"Authorization": "Bearer server-secret"},
        )
        self.assertEqual(
            _request_url(provider, base_url, "chat/completions"),
            "https://api.groq.com/openai/v1/chat/completions",
        )
        endpoint, payload = _chat_request(
            provider,
            "openai/gpt-oss-120b",
            [{"role": "user", "content": "Create a plan."}],
            512,
            True,
        )
        self.assertEqual(endpoint, "chat/completions")
        self.assertEqual(payload["model"], "openai/gpt-oss-120b")
        self.assertEqual(payload["max_completion_tokens"], 512)
        self.assertEqual(payload["response_format"]["type"], "json_object")

    def test_groq_does_not_claim_embedding_support(self):
        with patch("app.llm.get_config", return_value=SimpleNamespace(llm_provider="groq")):
            self.assertFalse(provider_supports_embeddings())

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


class ActivityScopeTests(unittest.IsolatedAsyncioTestCase):
    def test_activity_query_is_limited_to_the_workspace(self):
        class EmptyResult:
            def all(self):
                return []

        class RecordingSession:
            statement = None

            def scalars(self, statement):
                self.statement = statement
                return EmptyResult()

        db = RecordingSession()
        self.assertEqual(list_events(workspace_id="current-workspace", db=db), [])
        compiled = db.statement.compile(dialect=postgresql.dialect())
        sql = str(compiled)
        self.assertIn("tasks.workspace_id", sql)
        self.assertIn("events.workspace_id", sql)
        self.assertIn("events.details", sql)
        self.assertIn("current-workspace", compiled.params.values())

    def test_agent_list_is_workspace_scoped(self):
        class EmptyResult:
            def all(self):
                return []

        class RecordingSession:
            statement = None

            def scalars(self, statement):
                self.statement = statement
                return EmptyResult()

        db = RecordingSession()
        self.assertEqual(list_agents(workspace_id="workspace-a", db=db), [])
        compiled = db.statement.compile(dialect=postgresql.dialect())
        self.assertIn("agents.workspace_id", str(compiled))
        self.assertIn("workspace-a", compiled.params.values())

    def test_sales_summary_is_workspace_scoped(self):
        class EmptyResult:
            def all(self):
                return []

        class RecordingSession:
            statement = None

            def scalars(self, statement):
                self.statement = statement
                return EmptyResult()

        db = RecordingSession()
        sales_summary(cast(Session, db), "workspace-a")
        compiled = db.statement.compile(dialect=postgresql.dialect())
        self.assertIn("sales_rows.workspace_id", str(compiled))
        self.assertIn("workspace-a", compiled.params.values())

    async def test_knowledge_retrieval_is_workspace_scoped(self):
        class EmptyResult:
            def all(self):
                return []

        class RecordingSession:
            statement = None

            def execute(self, statement):
                self.statement = statement
                return EmptyResult()

        db = RecordingSession()
        with (
            patch("app.knowledge.provider_supports_embeddings", return_value=False),
            patch(
                "app.knowledge.setting_value",
                side_effect=lambda _, key, **kwargs: 5,
            ),
        ):
            self.assertEqual(
                await retrieve(cast(Session, db), "policy", workspace_id="workspace-a"),
                [],
            )
        compiled = db.statement.compile(dialect=postgresql.dialect())
        self.assertIn("documents.workspace_id", str(compiled))
        self.assertIn("workspace-a", compiled.params.values())

    def test_workspace_task_lookup_is_scoped(self):
        class RecordingSession:
            statement = None

            def scalar(self, statement):
                self.statement = statement
                return None

        from app.main import _workspace_task

        db = RecordingSession()
        with self.assertRaises(HTTPException) as error:
            _workspace_task(db, "foreign-task", "current-workspace")
        self.assertEqual(error.exception.status_code, 404)
        compiled = db.statement.compile(dialect=postgresql.dialect())
        self.assertIn("tasks.workspace_id", str(compiled))
        self.assertIn("current-workspace", compiled.params.values())


class ApiSurfaceTests(unittest.TestCase):
    def test_workflow_routes_are_present_without_chat_routes(self):
        paths = app.openapi()["paths"]
        self.assertIn("/api/tasks", paths)
        self.assertIn("/api/auth/login", paths)
        self.assertIn("/api/auth/signup", paths)
        self.assertIn("/api/approvals", paths)
        self.assertIn("/api/events", paths)
        self.assertFalse(any("/chat" in path for path in paths))


class ApprovalExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_rejection_schedules_regeneration_on_the_running_event_loop(self):
        task = SimpleNamespace(
            id="task-id",
            status=TaskStatus.AWAITING_APPROVAL.value,
            shared_context={},
        )
        step = SimpleNamespace(
            id="step-id",
            agent_id="agent-id",
            status=StepStatus.AWAITING_APPROVAL.value,
            completed_at=None,
        )

        class FakeSession:
            def get(self, model, item_id):
                return task

            def scalar(self, statement):
                if "tasks" in str(statement):
                    return task
                return step

            def add(self, item):
                pass

            def commit(self):
                pass

        with (
            patch("app.main.execute_task", new_callable=AsyncMock) as execute_task,
            patch.object(app.state, "background_tasks", set(), create=True),
        ):
            result = await review_task(
                task.id,
                ApprovalInput(action="reject", reason="Please remove unsupported assumptions."),
                workspace_id="workspace-id",
                db=cast(Session, FakeSession()),
            )
            scheduled = list(app.state.background_tasks)
            self.assertEqual(result, {"status": "regenerating"})
            self.assertEqual(task.status, TaskStatus.REJECTED.value)
            self.assertEqual(step.status, StepStatus.QUEUED.value)
            self.assertEqual(len(scheduled), 1)
            await asyncio.gather(*scheduled)

        execute_task.assert_awaited_once_with(task.id)

    async def test_failed_task_retry_schedules_on_the_running_event_loop(self):
        task = SimpleNamespace(id="task-id", status=TaskStatus.FAILED.value)

        class FakeSession:
            def scalar(self, statement):
                return task

            def add(self, item):
                pass

            def commit(self):
                pass

        with (
            patch("app.main.execute_task", new_callable=AsyncMock) as execute_task,
            patch.object(app.state, "background_tasks", set(), create=True),
        ):
            result = await retry_task(
                task.id,
                workspace_id="workspace-id",
                db=cast(Session, FakeSession()),
            )
            scheduled = list(app.state.background_tasks)
            self.assertEqual(result, {"status": "retrying"})
            self.assertEqual(len(scheduled), 1)
            await asyncio.gather(*scheduled)

        execute_task.assert_awaited_once_with(task.id)


class IngestionTests(unittest.TestCase):
    def test_full_text_query_is_sanitized_and_uses_unique_terms(self):
        self.assertEqual(_full_text_query("Policy, policy! Keep-all"), "policy | keep | all")
        self.assertEqual(_full_text_query("***"), "")

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


class KnowledgeChunkTests(unittest.IsolatedAsyncioTestCase):
    async def test_configured_overlap_preserves_boundary_context_without_duplicate_tail(self):
        class CaptureSession:
            def __init__(self):
                self.added = []

            def add(self, value):
                self.added.append(value)

            def flush(self):
                pass

            def commit(self):
                pass

            def refresh(self, value):
                pass

        text = "a" * 100 + "b" * 50
        db = CaptureSession()
        with (
            patch(
                "app.knowledge.setting_value",
                side_effect=lambda _, key, **kwargs: {
                    "chunk_size": 100,
                    "chunk_overlap": 20,
                }[key],
            ),
            patch("app.knowledge.provider_supports_embeddings", return_value=False),
        ):
            await ingest_document(
                cast(Session, db), "workspace-id", "policy.md", "text/markdown", text
            )

        chunks = [item.content for item in db.added if isinstance(item, KnowledgeChunk)]
        self.assertEqual(chunks, [text[:100], text[80:]])


class StateMachineTests(unittest.TestCase):
    def test_valid_transition_is_persisted_and_logged(self):
        db = RecordingSession()
        task = Task(id="task", status="planned")

        transition_task(cast(Session, db), task, TaskStatus.RUNNING, "Task execution started.")

        self.assertEqual(task.status, "running")
        self.assertEqual(len(db.added), 1)
        self.assertEqual(db.added[0].details, {"from": "planned", "to": "running"})

    def test_invalid_transition_is_rejected(self):
        db = RecordingSession()
        task = Task(id="task", status="done")

        with self.assertRaises(InvalidTransition):
            transition_task(cast(Session, db), task, TaskStatus.RUNNING, "Invalid transition.")

        self.assertEqual(task.status, "done")
        self.assertEqual(db.added, [])


class OrchestrationTests(unittest.IsolatedAsyncioTestCase):
    def test_legacy_sales_tool_name_is_normalized(self):
        agent = SimpleNamespace(
            allowed_tools=["sales_query", "knowledge_search", "sales_summary"],
            knowledge_access=False,
        )

        self.assertEqual(normalize_agent_tools(agent.allowed_tools), ["sales_summary", "knowledge_search"])
        self.assertEqual(_allowed_tools(agent), {"sales_summary"})

    async def test_plan_prompt_requires_tools_needed_for_correctness(self):
        agent = SimpleNamespace(
            id="sales-agent",
            name="Sales Analyst",
            role="Analyze uploaded sales data.",
            instructions="Use the sales data tool for every numeric claim.",
            allowed_tools=["sales_summary"],
            knowledge_access=False,
        )

        class FakeResult:
            def all(self):
                return [agent]

        class FakeSession:
            def scalars(self, statement):
                return FakeResult()

        model_response = SimpleNamespace(
            content=(
                '{"steps":[{"description":"Analyze sales data","agent_id":"sales-agent",'
                '"tools":["sales_summary"],"depends_on":[],"is_final":true}]}'
            ),
            model="test-model",
            cost=0,
        )
        with (
            patch(
                "app.orchestrator.setting_value",
                side_effect=lambda db, key, **kwargs: {"max_task_steps": 6, "system_prompt": "Base prompt"}[key],
            ),
            patch("app.orchestrator.complete", new_callable=AsyncMock) as complete,
        ):
            complete.return_value = model_response
            plan, _, _, _, _ = await create_plan(
                cast(Session, FakeSession()),
                "Analyze sales.",
                "task-id",
                "workspace-id",
            )

        prompt = complete.await_args.args[1][1]["content"]
        self.assertIn("Any tool required for correctness must be listed", prompt)
        self.assertIn('"sales_summary"', prompt)
        self.assertEqual(plan[0]["tools"], ["sales_summary"])

    async def test_agent_filter_limits_profiles_available_to_planner(self):
        selected = SimpleNamespace(
            id="selected-agent",
            name="Selected specialist",
            role="Handle this selected task.",
            instructions="Use the allowed tool.",
            allowed_tools=["sales_summary"],
            knowledge_access=False,
        )

        class FakeResult:
            def all(self):
                return [selected]

        class FakeSession:
            statement: Any = None

            def scalars(self, statement):
                self.statement = statement
                return FakeResult()

        model_response = SimpleNamespace(
            content=(
                '{"steps":[{"description":"Analyze sales data","agent_id":"selected-agent",'
                '"tools":["sales_summary"],"depends_on":[],"is_final":true}]}'
            ),
            model="test-model",
            cost=0,
        )
        db = FakeSession()
        with (
            patch(
                "app.orchestrator.setting_value",
                side_effect=lambda session, key, **kwargs: {"max_task_steps": 6, "system_prompt": "Base prompt"}[key],
            ),
            patch("app.orchestrator.complete", new_callable=AsyncMock) as complete,
        ):
            complete.return_value = model_response
            plan, _, _, _, _ = await create_plan(
                cast(Session, db),
                "Analyze sales.",
                "task-id",
                "workspace-id",
                ["selected-agent"],
            )

        compiled = db.statement.compile(dialect=postgresql.dialect())
        self.assertIn(["selected-agent"], compiled.params.values())
        prompt = complete.await_args.args[1][1]["content"]
        self.assertIn("Selected specialist", prompt)
        self.assertEqual(plan[0]["agent_id"], "selected-agent")


if __name__ == "__main__":
    unittest.main()
