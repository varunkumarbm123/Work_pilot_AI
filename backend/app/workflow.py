import asyncio
import json
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.knowledge import retrieve
from app.llm import ModelRequestError, complete
from app.models import Agent, Enquiry, Event, Task, TaskStep
from app.orchestrator import InvalidPlan, create_plan
from app.sales import sales_summary
from app.settings_store import setting_value
from app.state import InvalidTransition, transition_task
from app.status import StepStatus, TaskStatus
from app.validator import InvalidVerdict, validate_draft


def _record_model_call(
    db: Session,
    task_id: str,
    step_id: str | None,
    model: str,
    prompt: str,
    cost: float,
) -> None:
    db.add(Event(
        task_id=task_id,
        event_type="model_call",
        message="A language-model request completed.",
        details={
            "step_id": step_id,
            "prompt": prompt,
            "cost": cost,
            "currency": str(setting_value(db, "billing_currency")),
        },
        model=model,
    ))
    db.commit()


async def _execute_step(task_id: str, step_id: str, context: dict) -> dict:
    with SessionLocal() as db:
        step = db.get(TaskStep, step_id)
        task = db.get(Task, task_id)
        if step is None or task is None:
            raise ValueError("The task or planned step no longer exists.")
        agent = db.get(Agent, step.agent_id) if step.agent_id else None
        if agent is None:
            raise ValueError("The assigned agent no longer exists; edit the plan and retry.")
        step.status = StepStatus.RUNNING.value
        step.started_at = datetime.now(timezone.utc)
        step.attempt_count += 1
        db.add(Event(
            task_id=task_id,
            event_type="step_status",
            message="An agent started a planned step.",
            details={"step_id": step_id, "agent_id": agent.id, "status": StepStatus.RUNNING.value},
        ))
        db.commit()

        tool_results: dict[str, object] = {}
        sources = []
        if "knowledge_search" in step.tools and agent.knowledge_access:
            sources = await retrieve(
                db,
                f"{task.instruction}\n{step.description}",
                task_id=task_id,
                step_id=step_id,
            )
            tool_results["knowledge_search"] = [
                {
                    "document": source.document,
                    "chunk_id": source.chunk_id,
                    "content": source.content,
                }
                for source in sources
            ]
            db.add(Event(
                task_id=task_id,
                event_type="retrieval",
                message="Knowledge Hub search completed.",
                details={
                    "step_id": step_id,
                    "query": f"{task.instruction}\n{step.description}",
                    "sources": tool_results["knowledge_search"],
                },
            ))
            db.commit()
        if "sales_summary" in step.tools:
            tool_results["sales_summary"] = sales_summary(db)
            db.add(Event(
                task_id=task_id,
                event_type="tool_call",
                message="Read-only sales summary tool completed.",
                details={"step_id": step_id, "tool": "sales_summary", "result": tool_results["sales_summary"]},
            ))
            db.commit()
        if "enquiry_search" in step.tools:
            enquiries = db.scalars(
                select(Enquiry).order_by(Enquiry.created_at.desc()).limit(20)
            ).all()
            tool_results["enquiry_search"] = [
                {
                    "id": item.id,
                    "customer_name": item.customer_name,
                    "customer_email": item.customer_email,
                    "subject": item.subject,
                    "message": item.message,
                    "status": item.status,
                    "created_at": item.created_at.isoformat(),
                }
                for item in enquiries
            ]
            db.add(Event(
                task_id=task_id,
                event_type="tool_call",
                message="Read-only customer enquiry search completed.",
                details={"step_id": step_id, "tool": "enquiry_search", "result": tool_results["enquiry_search"]},
            ))
            db.commit()

        retry_limit = max(0, min(int(setting_value(db, "retry_limit")), 10))
        feedback: list[str] = []
        verdict: dict = {}
        output = ""
        validator_sources = []
        for attempt in range(retry_limit + 1):
            review_feedback = (context.get("review_feedback") or {}).get(step_id, [])
            user_prompt = (
                f"Business instruction:\n{task.instruction}\n\nAssigned work:\n{step.description}"
                f"\n\nShared context from completed work:\n{json.dumps(context, ensure_ascii=False)}"
                f"\n\nResults from tools you are allowed to use:\n{json.dumps(tool_results, ensure_ascii=False)}"
                f"\n\nRevision feedback from validation:\n{json.dumps(feedback, ensure_ascii=False)}"
                f"\n\nReviewer feedback from a previous rejection:\n{json.dumps(review_feedback, ensure_ascii=False)}"
                "\n\nDo the assigned work using only the supplied evidence and tool results. "
                "Do not invent policies, facts, or numeric results. Cite knowledge sources by document "
                "and chunk ID. Return the draft only; no external action is authorized."
            )
            system_prompt = str(setting_value(db, "system_prompt"))
            system_prompt += (
                f"\n\nAgent name: {agent.name}\nRole: {agent.role}\n"
                f"Agent instructions: {agent.instructions}\n"
                f"Tools available for this step: {json.dumps(step.tools)}"
            )
            response = await complete(
                db,
                [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
                task_id=task_id,
                step_id=step_id,
            )
            output = response.content
            _record_model_call(db, task_id, step_id, response.model, user_prompt, response.cost)
            verdict, validator_sources, validator_model, validator_prompt = await validate_draft(
                db, task_id, step_id, task.instruction, output, tool_results
            )
            if verdict["valid"]:
                break
            feedback = verdict["reasons"]
            if attempt < retry_limit:
                db.add(Event(
                    task_id=task_id,
                    event_type="validation_retry",
                    message="The validator requested another draft attempt.",
                    details={"step_id": step_id, "attempt": attempt + 1, "reasons": feedback},
                    model=validator_model,
                ))
                db.commit()

        for source in validator_sources:
            if all(existing.chunk_id != source.chunk_id for existing in sources):
                sources.append(source)
        step.output = output
        step.sources = [
            {"document": source.document, "chunk_id": source.chunk_id, "content": source.content}
            for source in sources
        ]
        step.status = StepStatus.DONE.value
        step.completed_at = datetime.now(timezone.utc)
        db.add(Event(
            task_id=task_id,
            event_type="step_status",
            message="An agent completed a planned step.",
            details={"step_id": step_id, "agent_id": agent.id, "status": StepStatus.DONE.value},
        ))
        db.commit()
        return {
            "step_id": step_id,
            "description": step.description,
            "output": output,
            "validation": verdict,
            "sources": step.sources,
        }


async def execute_task(task_id: str) -> None:
    with SessionLocal() as db:
        task = db.get(Task, task_id)
        if task is None:
            return
        if task.status in {TaskStatus.DONE.value, TaskStatus.AWAITING_APPROVAL.value}:
            return
        try:
            if not task.plan:
                plan, model, planner_output, plan_prompt, plan_cost = await create_plan(
                    db,
                    task.instruction,
                    task.id,
                    (task.shared_context or {}).get("agent_filter"),
                )
                task.plan = plan
                db.add(Event(
                    task_id=task.id,
                    event_type="plan_generated",
                    message="The orchestrator generated and validated a dependency-ordered plan.",
                    details={"plan": plan, "prompt": plan_prompt, "response": planner_output},
                    model=model,
                ))
                db.add(Event(
                    task_id=task.id,
                    event_type="model_call",
                    message="The orchestrator plan request completed.",
                    details={
                        "step_id": None,
                        "prompt": plan_prompt,
                        "cost": plan_cost,
                        "currency": str(setting_value(db, "billing_currency")),
                    },
                    model=model,
                ))
                for index, item in enumerate(plan):
                    planned_step = TaskStep(
                        task_id=task.id,
                        order_index=index,
                        description=item["description"],
                        agent_id=item["agent_id"],
                        tools=item["tools"],
                        depends_on=item["depends_on"],
                    )
                    db.add(planned_step)
                    db.flush()
                    db.add(Event(
                        task_id=task.id,
                        event_type="step_status",
                        message="An agent step was queued from the orchestrator plan.",
                        details={
                            "step_id": planned_step.id,
                            "agent_id": item["agent_id"],
                            "status": StepStatus.QUEUED.value,
                            "order_index": index,
                        },
                    ))
                db.commit()
            if task.status in {
                TaskStatus.PLANNED.value,
                TaskStatus.FAILED.value,
                TaskStatus.REJECTED.value,
            }:
                transition_task(db, task, TaskStatus.RUNNING, "Task execution started.")
                db.commit()

            steps = db.scalars(
                select(TaskStep).where(TaskStep.task_id == task_id).order_by(TaskStep.order_index)
            ).all()
            for step in steps:
                if step.status in {StepStatus.RUNNING.value, StepStatus.WAITING_CAPACITY.value}:
                    step.status = StepStatus.QUEUED.value
            db.commit()
            context = dict(task.shared_context or {})
            completed = {
                step.order_index: step
                for step in steps
                if step.status in {StepStatus.DONE.value, StepStatus.APPROVED.value}
            }
            while len(completed) < len(steps):
                ready = [
                    step for step in steps
                    if step.order_index not in completed
                    and step.status in {StepStatus.QUEUED.value, StepStatus.FAILED.value}
                    and all(dependency in completed for dependency in step.depends_on)
                ]
                if not ready:
                    raise InvalidPlan("The stored plan cannot make progress because dependencies are unresolved.")
                shared_context = {
                    **context,
                    "completed_steps": [
                        {
                            "description": completed[index].description,
                            "output": completed[index].output,
                        }
                        for index in sorted(completed)
                    ],
                }
                results = await asyncio.gather(
                    *[_execute_step(task_id, step.id, shared_context) for step in ready],
                    return_exceptions=True,
                )
                failure = next((result for result in results if isinstance(result, BaseException)), None)
                if failure is not None:
                    raise failure
                for step, result in zip(ready, results):
                    completed[step.order_index] = step
                    context[f"step_{step.order_index}"] = result
                task.shared_context = context
                db.commit()

            final_step = steps[-1]
            final_step.status = StepStatus.AWAITING_APPROVAL.value
            db.add(Event(
                task_id=task_id,
                event_type="step_status",
                message="The final agent draft is awaiting human approval.",
                details={
                    "step_id": final_step.id,
                    "agent_id": final_step.agent_id,
                    "status": StepStatus.AWAITING_APPROVAL.value,
                },
            ))
            transition_task(
                db,
                task,
                TaskStatus.AWAITING_APPROVAL,
                "Work completed and is waiting for human approval.",
            )
            db.add(Event(
                task_id=task_id,
                event_type="approval_requested",
                message="The final draft is ready for human review.",
                details={"step_id": final_step.id, "validation": context.get(f"step_{final_step.order_index}", {}).get("validation", {})},
            ))
            db.commit()
        except (InvalidPlan, InvalidVerdict, ModelRequestError, InvalidTransition, SQLAlchemyError, ValueError) as error:
            db.rollback()
            _record_task_failure(db, task_id, error)
        except Exception as error:
            db.rollback()
            _record_task_failure(db, task_id, error)


def _record_task_failure(db: Session, task_id: str, error: Exception) -> None:
    task = db.get(Task, task_id)
    if task is None or task.status in {TaskStatus.DONE.value, TaskStatus.REJECTED.value}:
        return
    for step in db.scalars(select(TaskStep).where(
        TaskStep.task_id == task_id,
        TaskStep.status.in_((StepStatus.RUNNING.value, StepStatus.WAITING_CAPACITY.value)),
    )).all():
        step.status = StepStatus.FAILED.value
        db.add(Event(
            task_id=task_id,
            event_type="step_status",
            message="An agent step failed.",
            details={"step_id": step.id, "agent_id": step.agent_id, "status": StepStatus.FAILED.value},
        ))
    if task.status != TaskStatus.FAILED.value:
        transition_task(db, task, TaskStatus.FAILED, "Task execution failed.")
    db.add(Event(
        task_id=task_id,
        event_type="task_error",
        message=str(error),
        details={"error_type": type(error).__name__},
    ))
    db.commit()
