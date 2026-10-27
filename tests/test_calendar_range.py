import sys,types
from datetime import date
security_stub=types.ModuleType('app.core.security');security_stub.decode_access_token=lambda token:{'sub':'0'};sys.modules.setdefault('app.core.security',security_stub)
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.api.calendar import router
from app.core.database import Base,get_db
from app.core.dependencies import get_current_user
from app.models.entities import Allocation,AllocationStatus,Employee,Project,Role,User


def setup():
 e=create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool);Base.metadata.create_all(e);S=sessionmaker(bind=e,expire_on_commit=False);db=S();m1=User(email='m1@x',full_name='M1',password_hash='x',role=Role.MANAGER);m2=User(email='m2@x',full_name='M2',password_hash='x',role=Role.MANAGER);db.add_all([m1,m2]);db.flush();e1=Employee(employee_code='E1',name='Visible');e2=Employee(employee_code='E2',name='Hidden');db.add_all([e1,e2]);db.flush();p1=Project(code='P1',name='Owned',manager_user_id=m1.id);p2=Project(code='P2',name='Other',manager_user_id=m2.id);db.add_all([p1,p2]);db.flush();db.add_all([Allocation(employee_id=e1.id,project_id=p1.id,start_date=date(2026,10,1),end_date=date(2026,10,31),allocation_percentage=50,status=AllocationStatus.ACTIVE,created_by_user_id=m1.id),Allocation(employee_id=e2.id,project_id=p2.id,start_date=date(2026,10,1),end_date=date(2026,10,31),allocation_percentage=30,status=AllocationStatus.ACTIVE,created_by_user_id=m2.id)]);db.commit();cur={'u':m1};app=FastAPI();app.include_router(router,prefix='/api/v1');app.dependency_overrides[get_db]=lambda:db;app.dependency_overrides[get_current_user]=lambda:cur['u'];return TestClient(app)

def test_calendar_events_are_range_paginated_and_manager_scoped():
 c=setup();r=c.get('/api/v1/calendar/events',params={'start':'2026-10-01','end':'2026-10-31','limit':1,'offset':0});assert r.status_code==200,r.text;body=r.json();assert body['total']==1;assert len(body['items'])==1;assert body['items'][0]['employee_name']=='Visible'

def test_month_summary_returns_daily_counts_with_three_item_preview_contract():
 c=setup();r=c.get('/api/v1/calendar/summary',params={'start':'2026-10-01','end':'2026-10-07'});assert r.status_code==200,r.text;body=r.json();assert len(body['days'])==7;assert all(d['count']==1 for d in body['days']);assert all(len(d['preview'])<=3 for d in body['days'])

def test_calendar_facets_and_event_detail_stay_within_visible_range():
 c=setup()
 facets=c.get('/api/v1/calendar/facets',params={'start':'2026-10-01','end':'2026-10-31'});assert facets.status_code==200,facets.text;body=facets.json();assert body['employees']==[{'id':1,'label':'Visible','count':1}];assert body['projects'][0]['label']=='Owned'
 events=c.get('/api/v1/calendar/events',params={'start':'2026-10-01','end':'2026-10-31'}).json()['items'];eid=events[0]['id']
 detail=c.get(f'/api/v1/calendar/events/{eid}');assert detail.status_code==200,detail.text;assert detail.json()['employee_name']=='Visible';assert detail.json()['project_name']=='Owned'

def test_calendar_does_not_return_events_outside_requested_range():
 c=setup();r=c.get('/api/v1/calendar/events',params={'start':'2026-11-01','end':'2026-11-30'});assert r.status_code==200,r.text;assert r.json()['total']==0


def test_calendar_aggregate_includes_count_status_breakdown_and_heat():
 c=setup();r=c.get('/api/v1/calendar/aggregate',params={'start':'2026-10-01','end':'2026-10-07'});assert r.status_code==200,r.text;body=r.json();assert body['peak_count']==1;assert all(day['status_counts']['ACTIVE']==1 for day in body['days']);assert all(day['heat']==1 for day in body['days']);assert all(len(day['preview'])<=2 for day in body['days'])


def test_day_detail_handles_500_rows_without_n_plus_one_shape():
 import time
 e=create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool);Base.metadata.create_all(e);S=sessionmaker(bind=e,expire_on_commit=False);db=S();manager=User(email='bulk@x',full_name='Bulk Manager',password_hash='x',role=Role.MANAGER);db.add(manager);db.flush();p=Project(code='BULK',name='Bulk Project',manager_user_id=manager.id);db.add(p);db.flush()
 employees=[Employee(employee_code=f'B{i:04d}',name=f'Employee {i:04d}',department='Delivery') for i in range(500)];db.add_all(employees);db.flush();db.add_all([Allocation(employee_id=emp.id,project_id=p.id,start_date=date(2026,10,23),end_date=date(2026,10,23),allocation_percentage=25,status=AllocationStatus.ACTIVE,created_by_user_id=manager.id) for emp in employees]);db.commit();cur={'u':manager};app=FastAPI();app.include_router(router,prefix='/api/v1');app.dependency_overrides[get_db]=lambda:db;app.dependency_overrides[get_current_user]=lambda:cur['u'];client=TestClient(app)
 started=time.perf_counter();r=client.get('/api/v1/calendar/day-details',params={'day':'2026-10-23','limit':500,'offset':0,'group_by':'project'});elapsed=time.perf_counter()-started
 assert r.status_code==200,r.text;body=r.json();assert body['total']==500;assert len(body['items'])==500;assert elapsed<0.30, f'day panel backend took {elapsed:.3f}s'


def test_resource_timeline_is_employee_paginated_and_range_scoped():
 c=setup();r=c.get('/api/v1/calendar/resource-timeline',params={'start':'2026-10-01','end':'2026-10-31','limit':10,'offset':0});assert r.status_code==200,r.text;body=r.json();assert body['total']==1;assert body['rows'][0]['employee_name']=='Visible';assert body['rows'][0]['allocations'][0]['project_name']=='Owned'
