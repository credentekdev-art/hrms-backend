from datetime import date, timedelta
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.dependencies import get_current_user
from app.models.entities import Allocation, AllocationStatus, DesignationMaster, Employee, FeedbackStatus, FeedbackTask, Project, Role, User
from app.services.access import allocation_visible_to, manager_visible_employee_ids_stmt

router = APIRouter(prefix='/calendar', tags=['Calendar'])
HISTORY = {AllocationStatus.COMPLETED, AllocationStatus.RELEASED_EARLY, AllocationStatus.RELEASED, AllocationStatus.CANCELLED}


def _validate_range(start: date, end: date, max_days: int = 93):
    if end < start:
        raise HTTPException(422, 'End date must be on or after start date')
    if (end - start).days > max_days:
        raise HTTPException(422, f'Date range cannot exceed {max_days + 1} days')


def _scope(stmt, user):
    if user.role == Role.EMPLOYEE:
        return stmt.where(Allocation.employee_id == user.employee_id)
    if user.role == Role.MANAGER:
        return stmt.where(Allocation.employee_id.in_(manager_visible_employee_ids_stmt(user.id)))
    return stmt


def _base(start: date, end: date):
    return (
        select(Allocation, Employee.name, Employee.employee_code, Project.name, Project.code)
        .join(Employee, Employee.id == Allocation.employee_id)
        .join(Project, Project.id == Allocation.project_id)
        .where(Allocation.start_date <= end, Allocation.end_date >= start)
        .where(
            or_(
                Allocation.status == AllocationStatus.COMPLETED,
                Allocation.release_date > start,
                Allocation.scheduled_release_date > start,
                and_(Allocation.release_date.is_(None), Allocation.scheduled_release_date.is_(None)),
            )
        )
    )


def _filters(stmt, user, start, end, employee_id=None, project_id=None, status=None, kind=None, search=None, include_history=False):
    stmt = _scope(stmt, user)
    if employee_id:
        stmt = stmt.where(Allocation.employee_id == employee_id)
    if project_id:
        stmt = stmt.where(Allocation.project_id == project_id)
    if status:
        stmt = stmt.where(Allocation.status == status)
    elif not include_history and kind != 'HISTORY':
        stmt = stmt.where(Allocation.status.not_in(tuple(HISTORY)))
    if kind == 'CURRENT':
        stmt = stmt.where(Allocation.status == AllocationStatus.ACTIVE)
    elif kind == 'PLANNED':
        stmt = stmt.where(Allocation.status == AllocationStatus.PLANNED)
    elif kind == 'HISTORY':
        stmt = stmt.where(Allocation.status.in_(tuple(HISTORY)))
    if search:
        needle = f'%{search.strip()}%'
        stmt = stmt.where(
            or_(
                Employee.name.ilike(needle),
                Employee.employee_code.ilike(needle),
                Project.name.ilike(needle),
                Project.code.ilike(needle),
            )
        )
    return stmt


def _effective_end(a):
    if a.status == AllocationStatus.COMPLETED:
        return a.end_date
    if a.release_date:
        return min(a.end_date, a.release_date - timedelta(days=1))
    if a.scheduled_release_date:
        return min(a.end_date, a.scheduled_release_date - timedelta(days=1))
    return a.end_date


def _event_type(status):
    if status == AllocationStatus.ACTIVE:
        return 'CURRENT'
    if status == AllocationStatus.PLANNED:
        return 'PLANNED'
    return 'HISTORY'


def _item(a, employee_name, employee_code, project_name, project_code):
    return {
        'id': a.id,
        'title': f'{employee_name} · {project_name}',
        'employee_id': a.employee_id,
        'employee_name': employee_name,
        'employee_code': employee_code,
        'project_id': a.project_id,
        'project_name': project_name,
        'project_code': project_code,
        'start': a.start_date,
        'end': _effective_end(a),
        'start_date': a.start_date,
        'end_date': _effective_end(a),
        'planned_end_date': a.end_date,
        'allocation_percentage': a.allocation_percentage,
        'status': a.status.value,
        'type': _event_type(a.status),
    }


