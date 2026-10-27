from datetime import date
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.dependencies import get_current_user
from app.models.entities import Allocation, AllocationStatus, FeedbackStatus, FeedbackTask, RequestStatus, ResourceRequest, Role, User
from app.services.access import manager_visible_employee_ids_stmt
from app.services.capacity import get_max_concurrent_projects
router=APIRouter(prefix="/dashboard",tags=["Dashboard"])
@router.get("/summary")
def summary(db:Session=Depends(get_db),user:User=Depends(get_current_user)):
    today=date.today();astmt=select(Allocation).where(Allocation.status.in_([AllocationStatus.ACTIVE,AllocationStatus.PLANNED]),Allocation.end_date>=today);rstmt=select(ResourceRequest).where(ResourceRequest.status.in_([RequestStatus.SUBMITTED,RequestStatus.UNDER_REVIEW]));fstmt=select(FeedbackTask).where(FeedbackTask.status==FeedbackStatus.PENDING)
    if user.role==Role.MANAGER:
        ids=manager_visible_employee_ids_stmt(user.id);astmt=astmt.where(Allocation.employee_id.in_(ids));rstmt=rstmt.where(ResourceRequest.requested_by_user_id==user.id);fstmt=fstmt.where(FeedbackTask.manager_user_id==user.id)
    elif user.role==Role.EMPLOYEE:
        astmt=astmt.where(Allocation.employee_id==user.employee_id); fstmt=fstmt.where(FeedbackTask.employee_id==user.employee_id)
    allocations=list(db.execute(astmt).scalars());current=[a for a in allocations if a.start_date<=today<=a.end_date and (not a.scheduled_release_date or a.scheduled_release_date>today)]
    return {"active_or_planned_allocations":len(allocations),"visible_resources":len({a.employee_id for a in allocations}),"current_allocation_percentage_sum":round(sum(a.allocation_percentage for a in current),2),"pending_requests":len(list(db.execute(rstmt).scalars())),"pending_feedback_tasks":len(list(db.execute(fstmt).scalars())),"max_concurrent_projects":get_max_concurrent_projects(db)}
