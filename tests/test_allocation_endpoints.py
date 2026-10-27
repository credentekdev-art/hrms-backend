from datetime import date, timedelta
import sys
import types

# The endpoint tests override authentication; avoid requiring optional crypto packages in the test runner.
security_stub = types.ModuleType("app.core.security")
security_stub.decode_access_token = lambda token: {"sub": "0"}
sys.modules.setdefault("app.core.security", security_stub)

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.allocations import router as allocations_router
from app.core.database import Base, get_db
from app.core.dependencies import get_current_user
from app.models.entities import Allocation, AllocationStatus, AuditLog, Employee, FeedbackTask, Project, ResourceCapacity, Role, User


def setup_api():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = Session()
    manager = User(email="manager@test.com", full_name="Manager", password_hash="x", role=Role.MANAGER)
    other = User(email="other@test.com", full_name="Other", password_hash="x", role=Role.MANAGER)
    db.add_all([manager, other]); db.flush()
    employee = Employee(employee_code="E100", name="Resource One", manager_user_id=manager.id, skills="Python, React")
    db.add(employee); db.flush()
    project = Project(code="HRMS", name="HRMS", manager_user_id=manager.id)
    db.add(project); db.flush()
    allocation = Allocation(
        employee_id=employee.id, project_id=project.id,
        start_date=date.today() - timedelta(days=3), end_date=date.today() + timedelta(days=30),
        allocation_percentage=60, status=AllocationStatus.ACTIVE, created_by_user_id=manager.id,
    )
    db.add(allocation); db.commit()

    current = {"user": manager}
    app = FastAPI()
    app.include_router(allocations_router, prefix="/api/v1")
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: current["user"]
    return TestClient(app), db, manager, other, employee, allocation, current


def test_manager_can_edit_allocation_and_audit_is_recorded():
    client, db, manager, _, employee, allocation, _ = setup_api()
    new_end = date.today() + timedelta(days=20)
    response = client.patch(f"/api/v1/allocations/{allocation.id}", json={
        "allocation_percentage": 40,
        "end_date": new_end.isoformat(),
    })
    assert response.status_code == 200, response.text
    assert response.json()["allocation_percentage"] == 40
    assert response.json()["end_date"] == new_end.isoformat()
    db.refresh(allocation)
    assert allocation.allocation_percentage == 40
    assert db.execute(select(AuditLog).where(AuditLog.action == "EDIT_ALLOCATION")).scalar_one().actor_user_id == manager.id
    capacity = db.get(ResourceCapacity, employee.id)
    assert capacity.allocated_percentage == 40
    assert capacity.available_percentage == 60


def test_manager_can_release_now_without_hard_delete_and_feedback_is_created():
    client, db, manager, _, employee, allocation, _ = setup_api()
    response = client.patch(f"/api/v1/allocations/{allocation.id}/release", json={
        "release_date": date.today().isoformat(),
        "reason": "Project work completed",
    })
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "RELEASED_EARLY"
    db.refresh(allocation)
    assert db.get(Allocation, allocation.id) is not None
    assert allocation.status == AllocationStatus.RELEASED_EARLY
    assert allocation.release_reason == "Project work completed"
    assert db.execute(select(FeedbackTask).where(FeedbackTask.allocation_id == allocation.id)).scalar_one_or_none() is not None
    capacity = db.get(ResourceCapacity, employee.id)
    assert capacity.allocated_percentage == 0
    assert capacity.available_percentage == 100
    log = db.execute(select(AuditLog).where(AuditLog.action == "RELEASE_ALLOCATION")).scalar_one()
    assert log.actor_user_id == manager.id


def test_future_release_frees_capacity_from_release_date_and_keeps_active_history():
    client, db, _, _, _, allocation, _ = setup_api()
    release_date = date.today() + timedelta(days=7)
    response = client.patch(f"/api/v1/allocations/{allocation.id}/release", json={
        "release_date": release_date.isoformat(),
        "reason": "Planned project handover",
    })
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "ACTIVE"
    assert response.json()["scheduled_release_date"] == release_date.isoformat()
    db.refresh(allocation)
    assert allocation.status == AllocationStatus.ACTIVE
    assert allocation.scheduled_release_date == release_date


def test_manager_cannot_change_another_managers_allocation():
    client, _, _, other, _, allocation, current = setup_api()
    current["user"] = other
    response = client.patch(f"/api/v1/allocations/{allocation.id}", json={"allocation_percentage": 50})
    assert response.status_code == 403
