"""Re-export all schemas for backward compatibility.

Individual schemas live in their own modules:
  - common.py      PaginatedResponse, MessageResponse, HealthResponse
  - auth.py        Auth request/response schemas
  - strategy.py    Strategy CRUD schemas
  - signal.py      Signal display schemas
  - execution.py   Execution schemas
  - transaction.py Transaction schemas
  - billing.py     Plan, Subscription, Payment schemas
  - ambassador.py  Ambassador schemas
  - content.py     Learning content schemas
  - support.py     Support ticket schemas
  - admin.py       Admin config schemas
"""

from app.models.schemas.common import (  # noqa: F401
    HealthResponse,
    MessageResponse,
    PaginatedResponse,
)
from app.models.schemas.auth import (  # noqa: F401
    AuthResponse,
    RefreshTokenRequest,
    UserResponse,
    WalletConnectRequest,
    WalletGenerateRequest,
    WalletGenerateResponse,
)
from app.models.schemas.strategy import (  # noqa: F401
    StrategyCreate,
    StrategyResponse,
    StrategyUpdate,
)
from app.models.schemas.signal import (  # noqa: F401
    SignalDetailResponse,
    SignalResponse,
)
from app.models.schemas.execution import (  # noqa: F401
    ExecutionResponse,
    ExecutionVerifyResponse,
)
from app.models.schemas.transaction import TransactionResponse  # noqa: F401
from app.models.schemas.billing import (  # noqa: F401
    NOWPaymentsWebhook,
    PaymentResponse,
    PlanCreate,
    PlanResponse,
    PlanUpdate,
    SubscribeRequest,
    SubscriptionOverride,
    SubscriptionResponse,
)
from app.models.schemas.ambassador import (  # noqa: F401
    AmbassadorResponse,
    LeaderboardEntry,
    ReferralResponse,
)
from app.models.schemas.content import (  # noqa: F401
    LearningContentCreate,
    LearningContentResponse,
    LearningContentUpdate,
)
from app.models.schemas.support import (  # noqa: F401
    MessageCreate,
    SupportMessageResponse,
    SupportTicketResponse,
    TicketCreate,
    TicketDetailResponse,
    TicketUpdateStatus,
)
from app.models.schemas.admin import (  # noqa: F401
    AdminConfigResponse,
    AdminConfigUpdate,
)
