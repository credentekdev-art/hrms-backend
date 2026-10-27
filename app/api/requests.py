from datetime import date
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.dependencies import get_current_user, require_roles
from app.models.entities import (
    Allocation, AllocationStatus, DesignationMaster, Employee, EmployeeSkill, FeedbackStatus, FeedbackTask,
    RequestStatus, ResourceRequest, ResourceRequestSelection, Role, SkillMaster, User,
)
from app.schemas.entities import RequestDecision, ResourceRequestCreate, ResourceRequestOut, ResourceRequestUpdate
from app.services.access import manager_visible_employee_ids_stmt, project_visible_to
from app.services.audit import audit
from app.services.capacity import CapacityValidationError
from app.services.capacity_metrics import recalculate_capacity_metrics
from app.services.employee_directory import eligible_employee_search, validate_employee_master_fit
from app.services.workflow import can_transition

router = APIRouter(prefix='/requests', tags=['Resource Requests'])
TERMINAL = {RequestStatus.FULFILLED, RequestStatus.CLOSED, RequestStatus.CANCELLED, RequestStatus.REJECTED}


def _selected(db: Session, request_id: int, fallback=None) -> list[int]:
    ids = list(db.execute(select(ResourceRequestSelection.employee_id).where(ResourceRequestSelection.request_id == request_id).order_by(ResourceRequestSelection.id)).scalars())
    if not ids and fallback: ids = [fallback]
    return ids


def _serialize(db: Session, req: ResourceRequest) -> dict:
    d = ResourceRequestOut.model_validate(req).model_dump()
    d['requested_employee_ids'] = _selected(db, req.id, req.requested_employee_id)
    user = db.get(User, req.requested_by_user_id)
    designation = db.get(DesignationMaster, req.designation_id) if req.designation_id else None
    skill = db.get(SkillMaster, req.skill_id) if req.skill_id else None
    d['requested_by_name'] = user.full_name if user else ''
    d['remark'] = req.reason or ''
    d['designation_name'] = designation.name if designation else (req.role_required or '')
    d['skill_name'] = skill.name if skill else (req.skills_required or '')
    return d


def _visible_request(db: Session, user: User, request_id: int, lock=False) -> ResourceRequest:
    stmt = select(ResourceRequest).where(ResourceRequest.id == request_id)
    if user.role == Role.MANAGER: stmt = stmt.where(ResourceRequest.requested_by_user_id == user.id)
    elif user.role == Role.EMPLOYEE:
        ids = select(ResourceRequestSelection.request_id).where(ResourceRequestSelection.employee_id == user.employee_id)
        stmt = stmt.where(ResourceRequest.id.in_(ids))
    if lock: stmt = stmt.with_for_update()
    req = db.execute(stmt).scalar_one_or_none()
    if not req: raise HTTPException(404, 'Request not found')
    return req


def _employee_scope(stmt, user: User):
    if user.role == Role.MANAGER: return stmt.where(Employee.id.in_(manager_visible_employee_ids_stmt(user.id)))
    if user.role == Role.EMPLOYEE: return stmt.where(Employee.id == user.employee_id)
    return stmt


def _resolve_designation(db: Session, designation_id: int | None, legacy: str | None) -> DesignationMaster:
    item = db.get(DesignationMaster, designation_id) if designation_id else None
    if not item and legacy and legacy.strip():
        name=legacy.strip(); item = db.execute(select(DesignationMaster).where(func.lower(DesignationMaster.name) == name.lower())).scalar_one_or_none()
    if not item or not item.is_active: raise HTTPException(422, 'Select an active designation from the designation master')
    return item


def _resolve_skill(db: Session, skill_id: int | None, legacy: str | None) -> SkillMaster:
    item = db.get(SkillMaster, skill_id) if skill_id else None
    if not item and legacy and legacy.strip():
        name=legacy.strip(); item = db.execute(select(SkillMaster).where(func.lower(SkillMaster.name) == name.lower())).scalar_one_or_none()
    if not item or not item.is_active: raise HTTPException(422, 'Select an active skill from the skill master')
    return item


