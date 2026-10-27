import os
from alembic import command
from alembic.config import Config
from sqlalchemy import MetaData
from app.core.database import engine

def main():
    if os.getenv('CONFIRM_DB_RESET')!='YES':raise SystemExit('Refusing to reset. Run with CONFIRM_DB_RESET=YES.')
    metadata=MetaData();metadata.reflect(bind=engine);metadata.drop_all(bind=engine)
    cfg=Config('alembic.ini');command.upgrade(cfg,'head');print('Development database reset and migrated to head. No sample data was inserted.')
if __name__=='__main__':main()
