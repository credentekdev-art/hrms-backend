import enum
from datetime import date, datetime
from sqlalchemy import Boolean, CheckConstraint, Date, DateTime, Enum, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


class Role(str, enum.Enum):
    MANAGER = 'MANAGER'
    RM_TEAM = 'RM_TEAM'
    EMPLOYEE = 'EMPLOYEE'
    ADMIN = 'ADMIN'
    HR = 'HR'


class RequestStatus(str, enum.Enum):
    DRAFT = 'DRAFT'
    SUBMITTED = 'SUBMITTED'
    UNDER_REVIEW = 'UNDER_REVIEW'
    APPROVED = 'APPROVED'
    REJECTED = 'REJECTED'
    FULFILLED = 'FULFILLED'
    CLOSED = 'CLOSED'
    CANCELLED = 'CANCELLED'


class AllocationStatus(str, enum.Enum):
    PLANNED = 'PLANNED'
    ACTIVE = 'ACTIVE'
    COMPLETED = 'COMPLETED'
    RELEASED_EARLY = 'RELEASED_EARLY'
    RELEASED = 'RELEASED'
    CANCELLED = 'CANCELLED'


class FeedbackStatus(str, enum.Enum):
    PENDING = 'PENDING'
    COMPLETED = 'COMPLETED'


class DesignationMaster(Base):
    __tablename__ = 'designation_master'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class SkillMaster(Base):
    __tablename__ = 'skill_master'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class User(Base):
    __tablename__ = 'users'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(255))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[Role] = mapped_column(Enum(Role), index=True)
    employee_id: Mapped[int | None] = mapped_column(ForeignKey('employees.id'), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Employee(Base):
    __tablename__ = 'employees'
    __table_args__ = (Index('ix_employees_designation_id', 'designation_id'),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    employee_code: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255), index=True)
    # Legacy text columns are intentionally retained during the normalized-master transition.
    designation: Mapped[str] = mapped_column(String(255), default='', index=True)
    designation_id: Mapped[int | None] = mapped_column(ForeignKey('designation_master.id'), nullable=True)
    department: Mapped[str] = mapped_column(String(255), default='')
    skills: Mapped[str] = mapped_column(Text, default='')
    experience_years: Mapped[float | None] = mapped_column(Float, nullable=True)
    joining_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    manager_user_id: Mapped[int | None] = mapped_column(ForeignKey('users.id'), nullable=True, index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)


class EmployeeSkill(Base):
    __tablename__ = 'employee_skill'
    __table_args__ = (
        UniqueConstraint('employee_id', 'skill_id', name='uq_employee_skill_employee_skill'),
        CheckConstraint("proficiency IN ('Beginner','Intermediate','Expert')", name='ck_employee_skill_proficiency'),
        CheckConstraint('years_of_experience IS NULL OR years_of_experience >= 0', name='ck_employee_skill_years'),
        Index('ix_employee_skill_skill_employee', 'skill_id', 'employee_id'),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey('employees.id'), index=True)
    skill_id: Mapped[int] = mapped_column(ForeignKey('skill_master.id'), index=True)
    proficiency: Mapped[str] = mapped_column(String(20), default='Intermediate')
    years_of_experience: Mapped[float | None] = mapped_column(Float, nullable=True)


