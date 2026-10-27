from datetime import date
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.core.database import Base
from app.models.entities import Allocation, AllocationStatus, Employee, Project, Role, SystemConfig, User
from app.services.capacity import validate_capacity

def db_session():
    e=create_engine('sqlite:///:memory:');Base.metadata.create_all(e);return sessionmaker(bind=e)()
def seed(db):
    m=User(email='m@test.com',full_name='Manager',password_hash='x',role=Role.MANAGER);db.add(m);db.flush();emp=Employee(employee_code='E1',name='Sachin',manager_user_id=m.id);db.add(emp);db.flush();ps=[Project(code=f'P{i}',name=f'P{i}',manager_user_id=m.id) for i in range(1,6)];db.add_all(ps);db.add(SystemConfig(key='max_concurrent_projects',value='4'));db.commit();return emp,ps
def add(db,e,p,pct,start=date(2026,10,1),end=date(2026,10,31)):
    db.add(Allocation(employee_id=e.id,project_id=p.id,start_date=start,end_date=end,allocation_percentage=pct,status=AllocationStatus.ACTIVE,created_by_user_id=1));db.commit()
def check(db,e,p,pct,start=date(2026,10,10),end=date(2026,10,12)):
    return validate_capacity(db,employee_id=e.id,project_id=p.id,start_date=start,end_date=end,allocation_percentage=pct)

def test_70_15_15_passes():
    db=db_session();e,p=seed(db);add(db,e,p[0],70);add(db,e,p[1],15);r=check(db,e,p[2],15);assert r.valid;assert r.peak_percentage==100

def test_70_15_20_fails_with_available_message():
    db=db_session();e,p=seed(db);add(db,e,p[0],70);add(db,e,p[1],15);r=check(db,e,p[2],20);assert not r.valid;assert 'Sachin has 85% allocated. Only 15% available.' in r.conflicts[0].reason

def test_duplicate_same_project_overlap_fails():
    db=db_session();e,p=seed(db);add(db,e,p[0],40);r=check(db,e,p[0],20);assert not r.valid;assert any(c.code=='DUPLICATE_PROJECT_ALLOCATION' for c in r.conflicts)

def test_employee_at_100_fails():
    db=db_session();e,p=seed(db);add(db,e,p[0],70);add(db,e,p[1],30);r=check(db,e,p[2],1);assert not r.valid;assert r.available_capacity==0

def test_non_overlapping_period_passes():
    db=db_session();e,p=seed(db);add(db,e,p[0],100,date(2026,10,1),date(2026,10,15));r=check(db,e,p[1],100,date(2026,10,16),date(2026,10,31));assert r.valid
