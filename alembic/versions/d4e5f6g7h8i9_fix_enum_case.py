"""Fix enum case: rename all DB enum values from UPPERCASE to lowercase

The initial migration created all PostgreSQL enum types with uppercase labels
(e.g. 'CONSERVATIVE', 'LOW', 'ACTIVE') but the Python str-enums define
lowercase values (e.g. 'conservative', 'low', 'active').  SQLAlchemy maps
by value, so reads and writes were failing for every enum column.

ALTER TYPE ... RENAME VALUE is safe: existing rows automatically reference
the new label without any data loss.

Revision ID: d4e5f6g7h8i9
Revises: c3d4e5f6g7h8
Create Date: 2026-02-26

"""

from typing import Sequence, Union

from alembic import op

revision: str = "d4e5f6g7h8i9"
down_revision: Union[str, None] = "c3d4e5f6g7h8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # strategy_type_enum
    op.execute(
        "ALTER TYPE strategy_type_enum RENAME VALUE 'CONSERVATIVE' TO 'conservative'"
    )
    op.execute("ALTER TYPE strategy_type_enum RENAME VALUE 'MODERATE' TO 'moderate'")
    op.execute(
        "ALTER TYPE strategy_type_enum RENAME VALUE 'AGGRESSIVE' TO 'aggressive'"
    )
    op.execute("ALTER TYPE strategy_type_enum RENAME VALUE 'CUSTOM' TO 'custom'")

    # risk_profile_enum
    op.execute("ALTER TYPE risk_profile_enum RENAME VALUE 'LOW' TO 'low'")
    op.execute("ALTER TYPE risk_profile_enum RENAME VALUE 'MEDIUM' TO 'medium'")
    op.execute("ALTER TYPE risk_profile_enum RENAME VALUE 'HIGH' TO 'high'")

    # wallet_type_enum
    op.execute("ALTER TYPE wallet_type_enum RENAME VALUE 'CONNECTED' TO 'connected'")
    op.execute("ALTER TYPE wallet_type_enum RENAME VALUE 'GENERATED' TO 'generated'")

    # billing_cycle_enum
    op.execute("ALTER TYPE billing_cycle_enum RENAME VALUE 'MONTHLY' TO 'monthly'")
    op.execute("ALTER TYPE billing_cycle_enum RENAME VALUE 'QUARTERLY' TO 'quarterly'")
    op.execute("ALTER TYPE billing_cycle_enum RENAME VALUE 'YEARLY' TO 'yearly'")

    # plan_status_enum
    op.execute("ALTER TYPE plan_status_enum RENAME VALUE 'ACTIVE' TO 'active'")
    op.execute("ALTER TYPE plan_status_enum RENAME VALUE 'INACTIVE' TO 'inactive'")
    op.execute("ALTER TYPE plan_status_enum RENAME VALUE 'ARCHIVED' TO 'archived'")

    # subscription_status_enum
    op.execute(
        "ALTER TYPE subscription_status_enum RENAME VALUE 'INACTIVE' TO 'inactive'"
    )
    op.execute(
        "ALTER TYPE subscription_status_enum RENAME VALUE 'PENDING_PAYMENT' TO 'pending_payment'"
    )
    op.execute("ALTER TYPE subscription_status_enum RENAME VALUE 'ACTIVE' TO 'active'")
    op.execute(
        "ALTER TYPE subscription_status_enum RENAME VALUE 'EXPIRING_SOON' TO 'expiring_soon'"
    )
    op.execute(
        "ALTER TYPE subscription_status_enum RENAME VALUE 'EXPIRED' TO 'expired'"
    )
    op.execute(
        "ALTER TYPE subscription_status_enum RENAME VALUE 'CANCELLED' TO 'cancelled'"
    )

    # payment_status_enum
    op.execute("ALTER TYPE payment_status_enum RENAME VALUE 'WAITING' TO 'waiting'")
    op.execute(
        "ALTER TYPE payment_status_enum RENAME VALUE 'CONFIRMING' TO 'confirming'"
    )
    op.execute("ALTER TYPE payment_status_enum RENAME VALUE 'CONFIRMED' TO 'confirmed'")
    op.execute("ALTER TYPE payment_status_enum RENAME VALUE 'SENDING' TO 'sending'")
    op.execute(
        "ALTER TYPE payment_status_enum RENAME VALUE 'PARTIALLY_PAID' TO 'partially_paid'"
    )
    op.execute("ALTER TYPE payment_status_enum RENAME VALUE 'FINISHED' TO 'finished'")
    op.execute("ALTER TYPE payment_status_enum RENAME VALUE 'FAILED' TO 'failed'")
    op.execute("ALTER TYPE payment_status_enum RENAME VALUE 'REFUNDED' TO 'refunded'")
    op.execute("ALTER TYPE payment_status_enum RENAME VALUE 'EXPIRED' TO 'expired'")

    # signal_direction_enum
    op.execute("ALTER TYPE signal_direction_enum RENAME VALUE 'BUY' TO 'buy'")
    op.execute("ALTER TYPE signal_direction_enum RENAME VALUE 'SELL' TO 'sell'")
    op.execute("ALTER TYPE signal_direction_enum RENAME VALUE 'HOLD' TO 'hold'")

    # signal_status_enum
    op.execute("ALTER TYPE signal_status_enum RENAME VALUE 'NEW' TO 'new'")
    op.execute("ALTER TYPE signal_status_enum RENAME VALUE 'EXECUTING' TO 'executing'")
    op.execute("ALTER TYPE signal_status_enum RENAME VALUE 'FILLED' TO 'filled'")
    op.execute("ALTER TYPE signal_status_enum RENAME VALUE 'EXPIRED' TO 'expired'")
    op.execute("ALTER TYPE signal_status_enum RENAME VALUE 'CANCELLED' TO 'cancelled'")

    # order_type_enum
    op.execute("ALTER TYPE order_type_enum RENAME VALUE 'MARKET' TO 'market'")
    op.execute("ALTER TYPE order_type_enum RENAME VALUE 'LIMIT' TO 'limit'")

    # execution_status_enum
    op.execute("ALTER TYPE execution_status_enum RENAME VALUE 'PENDING' TO 'pending'")
    op.execute("ALTER TYPE execution_status_enum RENAME VALUE 'FILLED' TO 'filled'")
    op.execute(
        "ALTER TYPE execution_status_enum RENAME VALUE 'PARTIALLY_FILLED' TO 'partially_filled'"
    )
    op.execute("ALTER TYPE execution_status_enum RENAME VALUE 'CLOSED' TO 'closed'")
    op.execute(
        "ALTER TYPE execution_status_enum RENAME VALUE 'CANCELLED' TO 'cancelled'"
    )
    op.execute("ALTER TYPE execution_status_enum RENAME VALUE 'FAILED' TO 'failed'")

    # transaction_category_enum
    op.execute(
        "ALTER TYPE transaction_category_enum RENAME VALUE 'DEPOSIT' TO 'deposit'"
    )
    op.execute(
        "ALTER TYPE transaction_category_enum RENAME VALUE 'EXECUTION' TO 'execution'"
    )
    op.execute(
        "ALTER TYPE transaction_category_enum RENAME VALUE 'SUBSCRIPTION' TO 'subscription'"
    )
    op.execute("ALTER TYPE transaction_category_enum RENAME VALUE 'REWARD' TO 'reward'")

    # transaction_status_enum
    op.execute("ALTER TYPE transaction_status_enum RENAME VALUE 'PENDING' TO 'pending'")
    op.execute(
        "ALTER TYPE transaction_status_enum RENAME VALUE 'CONFIRMED' TO 'confirmed'"
    )
    op.execute("ALTER TYPE transaction_status_enum RENAME VALUE 'FAILED' TO 'failed'")

    # ticket_status_enum
    op.execute("ALTER TYPE ticket_status_enum RENAME VALUE 'OPEN' TO 'open'")
    op.execute(
        "ALTER TYPE ticket_status_enum RENAME VALUE 'IN_PROGRESS' TO 'in_progress'"
    )
    op.execute("ALTER TYPE ticket_status_enum RENAME VALUE 'RESOLVED' TO 'resolved'")
    op.execute("ALTER TYPE ticket_status_enum RENAME VALUE 'CLOSED' TO 'closed'")

    # ticket_priority_enum
    op.execute("ALTER TYPE ticket_priority_enum RENAME VALUE 'LOW' TO 'low'")
    op.execute("ALTER TYPE ticket_priority_enum RENAME VALUE 'MEDIUM' TO 'medium'")
    op.execute("ALTER TYPE ticket_priority_enum RENAME VALUE 'HIGH' TO 'high'")
    op.execute("ALTER TYPE ticket_priority_enum RENAME VALUE 'URGENT' TO 'urgent'")

    # ambassador_rank_enum
    op.execute("ALTER TYPE ambassador_rank_enum RENAME VALUE 'BRONZE' TO 'bronze'")
    op.execute("ALTER TYPE ambassador_rank_enum RENAME VALUE 'SILVER' TO 'silver'")
    op.execute("ALTER TYPE ambassador_rank_enum RENAME VALUE 'GOLD' TO 'gold'")
    op.execute("ALTER TYPE ambassador_rank_enum RENAME VALUE 'PLATINUM' TO 'platinum'")
    op.execute("ALTER TYPE ambassador_rank_enum RENAME VALUE 'DIAMOND' TO 'diamond'")

    # content_category_enum
    op.execute(
        "ALTER TYPE content_category_enum RENAME VALUE 'CRYPTO_BASICS' TO 'crypto_basics'"
    )
    op.execute(
        "ALTER TYPE content_category_enum RENAME VALUE 'AI_TRADING' TO 'ai_trading'"
    )
    op.execute(
        "ALTER TYPE content_category_enum RENAME VALUE 'RISK_MANAGEMENT' TO 'risk_management'"
    )
    op.execute(
        "ALTER TYPE content_category_enum RENAME VALUE 'AUTOMATED_TRADING' TO 'automated_trading'"
    )
    op.execute(
        "ALTER TYPE content_category_enum RENAME VALUE 'PLATFORM_GUIDE' TO 'platform_guide'"
    )

    # content_type_enum
    op.execute("ALTER TYPE content_type_enum RENAME VALUE 'VIDEO' TO 'video'")
    op.execute("ALTER TYPE content_type_enum RENAME VALUE 'ARTICLE' TO 'article'")
    op.execute("ALTER TYPE content_type_enum RENAME VALUE 'PDF' TO 'pdf'")