def _set_selections(db: Session, req: ResourceRequest, employee_ids: list[int]) -> None:
    db.execute(ResourceRequestSelection.__table__.delete().where(ResourceRequestSelection.request_id == req.id))
    for eid in employee_ids:
        db.add(ResourceRequestSelection(request_id=req.id, employee_id=eid))
    req.requested_employee_id = employee_ids[0] if employee_ids else None


def _validate_selections(db: Session, req: ResourceRequest, employee_ids: list[int], *, lock: bool) -> None:
    if not req.designation_id or not req.skill_id:
        raise HTTPException(422, 'Designation and Skill must be selected from the masters before employees can be assigned')
    unique_ids = list(dict.fromkeys(employee_ids))
    if len(unique_ids) != req.number_of_resources:
        raise HTTPException(422, f'Select exactly {req.number_of_resources} eligible employee(s)')
    try:
        for employee_id in sorted(unique_ids):
            validate_employee_master_fit(
                db,
                employee_id=employee_id,
                designation_id=req.designation_id,
                skill_id=req.skill_id,
                project_id=req.project_id,
                start_date=req.start_date,
                end_date=req.end_date,
                allocation_percentage=req.allocation_percentage,
                lock=lock,
            )
    except CapacityValidationError as exc:
        reason = exc.result.conflicts[0].reason if exc.result.conflicts else 'Employee does not have enough available allocation'
        raise HTTPException(409, detail={'message': reason, 'conflicts': [{'date': c.date.isoformat(), 'reason': c.reason, 'code': c.code, 'total_percentage': c.total_percentage} for c in exc.result.conflicts]})
    except ValueError as exc:
        raise HTTPException(422, str(exc))


