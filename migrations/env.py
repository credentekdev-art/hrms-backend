from logging.config import fileConfig
from alembic import context
from sqlalchemy import engine_from_config, pool, inspect, text
from app.core.config import settings
from app.core.database import Base
from app.models import entities  # noqa: F401

config = context.config
config.set_main_option("sqlalchemy.url", settings.database_url)
if config.config_file_name:
    fileConfig(config.config_file_name)
target_metadata = Base.metadata


def _cleanup_interrupted_sqlite_batch_tables(connection):
    """Remove only stale Alembic batch temp tables whose source table still exists.

    SQLite DDL is non-transactional. If a previous process is interrupted after a
    batch temp table is created, the next `alembic upgrade` otherwise fails with
    `table _alembic_tmp_* already exists`. Keeping the original table as a safety
    precondition prevents accidental deletion of the only surviving copy.
    """
    if connection.dialect.name != 'sqlite':
        return
    names = set(inspect(connection).get_table_names())
    for name in list(names):
        if not name.startswith('_alembic_tmp_'):
            continue
        original = name[len('_alembic_tmp_'):]
        if original in names:
            connection.execute(text(f'DROP TABLE IF EXISTS "{name}"'))


def run_migrations_offline():
    context.configure(url=settings.database_url, target_metadata=target_metadata, literal_binds=True, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    connectable = engine_from_config(config.get_section(config.config_ini_section), prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        _cleanup_interrupted_sqlite_batch_tables(connection)
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
        # SQLite is reported as non-transactional DDL by Alembic; explicitly
        # commit version-table DML so a successful local upgrade is not rerun.
        if connection.in_transaction():
            connection.commit()


run_migrations_offline() if context.is_offline_mode() else run_migrations_online()
