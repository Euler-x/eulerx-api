from sqladmin import ModelView
from sqladmin.filters import AllUniqueStringValuesFilter, BooleanFilter

from app.models.user import User


class UserAdmin(ModelView, model=User):
    name = "User"
    name_plural = "Users"
    icon = "fa-solid fa-users"
    category = "Users & Auth"

    column_list = [
        User.id,
        User.email,
        User.wallet_address,
        User.wallet_type,
        User.is_admin,
        User.is_active,
        User.is_subscribed,
        User.email_verified,
        User.created_at,
    ]

    column_details_exclude_list = [
        User.encrypted_private_key,
        User.password_hash,
        User.telegram_bot_token,
        User.email_verification_code,
        User.password_reset_token,
    ]

    form_excluded_columns = [
        User.encrypted_private_key,
        User.password_hash,
        User.telegram_bot_token,
        User.email_verification_code,
        User.password_reset_token,
        User.password_reset_expires_at,
        User.email_verification_expires_at,
        User.wallet_address_hash,
        User.created_at,
        User.updated_at,
        User.strategies,
        User.executions,
        User.transactions,
        User.subscriptions,
        User.ambassador,
        User.support_tickets,
    ]

    column_searchable_list = [User.email, User.wallet_address]
    column_sortable_list = [
        User.email,
        User.is_admin,
        User.is_active,
        User.is_subscribed,
        User.email_verified,
        User.created_at,
    ]
    column_filters = [
        BooleanFilter("is_admin"),
        BooleanFilter("is_active"),
        BooleanFilter("is_subscribed"),
        BooleanFilter("email_verified"),
        AllUniqueStringValuesFilter("wallet_type"),
    ]

    column_default_sort = (User.created_at, True)
    page_size = 25
    page_size_options = [25, 50, 100]

    can_create = False
    can_delete = False
    can_edit = True
    can_view_details = True