def downgrade() -> None:
    # strategy_type_enum
    op.execute(
        "ALTER TYPE strategy_type_enum RENAME VALUE 'conservative' TO 'CONSERVATIVE'"
    )
    op.execute("ALTER TYPE strategy_type_enum RENAME VALUE 'moderate' TO 'MODERATE'")
    op.execute(
        "ALTER TYPE strategy_type_enum RENAME VALUE 'aggressive' TO 'AGGRESSIVE'"
    )
    op.execute("ALTER TYPE strategy_type_enum RENAME VALUE 'custom' TO 'CUSTOM'")

    # risk_profile_enum
    op.execute("ALTER TYPE risk_profile_enum RENAME VALUE 'low' TO 'LOW'")
    op.execute("ALTER TYPE risk_profile_enum RENAME VALUE 'medium' TO 'MEDIUM'")
    op.execute("ALTER TYPE risk_profile_enum RENAME VALUE 'high' TO 'HIGH'")

    # wallet_type_enum
    op.execute("ALTER TYPE wallet_type_enum RENAME VALUE 'connected' TO 'CONNECTED'")
    op.execute("ALTER TYPE wallet_type_enum RENAME VALUE 'generated' TO 'GENERATED'")

    # billing_cycle_enum
    op.execute("ALTER TYPE billing_cycle_enum RENAME VALUE 'monthly' TO 'MONTHLY'")
    op.execute("ALTER TYPE billing_cycle_enum RENAME VALUE 'quarterly' TO 'QUARTERLY'")
    op.execute("ALTER TYPE billing_cycle_enum RENAME VALUE 'yearly' TO 'YEARLY'")

    # plan_status_enum
    op.execute("ALTER TYPE plan_status_enum RENAME VALUE 'active' TO 'ACTIVE'")
    op.execute("ALTER TYPE plan_status_enum RENAME VALUE 'inactive' TO 'INACTIVE'")
    op.execute("ALTER TYPE plan_status_enum RENAME VALUE 'archived' TO 'ARCHIVED'")

    # subscription_status_enum
    op.execute(
        "ALTER TYPE subscription_status_enum RENAME VALUE 'inactive' TO 'INACTIVE'"
    )
    op.execute(
        "ALTER TYPE subscription_status_enum RENAME VALUE 'pending_payment' TO 'PENDING_PAYMENT'"
    )
    op.execute("ALTER TYPE subscription_status_enum RENAME VALUE 'active' TO 'ACTIVE'")
    op.execute(
        "ALTER TYPE subscription_status_enum RENAME VALUE 'expiring_soon' TO 'EXPIRING_SOON'"
    )
    op.execute(
        "ALTER TYPE subscription_status_enum RENAME VALUE 'expired' TO 'EXPIRED'"
    )
    op.execute(
        "ALTER TYPE subscription_status_enum RENAME VALUE 'cancelled' TO 'CANCELLED'"
    )

    # payment_status_enum
    op.execute("ALTER TYPE payment_status_enum RENAME VALUE 'waiting' TO 'WAITING'")
    op.execute(
        "ALTER TYPE payment_status_enum RENAME VALUE 'confirming' TO 'CONFIRMING'"
    )
    op.execute("ALTER TYPE payment_status_enum RENAME VALUE 'confirmed' TO 'CONFIRMED'")
    op.execute("ALTER TYPE payment_status_enum RENAME VALUE 'sending' TO 'SENDING'")
    op.execute(
        "ALTER TYPE payment_status_enum RENAME VALUE 'partially_paid' TO 'PARTIALLY_PAID'"
    )
    op.execute("ALTER TYPE payment_status_enum RENAME VALUE 'finished' TO 'FINISHED'")
    op.execute("ALTER TYPE payment_status_enum RENAME VALUE 'failed' TO 'FAILED'")
    op.execute("ALTER TYPE payment_status_enum RENAME VALUE 'refunded' TO 'REFUNDED'")
    op.execute("ALTER TYPE payment_status_enum RENAME VALUE 'expired' TO 'EXPIRED'")

    # signal_direction_enum
    op.execute("ALTER TYPE signal_direction_enum RENAME VALUE 'buy' TO 'BUY'")
    op.execute("ALTER TYPE signal_direction_enum RENAME VALUE 'sell' TO 'SELL'")
    op.execute("ALTER TYPE signal_direction_enum RENAME VALUE 'hold' TO 'HOLD'")

    # signal_status_enum
    op.execute("ALTER TYPE signal_status_enum RENAME VALUE 'new' TO 'NEW'")
    op.execute("ALTER TYPE signal_status_enum RENAME VALUE 'executing' TO 'EXECUTING'")
    op.execute("ALTER TYPE signal_status_enum RENAME VALUE 'filled' TO 'FILLED'")
    op.execute("ALTER TYPE signal_status_enum RENAME VALUE 'expired' TO 'EXPIRED'")
    op.execute("ALTER TYPE signal_status_enum RENAME VALUE 'cancelled' TO 'CANCELLED'")

    # order_type_enum
    op.execute("ALTER TYPE order_type_enum RENAME VALUE 'market' TO 'MARKET'")
    op.execute("ALTER TYPE order_type_enum RENAME VALUE 'limit' TO 'LIMIT'")

    # execution_status_enum
    op.execute("ALTER TYPE execution_status_enum RENAME VALUE 'pending' TO 'PENDING'")
    op.execute("ALTER TYPE execution_status_enum RENAME VALUE 'filled' TO 'FILLED'")
    op.execute(
        "ALTER TYPE execution_status_enum RENAME VALUE 'partially_filled' TO 'PARTIALLY_FILLED'"
    )
    op.execute("ALTER TYPE execution_status_enum RENAME VALUE 'closed' TO 'CLOSED'")
    op.execute(
        "ALTER TYPE execution_status_enum RENAME VALUE 'cancelled' TO 'CANCELLED'"
    )
    op.execute("ALTER TYPE execution_status_enum RENAME VALUE 'failed' TO 'FAILED'")

    # transaction_category_enum
    op.execute(
        "ALTER TYPE transaction_category_enum RENAME VALUE 'deposit' TO 'DEPOSIT'"
    )
    op.execute(
        "ALTER TYPE transaction_category_enum RENAME VALUE 'execution' TO 'EXECUTION'"
    )
    op.execute(
        "ALTER TYPE transaction_category_enum RENAME VALUE 'subscription' TO 'SUBSCRIPTION'"
    )
    op.execute("ALTER TYPE transaction_category_enum RENAME VALUE 'reward' TO 'REWARD'")

    # transaction_status_enum
    op.execute("ALTER TYPE transaction_status_enum RENAME VALUE 'pending' TO 'PENDING'")
    op.execute(
        "ALTER TYPE transaction_status_enum RENAME VALUE 'confirmed' TO 'CONFIRMED'"
    )
    op.execute("ALTER TYPE transaction_status_enum RENAME VALUE 'failed' TO 'FAILED'")

    # ticket_status_enum
    op.execute("ALTER TYPE ticket_status_enum RENAME VALUE 'open' TO 'OPEN'")
    op.execute(
        "ALTER TYPE ticket_status_enum RENAME VALUE 'in_progress' TO 'IN_PROGRESS'"
    )
    op.execute("ALTER TYPE ticket_status_enum RENAME VALUE 'resolved' TO 'RESOLVED'")
    op.execute("ALTER TYPE ticket_status_enum RENAME VALUE 'closed' TO 'CLOSED'")

    # ticket_priority_enum
    op.execute("ALTER TYPE ticket_priority_enum RENAME VALUE 'low' TO 'LOW'")
    op.execute("ALTER TYPE ticket_priority_enum RENAME VALUE 'medium' TO 'MEDIUM'")
    op.execute("ALTER TYPE ticket_priority_enum RENAME VALUE 'high' TO 'HIGH'")
    op.execute("ALTER TYPE ticket_priority_enum RENAME VALUE 'urgent' TO 'URGENT'")

    # ambassador_rank_enum
    op.execute("ALTER TYPE ambassador_rank_enum RENAME VALUE 'bronze' TO 'BRONZE'")
    op.execute("ALTER TYPE ambassador_rank_enum RENAME VALUE 'silver' TO 'SILVER'")
    op.execute("ALTER TYPE ambassador_rank_enum RENAME VALUE 'gold' TO 'GOLD'")
    op.execute("ALTER TYPE ambassador_rank_enum RENAME VALUE 'platinum' TO 'PLATINUM'")
    op.execute("ALTER TYPE ambassador_rank_enum RENAME VALUE 'diamond' TO 'DIAMOND'")

    # content_category_enum
    op.execute(
        "ALTER TYPE content_category_enum RENAME VALUE 'crypto_basics' TO 'CRYPTO_BASICS'"
    )
    op.execute(
        "ALTER TYPE content_category_enum RENAME VALUE 'ai_trading' TO 'AI_TRADING'"
    )
    op.execute(
        "ALTER TYPE content_category_enum RENAME VALUE 'risk_management' TO 'RISK_MANAGEMENT'"
    )
    op.execute(
        "ALTER TYPE content_category_enum RENAME VALUE 'automated_trading' TO 'AUTOMATED_TRADING'"
    )
    op.execute(
        "ALTER TYPE content_category_enum RENAME VALUE 'platform_guide' TO 'PLATFORM_GUIDE'"
    )

    # content_type_enum
    op.execute("ALTER TYPE content_type_enum RENAME VALUE 'video' TO 'VIDEO'")
    op.execute("ALTER TYPE content_type_enum RENAME VALUE 'article' TO 'ARTICLE'")
    op.execute("ALTER TYPE content_type_enum RENAME VALUE 'pdf' TO 'PDF'")
