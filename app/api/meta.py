from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.dependencies import get_current_user
from app.models.entities import Employee, Project, ResourceRequest, RequestStatus, Role, User
from app.schemas.entities import ProjectOut, UserOut

router = APIRouter(tags=['Metadata'])


@router.get('/me', response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return user


@router.get('/profile')
def profile(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    emp = db.get(Employee, user.employee_id) if user.employee_id else None
    reporting = db.get(User, emp.manager_user_id) if emp and emp.manager_user_id else None
    projects = []
    if user.role == Role.MANAGER:
        projects = [{'id': p.id, 'code': p.code, 'name': p.name} for p in db.execute(select(Project).where(Project.manager_user_id == user.id, Project.active.is_(True)).order_by(Project.name)).scalars()]
    return {'id': user.id, 'name': user.full_name, 'email': user.email, 'role': user.role.value, 'department': emp.department if emp else '', 'employee_id': emp.employee_code if emp else None, 'reporting_manager': reporting.full_name if reporting else None, 'projects': projects}


@router.get('/projects', response_model=list[ProjectOut])
def projects(include_inactive: bool = False, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    stmt = select(Project).order_by(Project.name)
    if not include_inactive: stmt = stmt.where(Project.active.is_(True))
    if user.role == Role.MANAGER: stmt = stmt.where(Project.manager_user_id == user.id)
    elif user.role == Role.EMPLOYEE: stmt = stmt.where(Project.id == -1)
    return list(db.execute(stmt).scalars())


@router.get('/pending-resource-requests')
def pending_requests(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    stmt = select(ResourceRequest).where(ResourceRequest.status.in_([RequestStatus.DRAFT, RequestStatus.SUBMITTED, RequestStatus.UNDER_REVIEW, RequestStatus.APPROVED]))
    if user.role == Role.MANAGER: stmt = stmt.where(ResourceRequest.requested_by_user_id == user.id)
    elif user.role == Role.EMPLOYEE: return []
    return [{'id': r.id, 'project_id': r.project_id, 'number_of_resources': r.number_of_resources, 'allocation_percentage': r.allocation_percentage, 'start_date': r.start_date, 'end_date': r.end_date, 'status': r.status.value} for r in db.execute(stmt.order_by(ResourceRequest.created_at.desc())).scalars()]

# Keep the historic /employees endpoints available when this metadata router is mounted by itself.
from app.api.employees import router as employees_router
router.include_router(employees_router)
