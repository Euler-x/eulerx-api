from sqladmin import ModelView
from sqladmin.filters import AllUniqueStringValuesFilter, BooleanFilter

from app.models.strategy import Strategy


class StrategyAdmin(ModelView, model=Strategy):
    name = "Strategy"
    name_plural = "Strategies"
    icon = "fa-solid fa-chess"
    category = "Trading"

    column_list = [
        Strategy.id,
        Strategy.name,
        Strategy.user_id,
        Strategy.strategy_type,
        Strategy.risk_profile,
        Strategy.capital_allocation,
        Strategy.is_active,
        Strategy.timeframe,
        Strategy.created_at,
    ]

    form_excluded_columns = [
        Strategy.created_at,
        Strategy.updated_at,
        Strategy.signals,
        Strategy.executions,
        Strategy.user,
    ]

    column_searchable_list = [Strategy.name]
    column_sortable_list = [
        Strategy.name,
        Strategy.strategy_type,
        Strategy.capital_allocation,
        Strategy.is_active,
        Strategy.created_at,
    ]
    column_filters = [
        AllUniqueStringValuesFilter("strategy_type"),
        AllUniqueStringValuesFilter("risk_profile"),
        BooleanFilter("is_active"),
        AllUniqueStringValuesFilter("timeframe"),
    ]

    column_default_sort = (Strategy.created_at, True)
    page_size = 25

    can_create = False
    can_delete = False
    can_edit = True
    can_view_details = True
