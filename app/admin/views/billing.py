from sqladmin import ModelView
from sqladmin.filters import AllUniqueStringValuesFilter

from app.models.billing import Payment, Plan, Subscription


class PlanAdmin(ModelView, model=Plan):
    name = "Plan"
    name_plural = "Plans"
    icon = "fa-solid fa-tags"
    category = "Billing"

    column_list = [
        Plan.id,
        Plan.name,
        Plan.price_usd,
        Plan.billing_cycle,
        Plan.max_strategies,
        Plan.max_allocation,
        Plan.ate_access,
        Plan.trial_days,
        Plan.status,
    ]

    form_excluded_columns = [
        Plan.created_at,
        Plan.updated_at,
        Plan.subscriptions,
    ]

    column_searchable_list = [Plan.name]
    column_sortable_list = [
        Plan.name,
        Plan.price_usd,
        Plan.status,
        Plan.created_at,
    ]
    column_filters = [
        AllUniqueStringValuesFilter("status"),
        AllUniqueStringValuesFilter("billing_cycle"),
        AllUniqueStringValuesFilter("ate_access"),
    ]

    column_default_sort = (Plan.created_at, True)
    page_size = 25

    can_create = True
    can_delete = False
    can_edit = True
    can_view_details = True


class SubscriptionAdmin(ModelView, model=Subscription):
    name = "Subscription"
    name_plural = "Subscriptions"
    icon = "fa-solid fa-credit-card"
    category = "Billing"

    column_list = [
        Subscription.id,
        Subscription.user_id,
        Subscription.plan_id,
        Subscription.status,
        Subscription.started_at,
        Subscription.expires_at,
        Subscription.grace_until,
        Subscription.created_at,
    ]

    form_excluded_columns = [
        Subscription.created_at,
        Subscription.updated_at,
        Subscription.payments,
        Subscription.user,
        Subscription.plan,
    ]

    column_searchable_list = [Subscription.nowpayments_invoice_id]
    column_sortable_list = [
        Subscription.status,
        Subscription.started_at,
        Subscription.expires_at,
        Subscription.created_at,
    ]
    column_filters = [AllUniqueStringValuesFilter("status")]

    column_default_sort = (Subscription.created_at, True)
    page_size = 25

    can_create = False
    can_delete = False
    can_edit = True
    can_view_details = True


class PaymentAdmin(ModelView, model=Payment):
    name = "Payment"
    name_plural = "Payments"
    icon = "fa-solid fa-receipt"
    category = "Billing"

    column_list = [
        Payment.id,
        Payment.subscription_id,
        Payment.user_id,
        Payment.amount_usd,
        Payment.amount_crypto,
        Payment.crypto_currency,
        Payment.status,
        Payment.paid_at,
        Payment.created_at,
    ]

    column_searchable_list = [
        Payment.nowpayments_payment_id,
        Payment.nowpayments_invoice_id,
    ]
    column_sortable_list = [
        Payment.amount_usd,
        Payment.status,
        Payment.paid_at,
        Payment.created_at,
    ]
    column_filters = [
        AllUniqueStringValuesFilter("status"),
        AllUniqueStringValuesFilter("crypto_currency"),
    ]

    column_default_sort = (Payment.created_at, True)
    page_size = 25

    can_create = False
    can_delete = False
    can_edit = False
    can_view_details = True
