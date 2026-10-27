from collections import defaultdict
from datetime import date, timedelta
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session
from app.models.entities import (
    Allocation, AllocationStatus, DesignationMaster, Employee, EmployeeSkill,
    FeedbackStatus, FeedbackTask, SkillMaster,
)
from app.services.capacity import allocation_effective_end, assert_capacity, get_max_concurrent_projects

PROFICIENCY_RANK = {'Beginner': 1, 'Intermediate': 2, 'Expert': 3}


def serialize_employees(db: Session, employees: list[Employee]) -> list[dict]:
    if not employees:
        return []
    ids = [e.id for e in employees]
    designation_ids = {e.designation_id for e in employees if e.designation_id}
    designations = {
        d.id: d for d in db.execute(select(DesignationMaster).where(DesignationMaster.id.in_(designation_ids))).scalars()
    } if designation_ids else {}
    skill_rows = db.execute(
        select(EmployeeSkill, SkillMaster)
        .join(SkillMaster, SkillMaster.id == EmployeeSkill.skill_id)
        .where(EmployeeSkill.employee_id.in_(ids))
        .order_by(SkillMaster.name)
    ).all()
    by_employee: dict[int, list[dict]] = defaultdict(list)
    for link, skill in skill_rows:
        by_employee[link.employee_id].append({
            'id': skill.id,
            'name': skill.name,
            'proficiency': link.proficiency,
            'years': link.years_of_experience,
        })
    out = []
    for emp in employees:
        designation = designations.get(emp.designation_id)
        skill_items = by_employee.get(emp.id, [])
        out.append({
            'id': emp.id,
            'employee_code': emp.employee_code,
            'name': emp.name,
            'designation': {'id': designation.id, 'name': designation.name} if designation else None,
            'designation_name': designation.name if designation else (emp.designation or ''),
            'department': emp.department or '',
            'skills': skill_items,
            'skills_text': ', '.join(x['name'] for x in skill_items) or (emp.skills or ''),
            'experience_years': emp.experience_years,
            'manager_user_id': emp.manager_user_id,
            'active': emp.active,
            'joining_date': emp.joining_date,
        })
    return out


def serialize_employee(db: Session, employee: Employee) -> dict:
    return serialize_employees(db, [employee])[0]


def sync_employee_legacy_fields(db: Session, employee: Employee) -> None:
    designation = db.get(DesignationMaster, employee.designation_id) if employee.designation_id else None
    employee.designation = designation.name if designation else ''
    links = db.execute(
        select(EmployeeSkill, SkillMaster)
        .join(SkillMaster, SkillMaster.id == EmployeeSkill.skill_id)
        .where(EmployeeSkill.employee_id == employee.id)
        .order_by(SkillMaster.name)
    ).all()
    employee.skills = ', '.join(skill.name for _, skill in links)


def validate_employee_master_fit(
    db: Session,
    *,
    employee_id: int,
    designation_id: int,
    skill_id: int,
    project_id: int,
    start_date: date,
    end_date: date,
    allocation_percentage: float,
    exclude_allocation_id: int | None = None,
    lock: bool = False,
) -> Employee:
    stmt = select(Employee).where(Employee.id == employee_id)
    if lock:
        stmt = stmt.with_for_update()
    employee = db.execute(stmt).scalar_one_or_none()
    if not employee or not employee.active:
        raise ValueError('Employee is inactive or no longer available')
    if employee.designation_id != designation_id:
        raise ValueError(f'{employee.name} does not have the selected designation')
    has_skill = db.execute(
        select(EmployeeSkill.id).where(EmployeeSkill.employee_id == employee_id, EmployeeSkill.skill_id == skill_id)
    ).scalar_one_or_none()
    if not has_skill:
        raise ValueError(f'{employee.name} does not have the selected skill')
    assert_capacity(
        db=db,
        employee_id=employee_id,
        project_id=project_id,
        start_date=start_date,
        end_date=end_date,
        allocation_percentage=allocation_percentage,
        exclude_allocation_id=exclude_allocation_id,
        lock_employee=lock,
    )
    return employee


