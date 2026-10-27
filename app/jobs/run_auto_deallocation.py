from app.core.database import SessionLocal
from app.jobs.auto_deallocate import run_auto_deallocation


def main():
    db = SessionLocal()
    try:
        print(run_auto_deallocation(db))
    finally:
        db.close()


if __name__ == "__main__":
    main()