@router.get('/summary')
def month_summary(
    start: date,
    end: date,
    employee_id: int | None = None,
    project_id: int | None = None,
    status: AllocationStatus | None = None,
    type: str | None = Query(None, pattern='^(CURRENT|PLANNED|HISTORY)$'),
    search: str | None = Query(None, max_length=100),
    include_history: bool = False,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _validate_range(start, end, 62)
    stmt = _filters(_base(start, end), user, start, end, employee_id, project_id, status, type, search, include_history)
    rows = list(db.execute(stmt.order_by(Allocation.start_date, Allocation.id)).all())
    days = []
    cursor = start
    while cursor <= end:
        active = []
        for a, en, ec, pn, pc in rows:
            if a.start_date <= cursor <= _effective_end(a):
                event = _item(a, en, ec, pn, pc)
                event['start'] = cursor
                event['end'] = cursor
                active.append(event)
        if active:
            status_counts = {}
            total_allocation = 0.0
            employee_ids = set()
            for event in active:
                status_counts[event['status']] = status_counts.get(event['status'], 0) + 1
                total_allocation += float(event['allocation_percentage'])
                employee_ids.add(event['employee_id'])
            total_capacity = float(len(employee_ids) * 100)
            days.append({
                'date': cursor,
                'count': len(active),
                'employee_count': len(employee_ids),
                'preview': active[:2],
                'status_counts': status_counts,
                'total_allocation_percentage': round(total_allocation, 2),
                'capacity_total_percentage': round(total_capacity, 2),
                'available_capacity_percentage': round(max(0.0, total_capacity - total_allocation), 2),
            })
        cursor += timedelta(days=1)
    peak = max([day['count'] for day in days], default=0)
    for day in days:
        day['heat'] = round(day['count'] / peak, 3) if peak else 0
    return {'start': start, 'end': end, 'days': days, 'peak_count': peak}


@router.get('/events')
def calendar_events(
    start: date,
    end: date,
    employee_id: int | None = None,
    project_id: int | None = None,
    status: AllocationStatus | None = None,
    type: str | None = Query(None, pattern='^(CURRENT|PLANNED|HISTORY)$'),
    search: str | None = Query(None, max_length=100),
    include_history: bool = False,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _validate_range(start, end, 366)
    base = _filters(_base(start, end), user, start, end, employee_id, project_id, status, type, search, include_history)
    total = db.execute(select(func.count()).select_from(base.order_by(None).subquery())).scalar_one()
    rows = list(db.execute(base.order_by(Allocation.start_date, Allocation.id).offset(offset).limit(limit)).all())
    return {'items': [_item(*r) for r in rows], 'total': total, 'limit': limit, 'offset': offset, 'has_more': offset + len(rows) < total}


@router.get('/facets')
def calendar_facets(
    start: date,
    end: date,
    search: str | None = Query(None, max_length=100),
    include_history: bool = True,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Counts for the collapsible filter rail, scoped to the visible range and current user."""
    _validate_range(start, end, 366)
    base = _filters(_base(start, end), user, start, end, search=search, include_history=include_history).subquery()
    rows = db.execute(select(base)).all()
    employee_counts = {}
    project_counts = {}
    status_counts = {}
    type_counts = {'CURRENT': 0, 'PLANNED': 0, 'HISTORY': 0}
    for row in rows:
        m = row._mapping
        eid, en = m['employee_id'], m['name']
        pid, pn = m['project_id'], m['name_1']
        st = m['status'].value if hasattr(m['status'], 'value') else str(m['status'])
        employee_counts[(eid, en)] = employee_counts.get((eid, en), 0) + 1
        project_counts[(pid, pn)] = project_counts.get((pid, pn), 0) + 1
        status_counts[st] = status_counts.get(st, 0) + 1
        type_counts['CURRENT' if st == 'ACTIVE' else 'PLANNED' if st == 'PLANNED' else 'HISTORY'] += 1
    return {
        'employees': [{'id': k[0], 'label': k[1], 'count': v} for k, v in sorted(employee_counts.items(), key=lambda x: x[0][1].lower())],
        'projects': [{'id': k[0], 'label': k[1], 'count': v} for k, v in sorted(project_counts.items(), key=lambda x: x[0][1].lower())],
        'statuses': [{'value': k, 'label': k.replace('_', ' ').title(), 'count': v} for k, v in sorted(status_counts.items())],
        'types': [{'value': k, 'label': k.title(), 'count': v} for k, v in type_counts.items()],
    }


@router.get('/events/{event_id}')
def calendar_event_detail(event_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    a = db.get(Allocation, event_id)
    if not a or not allocation_visible_to(db, user, a):
        raise HTTPException(404, 'Calendar event not found')
    emp = db.get(Employee, a.employee_id)
    project = db.get(Project, a.project_id)
    creator = db.get(User, a.created_by_user_id)
    released_by = db.get(User, a.released_by_user_id) if a.released_by_user_id else None
    manager = db.get(User, project.manager_user_id) if project else None
    feedback = db.execute(select(FeedbackTask).where(FeedbackTask.allocation_id == a.id, FeedbackTask.status == FeedbackStatus.COMPLETED)).scalar_one_or_none()
    return {
        **_item(a, emp.name if emp else '', emp.employee_code if emp else '', project.name if project else '', project.code if project else ''),
        'planned_end_date': a.end_date,
        'scheduled_release_date': a.scheduled_release_date,
        'release_date': a.release_date,
        'release_reason_type': a.release_reason_type,
        'release_reason': a.release_reason,
        'allocated_by': creator.full_name if creator else 'Unknown',
        'project_manager': manager.full_name if manager else 'Unknown',
        'released_by': released_by.full_name if released_by else None,
        'feedback': None if not feedback else {
            'rating': feedback.rating,
            'description': feedback.feedback,
            'completed_at': feedback.completed_at,
            'edited_at': feedback.edited_at,
        },
    }


@router.get('/aggregate')
def calendar_aggregate(
    start: date,
    end: date,
    employee_id: int | None = None,
    project_id: int | None = None,
    status: AllocationStatus | None = None,
    type: str | None = Query(None, pattern='^(CURRENT|PLANNED|HISTORY)$'),
    search: str | None = Query(None, max_length=100),
    include_history: bool = True,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """High-density range aggregate used by Month view.

    Response is count-oriented and intentionally lightweight; individual rows
    are loaded only when a day is opened.
    """
    return month_summary(start, end, employee_id, project_id, status, type, search, include_history, db, user)


@router.get('/day-details')
def calendar_day_details(
    day: date,
    search: str | None = Query(None, max_length=100),
    project_id: int | None = None,
    department: str | None = Query(None, max_length=255),
    designation_id: int | None = None,
    status: AllocationStatus | None = None,
    group_by: str = Query('project', pattern='^(project|department|status)$'),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    stmt = (
        select(Allocation, Employee.name, Employee.employee_code, Employee.department, Employee.designation_id,
               DesignationMaster.name.label('designation_name'), Project.name.label('project_name'), Project.code.label('project_code'))
        .join(Employee, Employee.id == Allocation.employee_id)
        .join(Project, Project.id == Allocation.project_id)
        .outerjoin(DesignationMaster, DesignationMaster.id == Employee.designation_id)
        .where(Allocation.start_date <= day, Allocation.end_date >= day)
        .where(Allocation.status != AllocationStatus.CANCELLED)
        .where(or_(Allocation.release_date.is_(None), Allocation.release_date > day))
    )
    stmt = _scope(stmt, user)
    if project_id: stmt = stmt.where(Allocation.project_id == project_id)
    if department: stmt = stmt.where(Employee.department == department)
    if designation_id: stmt = stmt.where(Employee.designation_id == designation_id)
    if status: stmt = stmt.where(Allocation.status == status)
    if search:
        needle = f'%{search.strip()}%'
        stmt = stmt.where(or_(Employee.name.ilike(needle), Employee.employee_code.ilike(needle), Project.name.ilike(needle), Project.code.ilike(needle)))
    total = db.execute(select(func.count()).select_from(stmt.order_by(None).subquery())).scalar_one()
    order = {
        'project': (Project.name, Employee.name),
        'department': (Employee.department, Employee.name),
        'status': (Allocation.status, Employee.name),
    }[group_by]
    rows = list(db.execute(stmt.order_by(*order).offset(offset).limit(limit)).all())
    items = []
    for a, employee_name, employee_code, dept, did, designation_name, project_name, project_code in rows:
        group = project_name if group_by == 'project' else (dept or 'No department') if group_by == 'department' else a.status.value.replace('_', ' ').title()
        items.append({
            'id': a.id, 'employee_id': a.employee_id, 'employee_name': employee_name, 'employee_code': employee_code,
            'department': dept or '', 'designation_id': did, 'designation_name': designation_name or '',
            'project_id': a.project_id, 'project_name': project_name, 'project_code': project_code,
            'allocation_percentage': a.allocation_percentage, 'status': a.status.value, 'group': group,
            'start_date': a.start_date, 'end_date': _effective_end(a),
        })
    return {'date': day, 'items': items, 'total': total, 'limit': limit, 'offset': offset, 'has_more': offset + len(items) < total, 'group_by': group_by}


@router.get('/resource-timeline')
def resource_timeline(
    start: date,
    end: date,
    search: str | None = Query(None, max_length=100),
    project_id: int | None = None,
    department: str | None = Query(None, max_length=255),
    designation_id: int | None = None,
    status: AllocationStatus | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _validate_range(start, end, 366)
    employee_stmt = (
        select(Employee.id, Employee.name, Employee.employee_code, Employee.department, Employee.designation_id, DesignationMaster.name.label('designation_name'))
        .join(Allocation, Allocation.employee_id == Employee.id)
        .outerjoin(DesignationMaster, DesignationMaster.id == Employee.designation_id)
        .where(Allocation.start_date <= end, Allocation.end_date >= start, Allocation.status != AllocationStatus.CANCELLED)
        .where(or_(Allocation.release_date.is_(None), Allocation.release_date > start))
        .distinct()
    )
    if user.role == Role.EMPLOYEE: employee_stmt = employee_stmt.where(Employee.id == user.employee_id)
    elif user.role == Role.MANAGER: employee_stmt = employee_stmt.where(Employee.id.in_(manager_visible_employee_ids_stmt(user.id)))
    if project_id: employee_stmt = employee_stmt.where(Allocation.project_id == project_id)
    if department: employee_stmt = employee_stmt.where(Employee.department == department)
    if designation_id: employee_stmt = employee_stmt.where(Employee.designation_id == designation_id)
    if status: employee_stmt = employee_stmt.where(Allocation.status == status)
    if search:
        needle=f'%{search.strip()}%'
        employee_stmt=employee_stmt.where(or_(Employee.name.ilike(needle),Employee.employee_code.ilike(needle)))
    total = db.execute(select(func.count()).select_from(employee_stmt.order_by(None).subquery())).scalar_one()
    employee_rows = list(db.execute(employee_stmt.order_by(Employee.department, Employee.name).offset(offset).limit(limit)).all())
    employee_ids = [row.id for row in employee_rows]
    allocations_by_employee = {eid: [] for eid in employee_ids}
    if employee_ids:
        alloc_rows = db.execute(
            select(Allocation, Project.name, Project.code)
            .join(Project, Project.id == Allocation.project_id)
            .where(Allocation.employee_id.in_(employee_ids), Allocation.start_date <= end, Allocation.end_date >= start, Allocation.status != AllocationStatus.CANCELLED)
            .where(or_(Allocation.release_date.is_(None), Allocation.release_date > start))
            .order_by(Allocation.employee_id, Allocation.start_date, Allocation.id)
        ).all()
        for allocation, project_name, project_code in alloc_rows:
            allocations_by_employee[allocation.employee_id].append({
                'id': allocation.id, 'project_id': allocation.project_id, 'project_name': project_name, 'project_code': project_code,
                'start_date': allocation.start_date, 'end_date': _effective_end(allocation),
                'allocation_percentage': allocation.allocation_percentage, 'status': allocation.status.value,
            })
    rows = []
    for employee in employee_rows:
        allocations = allocations_by_employee[employee.id]
        peak = 0.0
        cursor = start
        while cursor <= end:
            total_pct = sum(float(a['allocation_percentage']) for a in allocations if a['start_date'] <= cursor <= a['end_date'])
            peak = max(peak, total_pct)
            cursor += timedelta(days=1)
        rows.append({
            'employee_id': employee.id, 'employee_name': employee.name, 'employee_code': employee.employee_code,
            'department': employee.department or '', 'designation_id': employee.designation_id, 'designation_name': employee.designation_name or '',
            'peak_allocation_percentage': round(peak, 2), 'available_capacity_percentage': round(max(0, 100-peak), 2),
            'over_allocated': peak > 100, 'allocations': allocations,
        })
    return {'start': start, 'end': end, 'rows': rows, 'total': total, 'limit': limit, 'offset': offset, 'has_more': offset + len(rows) < total}
