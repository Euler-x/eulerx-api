from sqladmin import ModelView
from sqladmin.filters import AllUniqueStringValuesFilter

from app.models.ambassador import Ambassador


class AmbassadorAdmin(ModelView, model=Ambassador):
    name = "Ambassador"
    name_plural = "Ambassadors"
    icon = "fa-solid fa-handshake"
    category = "Ambassador"

    column_list = [
        Ambassador.id,
        Ambassador.user_id,
        Ambassador.rank,
        Ambassador.referral_code,
        Ambassador.team_size,
        Ambassador.total_referrals,
        Ambassador.rewards_earned,
        Ambassador.created_at,
    ]

    form_excluded_columns = [
        Ambassador.created_at,
        Ambassador.updated_at,
        Ambassador.user,
        Ambassador.referrals,
        Ambassador.referrer,
    ]

    column_searchable_list = [Ambassador.referral_code]
    column_sortable_list = [
        Ambassador.rank,
        Ambassador.team_size,
        Ambassador.total_referrals,
        Ambassador.rewards_earned,
        Ambassador.created_at,
    ]
    column_filters = [AllUniqueStringValuesFilter("rank")]

    column_default_sort = (Ambassador.created_at, True)
    page_size = 25

    can_create = False
    can_delete = False
    can_edit = True
    can_view_details = True
