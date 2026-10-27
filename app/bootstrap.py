from datetime import date, timedelta
from sqlalchemy import select
from app.core.database import SessionLocal
from app.core.security import hash_password
from app.models.entities import (
    Allocation, AllocationStatus, DesignationMaster, Employee, EmployeeSkill, Project, Role, SkillMaster, SystemConfig, User,
)
from app.core.config import settings
from app.services.capacity_metrics import recalculate_capacity_metrics


def _master(db, cls, name, **extra):
    item = db.execute(select(cls).where(cls.name == name)).scalar_one_or_none()
    if item: return item
    item = cls(name=name, **extra); db.add(item); db.flush(); return item


def bootstrap_demo_data():
    if not settings.bootstrap_demo_data: return
    db = SessionLocal()
    try:
        if db.execute(select(User.id).limit(1)).first():
            if not db.get(SystemConfig, 'overallocation_policy'):
                db.add(SystemConfig(key='overallocation_policy', value='BLOCK')); db.commit()
            return

        admin = User(email='admin@hrms.local', full_name='Admin User', password_hash=hash_password('Admin@123'), role=Role.ADMIN)
        manager = User(email='manager@hrms.local', full_name='Aarav Manager', password_hash=hash_password('Manager@123'), role=Role.MANAGER)
        rm = User(email='rm@hrms.local', full_name='Riya RM', password_hash=hash_password('RM@12345'), role=Role.RM_TEAM)
        hr = User(email='hr@hrms.local', full_name='Hema HR', password_hash=hash_password('HR@12345'), role=Role.HR)
        db.add_all([admin, manager, rm, hr]); db.flush()

        d_senior = _master(db, DesignationMaster, 'Senior Engineer')
        d_engineer = _master(db, DesignationMaster, 'Engineer')
        d_qa = _master(db, DesignationMaster, 'QA Engineer')
        skill_names = ['React','Python','FastAPI','HRMS','Java','Spring','SQL','APIs','QA','Automation','Selenium','API Testing']
        skill_map = {name: _master(db, SkillMaster, name, category='Technical') for name in skill_names}

        emp1 = Employee(employee_code='E1001', name='Nikita Sharma', designation='Senior Engineer', designation_id=d_senior.id, department='Engineering', skills='React, Python, FastAPI, HRMS', experience_years=5.2, manager_user_id=manager.id, joining_date=date.today()-timedelta(days=1000))
        emp2 = Employee(employee_code='E1002', name='Mayur Patil', designation='Engineer', designation_id=d_engineer.id, department='Engineering', skills='Java, Spring, SQL, APIs', experience_years=4.1, manager_user_id=manager.id, joining_date=date.today()-timedelta(days=800))
        emp3 = Employee(employee_code='E1003', name='Abhijit Kulkarni', designation='QA Engineer', designation_id=d_qa.id, department='Quality', skills='QA, Automation, Selenium, API Testing', experience_years=6.0, manager_user_id=manager.id, joining_date=date.today()-timedelta(days=1200))
        db.add_all([emp1, emp2, emp3]); db.flush()
        for emp, names in [(emp1,['React','Python','FastAPI','HRMS']),(emp2,['Java','Spring','SQL','APIs']),(emp3,['QA','Automation','Selenium','API Testing'])]:
            for name in names:
                db.add(EmployeeSkill(employee_id=emp.id, skill_id=skill_map[name].id, proficiency='Expert' if name in ('React','Java','QA') else 'Intermediate', years_of_experience=max(1.0,(emp.experience_years or 1)-1)))

        employee_user = User(email='employee@hrms.local', full_name='Nikita Sharma', password_hash=hash_password('Employee@123'), role=Role.EMPLOYEE, employee_id=emp1.id)
        db.add(employee_user)

        p_hrms = Project(code='HRMS', name='HRMS Modernization', manager_user_id=manager.id)
        p_x4s = Project(code='X4S', name='Xfer4Sure', manager_user_id=manager.id)
        p_sh = Project(code='SH', name='StatementHive', manager_user_id=manager.id)
        p_bank = Project(code='BANK', name='API Banking', manager_user_id=manager.id)
        db.add_all([p_hrms, p_x4s, p_sh, p_bank]); db.flush()

        today = date.today()
        db.add_all([
            Allocation(employee_id=emp1.id, project_id=p_hrms.id, start_date=today-timedelta(days=4), end_date=today+timedelta(days=10), allocation_percentage=50, status=AllocationStatus.ACTIVE, created_by_user_id=rm.id),
            Allocation(employee_id=emp1.id, project_id=p_x4s.id, start_date=today+timedelta(days=2), end_date=today+timedelta(days=12), allocation_percentage=40, status=AllocationStatus.PLANNED, created_by_user_id=rm.id),
            Allocation(employee_id=emp2.id, project_id=p_sh.id, start_date=today-timedelta(days=2), end_date=today+timedelta(days=15), allocation_percentage=70, status=AllocationStatus.ACTIVE, created_by_user_id=rm.id),
            Allocation(employee_id=emp3.id, project_id=p_bank.id, start_date=today-timedelta(days=1), end_date=today+timedelta(days=7), allocation_percentage=60, status=AllocationStatus.ACTIVE, created_by_user_id=rm.id),
        ])
        db.add_all([
            SystemConfig(key='max_concurrent_projects', value=str(settings.default_max_concurrent_projects)),
            SystemConfig(key='overallocation_policy', value='BLOCK'),
        ])
        db.flush(); recalculate_capacity_metrics(db, {emp1.id, emp2.id, emp3.id}, as_of=today); db.commit()
    finally:
        db.close()
