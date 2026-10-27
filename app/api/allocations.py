from datetime import date, datetime
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.dependencies import get_current_user, require_roles
from app.models.entities import Allocation, AllocationStatus, Employee, FeedbackEditHistory, FeedbackTask, Project, Role, User
from app.schemas.entities import AllocationCreate, AllocationDetailOut, AllocationOut, AllocationRelease, AllocationUpdate, CapacityCheckRequest, CapacityCheckResponse, FeedbackOut
from app.services.access import allocation_manageable_by, allocation_visible_to, employee_visible_to, manager_visible_employee_ids_stmt
from app.services.audit import audit
from app.services.capacity import CapacityValidationError, assert_capacity, validate_capacity
from app.services.capacity_metrics import recalculate_capacity_metrics

router=APIRouter(prefix="/allocations",tags=["Allocations"])
HISTORY_STATUSES=(AllocationStatus.COMPLETED,AllocationStatus.RELEASED_EARLY,AllocationStatus.RELEASED,AllocationStatus.CANCELLED)

def _capacity_response(r):
    conv=lambda xs:[{"date":c.date,"concurrent_projects":c.concurrent_projects,"total_percentage":c.total_percentage,"reason":c.reason,"code":c.code} for c in xs]
    return CapacityCheckResponse(valid=r.valid,max_concurrent_projects=r.max_concurrent_projects,peak_concurrent_projects=r.peak_concurrent_projects,peak_percentage=r.peak_percentage,available_capacity=r.available_capacity,overallocation_policy=r.overallocation_policy,conflicts=conv(r.conflicts),warnings=conv(r.warnings))

def _get_locked(db,id):
    a=db.execute(select(Allocation).where(Allocation.id==id).with_for_update()).scalar_one_or_none()
    if not a: raise HTTPException(404,"Allocation not found")
    return a

def _ensure_manageable(db,user,a):
    if not allocation_manageable_by(db,user,a): raise HTTPException(403,"You do not have permission to change this allocation")

def _feedback_payload(db,a):
    t=db.execute(select(FeedbackTask).where(FeedbackTask.allocation_id==a.id)).scalar_one_or_none()
    if not t or t.status.value!="COMPLETED": return None
    writer=db.get(User,t.manager_user_id); history=[{"edited_by_user_id":h.edited_by_user_id,"edited_at":h.edited_at,"previous_rating":h.previous_rating,"previous_feedback":h.previous_feedback} for h in db.execute(select(FeedbackEditHistory).where(FeedbackEditHistory.feedback_task_id==t.id).order_by(FeedbackEditHistory.edited_at.desc())).scalars()]
    d=FeedbackOut.model_validate(t).model_dump(); d["manager_name"]=writer.full_name if writer else ""; d["edit_history"]=history; return d

def _create_feedback_task(db,a,due):
    p=db.get(Project,a.project_id)
    if p and not db.execute(select(FeedbackTask).where(FeedbackTask.allocation_id==a.id)).scalar_one_or_none(): db.add(FeedbackTask(allocation_id=a.id,manager_user_id=p.manager_user_id,employee_id=a.employee_id,due_date=due))

@router.get("",response_model=list[AllocationOut])
def list_allocations(start:date|None=Query(None),end:date|None=Query(None),employee_id:int|None=None,project_id:int|None=None,status:AllocationStatus|None=None,resource_request_id:int|None=None,include_history:bool=False,db:Session=Depends(get_db),user:User=Depends(get_current_user)):
    stmt=select(Allocation)
    if not include_history: stmt=stmt.where(Allocation.status.not_in(HISTORY_STATUSES))
    if start: stmt=stmt.where(Allocation.end_date>=start)
    if end: stmt=stmt.where(Allocation.start_date<=end)
    if status: stmt=stmt.where(Allocation.status==status)
    if project_id: stmt=stmt.where(Allocation.project_id==project_id)
    if resource_request_id: stmt=stmt.where(Allocation.resource_request_id==resource_request_id)
    if employee_id:
        if not employee_visible_to(db,user,employee_id): raise HTTPException(404,"Employee not found")
        stmt=stmt.where(Allocation.employee_id==employee_id)
    elif user.role==Role.EMPLOYEE: stmt=stmt.where(Allocation.employee_id==user.employee_id)
    elif user.role==Role.MANAGER: stmt=stmt.where(Allocation.employee_id.in_(manager_visible_employee_ids_stmt(user.id)))
    rows=list(db.execute(stmt.order_by(Allocation.start_date,Allocation.id)).scalars())
    if user.role==Role.MANAGER: rows=[a for a in rows if allocation_visible_to(db,user,a)]
    return rows

