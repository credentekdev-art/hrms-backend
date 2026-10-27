import sys,types
from datetime import date
security_stub=types.ModuleType('app.core.security');security_stub.decode_access_token=lambda token:{'sub':'0'};sys.modules.setdefault('app.core.security',security_stub)
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine,select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.api.feedback import router
from app.core.database import Base,get_db
from app.core.dependencies import get_current_user
from app.models.entities import Allocation,AllocationStatus,Employee,FeedbackEditHistory,Project,Role,User

def setup():
 e=create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool);Base.metadata.create_all(e);S=sessionmaker(bind=e,expire_on_commit=False);db=S();m=User(email='m@x',full_name='Manager',password_hash='x',role=Role.MANAGER);other=User(email='o@x',full_name='Other',password_hash='x',role=Role.MANAGER);db.add_all([m,other]);db.flush();emp=Employee(employee_code='E1',name='Employee');db.add(emp);db.flush();p=Project(code='P1',name='P1',manager_user_id=m.id);db.add(p);db.flush();a=Allocation(employee_id=emp.id,project_id=p.id,start_date=date(2026,1,1),end_date=date(2026,12,31),allocation_percentage=50,status=AllocationStatus.ACTIVE,created_by_user_id=m.id);db.add(a);db.commit();cur={'u':m};app=FastAPI();app.include_router(router,prefix='/api/v1');app.dependency_overrides[get_db]=lambda:db;app.dependency_overrides[get_current_user]=lambda:cur['u'];return TestClient(app),db,m,other,a,cur

def test_feedback_create_then_author_edit_keeps_previous_value():
 c,db,m,_,a,_=setup();r=c.post('/api/v1/feedback',json={'allocation_id':a.id,'rating':4,'feedback':'Strong contribution'});assert r.status_code==200,r.text;tid=r.json()['id'];r=c.patch(f'/api/v1/feedback/{tid}',json={'rating':5,'feedback':'Outstanding contribution'});assert r.status_code==200,r.text;assert r.json()['edited_at'];h=db.execute(select(FeedbackEditHistory).where(FeedbackEditHistory.feedback_task_id==tid)).scalar_one();assert h.previous_rating==4;assert h.previous_feedback=='Strong contribution'

def test_other_manager_cannot_edit_feedback():
 c,_,_,other,a,cur=setup();r=c.post('/api/v1/feedback',json={'allocation_id':a.id,'rating':4,'feedback':'Strong contribution'});tid=r.json()['id'];cur['u']=other;r=c.patch(f'/api/v1/feedback/{tid}',json={'rating':3,'feedback':'Changed by other'});assert r.status_code in (403,404)
