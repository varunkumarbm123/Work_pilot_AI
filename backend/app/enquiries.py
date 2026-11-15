from sqlalchemy.orm import Session

from app.models import Enquiry, Event


def create_enquiry(
    db: Session,
    workspace_id: str,
    subject: str,
    message: str,
    customer_name: str = "",
    customer_email: str = "",
    source: str = "user",
) -> Enquiry:
    enquiry = Enquiry(
        workspace_id=workspace_id,
        subject=subject.strip(),
        message=message.strip(),
        customer_name=customer_name.strip(),
        customer_email=customer_email.strip(),
        source=source,
    )
    db.add(enquiry)
    db.flush()
    db.add(Event(
        workspace_id=workspace_id,
        event_type="enquiry_created",
        message="A customer enquiry was added to the workspace.",
        details={"enquiry_id": enquiry.id, "source": source},
    ))
    db.commit()
    db.refresh(enquiry)
    return enquiry
