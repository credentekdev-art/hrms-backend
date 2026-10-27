from datetime import date, timedelta
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from app.core.database import Base
from app.jobs.auto_deallocate import run_auto_deallocation
from app.models.entities import (
    Allocation, AllocationStatus, Employee, FeedbackTask, Project, ResourceCapacity, Role, User
)


def test_auto_release_feedback_and_capacity_are_idempotent():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    manager = User(email="manager@test.com", full_name="Manager", password_hash="x", role=Role.MANAGER)
    rm = User(email="rm@test.com", full_name="RM", password_hash="x", role=Role.RM_TEAM)
    db.add_all([manager, rm]); db.flush()
    employee = Employee(employee_code="E1", name="Employee", manager_user_id=manager.id)
    db.add(employee); db.flush()
    project = Project(code="P1", name="Project 1", manager_user_id=manager.id)
    db.add(project); db.flush()

    today = date(2026, 10, 19)
    allocation = Allocation(
        employee_id=employee.id,
        project_id=project.id,
        start_date=today - timedelta(days=10),
        end_date=today - timedelta(days=1),
        allocation_percentage=50,
        status=AllocationStatus.ACTIVE,
        created_by_user_id=rm.id,
    )
    db.add(allocation); db.commit()

    first = run_auto_deallocation(db, as_of=today)
    assert first["released"] == 1
    assert first["feedback_tasks_created"] == 1
    assert first["capacity_metrics_recalculated"] == 1

    db.refresh(allocation)
    assert allocation.status == AllocationStatus.COMPLETED
    assert db.execute(select(FeedbackTask)).scalars().all().__len__() == 1
    capacity = db.get(ResourceCapacity, employee.id)
    assert capacity.allocated_percentage == 0
    assert capacity.available_percentage == 100

    second = run_auto_deallocation(db, as_of=today)
    assert second["released"] == 0
    assert second["feedback_tasks_created"] == 0
    assert db.execute(select(FeedbackTask)).scalars().all().__len__() == 1