# Compatibility search endpoints now read only from active master data.
@router.get('/search/roles')
@router.get('/search/designations')
def search_designations(q: str = Query('', max_length=100), limit: int = Query(10, ge=1, le=50), offset: int = Query(0, ge=0), db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    stmt = select(DesignationMaster.name).where(DesignationMaster.is_active.is_(True))
    if q.strip(): stmt = stmt.where(DesignationMaster.name.ilike(f'%{q.strip()}%'))
    rows = list(db.execute(stmt.order_by(DesignationMaster.name).offset(offset).limit(limit + 1)).scalars())
    return {'items': rows[:limit], 'limit': limit, 'offset': offset, 'has_more': len(rows) > limit}


@router.get('/search/skills')
def search_skills(q: str = Query('', max_length=100), limit: int = Query(10, ge=1, le=50), offset: int = Query(0, ge=0), db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    stmt = select(SkillMaster.name).where(SkillMaster.is_active.is_(True))
    if q.strip(): stmt = stmt.where(SkillMaster.name.ilike(f'%{q.strip()}%'))
    rows = list(db.execute(stmt.order_by(SkillMaster.name).offset(offset).limit(limit + 1)).scalars())
    return {'items': rows[:limit], 'limit': limit, 'offset': offset, 'has_more': len(rows) > limit}


@router.get('/eligible-employees')
def eligible_employees(
    designation_id: int | None = None,
    skill_id: int | None = None,
    project_id: int | None = None,
    allocation_percentage: float | None = Query(None, gt=0, le=100),
    start_date: date | None = Query(None),
    end_date: date | None = Query(None),
    q: str = Query('', max_length=100),
    request_id: int | None = None,
    selected_employee_ids: str | None = Query(None, max_length=500),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if user.role not in (Role.MANAGER, Role.RM_TEAM, Role.ADMIN, Role.HR): raise HTTPException(403, 'You cannot search eligible employees')
    if project_id and user.role == Role.MANAGER and not project_visible_to(db, user, project_id): raise HTTPException(403, 'Manager can request only for managed projects')
    if start_date and end_date and end_date < start_date: raise HTTPException(422, 'End date must be on or after start date')
    if designation_id:
        designation = db.get(DesignationMaster, designation_id)
        if not designation or not designation.is_active: raise HTTPException(422, 'Select an active designation')
    if skill_id:
        skill = db.get(SkillMaster, skill_id)
        if not skill or not skill.is_active: raise HTTPException(422, 'Select an active skill')
    probe_ids=set()
    if selected_employee_ids:
        try: probe_ids={int(x) for x in selected_employee_ids.split(',') if x.strip()}
        except ValueError: raise HTTPException(422, 'selected_employee_ids must be a comma-separated list of employee IDs')
    return eligible_employee_search(db, designation_id=designation_id, skill_id=skill_id, project_id=project_id, start_date=start_date, end_date=end_date, allocation_percentage=allocation_percentage, q=q, request_id=request_id, probe_employee_ids=probe_ids, limit=limit, offset=offset)


# Deprecated compatibility endpoint. It no longer powers the Resource Request UI.
@router.get('/suggestions/employees')
def suggested_employees(role: str | None = Query(None, max_length=255), skill: str | None = Query(None, max_length=255), q: str | None = Query(None, max_length=100), limit: int = Query(10, ge=1, le=50), offset: int = Query(0, ge=0), db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    latest = select(FeedbackTask.employee_id, func.max(FeedbackTask.id).label('feedback_id')).where(FeedbackTask.status == FeedbackStatus.COMPLETED).group_by(FeedbackTask.employee_id).subquery()
    stmt = select(Employee, FeedbackTask.rating, FeedbackTask.feedback).outerjoin(latest, latest.c.employee_id == Employee.id).outerjoin(FeedbackTask, FeedbackTask.id == latest.c.feedback_id).where(Employee.active.is_(True))
    stmt = _employee_scope(stmt, user)
    if role:
        master_id=select(DesignationMaster.id).where(func.lower(DesignationMaster.name)==role.strip().lower()).scalar_subquery()
        stmt=stmt.where(or_(Employee.designation_id==master_id,Employee.designation.ilike(f'%{role.strip()}%')))
    if skill:
        skill_id=select(SkillMaster.id).where(func.lower(SkillMaster.name)==skill.strip().lower()).scalar_subquery()
        normalized=select(EmployeeSkill.employee_id).where(EmployeeSkill.skill_id==skill_id)
        stmt=stmt.where(or_(Employee.id.in_(normalized),Employee.skills.ilike(f'%{skill.strip()}%')))
    if q: stmt = stmt.where(or_(Employee.name.ilike(f'%{q}%'), Employee.employee_code.ilike(f'%{q}%'),Employee.designation.ilike(f'%{q}%'),Employee.skills.ilike(f'%{q}%')))
    rows = list(db.execute(stmt.order_by(Employee.name).offset(offset).limit(limit + 1)).all())
    items = [{'id': emp.id, 'employee_code': emp.employee_code, 'name': emp.name, 'designation': emp.designation, 'experience': emp.experience_years, 'last_feedback_rating': rating, 'last_feedback_description': feedback or None} for emp, rating, feedback in rows[:limit]]
    return {'items': items, 'limit': limit, 'offset': offset, 'has_more': len(rows) > limit}


@router.get('', response_model=list[ResourceRequestOut])
def list_requests(status: RequestStatus | None = None, project_id: int | None = None, priority: str | None = None, start: date | None = None, end: date | None = None, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    stmt = select(ResourceRequest).order_by(ResourceRequest.created_at.desc())
    if user.role == Role.MANAGER: stmt = stmt.where(ResourceRequest.requested_by_user_id == user.id)
    elif user.role == Role.EMPLOYEE:
        ids = select(ResourceRequestSelection.request_id).where(ResourceRequestSelection.employee_id == user.employee_id); stmt = stmt.where(ResourceRequest.id.in_(ids))
    if status: stmt = stmt.where(ResourceRequest.status == status)
    if project_id: stmt = stmt.where(ResourceRequest.project_id == project_id)
    if priority: stmt = stmt.where(ResourceRequest.priority == priority.upper())
    if start: stmt = stmt.where(ResourceRequest.end_date >= start)
    if end: stmt = stmt.where(ResourceRequest.start_date <= end)
    return [_serialize(db, r) for r in db.execute(stmt).scalars()]


@router.get('/{request_id}', response_model=ResourceRequestOut)
def get_request(request_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return _serialize(db, _visible_request(db, user, request_id))


@router.post('', response_model=ResourceRequestOut, dependencies=[Depends(require_roles(Role.MANAGER, Role.RM_TEAM, Role.ADMIN))])
def create_request(payload: ResourceRequestCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if user.role == Role.MANAGER and not project_visible_to(db, user, payload.project_id): raise HTTPException(403, 'Manager can request only for managed projects')
    designation = _resolve_designation(db, payload.designation_id, payload.role_required)
    skill = _resolve_skill(db, payload.skill_id, payload.skills_required)
    if len(set(payload.requested_employee_ids)) != payload.number_of_resources:
        raise HTTPException(422, f'Select exactly {payload.number_of_resources} eligible employee(s)')
    req = ResourceRequest(
        project_id=payload.project_id,
        requested_employee_id=None,
        requested_by_user_id=user.id,
        designation_id=designation.id,
        skill_id=skill.id,
        role_required=designation.name,
        skills_required=skill.name,
        start_date=payload.start_date,
        end_date=payload.end_date,
        allocation_percentage=payload.allocation_percentage,
        number_of_resources=payload.number_of_resources,
        priority=payload.priority,
        reason=payload.remark.strip(),
        status=RequestStatus.DRAFT if payload.save_as_draft else RequestStatus.SUBMITTED,
    )
    db.add(req); db.flush()
    _validate_selections(db, req, payload.requested_employee_ids, lock=True)
    _set_selections(db, req, list(dict.fromkeys(payload.requested_employee_ids)))
    audit(db, user.id, 'SAVE_RESOURCE_REQUEST_DRAFT' if payload.save_as_draft else 'SUBMIT_RESOURCE_REQUEST', 'ResourceRequest', req.id, {'remark': req.reason, 'designation_id': req.designation_id, 'skill_id': req.skill_id, 'employee_ids': payload.requested_employee_ids})
    db.commit(); db.refresh(req); return _serialize(db, req)


@router.patch('/{request_id}', response_model=ResourceRequestOut, dependencies=[Depends(require_roles(Role.MANAGER, Role.RM_TEAM, Role.ADMIN))])
def update_request(request_id: int, payload: ResourceRequestUpdate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    req = _visible_request(db, user, request_id, lock=True)
    if req.status in TERMINAL or req.status == RequestStatus.APPROVED: raise HTTPException(409, 'This request can no longer be edited')
    if user.role == Role.MANAGER and req.requested_by_user_id != user.id: raise HTTPException(403, 'You do not have permission to edit this request')
    if payload.project_id is not None and user.role == Role.MANAGER and not project_visible_to(db, user, payload.project_id): raise HTTPException(403, 'Manager can request only for managed projects')
    before = {'project_id': req.project_id, 'designation_id': req.designation_id, 'skill_id': req.skill_id, 'number_of_resources': req.number_of_resources, 'allocation_percentage': req.allocation_percentage, 'start_date': req.start_date, 'end_date': req.end_date, 'priority': req.priority, 'remark': req.reason, 'employee_ids': _selected(db, req.id, req.requested_employee_id)}
    values = payload.model_dump(exclude_unset=True)
    employee_ids = values.pop('requested_employee_ids', None)
    remark = values.pop('remark', None)
    legacy_role = values.pop('role_required', None) if 'role_required' in values else None
    legacy_skill = values.pop('skills_required', None) if 'skills_required' in values else None
    if remark is not None: req.reason = remark.strip()
    for key, value in values.items(): setattr(req, key, value)
    designation = _resolve_designation(db, req.designation_id, legacy_role or req.role_required)
    skill = _resolve_skill(db, req.skill_id, legacy_skill or req.skills_required)
    req.designation_id = designation.id; req.skill_id = skill.id; req.role_required = designation.name; req.skills_required = skill.name
    if req.end_date < req.start_date: raise HTTPException(422, 'End date must be on or after start date')
    selected = employee_ids if employee_ids is not None else _selected(db, req.id, req.requested_employee_id)
    _validate_selections(db, req, selected, lock=True)
    _set_selections(db, req, list(dict.fromkeys(selected)))
    audit(db, user.id, 'EDIT_RESOURCE_REQUEST', 'ResourceRequest', req.id, {'before': before, 'after': {'project_id': req.project_id, 'designation_id': req.designation_id, 'skill_id': req.skill_id, 'number_of_resources': req.number_of_resources, 'allocation_percentage': req.allocation_percentage, 'start_date': req.start_date, 'end_date': req.end_date, 'priority': req.priority, 'remark': req.reason, 'employee_ids': selected}})
    db.commit(); db.refresh(req); return _serialize(db, req)


@router.delete('/{request_id}', response_model=ResourceRequestOut, dependencies=[Depends(require_roles(Role.MANAGER, Role.RM_TEAM, Role.ADMIN))])
def cancel_request(request_id: int, remark: str = Query(min_length=3, max_length=1000), db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    req = _visible_request(db, user, request_id, lock=True)
    if req.status not in (RequestStatus.DRAFT, RequestStatus.SUBMITTED, RequestStatus.UNDER_REVIEW): raise HTTPException(409, 'Only open requests can be cancelled')
    req.status = RequestStatus.CANCELLED; audit(db, user.id, 'CANCEL_RESOURCE_REQUEST', 'ResourceRequest', req.id, {'remark': remark.strip()}); db.commit(); db.refresh(req); return _serialize(db, req)


@router.post('/{request_id}/submit', response_model=ResourceRequestOut, dependencies=[Depends(require_roles(Role.MANAGER, Role.RM_TEAM, Role.ADMIN))])
def submit_draft(request_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    req = _visible_request(db, user, request_id, lock=True)
    if not can_transition(req.status, RequestStatus.SUBMITTED): raise HTTPException(409, f'Invalid transition from {req.status.value} to SUBMITTED')
    selected = _selected(db, req.id, req.requested_employee_id)
    _validate_selections(db, req, selected, lock=True)
    req.status = RequestStatus.SUBMITTED; audit(db, user.id, 'SUBMIT_RESOURCE_REQUEST', 'ResourceRequest', req.id, {'remark': req.reason}); db.commit(); db.refresh(req); return _serialize(db, req)


@router.post('/{request_id}/decision', response_model=ResourceRequestOut, dependencies=[Depends(require_roles(Role.RM_TEAM, Role.ADMIN))])
def decide_request(request_id: int, payload: RequestDecision, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    req = _visible_request(db, user, request_id, lock=True)
    if req.status == RequestStatus.DRAFT: raise HTTPException(409, 'Draft requests must be submitted before review')
    if payload.decision == 'review':
        if not can_transition(req.status, RequestStatus.UNDER_REVIEW): raise HTTPException(409, 'Request cannot enter review from its current status')
        req.status = RequestStatus.UNDER_REVIEW; req.reviewed_by_user_id = user.id; audit(db, user.id, 'REQUEST_UNDER_REVIEW', 'ResourceRequest', req.id, {'remark': (payload.remark or '').strip()}); db.commit(); db.refresh(req); return _serialize(db, req)
    if req.status == RequestStatus.SUBMITTED: req.status = RequestStatus.UNDER_REVIEW
    remark = (payload.remark or '').strip()
    if payload.decision == 'reject':
        req.status = RequestStatus.REJECTED; req.rejection_reason = remark; req.reviewed_by_user_id = user.id; audit(db, user.id, 'REJECT_RESOURCE_REQUEST', 'ResourceRequest', req.id, {'remark': remark}); db.commit(); db.refresh(req); return _serialize(db, req)
    employee_ids = payload.employee_ids or _selected(db, req.id, req.requested_employee_id)
    try:
        _validate_selections(db, req, employee_ids, lock=True)
        _set_selections(db, req, list(dict.fromkeys(employee_ids)))
        req.status = RequestStatus.APPROVED; req.reviewed_by_user_id = user.id
        allocation_ids = []
        for index, eid in enumerate(employee_ids):
            allocation = Allocation(employee_id=eid, project_id=req.project_id, resource_request_id=req.id if index == 0 else None, start_date=req.start_date, end_date=req.end_date, allocation_percentage=req.allocation_percentage, created_by_user_id=user.id, status=AllocationStatus.ACTIVE if req.start_date <= date.today() <= req.end_date else AllocationStatus.PLANNED)
            db.add(allocation); db.flush(); allocation_ids.append(allocation.id)
        req.status = RequestStatus.FULFILLED
        audit(db, user.id, 'APPROVE_AND_FULFILL_REQUEST', 'ResourceRequest', req.id, {'allocation_ids': allocation_ids, 'employee_ids': employee_ids, 'remark': remark})
        recalculate_capacity_metrics(db, set(employee_ids)); db.commit(); db.refresh(req); return _serialize(db, req)
    except HTTPException:
        db.rollback(); raise
