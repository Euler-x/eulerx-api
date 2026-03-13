"""Create the initial admin user for the SQLAdmin dashboard."""

import asyncio
import uuid

from sqlalchemy import text

from app.db.base import engine
from app.utils.security import hash_password

ADMIN_EMAIL = "admin@eulerx.io"
ADMIN_PASSWORD = "QazWsx.123.!@#"


async def main():
    async with engine.begin() as conn:
        row = await conn.execute(
            text("SELECT id, is_admin FROM users WHERE email = :email"),
            {"email": ADMIN_EMAIL},
        )
        existing = row.fetchone()

        if existing:
            if not existing.is_admin:
                await conn.execute(
                    text("UPDATE users SET is_admin = true WHERE email = :email"),
                    {"email": ADMIN_EMAIL},
                )
                print(f"User '{ADMIN_EMAIL}' already exists — promoted to admin.")
            else:
                print(f"Admin user '{ADMIN_EMAIL}' already exists.")
            return

        await conn.execute(
            text(
                "INSERT INTO users (id, email, password_hash, is_admin, is_active, email_verified, created_at, updated_at)"
                " VALUES (:id, :email, :password_hash, true, true, true, now(), now())"
            ),
            {
                "id": str(uuid.uuid4()),
                "email": ADMIN_EMAIL,
                "password_hash": hash_password(ADMIN_PASSWORD),
            },
        )
        print(f"Admin user '{ADMIN_EMAIL}' created successfully.")


if __name__ == "__main__":
    asyncio.run(main())
