from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.dependencies import get_current_user, require_roles
from app.models.entities import DesignationMaster, Employee, EmployeeSkill, ResourceRequest, Role, SkillMaster, User
from app.schemas.entities import (
    DesignationMasterCreate, DesignationMasterOut, DesignationMasterUpdate,
    SkillMasterCreate, SkillMasterOut, SkillMasterUpdate,
)
from app.services.audit import audit

router = APIRouter(prefix='/masters', tags=['Masters'])


def _clean_name(value: str) -> str:
    return ' '.join(value.strip().split())


def _designation_duplicate(db: Session, name: str, exclude_id: int | None = None):
    stmt = select(DesignationMaster.id).where(func.lower(DesignationMaster.name) == _clean_name(name).lower())
    if exclude_id:
        stmt = stmt.where(DesignationMaster.id != exclude_id)
    return db.execute(stmt).scalar_one_or_none()


def _skill_duplicate(db: Session, name: str, exclude_id: int | None = None):
    stmt = select(SkillMaster.id).where(func.lower(SkillMaster.name) == _clean_name(name).lower())
    if exclude_id:
        stmt = stmt.where(SkillMaster.id != exclude_id)
    return db.execute(stmt).scalar_one_or_none()


@router.get('/designations')
def list_designations(
    q: str = Query('', max_length=100),
    status: str = Query('active', pattern='^(active|inactive|all)$'),
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    stmt = select(DesignationMaster)
    if status == 'active': stmt = stmt.where(DesignationMaster.is_active.is_(True))
    elif status == 'inactive': stmt = stmt.where(DesignationMaster.is_active.is_(False))
    if q.strip(): stmt = stmt.where(DesignationMaster.name.ilike(f'%{q.strip()}%'))
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = list(db.execute(stmt.order_by(DesignationMaster.name).offset(offset).limit(limit)).scalars())
    return {'items': [DesignationMasterOut.model_validate(x).model_dump() for x in rows], 'total': total, 'limit': limit, 'offset': offset, 'has_more': offset + limit < total}


@router.post('/designations', response_model=DesignationMasterOut, dependencies=[Depends(require_roles(Role.ADMIN, Role.HR))])
def create_designation(payload: DesignationMasterCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    name = _clean_name(payload.name)
    if len(name) < 2: raise HTTPException(422, 'Designation name must contain at least 2 characters')
    if _designation_duplicate(db, name): raise HTTPException(409, 'Designation already exists')
    item = DesignationMaster(name=name, is_active=payload.is_active)
    db.add(item)
    try:
        db.flush()
    except IntegrityError:
        db.rollback(); raise HTTPException(409, 'Designation already exists')
    audit(db, user.id, 'CREATE_DESIGNATION_MASTER', 'DesignationMaster', item.id, {'name': name})
    db.commit(); db.refresh(item); return item


@router.patch('/designations/{designation_id}', response_model=DesignationMasterOut, dependencies=[Depends(require_roles(Role.ADMIN, Role.HR))])
def update_designation(designation_id: int, payload: DesignationMasterUpdate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    item = db.get(DesignationMaster, designation_id)
    if not item: raise HTTPException(404, 'Designation not found')
    if payload.name is not None:
        name = _clean_name(payload.name)
        if len(name) < 2: raise HTTPException(422, 'Designation name must contain at least 2 characters')
        if _designation_duplicate(db, name, item.id): raise HTTPException(409, 'Designation already exists')
        item.name = name
    if payload.is_active is not None: item.is_active = payload.is_active
    audit(db, user.id, 'UPDATE_DESIGNATION_MASTER', 'DesignationMaster', item.id, payload.model_dump(exclude_unset=True))
    db.commit(); db.refresh(item); return item


@router.delete('/designations/{designation_id}', response_model=DesignationMasterOut, dependencies=[Depends(require_roles(Role.ADMIN, Role.HR))])
def deactivate_designation(designation_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    item = db.get(DesignationMaster, designation_id)
    if not item: raise HTTPException(404, 'Designation not found')
    item.is_active = False
    audit(db, user.id, 'DEACTIVATE_DESIGNATION_MASTER', 'DesignationMaster', item.id, {'used_by_employee': bool(db.execute(select(Employee.id).where(Employee.designation_id == item.id).limit(1)).first()), 'used_by_request': bool(db.execute(select(ResourceRequest.id).where(ResourceRequest.designation_id == item.id).limit(1)).first())})
    db.commit(); db.refresh(item); return item


@router.get('/skills')
def list_skills(
    q: str = Query('', max_length=100),
    status: str = Query('active', pattern='^(active|inactive|all)$'),
    category: str | None = Query(None, max_length=255),
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    stmt = select(SkillMaster)
    if status == 'active': stmt = stmt.where(SkillMaster.is_active.is_(True))
    elif status == 'inactive': stmt = stmt.where(SkillMaster.is_active.is_(False))
    if q.strip(): stmt = stmt.where(or_(SkillMaster.name.ilike(f'%{q.strip()}%'), SkillMaster.category.ilike(f'%{q.strip()}%')))
    if category: stmt = stmt.where(SkillMaster.category == category)
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = list(db.execute(stmt.order_by(SkillMaster.name).offset(offset).limit(limit)).scalars())
    return {'items': [SkillMasterOut.model_validate(x).model_dump() for x in rows], 'total': total, 'limit': limit, 'offset': offset, 'has_more': offset + limit < total}


@router.post('/skills', response_model=SkillMasterOut, dependencies=[Depends(require_roles(Role.ADMIN, Role.HR))])
def create_skill(payload: SkillMasterCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    name = _clean_name(payload.name)
    if len(name) < 2: raise HTTPException(422, 'Skill name must contain at least 2 characters')
    if _skill_duplicate(db, name): raise HTTPException(409, 'Skill already exists')
    item = SkillMaster(name=name, category=_clean_name(payload.category) if payload.category else None, is_active=payload.is_active)
    db.add(item)
    try:
        db.flush()
    except IntegrityError:
        db.rollback(); raise HTTPException(409, 'Skill already exists')
    audit(db, user.id, 'CREATE_SKILL_MASTER', 'SkillMaster', item.id, {'name': name, 'category': item.category})
    db.commit(); db.refresh(item); return item


@router.patch('/skills/{skill_id}', response_model=SkillMasterOut, dependencies=[Depends(require_roles(Role.ADMIN, Role.HR))])
def update_skill(skill_id: int, payload: SkillMasterUpdate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    item = db.get(SkillMaster, skill_id)
    if not item: raise HTTPException(404, 'Skill not found')
    if payload.name is not None:
        name = _clean_name(payload.name)
        if len(name) < 2: raise HTTPException(422, 'Skill name must contain at least 2 characters')
        if _skill_duplicate(db, name, item.id): raise HTTPException(409, 'Skill already exists')
        item.name = name
    if 'category' in payload.model_fields_set: item.category = _clean_name(payload.category) if payload.category else None
    if payload.is_active is not None: item.is_active = payload.is_active
    audit(db, user.id, 'UPDATE_SKILL_MASTER', 'SkillMaster', item.id, payload.model_dump(exclude_unset=True))
    db.commit(); db.refresh(item); return item


@router.delete('/skills/{skill_id}', response_model=SkillMasterOut, dependencies=[Depends(require_roles(Role.ADMIN, Role.HR))])
def deactivate_skill(skill_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    item = db.get(SkillMaster, skill_id)
    if not item: raise HTTPException(404, 'Skill not found')
    item.is_active = False
    audit(db, user.id, 'DEACTIVATE_SKILL_MASTER', 'SkillMaster', item.id, {'used_by_employee': bool(db.execute(select(EmployeeSkill.id).where(EmployeeSkill.skill_id == item.id).limit(1)).first()), 'used_by_request': bool(db.execute(select(ResourceRequest.id).where(ResourceRequest.skill_id == item.id).limit(1)).first())})
    db.commit(); db.refresh(item); return item
