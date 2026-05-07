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
    ASSOCIATE = "associate"
    BRONZE_LEADER = "bronze_leader"
    SILVER_LEADER = "silver_leader"
    GOLD_LEADER = "gold_leader"
    PLATINUM_LEADER = "platinum_leader"
    DIAMOND_LEADER = "diamond_leader"
    ELITE_DIAMOND = "elite_diamond"
    BLACK_DIAMOND = "black_diamond"
    CROWN_AMBASSADOR = "crown_ambassador"
    GRAND_CROWN = "grand_crown"


class TerritoryType(str, enum.Enum):
    GEOGRAPHIC = "geographic"
    DEMOGRAPHIC = "demographic"
    PLATFORM = "platform"


class CommissionStatus(str, enum.Enum):
    PENDING = "pending"
    PAID = "paid"


class BonusType(str, enum.Enum):
    RANK_ADVANCEMENT = "rank_advancement"
    PERFORMANCE_MILESTONE = "performance_milestone"
    FAST_START = "fast_start"
    LOYALTY_RETENTION = "loyalty_retention"
    LEADERSHIP_POOL = "leadership_pool"
    GENERATIONAL_OVERRIDE = "generational_override"


class TravelStatus(str, enum.Enum):
    QUALIFYING = "qualifying"
    QUALIFIED = "qualified"
    AWARDED = "awarded"
    EXPIRED = "expired"


class PayoutStatus(str, enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    PAID = "paid"
    FAILED = "failed"


class AmbassadorStatus(str, enum.Enum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    UNDER_REVIEW = "under_review"


class ActivityEventType(str, enum.Enum):
    REGISTERED = "registered"
    RANK_CHANGED = "rank_changed"
    COMMISSION_CALCULATED = "commission_calculated"
    COMMISSION_PAID = "commission_paid"
    BONUS_AWARDED = "bonus_awarded"
    BONUS_PAID = "bonus_paid"
    PAYOUT_CREATED = "payout_created"
    PAYOUT_STATUS_CHANGED = "payout_status_changed"
    PAYOUT_CANCELLED = "payout_cancelled"
    REFERRAL_JOINED = "referral_joined"
    TRAVEL_AWARDED = "travel_awarded"
    TRAVEL_STATUS_CHANGED = "travel_status_changed"
    TRAINING_COMPLETED = "training_completed"
    TRAINING_REMOVED = "training_removed"
    STATUS_CHANGED = "status_changed"
    ADMIN_NOTE_ADDED = "admin_note_added"
    POOL_CALCULATED = "pool_calculated"


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


class Exchange(str, enum.Enum):
    HYPERLIQUID = "hyperliquid"
    BYBIT = "bybit"
    BINANCE = "binance"
