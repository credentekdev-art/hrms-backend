from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.dependencies import require_roles, get_current_user
from app.models.entities import Role, SystemConfig, User
from app.schemas.entities import ConfigUpdate
from app.services.audit import audit
from app.services.capacity import get_max_concurrent_projects, get_overallocation_policy

router = APIRouter(prefix="/admin", tags=["Admin"])


@router.get("/capacity-config", dependencies=[Depends(require_roles(Role.ADMIN))])
def get_capacity_config(db: Session = Depends(get_db)):
    return {
        "max_concurrent_projects": get_max_concurrent_projects(db),
        "overallocation_policy": get_overallocation_policy(db),
        "maximum_total_allocation_percentage": 100,
    }


@router.put("/capacity-config", dependencies=[Depends(require_roles(Role.ADMIN))])
def update_capacity_config(payload: ConfigUpdate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    values = {"max_concurrent_projects": str(payload.max_concurrent_projects), "overallocation_policy": "BLOCK"}
    for key, value in values.items():
        config = db.get(SystemConfig, key)
        if not config:
            db.add(SystemConfig(key=key, value=value))
        else:
            config.value = value
    audit(db, user.id, "UPDATE_CAPACITY_CONFIG", "SystemConfig", "capacity", payload.model_dump())
    db.commit()
    return {**payload.model_dump(), "overallocation_policy": "BLOCK", "maximum_total_allocation_percentage": 100}
