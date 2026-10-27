"""restore request role/skill and calendar/search support
Revision ID: 20261023_01
Revises: 20261022_01
Create Date: 2026-10-23

Upgrade is intentionally additive/non-destructive and safe whether the earlier
role/skill removal migration ran or the columns already exist.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision='20261023_01'; down_revision='20261022_01'; branch_labels=None; depends_on=None

def _columns(bind, table): return {c['name'] for c in inspect(bind).get_columns(table)}
def _indexes(bind, table): return {i['name'] for i in inspect(bind).get_indexes(table)}

def upgrade():
    bind=op.get_bind(); tables=set(inspect(bind).get_table_names())
    if 'resource_requests' in tables:
        cols=_columns(bind,'resource_requests')
        with op.batch_alter_table('resource_requests') as batch:
            if 'role_required' not in cols: batch.add_column(sa.Column('role_required',sa.String(255),nullable=True))
            if 'skills_required' not in cols: batch.add_column(sa.Column('skills_required',sa.Text(),nullable=True))
        if 'ix_resource_requests_role_required' not in _indexes(bind,'resource_requests'):
            op.create_index('ix_resource_requests_role_required','resource_requests',['role_required'])
    if 'employees' in tables:
        cols=_columns(bind,'employees')
        with op.batch_alter_table('employees') as batch:
            if 'experience_years' not in cols: batch.add_column(sa.Column('experience_years',sa.Float(),nullable=True))
        idx=_indexes(bind,'employees')
        if 'ix_employees_designation' not in idx: op.create_index('ix_employees_designation','employees',['designation'])
    if 'feedback_tasks' in tables and 'ix_feedback_tasks_employee_status_id' not in _indexes(bind,'feedback_tasks'):
        op.create_index('ix_feedback_tasks_employee_status_id','feedback_tasks',['employee_id','status','id'])
    if 'allocations' in tables and 'ix_allocations_range' not in _indexes(bind,'allocations'):
        op.create_index('ix_allocations_range','allocations',['start_date','end_date','status'])

def downgrade():
    # Non-destructive by design: do not drop restored data-bearing columns. Only
    # remove performance indexes created by this revision when present.
    bind=op.get_bind(); tables=set(inspect(bind).get_table_names())
    for table,name in [('allocations','ix_allocations_range'),('feedback_tasks','ix_feedback_tasks_employee_status_id'),('employees','ix_employees_designation'),('resource_requests','ix_resource_requests_role_required')]:
        if table in tables and name in _indexes(bind,table): op.drop_index(name,table_name=table)
