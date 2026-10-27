from dataclasses import dataclass
from datetime import date, timedelta
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.core.config import settings
from app.models.entities import Allocation, AllocationStatus, Employee, SystemConfig

@dataclass
class CapacityConflict:
    date:date; concurrent_projects:int; total_percentage:float; reason:str; code:str="CAPACITY"
@dataclass
class CapacityResult:
    valid:bool; max_concurrent_projects:int; peak_concurrent_projects:int; peak_percentage:float; available_capacity:float; overallocation_policy:str; conflicts:list[CapacityConflict]; warnings:list[CapacityConflict]
class CapacityValidationError(ValueError):
    def __init__(self,result): self.result=result; super().__init__(result.conflicts[0].reason if result.conflicts else "Capacity validation failed")

def _get_config(db,key,default):
    c=db.get(SystemConfig,key); return c.value if c and c.value else default
def get_max_concurrent_projects(db):
    try:return int(_get_config(db,"max_concurrent_projects",str(settings.default_max_concurrent_projects)))
    except ValueError:return settings.default_max_concurrent_projects
def get_overallocation_policy(db): return "BLOCK"  # hard business ceiling for active allocations

def allocation_effective_end(a):
    if a.status in (AllocationStatus.COMPLETED,AllocationStatus.RELEASED_EARLY,AllocationStatus.RELEASED,AllocationStatus.CANCELLED) and a.release_date:
        return min(a.end_date,a.release_date-timedelta(days=1))
    if a.scheduled_release_date: return min(a.end_date,a.scheduled_release_date-timedelta(days=1))
    return a.end_date

def build_overlap_allocation_stmt(employee_id:int,start_date:date,end_date:date,exclude_allocation_id:int|None=None,lock:bool=False):
    stmt=select(Allocation).where(Allocation.employee_id==employee_id,Allocation.status.in_([AllocationStatus.PLANNED,AllocationStatus.ACTIVE]),Allocation.start_date<=end_date,Allocation.end_date>=start_date)
    if exclude_allocation_id: stmt=stmt.where(Allocation.id!=exclude_allocation_id)
    if lock: stmt=stmt.with_for_update()
    return stmt

def validate_capacity(db:Session,*,employee_id:int,project_id:int,start_date:date,end_date:date,allocation_percentage:float,exclude_allocation_id:int|None=None,lock_employee:bool=False):
    if end_date<start_date: raise ValueError("End date must be on or after start date")
    if allocation_percentage<=0 or allocation_percentage>100: raise ValueError("Allocation must be greater than 0% and no more than 100%")
    q=select(Employee).where(Employee.id==employee_id)
    if lock_employee:q=q.with_for_update()
    employee=db.execute(q).scalar_one_or_none()
    if not employee or not employee.active: raise ValueError("Employee not found or inactive")
    stmt=build_overlap_allocation_stmt(employee_id,start_date,end_date,exclude_allocation_id,lock_employee)
    overlaps=list(db.execute(stmt).scalars().all())
    boundaries={start_date,end_date+timedelta(days=1)}
    for a in overlaps:
        ee=allocation_effective_end(a); s=max(start_date,a.start_date); e=min(end_date,ee)
        if s<=e: boundaries.update([s,e+timedelta(days=1)])
    boundaries=sorted(boundaries)
    conflicts=[]; warnings=[]; peak_pct=0; peak_projects=0; min_available=100.0; max_projects=get_max_concurrent_projects(db)
    for seg in boundaries[:-1]:
        active=[a for a in overlaps if a.start_date<=seg<=allocation_effective_end(a)]
        existing_pct=sum(float(a.allocation_percentage) for a in active); available=max(0.0,100-existing_pct); min_available=min(min_available,available)
        project_ids={a.project_id for a in active}; projected_projects=len(project_ids|{project_id}); projected=existing_pct+allocation_percentage
        peak_pct=max(peak_pct,projected); peak_projects=max(peak_projects,projected_projects)
        duplicate=any(a.project_id==project_id for a in active)
        if duplicate:
            conflicts.append(CapacityConflict(seg,projected_projects,round(projected,2),f"{employee.name} already has an overlapping allocation on this project. Edit the existing allocation instead.","DUPLICATE_PROJECT_ALLOCATION"))
        if allocation_percentage>available+1e-9:
            conflicts.append(CapacityConflict(seg,projected_projects,round(projected,2),f"{employee.name} has {existing_pct:g}% allocated. Only {available:g}% available.","CAP_PERCENTAGE_EXCEEDED"))
        if projected_projects>max_projects:
            conflicts.append(CapacityConflict(seg,projected_projects,round(projected,2),f"{employee.name} would be assigned to {projected_projects} projects. The current limit is {max_projects}.","CAP_PROJECT_LIMIT_EXCEEDED"))
    return CapacityResult(not conflicts,max_projects,peak_projects,round(peak_pct,2),round(min_available,2),"BLOCK",conflicts,warnings)

def assert_capacity(**kwargs):
    r=validate_capacity(**kwargs)
    if not r.valid: raise CapacityValidationError(r)
    return r