class Project(Base):
    __tablename__ = 'projects'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255), index=True)
    manager_user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class ResourceRequest(Base):
    __tablename__ = 'resource_requests'
    __table_args__ = (
        CheckConstraint('end_date >= start_date', name='ck_request_date_range'),
        CheckConstraint('allocation_percentage > 0 AND allocation_percentage <= 100', name='ck_request_percentage'),
        CheckConstraint('number_of_resources >= 1', name='ck_request_resource_count'),
        Index('ix_resource_requests_designation_id', 'designation_id'),
        Index('ix_resource_requests_skill_id', 'skill_id'),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey('projects.id'), index=True)
    requested_employee_id: Mapped[int | None] = mapped_column(ForeignKey('employees.id'), nullable=True, index=True)
    requested_by_user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    designation_id: Mapped[int | None] = mapped_column(ForeignKey('designation_master.id'), nullable=True)
    skill_id: Mapped[int | None] = mapped_column(ForeignKey('skill_master.id'), nullable=True)
    # Legacy columns are dual-written for compatibility and downgrade safety.
    role_required: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    skills_required: Mapped[str | None] = mapped_column(Text, nullable=True)
    start_date: Mapped[date] = mapped_column(Date, index=True)
    end_date: Mapped[date] = mapped_column(Date, index=True)
    allocation_percentage: Mapped[float] = mapped_column(Float)
    number_of_resources: Mapped[int] = mapped_column(Integer, default=1)
    priority: Mapped[str] = mapped_column(String(20), default='MEDIUM', index=True)
    reason: Mapped[str] = mapped_column(Text, default='')
    status: Mapped[RequestStatus] = mapped_column(Enum(RequestStatus), default=RequestStatus.SUBMITTED, index=True)
    reviewed_by_user_id: Mapped[int | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ResourceRequestSelection(Base):
    __tablename__ = 'resource_request_selections'
    __table_args__ = (UniqueConstraint('request_id', 'employee_id', name='uq_request_employee_selection'),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    request_id: Mapped[int] = mapped_column(ForeignKey('resource_requests.id'), index=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey('employees.id'), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Allocation(Base):
    __tablename__ = 'allocations'
    __table_args__ = (
        UniqueConstraint('resource_request_id', 'employee_id', name='uq_allocation_request_employee'),
        CheckConstraint('end_date >= start_date', name='ck_allocation_date_range'),
        CheckConstraint('allocation_percentage > 0 AND allocation_percentage <= 100', name='ck_allocation_percentage'),
        Index('ix_allocations_range', 'start_date', 'end_date', 'status'),
        Index('ix_allocations_employee_range', 'employee_id', 'start_date', 'end_date'),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey('employees.id'), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey('projects.id'), index=True)
    resource_request_id: Mapped[int | None] = mapped_column(ForeignKey('resource_requests.id'), nullable=True)
    start_date: Mapped[date] = mapped_column(Date, index=True)
    end_date: Mapped[date] = mapped_column(Date, index=True)
    allocation_percentage: Mapped[float] = mapped_column(Float)
    status: Mapped[AllocationStatus] = mapped_column(Enum(AllocationStatus), default=AllocationStatus.PLANNED, index=True)
    scheduled_release_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    release_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    release_reason_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    release_reason: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    release_requested_by_user_id: Mapped[int | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    release_requested_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    released_by_user_id: Mapped[int | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    released_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_by_user_id: Mapped[int] = mapped_column(ForeignKey('users.id'))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class FeedbackTask(Base):
    __tablename__ = 'feedback_tasks'
    __table_args__ = (
        UniqueConstraint('allocation_id', name='uq_feedback_allocation'),
        CheckConstraint('rating IS NULL OR (rating >= 1 AND rating <= 5)', name='ck_feedback_rating'),
        Index('ix_feedback_tasks_employee_status_id', 'employee_id', 'status', 'id'),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    allocation_id: Mapped[int] = mapped_column(ForeignKey('allocations.id'), index=True)
    manager_user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey('employees.id'), index=True)
    due_date: Mapped[date] = mapped_column(Date)
    status: Mapped[FeedbackStatus] = mapped_column(Enum(FeedbackStatus), default=FeedbackStatus.PENDING)
    rating: Mapped[int | None] = mapped_column(Integer, nullable=True)
    feedback: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    edited_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class FeedbackEditHistory(Base):
    __tablename__ = 'feedback_edit_history'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    feedback_task_id: Mapped[int] = mapped_column(ForeignKey('feedback_tasks.id'), index=True)
    edited_by_user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    previous_rating: Mapped[int | None] = mapped_column(Integer, nullable=True)
    previous_feedback: Mapped[str | None] = mapped_column(Text, nullable=True)
    edited_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class ResourceCapacity(Base):
    __tablename__ = 'resource_capacity'
    employee_id: Mapped[int] = mapped_column(ForeignKey('employees.id'), primary_key=True)
    as_of_date: Mapped[date] = mapped_column(Date, index=True)
    active_project_count: Mapped[int] = mapped_column(Integer, default=0)
    allocated_percentage: Mapped[float] = mapped_column(Float, default=0)
    available_percentage: Mapped[float] = mapped_column(Float, default=100)
    capacity_state: Mapped[str] = mapped_column(String(30), default='AVAILABLE')
    recalculated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class SystemConfig(Base):
    __tablename__ = 'system_config'
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(String(500))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class AuditLog(Base):
    __tablename__ = 'audit_logs'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor_user_id: Mapped[int | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    action: Mapped[str] = mapped_column(String(100), index=True)
    entity_type: Mapped[str] = mapped_column(String(100))
    entity_id: Mapped[str] = mapped_column(String(100))
    details: Mapped[str] = mapped_column(Text, default='')
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

Index('uq_designation_master_name_ci', func.lower(DesignationMaster.name), unique=True)
Index('uq_skill_master_name_ci', func.lower(SkillMaster.name), unique=True)
