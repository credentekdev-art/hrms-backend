from sqlalchemy import select
from app.core.config import settings
from app.core.database import SessionLocal
from app.core.security import hash_password
from app.models.entities import Role,User

def main():
    if not settings.admin_email or not settings.admin_password:
        raise SystemExit('Set ADMIN_EMAIL and ADMIN_PASSWORD before running the bootstrap command.')
    if len(settings.admin_password)<12:
        raise SystemExit('ADMIN_PASSWORD must contain at least 12 characters.')
    db=SessionLocal()
    try:
        existing=db.execute(select(User).where(User.email==settings.admin_email.strip().lower())).scalar_one_or_none()
        if existing:
            if existing.role!=Role.ADMIN:raise SystemExit('ADMIN_EMAIL already belongs to a non-admin user; no privilege change was made.')
            print(f'Admin already exists: {existing.email}');return
        user=User(email=settings.admin_email.strip().lower(),full_name=settings.admin_name.strip() or 'Initial Administrator',password_hash=hash_password(settings.admin_password),role=Role.ADMIN,is_active=True)
        db.add(user);db.commit();print(f'Created initial admin: {user.email}')
    finally:db.close()
if __name__=='__main__':main()