@router.get("/{allocation_id}",response_model=AllocationDetailOut)
def allocation_detail(allocation_id:int,db:Session=Depends(get_db),user:User=Depends(get_current_user)):
    a=db.get(Allocation,allocation_id)
    if not a or not allocation_visible_to(db,user,a): raise HTTPException(404,"Allocation not found")
    emp=db.get(Employee,a.employee_id); project=db.get(Project,a.project_id); creator=db.get(User,a.created_by_user_id); pm=db.get(User,project.manager_user_id) if project else None; released_by=db.get(User,a.released_by_user_id) if a.released_by_user_id else None
    p=AllocationOut.model_validate(a).model_dump(); p.update({"can_manage":allocation_manageable_by(db,user,a),"employee_name":emp.name if emp else "","employee_code":emp.employee_code if emp else "","project_name":project.name if project else "","project_code":project.code if project else "","allocated_by_name":creator.full_name if creator else "Unknown","project_manager_name":pm.full_name if pm else "Unknown","released_by_name":released_by.full_name if released_by else None,"feedback":_feedback_payload(db,a)})
    return p

@router.post("/capacity-check",response_model=CapacityCheckResponse)
def capacity_check(payload:CapacityCheckRequest,db:Session=Depends(get_db),user:User=Depends(get_current_user)):
    if not employee_visible_to(db,user,payload.employee_id) and user.role not in (Role.ADMIN,Role.RM_TEAM): raise HTTPException(404,"Employee not found")
    return _capacity_response(validate_capacity(db,**payload.model_dump()))

@router.post("",response_model=AllocationOut,dependencies=[Depends(require_roles(Role.RM_TEAM,Role.ADMIN))])
def create_allocation(payload:AllocationCreate,db:Session=Depends(get_db),user:User=Depends(get_current_user)):
    try:
        r=assert_capacity(db=db,**payload.model_dump(exclude={"resource_request_id"}),lock_employee=True)
        a=Allocation(**payload.model_dump(),created_by_user_id=user.id,status=AllocationStatus.ACTIVE if payload.start_date<=date.today()<=payload.end_date else AllocationStatus.PLANNED); db.add(a); db.flush(); audit(db,user.id,"CREATE_ALLOCATION","Allocation",a.id,{**payload.model_dump(),"capacity_warnings":[w.reason for w in r.warnings]}); recalculate_capacity_metrics(db,{payload.employee_id}); db.commit(); db.refresh(a); return a
    except CapacityValidationError as e: db.rollback(); raise HTTPException(409,detail=_capacity_response(e.result).model_dump(mode="json"))

