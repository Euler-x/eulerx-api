import enum


class WalletType(str, enum.Enum):
    CONNECTED = "connected"
    GENERATED = "generated"


class StrategyType(str, enum.Enum):
    CONSERVATIVE = "conservative"
    MODERATE = "moderate"
    AGGRESSIVE = "aggressive"
    CUSTOM = "custom"


class RiskProfile(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class StrategyTimeframe(str, enum.Enum):
    SCALPING = "scalping"
    INTRADAY = "intraday"
    SWING = "swing"
    POSITION = "position"


class SignalDirection(str, enum.Enum):
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"


class SignalStatus(str, enum.Enum):
    NEW = "new"
    EXECUTING = "executing"
    FILLED = "filled"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class OrderType(str, enum.Enum):
    MARKET = "market"
    LIMIT = "limit"


class ExecutionStatus(str, enum.Enum):
    PENDING = "pending"
    FILLED = "filled"
    PARTIALLY_FILLED = "partially_filled"
    CLOSED = "closed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class TransactionCategory(str, enum.Enum):
    DEPOSIT = "deposit"
    EXECUTION = "execution"
    SUBSCRIPTION = "subscription"
    REWARD = "reward"


class TransactionStatus(str, enum.Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    FAILED = "failed"


class SubscriptionStatus(str, enum.Enum):
    INACTIVE = "inactive"
    PENDING_PAYMENT = "pending_payment"
    ACTIVE = "active"
    EXPIRING_SOON = "expiring_soon"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class PaymentStatus(str, enum.Enum):
    WAITING = "waiting"
    CONFIRMING = "confirming"
    CONFIRMED = "confirmed"
    SENDING = "sending"
    PARTIALLY_PAID = "partially_paid"
    FINISHED = "finished"
    FAILED = "failed"
    REFUNDED = "refunded"
    EXPIRED = "expired"


class PlanStatus(str, enum.Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    ARCHIVED = "archived"


class BillingCycle(str, enum.Enum):
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    YEARLY = "yearly"


class AmbassadorRank(str, enum.Enum):
    BRONZE = "bronze"
    SILVER = "silver"
    GOLD = "gold"
    PLATINUM = "platinum"
    DIAMOND = "diamond"


class TicketStatus(str, enum.Enum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"
    CLOSED = "closed"


class TicketPriority(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"


class ContentCategory(str, enum.Enum):
    CRYPTO_BASICS = "crypto_basics"
    AI_TRADING = "ai_trading"
    RISK_MANAGEMENT = "risk_management"
    AUTOMATED_TRADING = "automated_trading"
    PLATFORM_GUIDE = "platform_guide"


class ContentType(str, enum.Enum):
    VIDEO = "video"
    ARTICLE = "article"
    PDF = "pdf"
