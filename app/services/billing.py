import hashlib
import hmac
import logging

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.billing import Payment, Plan, Subscription
from app.models.enums import (
    BillingCycle,
    PaymentStatus,
    SubscriptionStatus,
    TransactionCategory,
    TransactionStatus,
)
from app.models.transaction import Transaction
from app.models.user import User
from app.services.notifications import NotificationService
from app.utils.helpers import add_days, add_months, utc_now

logger = logging.getLogger(__name__)
settings = get_settings()


class BillingService:
    def __init__(self):
        self.api_key = settings.nowpayments_api_key
        self.ipn_secret = settings.nowpayments_ipn_secret
        self.base_url = settings.nowpayments_base_url

    async def create_invoice(
        self,
        price_amount: float,
        price_currency: str = "usd",
        order_id: str = "",
        order_description: str = "",
    ) -> dict | None:
        if not self.api_key:
            logger.warning("NOWPayments API key not configured")
            return None

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{self.base_url}/invoice",
                    headers={
                        "x-api-key": self.api_key,
                        "Content-Type": "application/json",
                    },
                    json={
                        "price_amount": price_amount,
                        "price_currency": price_currency,
                        "order_id": order_id,
                        "order_description": order_description,
                        "ipn_callback_url": "",
                    },
                )
                response.raise_for_status()
                return response.json()
        except Exception as e:
            logger.error(f"Failed to create NOWPayments invoice: {e}")
            return None

    async def check_payment_status(self, payment_id: str) -> dict | None:
        if not self.api_key:
            return None

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(
                    f"{self.base_url}/payment/{payment_id}",
                    headers={"x-api-key": self.api_key},
                )
                response.raise_for_status()
                return response.json()
        except Exception as e:
            logger.error(f"Failed to check payment status: {e}")
            return None

    def verify_webhook_signature(self, payload_bytes: bytes, signature: str) -> bool:
        if not self.ipn_secret:
            logger.warning("NOWPayments IPN secret not configured")
            return False

        expected = hmac.new(
            self.ipn_secret.encode(),
            payload_bytes,
            hashlib.sha512,
        ).hexdigest()

        return hmac.compare_digest(expected, signature)

    async def process_webhook(
        self,
        db: AsyncSession,
        webhook_data: dict,
    ) -> bool:
        payment_id = str(webhook_data.get("payment_id", ""))
        invoice_id = str(webhook_data.get("invoice_id", ""))
        payment_status = webhook_data.get("payment_status", "")

        if not payment_id or not invoice_id:
            logger.warning("Webhook missing payment_id or invoice_id")
            return False

        # Idempotency: check if this payment was already processed
        result = await db.execute(
            select(Payment).where(Payment.nowpayments_payment_id == payment_id)
        )
        existing_payment = result.scalar_one_or_none()

        if existing_payment and existing_payment.status in (
            PaymentStatus.FINISHED,
            PaymentStatus.CONFIRMED,
        ):
            logger.info(f"Payment {payment_id} already processed")
            return True

        # Find subscription by invoice ID
        sub_result = await db.execute(
            select(Subscription).where(
                Subscription.nowpayments_invoice_id == invoice_id
            )
        )
        subscription = sub_result.scalar_one_or_none()

        if not subscription:
            logger.warning(f"No subscription found for invoice {invoice_id}")
            return False

        # Map NOWPayments status to our status
        status_map = {
            "waiting": PaymentStatus.WAITING,
            "confirming": PaymentStatus.CONFIRMING,
            "confirmed": PaymentStatus.CONFIRMED,
            "sending": PaymentStatus.SENDING,
            "partially_paid": PaymentStatus.PARTIALLY_PAID,
            "finished": PaymentStatus.FINISHED,
            "failed": PaymentStatus.FAILED,
            "refunded": PaymentStatus.REFUNDED,
            "expired": PaymentStatus.EXPIRED,
        }

        mapped_status = status_map.get(payment_status, PaymentStatus.WAITING)

        if existing_payment:
            existing_payment.status = mapped_status
            if mapped_status == PaymentStatus.FINISHED:
                existing_payment.paid_at = utc_now()
                existing_payment.amount_crypto = webhook_data.get("pay_amount")
                existing_payment.crypto_currency = webhook_data.get("pay_currency")
        else:
            payment = Payment(
                subscription_id=subscription.id,
                user_id=subscription.user_id,
                amount_usd=webhook_data.get("price_amount", 0),
                amount_crypto=webhook_data.get("pay_amount"),
                crypto_currency=webhook_data.get("pay_currency"),
                nowpayments_payment_id=payment_id,
                nowpayments_invoice_id=invoice_id,
                status=mapped_status,
                paid_at=utc_now() if mapped_status == PaymentStatus.FINISHED else None,
            )
            db.add(payment)

        # Activate subscription on successful payment
        if mapped_status in (PaymentStatus.FINISHED, PaymentStatus.CONFIRMED):
            await self._activate_subscription(db, subscription)

        return True

    async def _activate_subscription(
        self,
        db: AsyncSession,
        subscription: Subscription,
    ) -> None:
        now = utc_now()
        subscription.status = SubscriptionStatus.ACTIVE
        subscription.started_at = now

        # Fetch plan to determine billing cycle
        plan_result = await db.execute(
            select(Plan).where(Plan.id == subscription.plan_id)
        )
        plan = plan_result.scalar_one_or_none()

        if plan:
            if plan.billing_cycle == BillingCycle.MONTHLY:
                subscription.expires_at = add_months(now, 1)
            elif plan.billing_cycle == BillingCycle.QUARTERLY:
                subscription.expires_at = add_months(now, 3)
            elif plan.billing_cycle == BillingCycle.YEARLY:
                subscription.expires_at = add_months(now, 12)

            subscription.grace_until = add_days(
                subscription.expires_at,
                settings.subscription_grace_period_days,
            )

        # Log transaction
        transaction = Transaction(
            user_id=subscription.user_id,
            category=TransactionCategory.SUBSCRIPTION,
            amount=float(plan.price_usd) if plan else 0,
            asset="USD",
            wallet_address_hash="",
            status=TransactionStatus.CONFIRMED,
            description=f"Subscription activated: {plan.name}"
            if plan
            else "Subscription activated",
        )
        db.add(transaction)

        # Send subscription activated email
        try:
            user_result = await db.execute(
                select(User).where(User.id == subscription.user_id)
            )
            user = user_result.scalar_one_or_none()
            if user:
                notification_service = NotificationService()
                expires_str = (
                    subscription.expires_at.strftime("%B %d, %Y")
                    if subscription.expires_at
                    else "N/A"
                )
                await notification_service.send_subscription_activated(
                    user=user,
                    plan_name=plan.name if plan else "Unknown",
                    billing_cycle=plan.billing_cycle.value if plan else "N/A",
                    expires_at=expires_str,
                )
        except Exception as e:
            logger.error("Failed to send subscription activated email: %s", e)

    @staticmethod
    async def get_active_subscription(
        db: AsyncSession, user_id: str
    ) -> Subscription | None:
        result = await db.execute(
            select(Subscription)
            .where(Subscription.user_id == user_id)
            .where(
                Subscription.status.in_(
                    [
                        SubscriptionStatus.ACTIVE,
                        SubscriptionStatus.EXPIRING_SOON,
                    ]
                )
            )
            .order_by(Subscription.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()
