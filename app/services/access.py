from sqlalchemy import or_, select
from sqlalchemy.orm import Session
from app.models.entities import Allocation, Employee, Project, ResourceRequest, ResourceRequestSelection, Role, User


def manager_visible_employee_ids_stmt(user_id: int):
    allocation_ids = select(Allocation.employee_id).join(Project, Project.id == Allocation.project_id).where(Project.manager_user_id == user_id)
    request_selection_ids = select(ResourceRequestSelection.employee_id).join(ResourceRequest, ResourceRequest.id == ResourceRequestSelection.request_id).where(ResourceRequest.requested_by_user_id == user_id)
    legacy_request_ids = select(ResourceRequest.requested_employee_id).where(ResourceRequest.requested_by_user_id == user_id, ResourceRequest.requested_employee_id.is_not(None))
    return allocation_ids.union(request_selection_ids, legacy_request_ids)


def scoped_employee_stmt(user: User, *, active_only: bool = True):
    stmt = select(Employee)
    if active_only:
        stmt = stmt.where(Employee.active.is_(True))
    if user.role == Role.MANAGER:
        stmt = stmt.where(Employee.id.in_(manager_visible_employee_ids_stmt(user.id)))
    elif user.role == Role.EMPLOYEE:
        stmt = stmt.where(Employee.id == user.employee_id)
    return stmt


def employee_visible_to(db: Session, user: User, employee_id: int) -> bool:
    if user.role in (Role.ADMIN, Role.HR, Role.RM_TEAM): return True
    if user.role == Role.EMPLOYEE: return user.employee_id == employee_id
    if user.role == Role.MANAGER:
        return db.execute(select(Employee.id).where(Employee.id == employee_id, Employee.id.in_(manager_visible_employee_ids_stmt(user.id)))).scalar_one_or_none() is not None
    return False


def project_visible_to(db: Session, user: User, project_id: int) -> bool:
    if user.role in (Role.ADMIN, Role.RM_TEAM): return True
    project=db.get(Project,project_id)
    return bool(project and user.role==Role.MANAGER and project.manager_user_id==user.id)


def allocation_visible_to(db: Session, user: User, allocation: Allocation) -> bool:
    if user.role in (Role.ADMIN, Role.HR, Role.RM_TEAM): return True
    if user.role == Role.EMPLOYEE: return allocation.employee_id == user.employee_id
    if user.role == Role.MANAGER:
        project=db.get(Project,allocation.project_id)
        return bool(project and project.manager_user_id==user.id) or employee_visible_to(db,user,allocation.employee_id)
    return False


def allocation_manageable_by(db: Session, user: User, allocation: Allocation) -> bool:
    if user.role in (Role.ADMIN, Role.RM_TEAM): return True
    if user.role != Role.MANAGER: return False
    project=db.get(Project,allocation.project_id)
    return bool(project and project.manager_user_id==user.id)
