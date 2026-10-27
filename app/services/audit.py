import json
from sqlalchemy.orm import Session
from app.models.entities import AuditLog


def audit(db: Session, actor_user_id: int | None, action: str, entity_type: str, entity_id: str | int, details: dict | None = None):
    db.add(AuditLog(
        actor_user_id=actor_user_id,
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id),
        details=json.dumps(details or {}, default=str),
    ))
