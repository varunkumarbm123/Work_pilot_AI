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


def _provider_config() -> tuple[str, str, str]:
    config = get_config()
    provider = config.llm_provider.strip().lower()
    if provider == "openai":
        api_key, base_url = config.openai_api_key, config.openai_base_url
    elif provider == "gemini":
        api_key, base_url = config.gemini_api_key, config.gemini_base_url
    else:
        raise ModelRequestError(f"Unsupported LLM_PROVIDER '{provider}'. Choose openai or gemini.")
    if not api_key:
        raise ModelRequestError(f"Configure the API key for the selected provider ({provider}) on the server.")
    if not base_url:
        raise ModelRequestError(f"Configure the base URL for the selected provider ({provider}) on the server.")
    return provider, api_key, base_url.rstrip("/")


def provider_is_configured() -> bool:
    try:
        _provider_config()
    except ModelRequestError:
        return False
    return True


def _request_url(provider: str, base_url: str, endpoint: str) -> str:
    return f"{base_url}/{endpoint}"


def _request_headers(provider: str, api_key: str, endpoint: str) -> dict[str, str]:
    if provider == "gemini" and endpoint.startswith("models/"):
        return {"x-goog-api-key": api_key}
    return {"Authorization": f"Bearer {api_key}"}


def _embedding_request(provider: str, model: str, text: str) -> tuple[str, dict[str, Any]]:
    if provider == "gemini":
        resource = model if model.startswith("models/") else f"models/{model}"
        return f"{resource}:embedContent", {
            "model": resource,
            "content": {"parts": [{"text": text}]},
        }
    return "embeddings", {"model": model, "input": text}


def _chat_request(
    provider: str,
    model: str,
    messages: list[dict[str, str]],
    max_tokens: int,
    json_mode: bool,
) -> tuple[str, dict[str, Any]]:
    if provider == "gemini":
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        return "openai/chat/completions", payload

    payload = {
        "model": model,
        "messages": messages,
        "max_completion_tokens": max_tokens,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    return "chat/completions", payload


def _chat_result(response: dict[str, Any]) -> str:
    choices = response.get("choices") or []
    content = choices[0].get("message", {}).get("content", "") if choices else ""
    if not isinstance(content, str) or not content:
        raise ModelRequestError("Model provider returned no completion content.")
    return content


def _embedding_result(provider: str, response: dict[str, Any]) -> tuple[list[float], int]:
    if provider == "gemini":
        embedding = (response.get("embedding") or {}).get("values")
        usage = response.get("usageMetadata") or {}
        token_count = int(usage.get("promptTokenCount", 0))
    else:
        data = response.get("data") or []
        embedding = data[0].get("embedding") if data else None
        usage = response.get("usage") or {}
        token_count = int(usage.get("prompt_tokens", 0))
    if not isinstance(embedding, list) or not embedding:
        raise ModelRequestError("Model provider returned no embedding.")
    return embedding, token_count


def _request_settings(db: Session) -> tuple[int, float]:
    retries = max(0, min(int(setting_value(db, "retry_limit")), 10))
    timeout = max(1.0, min(float(setting_value(db, "request_timeout_seconds")), 180.0))
    return retries, timeout


def _model_and_cost(db: Session, response: dict[str, Any]) -> tuple[str, float]:
    model = str(response.get("model") or setting_value(db, "model_name"))
    usage = response.get("usage") or {}
    prompt_tokens = int(usage.get("prompt_tokens", 0))
    completion_tokens = int(usage.get("completion_tokens", 0))
    prompt_rate = float(setting_value(db, "input_cost_per_1k_usd"))
    completion_rate = float(setting_value(db, "output_cost_per_1k_usd"))
    cost = prompt_tokens * prompt_rate / 1000
    cost += completion_tokens * completion_rate / 1000
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
    provider, api_key, base_url = _provider_config()
    retries, timeout = _request_settings(db)
    headers = _request_headers(provider, api_key, endpoint)
    async with httpx.AsyncClient(timeout=timeout) as client:
        for attempt in range(retries + 1):
            try:
                response = await client.post(
                    _request_url(provider, base_url, endpoint),
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
    _check_model_cost_cap(db, task_id)
    model = str(setting_value(db, "model_name"))
    if not model:
        raise ModelRequestError("Configure a chat model in Settings.")
    provider, _, _ = _provider_config()
    endpoint, payload = _chat_request(
        provider,
        model,
        messages,
        int(setting_value(db, "max_completion_tokens")),
        json_mode,
    )
    response = await _post_with_retry(db, endpoint, payload, task_id, step_id)
    content = _chat_result(response)
    used_model, cost = _model_and_cost(db, response)
    return ModelResponse(content, used_model, cost)


async def embed(
    db: Session,
    text: str,
    task_id: str | None = None,
    step_id: str | None = None,
    session_id: str | None = None,
) -> list[float]:
    _check_model_cost_cap(db, task_id, session_id)
    model = str(setting_value(db, "embedding_model_name"))
    if not model:
        raise ModelRequestError("Configure an embedding model in Settings.")
    provider, _, _ = _provider_config()
    endpoint, payload = _embedding_request(provider, model, text)
    response = await _post_with_retry(db, endpoint, payload, task_id, step_id)
    vector, token_count = _embedding_result(provider, response)
    token_rate = float(setting_value(db, "embedding_cost_per_1k_usd"))
    cost = token_count * token_rate / 1000
    db.add(Event(
        task_id=task_id,
        event_type="embedding_call",
        message="A text embedding request completed.",
        details={"step_id": step_id, "cost_usd": cost, "session_id": session_id},
        model=str(response.get("model") or model),
    ))
    return vector
