from datetime import date, datetime
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.dependencies import get_current_user
from app.models.entities import Allocation, FeedbackEditHistory, FeedbackStatus, FeedbackTask, Project, Role, User
from app.schemas.entities import FeedbackComplete, FeedbackCreate, FeedbackOut
from app.services.access import allocation_visible_to
from app.services.audit import audit

router=APIRouter(prefix="/feedback",tags=["Feedback"])

def serialize(db,task):
    writer=db.get(User,task.manager_user_id)
    history=[{"edited_by_user_id":h.edited_by_user_id,"edited_at":h.edited_at,"previous_rating":h.previous_rating,"previous_feedback":h.previous_feedback} for h in db.execute(select(FeedbackEditHistory).where(FeedbackEditHistory.feedback_task_id==task.id).order_by(FeedbackEditHistory.edited_at.desc())).scalars()]
    data=FeedbackOut.model_validate(task).model_dump(); data["manager_name"]=writer.full_name if writer else ""; data["edit_history"]=history; return data

def permitted(db,user,task):
    if user.role in (Role.ADMIN,Role.RM_TEAM): return True
    if user.role==Role.EMPLOYEE: return user.employee_id==task.employee_id
    if user.role==Role.MANAGER:
        allocation=db.get(Allocation,task.allocation_id)
        return bool(allocation and allocation_visible_to(db,user,allocation))
    return False

@router.get("")
def list_feedback(employee_id:int|None=None,allocation_id:int|None=None,db:Session=Depends(get_db),user:User=Depends(get_current_user)):
    stmt=select(FeedbackTask).order_by(FeedbackTask.due_date.desc())
    if employee_id is not None: stmt=stmt.where(FeedbackTask.employee_id==employee_id)
    if allocation_id is not None: stmt=stmt.where(FeedbackTask.allocation_id==allocation_id)
    rows=[t for t in db.execute(stmt).scalars() if permitted(db,user,t)]
    return [serialize(db,t) for t in rows]

@router.post("",response_model=FeedbackOut)
def create_feedback(payload:FeedbackCreate,db:Session=Depends(get_db),user:User=Depends(get_current_user)):
    allocation=db.get(Allocation,payload.allocation_id)
    if not allocation or not allocation_visible_to(db,user,allocation): raise HTTPException(404,"Allocation not found")
    project=db.get(Project,allocation.project_id)
    if user.role==Role.MANAGER and (not project or project.manager_user_id!=user.id): raise HTTPException(403,"Only the project manager can submit feedback")
    if user.role not in (Role.MANAGER,Role.RM_TEAM,Role.ADMIN): raise HTTPException(403,"You cannot submit feedback")
    existing=db.execute(select(FeedbackTask).where(FeedbackTask.allocation_id==allocation.id)).scalar_one_or_none()
    if existing and existing.status==FeedbackStatus.COMPLETED: raise HTTPException(409,"Feedback already exists. Use edit feedback.")
    task=existing or FeedbackTask(allocation_id=allocation.id,manager_user_id=user.id,employee_id=allocation.employee_id,due_date=date.today())
    if not existing: db.add(task)
    task.manager_user_id=user.id; task.rating=payload.rating; task.feedback=payload.feedback; task.status=FeedbackStatus.COMPLETED; task.completed_at=datetime.utcnow(); task.updated_at=datetime.utcnow()
    db.flush(); audit(db,user.id,"CREATE_FEEDBACK","FeedbackTask",task.id,payload.model_dump()); db.commit(); db.refresh(task); return serialize(db,task)

@router.post("/{task_id}/complete",response_model=FeedbackOut)
def complete_feedback(task_id:int,payload:FeedbackComplete,db:Session=Depends(get_db),user:User=Depends(get_current_user)):
    task=db.execute(select(FeedbackTask).where(FeedbackTask.id==task_id).with_for_update()).scalar_one_or_none()
    if not task: raise HTTPException(404,"Feedback task not found")
    if user.role not in (Role.ADMIN,Role.RM_TEAM) and task.manager_user_id!=user.id: raise HTTPException(403,"Only the responsible manager may complete this feedback")
    if task.status==FeedbackStatus.COMPLETED: raise HTTPException(409,"Feedback already completed")
    task.rating=payload.rating; task.feedback=payload.feedback; task.status=FeedbackStatus.COMPLETED; task.completed_at=datetime.utcnow(); task.updated_at=datetime.utcnow()
    audit(db,user.id,"COMPLETE_FEEDBACK","FeedbackTask",task.id,payload.model_dump()); db.commit(); db.refresh(task); return serialize(db,task)

@router.patch("/{task_id}",response_model=FeedbackOut)
def update_feedback(task_id:int,payload:FeedbackComplete,db:Session=Depends(get_db),user:User=Depends(get_current_user)):
    task=db.execute(select(FeedbackTask).where(FeedbackTask.id==task_id).with_for_update()).scalar_one_or_none()
    if not task or not permitted(db,user,task): raise HTTPException(404,"Feedback not found")
    if user.id!=task.manager_user_id: raise HTTPException(403,"Only the manager who wrote this feedback can edit it")
    if task.status!=FeedbackStatus.COMPLETED: raise HTTPException(409,"Complete the feedback before editing it")
    now=datetime.utcnow(); db.add(FeedbackEditHistory(feedback_task_id=task.id,edited_by_user_id=user.id,previous_rating=task.rating,previous_feedback=task.feedback,edited_at=now))
    task.rating=payload.rating; task.feedback=payload.feedback; task.edited_at=now; task.updated_at=now
    audit(db,user.id,"EDIT_FEEDBACK","FeedbackTask",task.id,{"rating":payload.rating}); db.commit(); db.refresh(task); return serialize(db,task)
