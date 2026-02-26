"""Admin router package — split into focused sub-modules.

Sub-modules:
  - plans.py         Plan CRUD
  - config.py        Admin config management
  - users.py         User management
  - subscriptions.py Subscription management
  - content.py       Learning content CRUD
  - tickets.py       Support ticket management
  - analytics.py     Revenue analytics
  - trading.py       Emergency trading halt (kill switch)
  - signals.py       Signal management
  - executions.py    Execution read-only views
  - strategies.py    Strategy CRUD
  - transactions.py  Transaction read-only views
  - ambassadors.py   Ambassador management
  - audit_logs.py    Audit log read-only views
"""

from fastapi import APIRouter, Depends

from app.middleware.permissions import _require_admin
from app.routers.admin.plans import router as plans_router
from app.routers.admin.config import router as config_router
from app.routers.admin.users import router as users_router
from app.routers.admin.subscriptions import router as subscriptions_router
from app.routers.admin.content import router as content_router
from app.routers.admin.tickets import router as tickets_router
from app.routers.admin.analytics import router as analytics_router
from app.routers.admin.trading import router as trading_router
from app.routers.admin.signals import router as signals_router
from app.routers.admin.executions import router as executions_router
from app.routers.admin.strategies import router as strategies_router
from app.routers.admin.transactions import router as transactions_router
from app.routers.admin.ambassadors import router as ambassadors_router
from app.routers.admin.audit_logs import router as audit_logs_router

router = APIRouter(
    prefix="/admin",
    tags=["Admin"],
    dependencies=[Depends(_require_admin)],
)

router.include_router(plans_router)
router.include_router(config_router)
router.include_router(users_router)
router.include_router(subscriptions_router)
router.include_router(content_router)
router.include_router(tickets_router)
router.include_router(analytics_router)
router.include_router(trading_router)
router.include_router(signals_router)
router.include_router(executions_router)
router.include_router(strategies_router)
router.include_router(transactions_router)
router.include_router(ambassadors_router)
router.include_router(audit_logs_router)
