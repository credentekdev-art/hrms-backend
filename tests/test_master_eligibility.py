import sys, types
from datetime import date
security_stub=types.ModuleType('app.core.security');security_stub.decode_access_token=lambda token:{'sub':'0'};sys.modules.setdefault('app.core.security',security_stub)
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.api.masters import router as masters_router
from app.api.employees import router as employees_router
from app.api.requests import router as requests_router
from app.core.database import Base,get_db
from app.core.dependencies import get_current_user
from app.models.entities import Allocation,AllocationStatus,DesignationMaster,Employee,EmployeeSkill,Project,Role,SkillMaster,User
from app.services.capacity import build_overlap_allocation_stmt


def setup():
    engine=create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool);Base.metadata.create_all(engine);S=sessionmaker(bind=engine,expire_on_commit=False);db=S()
    admin=User(email='admin@x',full_name='Admin',password_hash='x',role=Role.ADMIN);manager=User(email='manager@x',full_name='Manager',password_hash='x',role=Role.MANAGER);rm=User(email='rm@x',full_name='RM',password_hash='x',role=Role.RM_TEAM);db.add_all([admin,manager,rm]);db.flush()
    des=DesignationMaster(name='Senior Engineer');other_des=DesignationMaster(name='QA Engineer');skill=SkillMaster(name='React');other_skill=SkillMaster(name='Python');db.add_all([des,other_des,skill,other_skill]);db.flush()
    p=Project(code='P1',name='Product',manager_user_id=manager.id);p2=Project(code='P2',name='Support',manager_user_id=manager.id);db.add_all([p,p2]);db.flush()
    employees=[]
    for code,name,did,sid,prof,exp in [('E1','Best Fit',des.id,skill.id,'Expert',8),('E2','Wrong Designation',other_des.id,skill.id,'Expert',9),('E3','Wrong Skill',des.id,other_skill.id,'Expert',9),('E4','No Capacity',des.id,skill.id,'Expert',7),('E5','Future Free',des.id,skill.id,'Intermediate',5)]:
        e=Employee(employee_code=code,name=name,designation_id=did,designation=des.name if did==des.id else other_des.name,skills=skill.name if sid==skill.id else other_skill.name,experience_years=exp,active=True);db.add(e);db.flush();db.add(EmployeeSkill(employee_id=e.id,skill_id=sid,proficiency=prof,years_of_experience=exp));employees.append(e)
    db.add(Allocation(employee_id=employees[3].id,project_id=p2.id,start_date=date(2026,11,1),end_date=date(2026,11,30),allocation_percentage=90,status=AllocationStatus.ACTIVE,created_by_user_id=rm.id))
    db.add(Allocation(employee_id=employees[4].id,project_id=p2.id,start_date=date(2026,10,1),end_date=date(2026,10,31),allocation_percentage=100,status=AllocationStatus.ACTIVE,created_by_user_id=rm.id));db.commit()
    current={'u':manager};app=FastAPI();app.include_router(requests_router,prefix='/api/v1');app.include_router(masters_router,prefix='/api/v1');app.include_router(employees_router,prefix='/api/v1');app.dependency_overrides[get_db]=lambda:db;app.dependency_overrides[get_current_user]=lambda:current['u'];return TestClient(app),db,current,admin,manager,rm,des,skill,p,employees


def test_eligibility_requires_designation_skill_and_available_overlap_capacity():
    c,db,current,admin,manager,rm,des,skill,p,employees=setup()
    r=c.get('/api/v1/requests/eligible-employees',params={'designation_id':des.id,'skill_id':skill.id,'project_id':p.id,'allocation_percentage':20,'start_date':'2026-11-01','end_date':'2026-11-30'})
    assert r.status_code==200,r.text
    names=[x['name'] for x in r.json()['items']]
    assert names==['Best Fit','Future Free']
    assert r.json()['items'][0]['available_allocation_percentage']==100
    assert 'Wrong Designation' not in names and 'Wrong Skill' not in names and 'No Capacity' not in names


def test_non_overlapping_allocation_does_not_reduce_future_availability():
    c,db,current,admin,manager,rm,des,skill,p,employees=setup()
    r=c.get('/api/v1/requests/eligible-employees',params={'designation_id':des.id,'skill_id':skill.id,'project_id':p.id,'allocation_percentage':100,'start_date':'2026-11-01','end_date':'2026-11-30'})
    assert r.status_code==200
    item=next(x for x in r.json()['items'] if x['name']=='Future Free')
    assert item['available_allocation_percentage']==100


def test_master_duplicate_is_case_insensitive():
    c,db,current,admin,*_=setup();current['u']=admin
    r=c.post('/api/v1/masters/designations',json={'name':' senior engineer '})
    assert r.status_code==409
    r=c.post('/api/v1/masters/skills',json={'name':'REACT'})
    assert r.status_code==409


def test_capacity_guard_locks_overlapping_allocation_rows_for_postgres():
    stmt=build_overlap_allocation_stmt(1,date(2026,11,1),date(2026,11,30),lock=True)
    sql=str(stmt.compile(dialect=postgresql.dialect(),compile_kwargs={'literal_binds':True})).upper()
    assert 'FOR UPDATE' in sql