def _peak_existing(allocations: list[Allocation], start: date, end: date) -> tuple[float, int, bool]:
    if not allocations:
        return 0.0, 0, False
    boundaries = {start, end + timedelta(days=1)}
    for allocation in allocations:
        effective_end = min(end, allocation_effective_end(allocation))
        effective_start = max(start, allocation.start_date)
        if effective_start <= effective_end:
            boundaries.add(effective_start)
            boundaries.add(effective_end + timedelta(days=1))
    peak_pct = 0.0
    peak_projects = 0
    for boundary in sorted(boundaries)[:-1]:
        active = [a for a in allocations if a.start_date <= boundary <= allocation_effective_end(a)]
        peak_pct = max(peak_pct, sum(float(a.allocation_percentage) for a in active))
        peak_projects = max(peak_projects, len({a.project_id for a in active}))
    return peak_pct, peak_projects, False


def eligible_employee_search(
    db: Session,
    *,
    designation_id: int | None = None,
    skill_id: int | None = None,
    project_id: int | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    allocation_percentage: float | None = None,
    q: str = '',
    request_id: int | None = None,
    probe_employee_ids: set[int] | None = None,
    limit: int = 20,
    offset: int = 0,
) -> dict:
    """Progressive eligible-employee search without N+1 queries.

    Only filters whose values are supplied are applied. Capacity and duplicate-
    project checks activate once a complete date range and requested percentage
    are available. This lets the employee picker remain useful from the first
    interaction while create/update/approval still perform authoritative full
    validation.
    """
    latest_feedback = (
        select(FeedbackTask.employee_id, func.max(FeedbackTask.id).label('feedback_id'))
        .where(FeedbackTask.status == FeedbackStatus.COMPLETED)
        .group_by(FeedbackTask.employee_id)
        .subquery()
    )
    stmt = (
        select(
            Employee,
            DesignationMaster.name.label('designation_name'),
            EmployeeSkill.proficiency,
            EmployeeSkill.years_of_experience.label('skill_years'),
            FeedbackTask.rating,
            FeedbackTask.feedback,
            Allocation,
        )
        .outerjoin(DesignationMaster, DesignationMaster.id == Employee.designation_id)
        .outerjoin(EmployeeSkill, EmployeeSkill.employee_id == Employee.id)
        .outerjoin(SkillMaster, SkillMaster.id == EmployeeSkill.skill_id)
        .outerjoin(latest_feedback, latest_feedback.c.employee_id == Employee.id)
        .outerjoin(FeedbackTask, FeedbackTask.id == latest_feedback.c.feedback_id)
        .where(Employee.active.is_(True))
    )
    if designation_id:
        stmt = stmt.where(Employee.designation_id == designation_id, DesignationMaster.is_active.is_(True))
    if skill_id:
        stmt = stmt.where(EmployeeSkill.skill_id == skill_id, SkillMaster.is_active.is_(True))

    complete_range = bool(start_date and end_date)
    if complete_range:
        stmt = stmt.outerjoin(
            Allocation,
            and_(
                Allocation.employee_id == Employee.id,
                Allocation.status.in_([AllocationStatus.ACTIVE, AllocationStatus.PLANNED]),
                Allocation.start_date <= end_date,
                Allocation.end_date >= start_date,
            ),
        )
    else:
        # Preserve one-query shape while avoiding unrelated allocation rows.
        stmt = stmt.outerjoin(Allocation, and_(Allocation.employee_id == Employee.id, Allocation.id == -1))

    q = q.strip()
    if q:
        stmt = stmt.where(or_(Employee.name.ilike(f'%{q}%'), Employee.employee_code.ilike(f'%{q}%')))
    rows = db.execute(stmt.order_by(Employee.name, EmployeeSkill.id, Allocation.id)).all()

    selected_ids: set[int] = set()
    if request_id:
        from app.models.entities import ResourceRequestSelection
        selected_ids = set(db.execute(select(ResourceRequestSelection.employee_id).where(ResourceRequestSelection.request_id == request_id)).scalars())

    grouped: dict[int, dict] = {}
    for emp, designation_name, proficiency, skill_years, rating, feedback, allocation in rows:
        item = grouped.setdefault(emp.id, {
            'employee_id': emp.id,
            'employee_code': emp.employee_code,
            'name': emp.name,
            'designation_id': emp.designation_id,
            'designation_name': designation_name or emp.designation or '',
            'available_allocation_percentage': 100.0,
            'experience': emp.experience_years,
            'skill_proficiency': None,
            'skill_years_of_experience': None,
            'last_feedback_rating': rating,
            'last_feedback_description': feedback or None,
            'skills': {},
            '_allocations': {},
        })
        if proficiency is not None:
            # When skill_id is supplied this is the selected skill. Otherwise keep
            # all employee skills so selecting an employee first can drive the form.
            # EmployeeSkill.skill_id is not selected separately above, so derive
            # master skill details in one additional bulk query after grouping.
            if skill_id:
                item['skill_proficiency'] = proficiency
                item['skill_years_of_experience'] = skill_years
        if allocation:
            item['_allocations'][allocation.id] = allocation

    # Bulk-load skill metadata once for all candidate employees (still no N+1).
    if grouped:
        skill_rows = db.execute(
            select(EmployeeSkill.employee_id, EmployeeSkill.skill_id, EmployeeSkill.proficiency, EmployeeSkill.years_of_experience, SkillMaster.name)
            .join(SkillMaster, SkillMaster.id == EmployeeSkill.skill_id)
            .where(EmployeeSkill.employee_id.in_(list(grouped)), SkillMaster.is_active.is_(True))
            .order_by(SkillMaster.name)
        ).all()
        for employee_id, sid, proficiency, years, name in skill_rows:
            grouped[employee_id]['skills'][sid] = {'id': sid, 'name': name, 'proficiency': proficiency, 'years': years}
            if skill_id and sid == skill_id:
                grouped[employee_id]['skill_proficiency'] = proficiency
                grouped[employee_id]['skill_years_of_experience'] = years
            elif not skill_id:
                current = grouped[employee_id].get('skill_proficiency')
                if PROFICIENCY_RANK.get(proficiency, 0) > PROFICIENCY_RANK.get(current, 0):
                    grouped[employee_id]['skill_proficiency'] = proficiency
                    grouped[employee_id]['skill_years_of_experience'] = years

    max_projects = get_max_concurrent_projects(db)
    eligible = []
    for item in grouped.values():
        allocations = list(item.pop('_allocations').values())
        item['skills'] = list(item['skills'].values())
        if complete_range:
            peak_pct, _, _ = _peak_existing(allocations, start_date, end_date)
            available = max(0.0, 100.0 - peak_pct)
        else:
            peak_pct, available = 0.0, 100.0
        item['allocated_percentage_in_period'] = round(peak_pct, 2)
        item['available_allocation_percentage'] = round(available, 2)

        duplicate_project = bool(project_id and any(a.project_id == project_id for a in allocations))
        projected_projects = len({a.project_id for a in allocations} | ({project_id} if project_id else set()))
        if duplicate_project:
            continue
        if allocation_percentage and complete_range and available + 1e-9 < allocation_percentage:
            continue
        if project_id and complete_range and projected_projects > max_projects:
            continue
        eligible.append(item)

    eligible.sort(key=lambda x: (
        -float(x['available_allocation_percentage']),
        -PROFICIENCY_RANK.get(x.get('skill_proficiency'), 0),
        -float(x['experience'] or 0),
        x['name'].lower(),
    ))
    probe_ids = probe_employee_ids or set()
    selected_eligible_ids = [x['employee_id'] for x in eligible if x['employee_id'] in selected_ids or x['employee_id'] in probe_ids]
    selectable = [x for x in eligible if x['employee_id'] not in selected_ids]
    page = selectable[offset:offset + limit]
    return {'items': page, 'selected_eligible_ids': selected_eligible_ids, 'total': len(selectable), 'limit': limit, 'offset': offset, 'has_more': offset + limit < len(selectable)}
