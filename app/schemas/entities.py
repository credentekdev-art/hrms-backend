from datetime import date, datetime
from typing import Literal
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator
from app.models.entities import AllocationStatus, FeedbackStatus, RequestStatus, Role


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class LoginRequest(BaseModel):
    email: str = Field(min_length=5, max_length=255)
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = 'bearer'
    user: dict


class UserOut(ORMModel):
    id: int
    email: str
    full_name: str
    role: Role
    employee_id: int | None


class DesignationMasterCreate(BaseModel):
    name: str = Field(min_length=2, max_length=255)
    is_active: bool = True


class DesignationMasterUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=255)
    is_active: bool | None = None


class DesignationMasterOut(ORMModel):
    id: int
    name: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


class SkillMasterCreate(BaseModel):
    name: str = Field(min_length=2, max_length=255)
    category: str | None = Field(default=None, max_length=255)
    is_active: bool = True


class SkillMasterUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=255)
    category: str | None = Field(default=None, max_length=255)
    is_active: bool | None = None


class SkillMasterOut(ORMModel):
    id: int
    name: str
    category: str | None = None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class DesignationBrief(BaseModel):
    id: int
    name: str


class EmployeeSkillInput(BaseModel):
    skill_id: int
    proficiency: Literal['Beginner', 'Intermediate', 'Expert'] = 'Intermediate'
    years_of_experience: float | None = Field(default=None, ge=0, le=80)


class EmployeeSkillOut(BaseModel):
    id: int
    name: str
    proficiency: str
    years: float | None = None


class EmployeeCreate(BaseModel):
    employee_code: str = Field(min_length=1, max_length=50)
    name: str = Field(min_length=2, max_length=255)
    designation_id: int
    department: str = Field(default='', max_length=255)
    skill_items: list[EmployeeSkillInput] = Field(default_factory=list, max_length=100)
    experience_years: float | None = Field(default=None, ge=0, le=80)
    manager_user_id: int | None = None
    active: bool = True
    joining_date: date | None = None


class EmployeeUpdate(BaseModel):
    employee_code: str | None = Field(default=None, min_length=1, max_length=50)
    name: str | None = Field(default=None, min_length=2, max_length=255)
    designation_id: int | None = None
    department: str | None = Field(default=None, max_length=255)
    skill_items: list[EmployeeSkillInput] | None = Field(default=None, max_length=100)
    experience_years: float | None = Field(default=None, ge=0, le=80)
    manager_user_id: int | None = None
    active: bool | None = None
    joining_date: date | None = None


class EmployeeOut(BaseModel):
    id: int
    employee_code: str
    name: str
    designation: DesignationBrief | None = None
    designation_name: str = ''
    department: str
    skills: list[EmployeeSkillOut] = Field(default_factory=list)
    skills_text: str = ''
    experience_years: float | None = None
    manager_user_id: int | None = None
    active: bool
    joining_date: date | None = None


class ProjectOut(ORMModel):
    id: int
    code: str
    name: str
    manager_user_id: int
    active: bool


class DatePercentageMixin(BaseModel):
    start_date: date
    end_date: date
    allocation_percentage: float = Field(gt=0, le=100)

    @model_validator(mode='after')
    def validate_dates(self):
        if self.end_date < self.start_date:
            raise ValueError('End date must be on or after start date')
        return self


class ResourceRequestCreate(DatePercentageMixin):
    project_id: int
    number_of_resources: int = Field(ge=1, le=50)
    priority: Literal['LOW', 'MEDIUM', 'HIGH', 'CRITICAL'] = 'MEDIUM'
    designation_id: int | None = None
    skill_id: int | None = None
    requested_employee_ids: list[int] = Field(default_factory=list, max_length=50)
    # Backward-compatible aliases. New clients must use master IDs.
    role_required: str | None = Field(default=None, max_length=255)
    skills_required: str | None = Field(default=None, max_length=1000)
    remark: str = Field(validation_alias=AliasChoices('remark', 'reason'), min_length=3, max_length=2000)
    save_as_draft: bool = False


class ResourceRequestUpdate(BaseModel):
    project_id: int | None = None
    number_of_resources: int | None = Field(default=None, ge=1, le=50)
    allocation_percentage: float | None = Field(default=None, gt=0, le=100)
    start_date: date | None = None
    end_date: date | None = None
    priority: Literal['LOW', 'MEDIUM', 'HIGH', 'CRITICAL'] | None = None
    designation_id: int | None = None
    skill_id: int | None = None
    requested_employee_ids: list[int] | None = Field(default=None, max_length=50)
    role_required: str | None = Field(default=None, max_length=255)
    skills_required: str | None = Field(default=None, max_length=1000)
    remark: str | None = Field(default=None, validation_alias=AliasChoices('remark', 'reason'), min_length=3, max_length=2000)

    @model_validator(mode='after')
    def validate_dates(self):
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValueError('End date must be on or after start date')
        return self


