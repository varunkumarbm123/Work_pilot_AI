import asyncio
import random
from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.config import get_config
from app.limits import session_spend
from app.models import Event, Task, TaskStep
from app.settings_store import setting_value


@dataclass
class ModelResponse:
    content: str
    model: str
    cost_usd: float


class ModelRequestError(RuntimeError):
    def __init__(self, message: str, capacity: bool = False):
        super().__init__(message)
        self.capacity = capacity


def _request_settings(db: Session) -> tuple[int, float]:
    retries = max(0, min(int(setting_value(db, "retry_limit")), 10))
    timeout = max(1.0, min(float(setting_value(db, "request_timeout_seconds")), 180.0))
    return retries, timeout


def _model_and_cost(db: Session, response: dict[str, Any]) -> tuple[str, float]:
    model = str(response.get("model") or setting_value(db, "model_name"))
    usage = response.get("usage") or {}
    prompt_rate = float(setting_value(db, "input_cost_per_1k_usd"))
    completion_rate = float(setting_value(db, "output_cost_per_1k_usd"))
    cost = int(usage.get("prompt_tokens", 0)) * prompt_rate / 1000
    cost += int(usage.get("completion_tokens", 0)) * completion_rate / 1000
    return model, cost


def _check_model_cost_cap(
    db: Session, task_id: str | None, session_id: str | None = None
) -> None:
    if task_id:
        task = db.get(Task, task_id)
        if task is None:
            raise ModelRequestError("The task no longer exists.")
        session_id = task.session_id
    if not session_id:
        return
    spend = session_spend(db, session_id)
    cap = float(setting_value(db, "cost_cap_usd_per_session"))
    if spend >= cap:
        raise ModelRequestError(
            "The browser session reached its configured model cost cap; adjust Settings or start a new session."
        )


async def _post_with_retry(
    db: Session,
    endpoint: str,
    payload: dict[str, Any],
    task_id: str | None = None,
    step_id: str | None = None,
) -> dict[str, Any]:
    config = get_config()
    if not config.openai_api_key:
        raise ModelRequestError("The server has no model-provider API key configured.")
    if not config.openai_base_url:
        raise ModelRequestError("Configure OPENAI_BASE_URL on the server.")
    retries, timeout = _request_settings(db)
    headers = {"Authorization": f"Bearer {config.openai_api_key}"}
    async with httpx.AsyncClient(timeout=timeout) as client:
        for attempt in range(retries + 1):
            try:
                response = await client.post(
                    f"{config.openai_base_url.rstrip('/')}/{endpoint}",
                    headers=headers,
                    json=payload,
                )
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as error:
                status = error.response.status_code
                body = error.response.text[:500]
                capacity = status == 429 or "quota" in body.lower()
                if capacity and task_id:
                    if step_id:
                        step = db.get(TaskStep, step_id)
                        if step is not None:
                            step.status = "waiting_capacity"
                    db.add(Event(
                        task_id=task_id,
                        event_type="waiting_capacity",
                        message="Model provider capacity is limited; retrying automatically.",
                        details={"endpoint": endpoint, "status_code": status, "step_id": step_id},
                    ))
                    if step_id:
                        db.add(Event(
                            task_id=task_id,
                            event_type="step_status",
                            message="An agent step is waiting for model capacity.",
                            details={
                                "step_id": step_id,
                                "agent_id": step.agent_id if step else None,
                                "status": "waiting_capacity",
                            },
                        ))
                    db.commit()
                if attempt >= retries or (status < 500 and not capacity):
                    raise ModelRequestError(
                        f"Model provider returned HTTP {status}: {body}", capacity=capacity
                    ) from error
                if capacity:
                    await asyncio.sleep(min(2**attempt + random.uniform(0, 0.5), 8))
                else:
                    await asyncio.sleep(min(2**attempt + random.uniform(0, 0.5), 8))
            except (httpx.TimeoutException, httpx.TransportError) as error:
                if attempt >= retries:
                    raise ModelRequestError(f"Model provider request failed: {error}") from error
                await asyncio.sleep(min(2**attempt + random.uniform(0, 0.5), 8))
    raise ModelRequestError("Model provider request failed after configured retries.")


async def complete(
    db: Session,
    messages: list[dict[str, str]],
    json_mode: bool = False,
    task_id: str | None = None,
    step_id: str | None = None,
) -> ModelResponse:
    config = get_config()
    _check_model_cost_cap(db, task_id)
    model = str(setting_value(db, "model_name"))
    if not model:
        raise ModelRequestError("Configure a chat model in Settings or OPENAI_MODEL.")
    payload: dict[str, Any] = {"model": model, "messages": messages}
    payload["max_completion_tokens"] = int(setting_value(db, "max_completion_tokens"))
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    response = await _post_with_retry(db, "chat/completions", payload, task_id, step_id)
    choices = response.get("choices") or []
    if not choices or not choices[0].get("message", {}).get("content"):
        raise ModelRequestError("Model provider returned no completion content.")
    used_model, cost = _model_and_cost(db, response)
    return ModelResponse(choices[0]["message"]["content"], used_model, cost)


async def embed(
    db: Session,
    text: str,
    task_id: str | None = None,
    step_id: str | None = None,
    session_id: str | None = None,
) -> list[float]:
    config = get_config()
    _check_model_cost_cap(db, task_id, session_id)
    model = str(setting_value(db, "embedding_model_name"))
    if not model:
        raise ModelRequestError("Configure an embedding model in Settings or OPENAI_EMBEDDING_MODEL.")
    response = await _post_with_retry(
        db, "embeddings", {"model": model, "input": text}, task_id, step_id
    )
    data = response.get("data") or []
    if not data or not data[0].get("embedding"):
        raise ModelRequestError("Model provider returned no embedding.")
    usage = response.get("usage") or {}
    token_rate = float(setting_value(db, "embedding_cost_per_1k_usd"))
    cost = int(usage.get("prompt_tokens", 0)) * token_rate / 1000
    db.add(Event(
        task_id=task_id,
        event_type="embedding_call",
        message="A text embedding request completed.",
        details={"step_id": step_id, "cost_usd": cost, "session_id": session_id},
        model=str(response.get("model") or model),
    ))
    return data[0]["embedding"]