def test_master_management_is_denied_to_manager_and_allowed_to_hr():
    c,db,current,admin,manager,rm,des,skill,p,employees=setup()
    current['u']=manager
    denied=c.post('/api/v1/masters/designations',json={'name':'Architect'})
    assert denied.status_code==403
    hr=User(email='hr@x',full_name='HR',password_hash='x',role=Role.HR);db.add(hr);db.commit();current['u']=hr
    allowed=c.post('/api/v1/masters/designations',json={'name':'Architect'})
    assert allowed.status_code==200,allowed.text


def test_request_create_revalidates_selected_employee_master_fit_and_capacity():
    c,db,current,admin,manager,rm,des,skill,p,employees=setup();current['u']=manager
    base={'project_id':p.id,'designation_id':des.id,'skill_id':skill.id,'number_of_resources':1,'allocation_percentage':20,'start_date':'2026-11-01','end_date':'2026-11-30','priority':'HIGH','remark':'Need delivery support'}
    wrong=c.post('/api/v1/requests',json={**base,'requested_employee_ids':[employees[1].id]})
    assert wrong.status_code==422
    no_capacity=c.post('/api/v1/requests',json={**base,'requested_employee_ids':[employees[3].id]})
    assert no_capacity.status_code==409
    ok=c.post('/api/v1/requests',json={**base,'requested_employee_ids':[employees[0].id]})
    assert ok.status_code==200,ok.text


def test_employee_get_returns_master_designation_and_skill_details():
    c,db,current,admin,manager,rm,des,skill,p,employees=setup();current['u']=admin
    r=c.get(f'/api/v1/employees/{employees[0].id}')
    assert r.status_code==200,r.text
    body=r.json();assert body['designation']=={'id':des.id,'name':'Senior Engineer'}
    assert body['skills'][0]['name']=='React';assert body['skills'][0]['proficiency']=='Expert'


def test_employee_update_skills_add_change_clear_and_omit_regression():
    c,db,current,admin,manager,rm,des,skill,p,employees=setup();current['u']=admin
    employee=employees[0]
    python_skill=db.execute(select(SkillMaster).where(SkillMaster.name=='Python')).scalar_one()

    # Change the existing skill metadata and add a second skill. This used to
    # crash because model_dump() converted skill_items to dicts.
    r=c.patch(f'/api/v1/employees/{employee.id}',json={'skill_items':[{'skill_id':skill.id,'proficiency':'Intermediate','years_of_experience':3.5},{'skill_id':python_skill.id,'proficiency':'Beginner','years_of_experience':1.0}]})
    assert r.status_code==200,r.text
    assert {x['id'] for x in r.json()['skills']}=={skill.id,python_skill.id}

    # Empty list explicitly clears skills.
    r=c.patch(f'/api/v1/employees/{employee.id}',json={'skill_items':[]})
    assert r.status_code==200,r.text
    assert r.json()['skills']==[]

    # Add one back, then omit skill_items: omission must leave it unchanged.
    r=c.patch(f'/api/v1/employees/{employee.id}',json={'skill_items':[{'skill_id':python_skill.id,'proficiency':'Expert','years_of_experience':2.0}]})
    assert r.status_code==200,r.text
    r=c.patch(f'/api/v1/employees/{employee.id}',json={'department':'Engineering'})
    assert r.status_code==200,r.text
    assert [x['id'] for x in r.json()['skills']]==[python_skill.id]


def test_employee_update_rejects_duplicate_missing_and_inactive_master_ids():
    c,db,current,admin,manager,rm,des,skill,p,employees=setup();current['u']=admin
    employee=employees[0]
    duplicate=c.patch(f'/api/v1/employees/{employee.id}',json={'skill_items':[{'skill_id':skill.id},{'skill_id':skill.id}]})
    assert duplicate.status_code==422
    assert duplicate.json()['detail'][0]['loc'][-1]=='skill_items'
    missing=c.patch(f'/api/v1/employees/{employee.id}',json={'skill_items':[{'skill_id':99999}]})
    assert missing.status_code==422
    other=db.execute(select(SkillMaster).where(SkillMaster.name=='Python')).scalar_one();other.is_active=False;db.commit()
    inactive=c.patch(f'/api/v1/employees/{employee.id}',json={'skill_items':[{'skill_id':other.id}]})
    assert inactive.status_code==422
    des.is_active=False;db.commit()
    bad_designation=c.patch(f'/api/v1/employees/{employee.id}',json={'designation_id':des.id})
    assert bad_designation.status_code==422
    assert bad_designation.json()['detail'][0]['loc'][-1]=='designation_id'


def test_eligible_employee_search_progressively_applies_filters():
    c,db,current,admin,manager,rm,des,skill,p,employees=setup();current['u']=manager
    # No designation/skill/date criteria yet: active employees are still available
    # for progressive selection.
    r=c.get('/api/v1/requests/eligible-employees',params={'project_id':p.id,'limit':20})
    assert r.status_code==200,r.text
    assert len(r.json()['items'])>=4
    # Adding designation narrows, then skill narrows further.
    r2=c.get('/api/v1/requests/eligible-employees',params={'project_id':p.id,'designation_id':des.id,'limit':20})
    assert r2.status_code==200
    assert all(x['designation_id']==des.id for x in r2.json()['items'])
    r3=c.get('/api/v1/requests/eligible-employees',params={'project_id':p.id,'designation_id':des.id,'skill_id':skill.id,'allocation_percentage':20,'start_date':'2026-11-01','end_date':'2026-11-30'})
    assert r3.status_code==200
    names={x['name'] for x in r3.json()['items']}
    assert 'Wrong Skill' not in names and 'No Capacity' not in names
