import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.llm import ModelRequestError, complete
from app.models import Agent
from app.settings_store import setting_value


AVAILABLE_TOOLS = {"knowledge_search", "sales_summary", "enquiry_search"}
LEGACY_TOOL_ALIASES = {"sales_query": "sales_summary"}


def normalize_agent_tools(tools: list[str]) -> list[str]:
    normalized = []
    for tool in tools:
        canonical = LEGACY_TOOL_ALIASES.get(tool, tool)
        if canonical not in normalized:
            normalized.append(canonical)
    return normalized


class InvalidPlan(ValueError):
    pass


def _allowed_tools(agent: Agent) -> set[str]:
    tools = set(normalize_agent_tools(agent.allowed_tools)) & AVAILABLE_TOOLS
    if not agent.knowledge_access:
        tools.discard("knowledge_search")
    return tools


async def create_plan(
    db: Session,
    instruction: str,
    task_id: str,
    workspace_id: str,
    agent_ids: list[str] | None = None,
) -> tuple[list[dict], str, str, str, float]:
    statement = (
        select(Agent)
        .where(Agent.workspace_id == workspace_id)
        .order_by(Agent.created_at)
    )
    if agent_ids:
        statement = statement.where(Agent.id.in_(agent_ids))
    agents = db.scalars(statement).all()
    if not agents:
        raise InvalidPlan("Create at least one agent before starting a task.")
    agent_options = [
        {
            "id": agent.id,
            "name": agent.name,
            "role": agent.role,
            "instructions": agent.instructions,
            "allowed_tools": sorted(_allowed_tools(agent)),
            "knowledge_access": agent.knowledge_access,
        }
        for agent in agents
    ]
    max_steps = max(
        1,
        min(
            int(setting_value(db, "max_task_steps", workspace_id=workspace_id)),
            20,
        ),
    )
    system_prompt = str(setting_value(db, "system_prompt", workspace_id=workspace_id))
    prompt = (
        "Create a dependency-ordered execution plan for the user's instruction. "
        "Use the supplied agent profiles to select an agent for each step at runtime. "
        "Select only tools the chosen agent is allowed to use. Do not invent additional agents or tools. "
        "Return a JSON object with a 'steps' array. Each step must contain exactly: "
        "'description' (specific actionable string), 'agent_id' (one supplied ID), "
        "'tools' (array from that agent's allowed tools), and 'depends_on' "
        "(array of integer indices of prerequisite steps; only earlier indices are valid), "
        "and 'is_final' (boolean). Exactly one final step must be last and synthesize all required "
        "work; it must depend on every earlier step. Independent non-final steps should have no dependency. "
        "Choose tools based on the information each step needs, not only on what the agent can do. "
        "When an allowed tool is necessary to obtain current data or evidence, include it in that step's "
        "'tools' array. Any tool required for correctness must be listed; do not expect an agent to infer "
        "that a tool should be used later, and do not select unrelated tools. "
        f"Create between 1 and {max_steps} steps."
        f"\n\nUser instruction:\n{instruction}\n\nAgent profiles:\n{json.dumps(agent_options, ensure_ascii=False)}"
    )
    response = await complete(
        db,
        [{"role": "system", "content": system_prompt}, {"role": "user", "content": prompt}],
        json_mode=True,
        task_id=task_id,
        workspace_id=workspace_id,
    )
    try:
        parsed = json.loads(response.content)
    except json.JSONDecodeError as error:
        raise InvalidPlan("The orchestrator returned invalid JSON; retry the task.") from error
    raw_steps = parsed.get("steps") if isinstance(parsed, dict) else None
    if not isinstance(raw_steps, list) or not 1 <= len(raw_steps) <= max_steps:
        raise InvalidPlan("The orchestrator plan has an invalid number of steps.")
    agents_by_id = {agent.id: agent for agent in agents}
    plan: list[dict] = []
    for index, raw_step in enumerate(raw_steps):
        if not isinstance(raw_step, dict):
            raise InvalidPlan(f"Plan step {index + 1} is not an object.")
        description = raw_step.get("description")
        agent_id = raw_step.get("agent_id")
        selected_tools = raw_step.get("tools", [])
        dependencies = raw_step.get("depends_on", [])
        is_final = raw_step.get("is_final")
        if not isinstance(description, str) or not description.strip():
            raise InvalidPlan(f"Plan step {index + 1} has no actionable description.")
        if type(is_final) is not bool:
            raise InvalidPlan(f"Plan step {index + 1} has an invalid final-step marker.")
        agent = agents_by_id.get(agent_id)
        if agent is None:
            raise InvalidPlan(f"Plan step {index + 1} refers to an unknown agent.")
        allowed_tools = _allowed_tools(agent)
        if (
            not isinstance(selected_tools, list)
            or any(not isinstance(tool, str) or tool not in allowed_tools for tool in selected_tools)
        ):
            raise InvalidPlan(f"Plan step {index + 1} selected a tool not allowed for its agent.")
        if (
            not isinstance(dependencies, list)
            or any(type(dependency) is not int or dependency < 0 or dependency >= index for dependency in dependencies)
            or len(set(dependencies)) != len(dependencies)
        ):
            raise InvalidPlan(f"Plan step {index + 1} has invalid dependencies.")
        plan.append({
            "description": description.strip(),
            "agent_id": agent_id,
            "agent_name": agent.name,
            "tools": selected_tools,
            "depends_on": dependencies,
            "is_final": is_final,
        })
    if not plan[-1]["is_final"] or sum(step["is_final"] for step in plan) != 1:
        raise InvalidPlan("The plan must end with exactly one final synthesis step.")
    if plan[-1]["depends_on"] != list(range(len(plan) - 1)):
        raise InvalidPlan("The final synthesis step must depend on every earlier step.")
    return plan, response.model, response.content, prompt, response.cost
