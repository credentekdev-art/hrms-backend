"""round2 resource management changes
Revision ID: 20261022_01
Revises:
Create Date: 2026-10-22

This historical migration is intentionally defensive because some local SQLite
installs received compatibility upgrades before Alembic version tracking was
introduced. It therefore checks the live schema before every change and never
removes restored role/skill data from a database that already contains the
newer master-backed request columns.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect, text

revision = "20261022_01"
down_revision = None
branch_labels = None
depends_on = None


def _tables(bind):
    return set(inspect(bind).get_table_names())


def _columns(bind, table):
    return {c['name']: c for c in inspect(bind).get_columns(table)}


def _foreign_keys(bind, table):
    return {fk.get('name') for fk in inspect(bind).get_foreign_keys(table) if fk.get('name')}


def _indexes(bind, table):
    try:
        return {idx.get('name') for idx in inspect(bind).get_indexes(table) if idx.get('name')}
    except Exception:
        return set()


def _drop_stale_sqlite_batch_table(bind, table):
    if bind.dialect.name != 'sqlite':
        return
    temp = f'_alembic_tmp_{table}'
    names = _tables(bind)
    # A temp table left by an interrupted batch operation is safe to remove only
    # when the original table is still present. Never guess when the original is absent.
    if table in names and temp in names:
        bind.execute(text(f'DROP TABLE IF EXISTS "{temp}"'))


def upgrade():
    bind = op.get_bind()
    tables = _tables(bind)

    if bind.dialect.name == "postgresql":
        # Existing deployments may use SQLAlchemy native enum type allocationstatus.
        op.execute("ALTER TYPE allocationstatus ADD VALUE IF NOT EXISTS 'COMPLETED'")
        op.execute("ALTER TYPE allocationstatus ADD VALUE IF NOT EXISTS 'RELEASED_EARLY'")

    if 'resource_requests' in tables:
        _drop_stale_sqlite_batch_table(bind, 'resource_requests')
        cols = _columns(bind, 'resource_requests')
        # designation_id/skill_id prove that a later compatibility/master upgrade
        # has already restored Role/Skill semantics. In that drifted case, preserve
        # the legacy text columns rather than deleting their data and re-adding them.
        later_schema_present = 'designation_id' in cols or 'skill_id' in cols
        need_nullable = 'requested_employee_id' in cols and not cols['requested_employee_id'].get('nullable', True)
        drop_role = 'role_required' in cols and not later_schema_present
        drop_skills = 'skills_required' in cols and not later_schema_present
        if need_nullable or drop_role or drop_skills:
            with op.batch_alter_table('resource_requests') as batch:
                if need_nullable:
                    batch.alter_column('requested_employee_id', existing_type=sa.Integer(), nullable=True)
                if drop_role:
                    batch.drop_column('role_required')
                if drop_skills:
                    batch.drop_column('skills_required')

    if 'allocations' in tables:
        _drop_stale_sqlite_batch_table(bind, 'allocations')
        cols = _columns(bind, 'allocations')
        fks = _foreign_keys(bind, 'allocations')
        needs = any(name not in cols for name in ('release_date', 'release_reason_type', 'released_by_user_id'))
        reason_needs_resize = 'release_reason' in cols and getattr(cols['release_reason'].get('type'), 'length', None) not in (None, 1000)
        if needs or reason_needs_resize:
            with op.batch_alter_table('allocations') as batch:
                if reason_needs_resize:
                    batch.alter_column('release_reason', existing_type=cols['release_reason']['type'], type_=sa.String(1000), existing_nullable=cols['release_reason'].get('nullable', True))
                if 'release_date' not in cols:
                    batch.add_column(sa.Column('release_date', sa.Date(), nullable=True))
                if 'release_reason_type' not in cols:
                    batch.add_column(sa.Column('release_reason_type', sa.String(40), nullable=True))
                if 'released_by_user_id' not in cols:
                    batch.add_column(sa.Column('released_by_user_id', sa.Integer(), nullable=True))
            # Refresh schema after the batch before creating FK.
            if bind.dialect.name != 'sqlite' and 'fk_allocations_released_by' not in fks:
                with op.batch_alter_table('allocations') as batch:
                    batch.create_foreign_key('fk_allocations_released_by', 'users', ['released_by_user_id'], ['id'])

    if 'feedback_tasks' in tables:
        _drop_stale_sqlite_batch_table(bind, 'feedback_tasks')
        cols = _columns(bind, 'feedback_tasks')
        if 'updated_at' not in cols or 'edited_at' not in cols:
            with op.batch_alter_table('feedback_tasks') as batch:
                if 'updated_at' not in cols:
                    batch.add_column(sa.Column('updated_at', sa.DateTime(), nullable=True))
                if 'edited_at' not in cols:
                    batch.add_column(sa.Column('edited_at', sa.DateTime(), nullable=True))

    if 'feedback_edit_history' not in _tables(bind):
        op.create_table(
            'feedback_edit_history',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('feedback_task_id', sa.Integer(), sa.ForeignKey('feedback_tasks.id'), nullable=False),
            sa.Column('edited_by_user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
            sa.Column('previous_rating', sa.Integer()),
            sa.Column('previous_feedback', sa.Text()),
            sa.Column('edited_at', sa.DateTime(), nullable=False),
        )
    if 'feedback_edit_history' in _tables(bind) and 'ix_feedback_edit_history_feedback_task_id' not in _indexes(bind, 'feedback_edit_history'):
        op.create_index('ix_feedback_edit_history_feedback_task_id', 'feedback_edit_history', ['feedback_task_id'])


def downgrade():
    # Keep downgrade defensive as well; this is primarily for test/dev rollback.
    bind = op.get_bind()
    tables = _tables(bind)
    if 'feedback_edit_history' in tables:
        op.drop_table('feedback_edit_history')
    if 'feedback_tasks' in tables:
        _drop_stale_sqlite_batch_table(bind, 'feedback_tasks')
        cols = _columns(bind, 'feedback_tasks')
        if 'edited_at' in cols or 'updated_at' in cols:
            with op.batch_alter_table('feedback_tasks') as batch:
                if 'edited_at' in cols:
                    batch.drop_column('edited_at')
                if 'updated_at' in cols:
                    batch.drop_column('updated_at')
    if 'allocations' in tables:
        _drop_stale_sqlite_batch_table(bind, 'allocations')
        cols = _columns(bind, 'allocations')
        with op.batch_alter_table('allocations') as batch:
            if 'released_by_user_id' in cols:
                # SQLite batch mode rebuilds constraints with the table, so an
                # explicit FK drop is not required/reliable there.
                if bind.dialect.name != 'sqlite' and 'fk_allocations_released_by' in _foreign_keys(bind, 'allocations'):
                    batch.drop_constraint('fk_allocations_released_by', type_='foreignkey')
                batch.drop_column('released_by_user_id')
            if 'release_reason_type' in cols:
                batch.drop_column('release_reason_type')
            if 'release_date' in cols:
                batch.drop_column('release_date')
    if 'resource_requests' in tables:
        _drop_stale_sqlite_batch_table(bind, 'resource_requests')
        cols = _columns(bind, 'resource_requests')
        # Do not overwrite restored data if these columns already exist.
        with op.batch_alter_table('resource_requests') as batch:
            if 'role_required' not in cols:
                batch.add_column(sa.Column('role_required', sa.String(255), nullable=True))
            if 'skills_required' not in cols:
                batch.add_column(sa.Column('skills_required', sa.Text(), nullable=True))
