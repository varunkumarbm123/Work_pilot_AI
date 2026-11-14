import json

from sqlalchemy.orm import Session

from app.knowledge import RetrievedSource, retrieve
from app.llm import complete
from app.models import Event
from app.settings_store import setting_value


class InvalidVerdict(ValueError):
    pass


async def validate_draft(
    db: Session,
    task_id: str,
    step_id: str,
    instruction: str,
    draft: str,
    business_evidence: dict | None = None,
) -> tuple[dict, list[RetrievedSource], str, str]:
    policy_sources = await retrieve(db, f"{instruction}\n{draft}", task_id=task_id, step_id=step_id)
    policy = [
        {"document": source.document, "chunk_id": source.chunk_id, "text": source.content}
        for source in policy_sources
    ]
    policy_guidance = str(setting_value(db, "validation_policy"))
    prompt = (
        "Evaluate the provided draft against the retrieved policy evidence and task context. "
        "Do not apply rules that are not in the supplied guidance or evidence. "
        "Use only the evidence shown; explain missing support, conflicts, unsafe claims, or needed review. "
        "Return JSON with exactly: 'valid' (boolean), 'requires_human_review' (boolean), "
        "'reasons' (array of strings), and 'policy_citations' (array of objects with "
        "'document' and 'chunk_id' copied from the supplied evidence). "
        f"\n\nInstruction:\n{instruction}"
        f"\n\nDraft:\n{draft}\n\nRetrieved policy evidence:\n{json.dumps(policy, ensure_ascii=False)}"
        f"\n\nDatabase/tool evidence for numeric and enquiry claims:\n"
        f"{json.dumps(business_evidence or {}, ensure_ascii=False)}"
        "\nCheck that every quantitative claim is supported by the supplied tool evidence."
    )
    response = await complete(
        db,
        [{"role": "system", "content": policy_guidance},
         {"role": "user", "content": prompt}],
        json_mode=True,
        task_id=task_id,
        step_id=step_id,
    )
    try:
        verdict = json.loads(response.content)
    except json.JSONDecodeError as error:
        raise InvalidVerdict("The validator returned invalid JSON.") from error
    if (
        not isinstance(verdict, dict)
        or type(verdict.get("valid")) is not bool
        or type(verdict.get("requires_human_review")) is not bool
        or not isinstance(verdict.get("reasons"), list)
        or not all(isinstance(reason, str) for reason in verdict["reasons"])
        or not isinstance(verdict.get("policy_citations"), list)
    ):
        raise InvalidVerdict("The validator returned an incomplete structured verdict.")
    source_ids = {(source.document, source.chunk_id) for source in policy_sources}
    citations = verdict["policy_citations"]
    if any(
        not isinstance(citation, dict)
        or (citation.get("document"), citation.get("chunk_id")) not in source_ids
        for citation in citations
    ):
        raise InvalidVerdict("The validator cited policy evidence that was not retrieved.")
    db.add(Event(
        task_id=task_id,
        event_type="validation",
        message="Validator completed a policy-grounded draft review.",
        details={
            "step_id": step_id,
            "verdict": verdict,
            "prompt": prompt,
            "policy_sources": policy,
            "cost_usd": response.cost_usd,
        },
        model=response.model,
    ))
    db.commit()
    return verdict, policy_sources, response.model, prompt
