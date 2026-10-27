"""normalize designation/skill masters and employee skills

Revision ID: 20261023_02
Revises: 20261023_01
Create Date: 2026-10-23

The upgrade is idempotent and preserves every legacy designation/skills column.
Backfill issues are printed and, when audit_logs exists, persisted as an audit row.
The downgrade is intentionally non-destructive: it removes only indexes/constraints
created by this revision and keeps normalized data-bearing tables/columns so a
rollback cannot erase master, proficiency, or skill-experience data.
"""
from alembic import op
import json
import sqlalchemy as sa
from sqlalchemy import inspect, text

revision = '20261023_02'
down_revision = '20261023_01'
branch_labels = None
depends_on = None


def _tables(bind): return set(inspect(bind).get_table_names())
def _columns(bind, table): return {c['name'] for c in inspect(bind).get_columns(table)}
def _indexes(bind, table):
    try:
        return {i['name'] for i in inspect(bind).get_indexes(table)}
    except Exception:
        return set()

def _index_exists(bind, table, name):
    # SQLAlchemy/SQLite reflection skips expression indexes such as lower(name).
    # Fall back to the database catalogue so downgrade/re-upgrade remains idempotent.
    if name in _indexes(bind, table):
        return True
    if bind.dialect.name == 'sqlite':
        return bind.execute(text("SELECT 1 FROM sqlite_master WHERE type='index' AND name=:name"), {'name': name}).first() is not None
    if bind.dialect.name == 'postgresql':
        return bind.execute(text("SELECT 1 FROM pg_indexes WHERE tablename=:table AND indexname=:name"), {'table': table, 'name': name}).first() is not None
    return False
def _foreign_keys(bind, table): return {fk.get('name') for fk in inspect(bind).get_foreign_keys(table) if fk.get('name')}
def _clean(value): return ' '.join((value or '').strip().split())
def _tokens(value): return [_clean(x) for x in (value or '').split(',') if _clean(x)]


def _insert_master(bind, table_name, names):
    table = sa.Table(table_name, sa.MetaData(), autoload_with=bind)
    existing = {str(x).lower(): i for i, x in bind.execute(sa.select(table.c.id, table.c.name)).all()}
    for raw in names:
        name = _clean(raw)
        if not name or name.lower() in existing: continue
        result = bind.execute(table.insert().values(name=name, is_active=True))
        existing[name.lower()] = result.inserted_primary_key[0]
    return existing


def _persist_report(bind, issues):
    payload = {'revision': revision, 'unmapped': issues}
    print('MASTER_BACKFILL_REPORT ' + json.dumps(payload, default=str, sort_keys=True))
    if 'audit_logs' in _tables(bind) and issues:
        bind.execute(text("""INSERT INTO audit_logs(actor_user_id, action, entity_type, entity_id, details, created_at)
            VALUES (NULL, 'MASTER_BACKFILL_UNMAPPED', 'Migration', :entity_id, :details, CURRENT_TIMESTAMP)"""),
            {'entity_id': revision, 'details': json.dumps(payload, default=str)})


