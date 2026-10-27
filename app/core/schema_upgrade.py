"""Idempotent SQLite compatibility upgrades for existing local demo databases."""
import json
from sqlalchemy import inspect, text


def _clean(value): return ' '.join((value or '').strip().split())
def _tokens(value): return [_clean(x) for x in (value or '').split(',') if _clean(x)]


def _backfill_masters(conn):
    issues = []
    designation_values = []
    skill_values = []
    for designation, skills in conn.execute(text('SELECT designation, skills FROM employees')).all():
        if _clean(designation): designation_values.append(_clean(designation))
        skill_values.extend(_tokens(skills))
    for designation, skills in conn.execute(text('SELECT role_required, skills_required FROM resource_requests')).all():
        if _clean(designation): designation_values.append(_clean(designation))
        skill_values.extend(_tokens(skills))
    for name in designation_values:
        conn.execute(text('INSERT OR IGNORE INTO designation_master(name,is_active,created_at,updated_at) VALUES (:name,1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)'), {'name': name})
    for name in skill_values:
        conn.execute(text('INSERT OR IGNORE INTO skill_master(name,is_active,created_at,updated_at) VALUES (:name,1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)'), {'name': name})
    designations = {str(name).lower(): did for did, name in conn.execute(text('SELECT id,name FROM designation_master')).all()}
    skills = {str(name).lower(): sid for sid, name in conn.execute(text('SELECT id,name FROM skill_master')).all()}
    for row in conn.execute(text('SELECT id,designation,skills,designation_id FROM employees')).mappings():
        name = _clean(row['designation']); did = designations.get(name.lower()) if name else None
        if name and not did: issues.append({'entity':'employee','id':row['id'],'field':'designation','value':name})
        if did and not row['designation_id']: conn.execute(text('UPDATE employees SET designation_id=:did WHERE id=:id'), {'did':did,'id':row['id']})
        for skill_name in _tokens(row['skills']):
            sid = skills.get(skill_name.lower())
            if not sid: issues.append({'entity':'employee','id':row['id'],'field':'skill','value':skill_name}); continue
            conn.execute(text("INSERT OR IGNORE INTO employee_skill(employee_id,skill_id,proficiency,years_of_experience) VALUES (:eid,:sid,'Intermediate',NULL)"), {'eid':row['id'],'sid':sid})
    for row in conn.execute(text('SELECT id,role_required,skills_required,designation_id,skill_id FROM resource_requests')).mappings():
        designation_name = _clean(row['role_required']); skill_names = _tokens(row['skills_required'])
        did = designations.get(designation_name.lower()) if designation_name else None
        sid = skills.get(skill_names[0].lower()) if skill_names else None
        if designation_name and not did: issues.append({'entity':'resource_request','id':row['id'],'field':'designation','value':designation_name})
        if row['skills_required'] and not sid: issues.append({'entity':'resource_request','id':row['id'],'field':'skill','value':row['skills_required']})
        conn.execute(text('UPDATE resource_requests SET designation_id=COALESCE(designation_id,:did), skill_id=COALESCE(skill_id,:sid) WHERE id=:id'), {'did':did,'sid':sid,'id':row['id']})
    if issues:
        print('MASTER_BACKFILL_REPORT ' + json.dumps({'unmapped':issues}, sort_keys=True))