class ResourceRequestOut(ORMModel):
    id: int
    project_id: int
    requested_employee_id: int | None = None
    requested_employee_ids: list[int] = Field(default_factory=list)
    requested_by_user_id: int
    requested_by_name: str = ''
    designation_id: int | None = None
    skill_id: int | None = None
    designation_name: str = ''
    skill_name: str = ''
    role_required: str | None = None
    skills_required: str | None = None
    start_date: date
    end_date: date
    allocation_percentage: float
    number_of_resources: int = 1
    priority: str = 'MEDIUM'
    reason: str = ''
    remark: str = ''
    status: RequestStatus
    reviewed_by_user_id: int | None
    rejection_reason: str | None
    created_at: datetime
    updated_at: datetime | None = None


class RequestDecision(BaseModel):
    decision: Literal['approve', 'reject', 'review']
    remark: str | None = Field(default=None, min_length=3, max_length=1000)
    employee_ids: list[int] = Field(default_factory=list, max_length=50)

    @model_validator(mode='after')
    def validate_remark(self):
        if self.decision in ('approve', 'reject') and (not self.remark or len(self.remark.strip()) < 3):
            raise ValueError('Remark is required and must contain at least 3 characters')
        return self


class AllocationCreate(DatePercentageMixin):
    employee_id: int
    project_id: int
    resource_request_id: int | None = None


class AllocationUpdate(BaseModel):
    start_date: date | None = None
    end_date: date | None = None
    allocation_percentage: float | None = Field(default=None, gt=0, le=100)

    @model_validator(mode='after')
    def any_change(self):
        if self.start_date is None and self.end_date is None and self.allocation_percentage is None:
            raise ValueError('At least one allocation field must be provided')
        return self


class AllocationRelease(BaseModel):
    release_date: date
    reason_type: Literal['PROJECT_COMPLETED', 'RESOURCE_REPLACED', 'PERFORMANCE', 'CLIENT_REQUEST', 'OTHER'] = 'OTHER'
    reason: str = Field(min_length=3, max_length=1000)


class FeedbackOut(ORMModel):
    id: int
    allocation_id: int
    manager_user_id: int
    employee_id: int
    due_date: date
    status: FeedbackStatus
    rating: int | None
    feedback: str | None
    completed_at: datetime | None = None
    updated_at: datetime | None = None
    edited_at: datetime | None = None
    manager_name: str = ''
    edit_history: list[dict] = Field(default_factory=list)


class FeedbackComplete(BaseModel):
    rating: int = Field(ge=1, le=5)
    feedback: str = Field(min_length=5, max_length=4000)


class FeedbackCreate(FeedbackComplete):
    allocation_id: int


class AllocationOut(ORMModel):
    id: int
    employee_id: int
    project_id: int
    resource_request_id: int | None
    start_date: date
    end_date: date
    allocation_percentage: float
    status: AllocationStatus
    scheduled_release_date: date | None = None
    release_date: date | None = None
    release_reason_type: str | None = None
    release_reason: str | None = None
    release_requested_by_user_id: int | None = None
    release_requested_at: datetime | None = None
    released_by_user_id: int | None = None
    released_at: datetime | None = None
    created_by_user_id: int


class AllocationDetailOut(AllocationOut):
    can_manage: bool
    employee_name: str
    employee_code: str
    project_name: str
    project_code: str
    allocated_by_name: str
    project_manager_name: str
    released_by_name: str | None = None
    feedback: FeedbackOut | None = None


class CapacityCheckRequest(DatePercentageMixin):
    employee_id: int
    project_id: int
    exclude_allocation_id: int | None = None


class CapacityConflict(BaseModel):
    date: date
    concurrent_projects: int
    total_percentage: float
    reason: str
    code: str = 'CAPACITY'


class CapacityCheckResponse(BaseModel):
    valid: bool
    max_concurrent_projects: int
    peak_concurrent_projects: int
    peak_percentage: float
    available_capacity: float = 100
    overallocation_policy: str = 'BLOCK'
    conflicts: list[CapacityConflict]
    warnings: list[CapacityConflict] = Field(default_factory=list)


class ConfigUpdate(BaseModel):
    max_concurrent_projects: int = Field(ge=1, le=20)
