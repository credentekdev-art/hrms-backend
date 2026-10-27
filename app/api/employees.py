from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.dependencies import get_current_user, require_roles
from app.models.entities import DesignationMaster, Employee, EmployeeSkill, Role, SkillMaster, User
from app.schemas.entities import EmployeeCreate, EmployeeOut, EmployeeSkillInput, EmployeeUpdate
from app.services.access import employee_visible_to, scoped_employee_stmt
from app.services.audit import audit
from app.services.employee_directory import serialize_employee, serialize_employees, sync_employee_legacy_fields

router = APIRouter(prefix='/employees', tags=['Employees'])


def _field_error(field: str, message: str) -> HTTPException:
    return HTTPException(status_code=422, detail=[{'loc': ['body', field], 'msg': message, 'type': 'value_error'}])


def _validate_masters(db: Session, designation_id: int | None, skills: list[EmployeeSkillInput] | None) -> None:
    if designation_id is None:
        raise _field_error('designation_id', 'Designation is required')
    designation = db.get(DesignationMaster, designation_id)
    if not designation:
        raise _field_error('designation_id', 'Designation does not exist')
    if not designation.is_active:
        raise _field_error('designation_id', 'Designation is inactive')

    # None has semantic meaning on PATCH: skill_items was not sent, so do not
    # validate or mutate the current employee-skill links. [] means clear them.
    if skills is None:
        return

    ids = [skill.skill_id for skill in skills]
    if len(ids) != len(set(ids)):
        raise _field_error('skill_items', 'The same skill cannot be selected more than once')
    if not ids:
        return

    rows = list(db.execute(select(SkillMaster.id, SkillMaster.is_active).where(SkillMaster.id.in_(ids))).all())
    by_id = {skill_id: is_active for skill_id, is_active in rows}
    missing = [skill_id for skill_id in ids if skill_id not in by_id]
    inactive = [skill_id for skill_id in ids if skill_id in by_id and not by_id[skill_id]]
    if missing:
        raise _field_error('skill_items', f'Skill does not exist: {missing}')
    if inactive:
        raise _field_error('skill_items', f'Skill is inactive: {inactive}')


def _replace_skills(db: Session, employee: Employee, skill_items: list[EmployeeSkillInput]) -> None:
    db.query(EmployeeSkill).filter(EmployeeSkill.employee_id == employee.id).delete(synchronize_session=False)
    for skill in skill_items:
        db.add(EmployeeSkill(
            employee_id=employee.id,
            skill_id=skill.skill_id,
            proficiency=skill.proficiency,
            years_of_experience=skill.years_of_experience,
        ))
    db.flush()
    sync_employee_legacy_fields(db, employee)


