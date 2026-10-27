"""Calendar scale indexes.

Revision ID: 20261023_03
Revises: 20261023_02
"""
from alembic import op
from sqlalchemy import inspect

revision='20261023_03'
down_revision='20261023_02'
branch_labels=None
depends_on=None

def _has_index(table,name):
    bind=op.get_bind();return any(i.get('name')==name for i in inspect(bind).get_indexes(table))

def upgrade():
    if not _has_index('allocations','ix_allocations_project_status_range'):
        op.create_index('ix_allocations_project_status_range','allocations',['project_id','status','start_date','end_date'])
    if not _has_index('allocations','ix_allocations_status_range'):
        op.create_index('ix_allocations_status_range','allocations',['status','start_date','end_date'])

def downgrade():
    if _has_index('allocations','ix_allocations_status_range'):op.drop_index('ix_allocations_status_range',table_name='allocations')
    if _has_index('allocations','ix_allocations_project_status_range'):op.drop_index('ix_allocations_project_status_range',table_name='allocations')
