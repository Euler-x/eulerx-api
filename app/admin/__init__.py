from sqladmin import Admin
from starlette.middleware.sessions import SessionMiddleware

from app.admin.auth import AdminAuth
from app.admin.views import (
    AdminConfigAdmin,
    AmbassadorAdmin,
    AuditLogAdmin,
    ExecutionAdmin,
    LearningContentAdmin,
    PaymentAdmin,
    PlanAdmin,
    SignalAdmin,
    StrategyAdmin,
    SubscriptionAdmin,
    SupportMessageAdmin,
    SupportTicketAdmin,
    TransactionAdmin,
    UserAdmin,
)
from app.config import get_settings
from app.db.base import engine


def setup_admin(app):
    settings = get_settings()

    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.jwt_secret_key,
        session_cookie="eulerx-admin-session",
        max_age=8 * 60 * 60,  # 8 hours
        same_site="lax",
        https_only=settings.environment == "production",
    )

    authentication_backend = AdminAuth(secret_key=settings.jwt_secret_key)

    admin = Admin(
        app,
        engine,
        authentication_backend=authentication_backend,
        title="EulerX Admin",
        base_url="/admin",
    )

    # Users & Auth
    admin.add_view(UserAdmin)

    # Trading
    admin.add_view(StrategyAdmin)
    admin.add_view(SignalAdmin)
    admin.add_view(ExecutionAdmin)

    # Financial
    admin.add_view(TransactionAdmin)

    # Billing
    admin.add_view(PlanAdmin)
    admin.add_view(SubscriptionAdmin)
    admin.add_view(PaymentAdmin)

    # Ambassador
    admin.add_view(AmbassadorAdmin)

    # Content & Support
    admin.add_view(LearningContentAdmin)
    admin.add_view(SupportTicketAdmin)
    admin.add_view(SupportMessageAdmin)

    # System
    admin.add_view(AdminConfigAdmin)
    admin.add_view(AuditLogAdmin)
