import sys,types
from datetime import date
security_stub=types.ModuleType('app.core.security');security_stub.decode_access_token=lambda token:{'sub':'0'};sys.modules.setdefault('app.core.security',security_stub)
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.api.meta import router as meta_router
from app.api.allocations import router as allocation_router
from app.core.database import Base,get_db
from app.core.dependencies import get_current_user
from app.models.entities import Allocation,AllocationStatus,Employee,Project,ResourceRequest,ResourceRequestSelection,Role,User

def setup():
 e=create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool);Base.metadata.create_all(e);S=sessionmaker(bind=e,expire_on_commit=False);db=S();m1=User(email='m1@x',full_name='M1',password_hash='x',role=Role.MANAGER);m2=User(email='m2@x',full_name='M2',password_hash='x',role=Role.MANAGER);rm=User(email='rm@x',full_name='RM',password_hash='x',role=Role.RM_TEAM);admin=User(email='a@x',full_name='Admin',password_hash='x',role=Role.ADMIN);db.add_all([m1,m2,rm,admin]);db.flush();e1=Employee(employee_code='E1',name='Owned Allocation');e2=Employee(employee_code='E2',name='Own Request Link');e3=Employee(employee_code='E3',name='Hidden');db.add_all([e1,e2,e3]);db.flush();p1=Project(code='P1',name='P1',manager_user_id=m1.id);p2=Project(code='P2',name='P2',manager_user_id=m2.id);db.add_all([p1,p2]);db.flush();db.add(Allocation(employee_id=e1.id,project_id=p1.id,start_date=date(2026,1,1),end_date=date(2026,12,31),allocation_percentage=50,status=AllocationStatus.ACTIVE,created_by_user_id=rm.id));req=ResourceRequest(project_id=p2.id,requested_by_user_id=m1.id,start_date=date(2026,11,1),end_date=date(2026,11,30),allocation_percentage=20,number_of_resources=1,priority='MEDIUM',reason='Need',status='SUBMITTED');db.add(req);db.flush();db.add(ResourceRequestSelection(request_id=req.id,employee_id=e2.id));db.commit();cur={'u':m1};app=FastAPI();app.include_router(meta_router,prefix='/api/v1');app.include_router(allocation_router,prefix='/api/v1');app.dependency_overrides[get_db]=lambda:db;app.dependency_overrides[get_current_user]=lambda:cur['u'];return TestClient(app),cur,(m1,m2,rm,admin),(e1,e2,e3)

def test_manager_sees_only_owned_project_or_own_request_employees():
 c,cur,users,emps=setup();ids={x['id'] for x in c.get('/api/v1/employees').json()};assert ids=={emps[0].id,emps[1].id};assert c.get(f'/api/v1/employees/{emps[2].id}').status_code==404

def test_rm_and_admin_have_full_employee_visibility():
 c,cur,users,emps=setup();
 for u in (users[2],users[3]):cur['u']=u;assert len(c.get('/api/v1/employees').json())==3

def test_employee_self_only():
 c,cur,users,emps=setup();employee_user=User(email='e@x',full_name='E',password_hash='x',role=Role.EMPLOYEE,employee_id=emps[2].id);db=c.app.dependency_overrides[get_db]();db.add(employee_user);db.commit();cur['u']=employee_user;assert [x['id'] for x in c.get('/api/v1/employees').json()]==[emps[2].id];assert c.get(f'/api/v1/employees/{emps[0].id}').status_code==404
