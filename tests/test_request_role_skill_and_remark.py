import sys, types
from datetime import date
security_stub=types.ModuleType('app.core.security');security_stub.decode_access_token=lambda token:{'sub':'0'};sys.modules.setdefault('app.core.security',security_stub)
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.api.requests import router
from app.core.database import Base,get_db
from app.core.dependencies import get_current_user
from app.models.entities import Allocation,AllocationStatus,AuditLog,DesignationMaster,Employee,EmployeeSkill,FeedbackStatus,FeedbackTask,Project,RequestStatus,ResourceRequest,Role,SkillMaster,User


def setup():
    engine=create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool);Base.metadata.create_all(engine);S=sessionmaker(bind=engine,expire_on_commit=False);db=S()
    manager=User(email='manager@x',full_name='Manager',password_hash='x',role=Role.MANAGER);rm=User(email='rm@x',full_name='RM',password_hash='x',role=Role.RM_TEAM);db.add_all([manager,rm]);db.flush()
    d1=DesignationMaster(name='Senior Engineer');d2=DesignationMaster(name='Support Engineer');s1=SkillMaster(name='React');s2=SkillMaster(name='Python');s3=SkillMaster(name='Java');db.add_all([d1,d2,s1,s2,s3]);db.flush()
    e1=Employee(employee_code='E1',name='Nikita',designation='Senior Engineer',designation_id=d1.id,skills='React, Python, FastAPI',experience_years=5.2);e2=Employee(employee_code='E2',name='Mayur',designation='Support Engineer',designation_id=d2.id,skills='Java, SQL, Support',experience_years=4.0);db.add_all([e1,e2]);db.flush();db.add_all([EmployeeSkill(employee_id=e1.id,skill_id=s1.id,proficiency='Expert',years_of_experience=4.0),EmployeeSkill(employee_id=e1.id,skill_id=s2.id,proficiency='Intermediate',years_of_experience=3.0),EmployeeSkill(employee_id=e2.id,skill_id=s3.id,proficiency='Expert',years_of_experience=4.0)])
    p=Project(code='HRMS',name='HRMS',manager_user_id=manager.id);db.add(p);db.flush()
    a=Allocation(employee_id=e1.id,project_id=p.id,start_date=date(2026,1,1),end_date=date(2026,12,31),allocation_percentage=20,status=AllocationStatus.ACTIVE,created_by_user_id=rm.id);db.add(a);db.flush()
    db.add(FeedbackTask(allocation_id=a.id,manager_user_id=manager.id,employee_id=e1.id,due_date=date(2026,1,31),status=FeedbackStatus.COMPLETED,rating=4,feedback='Strong React delivery and ownership.'))
    req=ResourceRequest(project_id=p.id,requested_by_user_id=manager.id,designation_id=d1.id,skill_id=s1.id,role_required='Senior Engineer',skills_required='React',start_date=date(2026,11,1),end_date=date(2026,11,30),allocation_percentage=30,number_of_resources=1,priority='HIGH',reason='Need frontend capacity',status=RequestStatus.UNDER_REVIEW);db.add(req);db.commit()
    current={'u':rm};app=FastAPI();app.include_router(router,prefix='/api/v1');app.dependency_overrides[get_db]=lambda:db;app.dependency_overrides[get_current_user]=lambda:current['u'];return TestClient(app),db,current,manager,rm,e1,e2,p,req


def test_role_and_skill_search_are_limited_and_paginated():
    c,*_=setup();r=c.get('/api/v1/requests/search/roles',params={'q':'Eng','limit':1,'offset':0});assert r.status_code==200,r.text;assert len(r.json()['items'])==1;assert r.json()['limit']==1
    r=c.get('/api/v1/requests/search/skills',params={'q':'Re','limit':5,'offset':0});assert r.status_code==200,r.text;assert 'React' in r.json()['items']


def test_employee_suggestion_returns_experience_and_latest_feedback_in_one_select():
    c,db,*_=setup(); counts={'select':0}
    @event.listens_for(db.get_bind(),'before_cursor_execute')
    def count_select(conn,cursor,statement,parameters,context,executemany):
        if statement.lstrip().upper().startswith('SELECT'): counts['select']+=1
    r=c.get('/api/v1/requests/suggestions/employees',params={'role':'Senior','skill':'React','limit':10,'offset':0});assert r.status_code==200,r.text;item=r.json()['items'][0];assert item['name']=='Nikita';assert item['experience']==5.2;assert item['last_feedback_rating']==4;assert 'ownership' in item['last_feedback_description'];assert counts['select']==1


def test_create_accepts_restored_role_skill_and_legacy_reason_alias():
    c,db,current,manager,_,*rest=setup();current['u']=manager;p=rest[-2]
    r=c.post('/api/v1/requests',json={'project_id':p.id,'role_required':'Senior Engineer','skills_required':'Python','number_of_resources':1,'requested_employee_ids':[rest[0].id],'allocation_percentage':25,'start_date':'2027-01-01','end_date':'2027-01-31','priority':'MEDIUM','reason':'Legacy clients still work'})
    assert r.status_code==200,r.text;body=r.json();assert body['role_required']=='Senior Engineer';assert body['skills_required']=='Python';assert body['remark']=='Legacy clients still work'


def test_approve_and_reject_require_remark_in_pydantic_contract():
    c,*rest=setup();req=rest[-1]
    r=c.post(f'/api/v1/requests/{req.id}/decision',json={'decision':'reject','employee_ids':[]});assert r.status_code==422
    r=c.post(f'/api/v1/requests/{req.id}/decision',json={'decision':'approve','employee_ids':[rest[4].id]});assert r.status_code==422


def test_reject_remark_is_audited_with_actor_and_timestamp():
    c,db,current,manager,rm,e1,e2,p,req=setup();r=c.post(f'/api/v1/requests/{req.id}/decision',json={'decision':'reject','remark':'Skill mix changed','employee_ids':[]});assert r.status_code==200,r.text;log=db.execute(select(AuditLog).where(AuditLog.action=='REJECT_RESOURCE_REQUEST')).scalar_one();assert log.actor_user_id==rm.id;assert 'Skill mix changed' in log.details;assert log.created_at is not None

def test_designation_search_alias_returns_active_master_designations():
    c,*_=setup()
    r=c.get('/api/v1/requests/search/designations',params={'q':'Eng','limit':10,'offset':0})
    assert r.status_code==200,r.text
    assert r.json()['items']==['Senior Engineer','Support Engineer']
