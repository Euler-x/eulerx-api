from app.admin.views.users import UserAdmin
from app.admin.views.strategies import StrategyAdmin
from app.admin.views.signals import SignalAdmin
from app.admin.views.executions import ExecutionAdmin
from app.admin.views.transactions import TransactionAdmin
from app.admin.views.billing import PaymentAdmin, PlanAdmin, SubscriptionAdmin
from app.admin.views.ambassadors import AmbassadorAdmin
from app.admin.views.content import LearningContentAdmin
from app.admin.views.support import SupportMessageAdmin, SupportTicketAdmin
from app.admin.views.config import AdminConfigAdmin
from app.admin.views.audit_logs import AuditLogAdmin

__all__ = [
    "UserAdmin",
    "StrategyAdmin",
    "SignalAdmin",
    "ExecutionAdmin",
    "TransactionAdmin",
    "PlanAdmin",
    "SubscriptionAdmin",
    "PaymentAdmin",
    "AmbassadorAdmin",
    "LearningContentAdmin",
    "SupportTicketAdmin",
    "SupportMessageAdmin",
    "AdminConfigAdmin",
    "AuditLogAdmin",
]
