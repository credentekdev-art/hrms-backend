from datetime import date, datetime, timedelta
from sqlalchemy import or_, select
from sqlalchemy.orm import Session
from app.models.entities import Allocation, AllocationStatus, FeedbackTask, Project, ResourceRequest, RequestStatus
from app.services.audit import audit
from app.services.capacity_metrics import recalculate_capacity_metrics

def run_auto_deallocation(db:Session,as_of:date|None=None)->dict:
    as_of=as_of or date.today(); starting=list(db.execute(select(Allocation).where(Allocation.status==AllocationStatus.PLANNED,Allocation.start_date<=as_of,Allocation.end_date>=as_of,or_(Allocation.scheduled_release_date.is_(None),Allocation.scheduled_release_date>as_of)).with_for_update(skip_locked=True)).scalars())
    for a in starting:a.status=AllocationStatus.ACTIVE;audit(db,None,"AUTO_ACTIVATE","Allocation",a.id,{"as_of":as_of.isoformat()})
    rows=list(db.execute(select(Allocation).where(Allocation.status.in_([AllocationStatus.ACTIVE,AllocationStatus.PLANNED]),or_(Allocation.end_date<as_of,Allocation.scheduled_release_date<=as_of)).with_for_update(skip_locked=True)).scalars());released=feedback_created=0
    for a in rows:
        scheduled=bool(a.scheduled_release_date and a.scheduled_release_date<=as_of)
        a.status=AllocationStatus.RELEASED_EARLY if scheduled else AllocationStatus.COMPLETED;a.release_date=a.scheduled_release_date if scheduled else a.end_date;a.released_at=datetime.utcnow();
        if not a.release_reason_type:a.release_reason_type="OTHER" if scheduled else "PROJECT_COMPLETED"
        if not a.release_reason:a.release_reason="Scheduled release" if scheduled else "Assignment end date reached"
        if not a.released_by_user_id:a.released_by_user_id=a.release_requested_by_user_id
        released+=1;p=db.get(Project,a.project_id);existing=db.execute(select(FeedbackTask).where(FeedbackTask.allocation_id==a.id)).scalar_one_or_none()
        if p and not existing:db.add(FeedbackTask(allocation_id=a.id,manager_user_id=p.manager_user_id,employee_id=a.employee_id,due_date=as_of+timedelta(days=3)));feedback_created+=1
        if a.resource_request_id:
            req=db.get(ResourceRequest,a.resource_request_id)
            if req and req.status==RequestStatus.FULFILLED:req.status=RequestStatus.CLOSED
        audit(db,None,"AUTO_DEALLOCATE","Allocation",a.id,{"as_of":as_of.isoformat(),"release_date":a.release_date.isoformat(),"status":a.status.value})
    n=recalculate_capacity_metrics(db,as_of=as_of);db.commit();return {"activated":len(starting),"released":released,"feedback_tasks_created":feedback_created,"capacity_metrics_recalculated":n,"as_of":as_of.isoformat()}