@router.get('/managers')
def managers(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    rows = db.execute(select(User).where(User.role == Role.MANAGER, User.is_active.is_(True)).order_by(User.full_name)).scalars()
    return [{'id': x.id, 'name': x.full_name, 'email': x.email} for x in rows]


@router.get('/page')
def employee_page(
    q: str = Query('', max_length=100),
    designation_id: int | None = None,
    skill_id: int | None = None,
    status: str = Query('active', pattern='^(active|inactive|all)$'),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    stmt = scoped_employee_stmt(user, active_only=False)
    if status == 'active':
        stmt = stmt.where(Employee.active.is_(True))
    elif status == 'inactive':
        stmt = stmt.where(Employee.active.is_(False))
    if q.strip():
        stmt = stmt.where(or_(Employee.name.ilike(f'%{q.strip()}%'), Employee.employee_code.ilike(f'%{q.strip()}%'), Employee.department.ilike(f'%{q.strip()}%')))
    if designation_id:
        stmt = stmt.where(Employee.designation_id == designation_id)
    if skill_id:
        stmt = stmt.where(Employee.id.in_(select(EmployeeSkill.employee_id).where(EmployeeSkill.skill_id == skill_id)))
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = list(db.execute(stmt.order_by(Employee.name).offset(offset).limit(limit)).scalars())
    return {'items': serialize_employees(db, rows), 'total': total, 'limit': limit, 'offset': offset, 'has_more': offset + limit < total}


@router.get('', response_model=list[EmployeeOut])
def employees(
    designation_id: int | None = None,
    skill_id: int | None = None,
    active: bool | None = True,
    q: str = Query('', max_length=100),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    stmt = scoped_employee_stmt(user, active_only=False)
    if active is not None:
        stmt = stmt.where(Employee.active.is_(active))
    if designation_id:
        stmt = stmt.where(Employee.designation_id == designation_id)
    if skill_id:
        stmt = stmt.where(Employee.id.in_(select(EmployeeSkill.employee_id).where(EmployeeSkill.skill_id == skill_id)))
    if q.strip():
        stmt = stmt.where(or_(Employee.name.ilike(f'%{q.strip()}%'), Employee.employee_code.ilike(f'%{q.strip()}%')))
    rows = list(db.execute(stmt.order_by(Employee.name).limit(limit)).scalars())
    return serialize_employees(db, rows)


@router.get('/{employee_id}', response_model=EmployeeOut)
def employee_detail(employee_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    emp = db.get(Employee, employee_id)
    if not emp or not employee_visible_to(db, user, employee_id):
        raise HTTPException(404, 'Employee not found')
    return serialize_employee(db, emp)


@router.post('', response_model=EmployeeOut, dependencies=[Depends(require_roles(Role.ADMIN, Role.HR))])
def create_employee(payload: EmployeeCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _validate_masters(db, payload.designation_id, payload.skill_items)
    if db.execute(select(Employee.id).where(func.lower(Employee.employee_code) == payload.employee_code.strip().lower())).scalar_one_or_none():
        raise HTTPException(409, 'Employee ID already exists')
    emp = Employee(
        employee_code=payload.employee_code.strip(),
        name=payload.name.strip(),
        designation_id=payload.designation_id,
        department=payload.department.strip(),
        experience_years=payload.experience_years,
        manager_user_id=payload.manager_user_id,
        active=payload.active,
        joining_date=payload.joining_date,
    )
    db.add(emp)
    db.flush()
    _replace_skills(db, emp, payload.skill_items)
    audit(db, user.id, 'CREATE_EMPLOYEE', 'Employee', emp.id, {'employee_code': emp.employee_code, 'designation_id': emp.designation_id})
    db.commit()
    db.refresh(emp)
    return serialize_employee(db, emp)


def _update_employee(employee_id: int, payload: EmployeeUpdate, db: Session, user: User):
    emp = db.execute(select(Employee).where(Employee.id == employee_id).with_for_update()).scalar_one_or_none()
    if not emp:
        raise HTTPException(404, 'Employee not found')

    fields_sent = payload.model_fields_set
    skills_sent = 'skill_items' in fields_sent
    skill_items = payload.skill_items if skills_sent else None
    designation_id = payload.designation_id if 'designation_id' in fields_sent else emp.designation_id
    _validate_masters(db, designation_id, skill_items)

    values = payload.model_dump(exclude_unset=True, exclude={'skill_items'})
    if 'employee_code' in values and values['employee_code'] is not None:
        duplicate = db.execute(
            select(Employee.id).where(
                func.lower(Employee.employee_code) == values['employee_code'].strip().lower(),
                Employee.id != emp.id,
            )
        ).scalar_one_or_none()
        if duplicate:
            raise HTTPException(409, 'Employee ID already exists')
        values['employee_code'] = values['employee_code'].strip()
    if 'name' in values and values['name'] is not None:
        values['name'] = values['name'].strip()
    if 'department' in values and values['department'] is not None:
        values['department'] = values['department'].strip()

    for key, value in values.items():
        setattr(emp, key, value)

    if skills_sent:
        # Explicit [] means clear skills; None is impossible here unless a client
        # sent JSON null, which is treated as "not sent" for safety.
        if skill_items is not None:
            _replace_skills(db, emp, skill_items)
        else:
            sync_employee_legacy_fields(db, emp)
    else:
        sync_employee_legacy_fields(db, emp)

    audit(db, user.id, 'UPDATE_EMPLOYEE', 'Employee', emp.id, payload.model_dump(exclude_unset=True))
    db.commit()
    db.refresh(emp)
    return serialize_employee(db, emp)


@router.patch('/{employee_id}', response_model=EmployeeOut, dependencies=[Depends(require_roles(Role.ADMIN, Role.HR))])
def update_employee(employee_id: int, payload: EmployeeUpdate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return _update_employee(employee_id, payload, db, user)


@router.put('/{employee_id}', response_model=EmployeeOut, dependencies=[Depends(require_roles(Role.ADMIN, Role.HR))])
def replace_employee(employee_id: int, payload: EmployeeUpdate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    # PUT is kept as an alias for existing clients; EmployeeUpdate preserves
    # partial-update semantics so omitted skill_items remain unchanged.
    return _update_employee(employee_id, payload, db, user)