@router.patch("/{allocation_id}",response_model=AllocationOut,dependencies=[Depends(require_roles(Role.MANAGER,Role.RM_TEAM,Role.ADMIN))])
def update_allocation(allocation_id:int,payload:AllocationUpdate,db:Session=Depends(get_db),user:User=Depends(get_current_user)):
    a=_get_locked(db,allocation_id); _ensure_manageable(db,user,a)
    if a.status in HISTORY_STATUSES: raise HTTPException(409,"Historical allocation cannot be edited")
    ns=payload.start_date or a.start_date; ne=payload.end_date or a.end_date; np=payload.allocation_percentage if payload.allocation_percentage is not None else a.allocation_percentage
    if ne<ns: raise HTTPException(422,"End date must be on or after start date")
    if user.role==Role.MANAGER and payload.start_date is not None and payload.start_date!=a.start_date: raise HTTPException(403,"Managers can change allocation percentage and end date only")
    try:
        r=assert_capacity(db=db,employee_id=a.employee_id,project_id=a.project_id,start_date=ns,end_date=ne,allocation_percentage=np,exclude_allocation_id=a.id,lock_employee=True)
        before={"start_date":a.start_date,"end_date":a.end_date,"allocation_percentage":a.allocation_percentage}; a.start_date=ns;a.end_date=ne;a.allocation_percentage=np;a.status=AllocationStatus.ACTIVE if ns<=date.today()<=ne else AllocationStatus.PLANNED
        audit(db,user.id,"EDIT_ALLOCATION","Allocation",a.id,{"before":before,"after":{"start_date":ns,"end_date":ne,"allocation_percentage":np}});recalculate_capacity_metrics(db,{a.employee_id});db.commit();db.refresh(a);return a
    except CapacityValidationError as e: db.rollback(); raise HTTPException(409,detail=_capacity_response(e.result).model_dump(mode="json"))

@router.patch("/{allocation_id}/release",response_model=AllocationOut,dependencies=[Depends(require_roles(Role.MANAGER,Role.RM_TEAM,Role.ADMIN))])
def release_allocation(allocation_id:int,payload:AllocationRelease,db:Session=Depends(get_db),user:User=Depends(get_current_user)):
    a=_get_locked(db,allocation_id);_ensure_manageable(db,user,a)
    if a.status in HISTORY_STATUSES: raise HTTPException(409,"Allocation is already historical")
    if payload.release_date<a.start_date or payload.release_date>a.end_date: raise HTTPException(422,"Release date must be within the allocation period")
    now=datetime.utcnow(); a.release_reason_type=payload.reason_type; a.release_reason=payload.reason; a.release_requested_by_user_id=user.id; a.release_requested_at=now
    if payload.release_date<=date.today():
        a.status=AllocationStatus.RELEASED_EARLY; a.release_date=payload.release_date; a.released_at=now; a.released_by_user_id=user.id; a.scheduled_release_date=None; _create_feedback_task(db,a,date.today())
    else:
        a.scheduled_release_date=payload.release_date
    audit(db,user.id,"RELEASE_ALLOCATION","Allocation",a.id,{"release_date":payload.release_date,"reason_type":payload.reason_type,"reason":payload.reason}); recalculate_capacity_metrics(db,{a.employee_id}); db.commit();db.refresh(a);return a

@router.post("/{allocation_id}/deallocate",response_model=AllocationOut,dependencies=[Depends(require_roles(Role.MANAGER,Role.RM_TEAM,Role.ADMIN))])
def deallocate_compatibility_alias(allocation_id:int,payload:AllocationRelease,db:Session=Depends(get_db),user:User=Depends(get_current_user)):
    """Backward-compatible alias for the release endpoint; no allocation is hard-deleted."""
    return release_allocation(allocation_id,payload,db,user)

@router.delete("/{allocation_id}",response_model=AllocationOut,dependencies=[Depends(require_roles(Role.MANAGER,Role.RM_TEAM,Role.ADMIN))])
def soft_cancel(allocation_id:int,reason:str=Query(min_length=3,max_length=1000),db:Session=Depends(get_db),user:User=Depends(get_current_user)):
    a=_get_locked(db,allocation_id);_ensure_manageable(db,user,a)
    if a.status in HISTORY_STATUSES: raise HTTPException(409,"Allocation is already historical")
    a.status=AllocationStatus.CANCELLED;a.release_date=date.today();a.release_reason_type="OTHER";a.release_reason=reason;a.released_by_user_id=user.id;a.released_at=datetime.utcnow();audit(db,user.id,"CANCEL_ALLOCATION","Allocation",a.id,{"reason":reason});recalculate_capacity_metrics(db,{a.employee_id});db.commit();db.refresh(a);return a
