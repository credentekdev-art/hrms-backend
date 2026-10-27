from datetime import date, datetime
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models.entities import Allocation, AllocationStatus, Employee, ResourceCapacity
from app.services.capacity import allocation_effective_end


def recalculate_capacity_metrics(db: Session, employee_ids: set[int] | None = None, as_of: date | None = None) -> int:
    as_of = as_of or date.today()
    emp_stmt = select(Employee).where(Employee.active.is_(True))
    if employee_ids is not None:
        emp_stmt = emp_stmt.where(Employee.id.in_(employee_ids))
    employees = list(db.execute(emp_stmt).scalars().all())

    updated = 0
    for employee in employees:
        candidates = list(db.execute(select(Allocation).where(
            Allocation.employee_id == employee.id,
            Allocation.status.in_([AllocationStatus.ACTIVE, AllocationStatus.PLANNED]),
            Allocation.start_date <= as_of,
            Allocation.end_date >= as_of,
        )).scalars().all())
        allocations = [a for a in candidates if allocation_effective_end(a) >= as_of]
        projects = len({a.project_id for a in allocations})
        allocated = round(sum(float(a.allocation_percentage) for a in allocations), 2)
        available = round(100.0 - allocated, 2)
        if allocated > 100:
            state = "OVER_ALLOCATED"
        elif allocated >= 100:
            state = "FULLY_ALLOCATED"
        elif allocated > 0:
            state = "PARTIALLY_ALLOCATED"
        else:
            state = "AVAILABLE"

        metric = db.get(ResourceCapacity, employee.id)
        if not metric:
            metric = ResourceCapacity(employee_id=employee.id, as_of_date=as_of)
            db.add(metric)
        metric.as_of_date = as_of
        metric.active_project_count = projects
        metric.allocated_percentage = allocated
        metric.available_percentage = available
        metric.capacity_state = state
        metric.recalculated_at = datetime.utcnow()
        updated += 1
    return updated
