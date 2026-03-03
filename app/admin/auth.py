from sqladmin.authentication import AuthenticationBackend
from sqlalchemy import select
from starlette.requests import Request
from starlette.responses import RedirectResponse

from app.db.base import async_session_factory
from app.models.user import User
from app.utils.security import verify_password


class AdminAuth(AuthenticationBackend):
    """Session-cookie authentication for the SQLAdmin dashboard.

    Login uses the admin's email + password. Only users with
    ``is_admin=True`` and ``is_active=True`` are granted access.
    """

    async def login(self, request: Request) -> bool:
        form = await request.form()
        email = form.get("username")  # sqladmin form field name
        password = form.get("password")

        if not email or not password:
            return False

        async with async_session_factory() as session:
            result = await session.execute(select(User).where(User.email == str(email)))
            user = result.scalar_one_or_none()

            if user is None:
                return False
            if not user.is_admin or not user.is_active:
                return False
            if not user.password_hash:
                return False
            if not verify_password(str(password), user.password_hash):
                return False

            request.session.update({"admin_user_id": str(user.id)})
            return True

    async def logout(self, request: Request) -> bool:
        request.session.clear()
        return True

    async def authenticate(self, request: Request) -> RedirectResponse | bool:
        admin_user_id = request.session.get("admin_user_id")
        if not admin_user_id:
            return RedirectResponse(request.url_for("admin:login"), status_code=302)

        # Re-validate on every page load
        async with async_session_factory() as session:
            result = await session.execute(select(User).where(User.id == admin_user_id))
            user = result.scalar_one_or_none()
            if user is None or not user.is_admin or not user.is_active:
                request.session.clear()
                return RedirectResponse(request.url_for("admin:login"), status_code=302)

        return True