def apply_local_schema_upgrades(engine) -> None:
    if engine.dialect.name != 'sqlite': return
    tables = set(inspect(engine).get_table_names())
    additive = {
        'employees': {'experience_years':'FLOAT','designation_id':'INTEGER','joining_date':'DATE'},
        'resource_requests': {'role_required':'VARCHAR(255)','skills_required':'TEXT','designation_id':'INTEGER','skill_id':'INTEGER'},
        'allocations': {'scheduled_release_date':'DATE','release_requested_by_user_id':'INTEGER','release_requested_at':'DATETIME','release_date':'DATE','release_reason_type':'VARCHAR(40)','released_by_user_id':'INTEGER'},
        'feedback_tasks': {'updated_at':'DATETIME','edited_at':'DATETIME'},
    }
    with engine.begin() as conn:
        # Ensure new normalized tables exist even when upgrading a hand-built/older local DB.
        conn.execute(text('''CREATE TABLE IF NOT EXISTS designation_master (id INTEGER PRIMARY KEY, name VARCHAR(255) NOT NULL, is_active BOOLEAN NOT NULL DEFAULT 1, created_at DATETIME DEFAULT CURRENT_TIMESTAMP, updated_at DATETIME DEFAULT CURRENT_TIMESTAMP)'''))
        conn.execute(text('''CREATE TABLE IF NOT EXISTS skill_master (id INTEGER PRIMARY KEY, name VARCHAR(255) NOT NULL, category VARCHAR(255), is_active BOOLEAN NOT NULL DEFAULT 1, created_at DATETIME DEFAULT CURRENT_TIMESTAMP, updated_at DATETIME DEFAULT CURRENT_TIMESTAMP)'''))
        conn.execute(text('''CREATE TABLE IF NOT EXISTS employee_skill (id INTEGER PRIMARY KEY, employee_id INTEGER NOT NULL, skill_id INTEGER NOT NULL, proficiency VARCHAR(20) NOT NULL DEFAULT 'Intermediate', years_of_experience FLOAT, FOREIGN KEY(employee_id) REFERENCES employees(id), FOREIGN KEY(skill_id) REFERENCES skill_master(id), UNIQUE(employee_id,skill_id))'''))
        conn.execute(text('CREATE UNIQUE INDEX IF NOT EXISTS uq_designation_master_name_ci ON designation_master(lower(name))'))
        conn.execute(text('CREATE UNIQUE INDEX IF NOT EXISTS uq_skill_master_name_ci ON skill_master(lower(name))'))
        for table, cols in additive.items():
            if table not in tables: continue
            existing = {c['name'] for c in inspect(engine).get_columns(table)}
            for name, definition in cols.items():
                if name not in existing: conn.execute(text(f'ALTER TABLE {table} ADD COLUMN {name} {definition}'))
        # A pre-round2 local DB can still have requested_employee_id NOT NULL.
        if 'resource_requests' in tables:
            cols = inspect(engine).get_columns('resource_requests'); req_emp = next((c for c in cols if c['name'] == 'requested_employee_id'), None)
            if req_emp and not req_emp.get('nullable', True):
                conn.execute(text('PRAGMA foreign_keys=OFF'))
                conn.execute(text('DROP TABLE IF EXISTS resource_requests_compat'))
                conn.execute(text('''CREATE TABLE resource_requests_compat (
                  id INTEGER NOT NULL PRIMARY KEY, project_id INTEGER NOT NULL, requested_employee_id INTEGER,
                  requested_by_user_id INTEGER NOT NULL, designation_id INTEGER, skill_id INTEGER,
                  role_required VARCHAR(255), skills_required TEXT, start_date DATE NOT NULL, end_date DATE NOT NULL,
                  allocation_percentage FLOAT NOT NULL, number_of_resources INTEGER NOT NULL DEFAULT 1,
                  priority VARCHAR(20) NOT NULL DEFAULT 'MEDIUM', reason TEXT NOT NULL DEFAULT '', status VARCHAR(20) NOT NULL,
                  reviewed_by_user_id INTEGER, rejection_reason TEXT, created_at DATETIME, updated_at DATETIME,
                  FOREIGN KEY(project_id) REFERENCES projects(id), FOREIGN KEY(requested_employee_id) REFERENCES employees(id),
                  FOREIGN KEY(requested_by_user_id) REFERENCES users(id), FOREIGN KEY(reviewed_by_user_id) REFERENCES users(id),
                  FOREIGN KEY(designation_id) REFERENCES designation_master(id), FOREIGN KEY(skill_id) REFERENCES skill_master(id),
                  CONSTRAINT ck_request_date_range CHECK (end_date >= start_date),
                  CONSTRAINT ck_request_percentage CHECK (allocation_percentage > 0 AND allocation_percentage <= 100),
                  CONSTRAINT ck_request_resource_count CHECK (number_of_resources >= 1)
                )'''))
                conn.execute(text('''INSERT INTO resource_requests_compat
                  (id,project_id,requested_employee_id,requested_by_user_id,designation_id,skill_id,role_required,skills_required,start_date,end_date,allocation_percentage,number_of_resources,priority,reason,status,reviewed_by_user_id,rejection_reason,created_at,updated_at)
                  SELECT id,project_id,requested_employee_id,requested_by_user_id,designation_id,skill_id,role_required,skills_required,start_date,end_date,allocation_percentage,
                         COALESCE(number_of_resources,1),COALESCE(priority,'MEDIUM'),reason,status,reviewed_by_user_id,rejection_reason,created_at,updated_at
                  FROM resource_requests'''))
                conn.execute(text('DROP TABLE resource_requests'))
                conn.execute(text('ALTER TABLE resource_requests_compat RENAME TO resource_requests'))
                conn.execute(text('PRAGMA foreign_keys=ON'))
        # Base.metadata.create_all creates the master tables on new/current local starts.
        if {'designation_master','skill_master','employee_skill','employees','resource_requests'}.issubset(set(inspect(engine).get_table_names())):
            _backfill_masters(conn)
        conn.execute(text('CREATE INDEX IF NOT EXISTS ix_resource_requests_role_required ON resource_requests(role_required)'))
        conn.execute(text('CREATE INDEX IF NOT EXISTS ix_resource_requests_designation_id ON resource_requests(designation_id)'))
        conn.execute(text('CREATE INDEX IF NOT EXISTS ix_resource_requests_skill_id ON resource_requests(skill_id)'))
        conn.execute(text('CREATE INDEX IF NOT EXISTS ix_employees_designation ON employees(designation)'))
        conn.execute(text('CREATE INDEX IF NOT EXISTS ix_employees_designation_id ON employees(designation_id)'))
        conn.execute(text('CREATE INDEX IF NOT EXISTS ix_employee_skill_skill_employee ON employee_skill(skill_id,employee_id)'))
        conn.execute(text('CREATE INDEX IF NOT EXISTS ix_feedback_tasks_employee_status_id ON feedback_tasks(employee_id,status,id)'))
        conn.execute(text('CREATE INDEX IF NOT EXISTS ix_allocations_range ON allocations(start_date,end_date,status)'))
        conn.execute(text('CREATE INDEX IF NOT EXISTS ix_allocations_employee_range ON allocations(employee_id,start_date,end_date)'))