def upgrade():
    bind = op.get_bind()
    tables = _tables(bind)

    if bind.dialect.name == 'postgresql':
        # Additive enum change; PostgreSQL cannot safely remove enum values on downgrade.
        op.execute("ALTER TYPE role ADD VALUE IF NOT EXISTS 'HR'")

    if 'designation_master' not in tables:
        op.create_table('designation_master',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('name', sa.String(255), nullable=False),
            sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )
    if 'skill_master' not in tables:
        op.create_table('skill_master',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('name', sa.String(255), nullable=False),
            sa.Column('category', sa.String(255), nullable=True),
            sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )
    tables = _tables(bind)
    if 'employee_skill' not in tables:
        op.create_table('employee_skill',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('employee_id', sa.Integer(), sa.ForeignKey('employees.id'), nullable=False),
            sa.Column('skill_id', sa.Integer(), sa.ForeignKey('skill_master.id'), nullable=False),
            sa.Column('proficiency', sa.String(20), nullable=False, server_default='Intermediate'),
            sa.Column('years_of_experience', sa.Float(), nullable=True),
            sa.UniqueConstraint('employee_id', 'skill_id', name='uq_employee_skill_employee_skill'),
            sa.CheckConstraint("proficiency IN ('Beginner','Intermediate','Expert')", name='ck_employee_skill_proficiency'),
            sa.CheckConstraint('years_of_experience IS NULL OR years_of_experience >= 0', name='ck_employee_skill_years'),
        )

    if 'employees' in tables:
        cols = _columns(bind, 'employees')
        with op.batch_alter_table('employees') as batch:
            if 'designation_id' not in cols: batch.add_column(sa.Column('designation_id', sa.Integer(), nullable=True))
            if 'joining_date' not in cols: batch.add_column(sa.Column('joining_date', sa.Date(), nullable=True))
        if 'fk_employees_designation_id' not in _foreign_keys(bind, 'employees'):
            with op.batch_alter_table('employees') as batch:
                batch.create_foreign_key('fk_employees_designation_id', 'designation_master', ['designation_id'], ['id'])
    if 'resource_requests' in tables:
        cols = _columns(bind, 'resource_requests')
        with op.batch_alter_table('resource_requests') as batch:
            if 'designation_id' not in cols: batch.add_column(sa.Column('designation_id', sa.Integer(), nullable=True))
            if 'skill_id' not in cols: batch.add_column(sa.Column('skill_id', sa.Integer(), nullable=True))
        fks = _foreign_keys(bind, 'resource_requests')
        with op.batch_alter_table('resource_requests') as batch:
            if 'fk_resource_requests_designation_id' not in fks: batch.create_foreign_key('fk_resource_requests_designation_id', 'designation_master', ['designation_id'], ['id'])
            if 'fk_resource_requests_skill_id' not in fks: batch.create_foreign_key('fk_resource_requests_skill_id', 'skill_master', ['skill_id'], ['id'])

    # Performance and case-insensitive uniqueness indexes.
    if not _index_exists(bind, 'designation_master', 'uq_designation_master_name_ci'):
        op.execute('CREATE UNIQUE INDEX uq_designation_master_name_ci ON designation_master (lower(name))')
    if not _index_exists(bind, 'skill_master', 'uq_skill_master_name_ci'):
        op.execute('CREATE UNIQUE INDEX uq_skill_master_name_ci ON skill_master (lower(name))')
    for table, name, cols in [
        ('employees', 'ix_employees_designation_id', ['designation_id']),
        ('employee_skill', 'ix_employee_skill_skill_employee', ['skill_id', 'employee_id']),
        ('allocations', 'ix_allocations_employee_range', ['employee_id', 'start_date', 'end_date']),
        ('resource_requests', 'ix_resource_requests_designation_id', ['designation_id']),
        ('resource_requests', 'ix_resource_requests_skill_id', ['skill_id']),
    ]:
        if table in _tables(bind) and not _index_exists(bind, table, name): op.create_index(name, table, cols)

    # Seed masters from both employee and request legacy text so old requests are preserved.
    designation_values = []
    skill_values = []
    if 'employees' in _tables(bind):
        for designation, skills in bind.execute(text('SELECT designation, skills FROM employees')).all():
            if _clean(designation): designation_values.append(designation)
            skill_values.extend(_tokens(skills))
    if 'resource_requests' in _tables(bind):
        for designation, skills in bind.execute(text('SELECT role_required, skills_required FROM resource_requests')).all():
            if _clean(designation): designation_values.append(designation)
            skill_values.extend(_tokens(skills))
    designation_map = _insert_master(bind, 'designation_master', designation_values)
    skill_map = _insert_master(bind, 'skill_master', skill_values)

    issues = []
    if 'employees' in _tables(bind):
        rows = bind.execute(text('SELECT id, designation, skills, designation_id FROM employees')).mappings().all()
        for row in rows:
            name = _clean(row['designation'])
            designation_id = designation_map.get(name.lower()) if name else None
            if name and not designation_id: issues.append({'entity': 'employee', 'id': row['id'], 'field': 'designation', 'value': name})
            elif designation_id and not row['designation_id']:
                bind.execute(text('UPDATE employees SET designation_id=:did WHERE id=:id'), {'did': designation_id, 'id': row['id']})
            for skill_name in _tokens(row['skills']):
                skill_id = skill_map.get(skill_name.lower())
                if not skill_id:
                    issues.append({'entity': 'employee', 'id': row['id'], 'field': 'skill', 'value': skill_name}); continue
                exists = bind.execute(text('SELECT id FROM employee_skill WHERE employee_id=:eid AND skill_id=:sid'), {'eid': row['id'], 'sid': skill_id}).first()
                if not exists:
                    bind.execute(text("INSERT INTO employee_skill(employee_id,skill_id,proficiency,years_of_experience) VALUES (:eid,:sid,'Intermediate',NULL)"), {'eid': row['id'], 'sid': skill_id})

    if 'resource_requests' in _tables(bind):
        rows = bind.execute(text('SELECT id, role_required, skills_required, designation_id, skill_id FROM resource_requests')).mappings().all()
        for row in rows:
            designation_name = _clean(row['role_required'])
            skill_tokens = _tokens(row['skills_required'])
            designation_id = designation_map.get(designation_name.lower()) if designation_name else None
            skill_id = skill_map.get(skill_tokens[0].lower()) if skill_tokens else None
            if designation_name and not designation_id: issues.append({'entity': 'resource_request', 'id': row['id'], 'field': 'designation', 'value': designation_name})
            if row['skills_required'] and not skill_id: issues.append({'entity': 'resource_request', 'id': row['id'], 'field': 'skill', 'value': row['skills_required']})
            bind.execute(text('UPDATE resource_requests SET designation_id=COALESCE(designation_id,:did), skill_id=COALESCE(skill_id,:sid) WHERE id=:id'), {'did': designation_id, 'sid': skill_id, 'id': row['id']})
    _persist_report(bind, issues)


def downgrade():
    # Non-destructive rollback: remove only performance/uniqueness indexes created by
    # this revision. Data-bearing normalized tables/columns remain to prevent loss of
    # proficiency, skill experience, and newly created master records.
    bind = op.get_bind()
    for table, name in [
        ('resource_requests', 'ix_resource_requests_skill_id'),
        ('resource_requests', 'ix_resource_requests_designation_id'),
        ('allocations', 'ix_allocations_employee_range'),
        ('employee_skill', 'ix_employee_skill_skill_employee'),
        ('employees', 'ix_employees_designation_id'),
        ('skill_master', 'uq_skill_master_name_ci'),
        ('designation_master', 'uq_designation_master_name_ci'),
    ]:
        if table in _tables(bind) and _index_exists(bind, table, name): op.drop_index(name, table_name=table)
