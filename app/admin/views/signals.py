from sqladmin import ModelView
from sqladmin.filters import AllUniqueStringValuesFilter

from app.models.signal import Signal


class SignalAdmin(ModelView, model=Signal):
    name = "Signal"
    name_plural = "Signals"
    icon = "fa-solid fa-signal"
    category = "Trading"

    column_list = [
        Signal.id,
        Signal.symbol,
        Signal.direction,
        Signal.confidence,
        Signal.entry_price,
        Signal.status,
        Signal.strategy_id,
        Signal.created_at,
    ]

    form_excluded_columns = [
        Signal.created_at,
        Signal.updated_at,
        Signal.executions,
        Signal.strategy,
    ]

    column_searchable_list = [Signal.symbol]
    column_sortable_list = [
        Signal.symbol,
        Signal.direction,
        Signal.confidence,
        Signal.status,
        Signal.created_at,
    ]
    column_filters = [
        AllUniqueStringValuesFilter("symbol"),
        AllUniqueStringValuesFilter("direction"),
        AllUniqueStringValuesFilter("status"),
    ]

    column_default_sort = (Signal.created_at, True)
    page_size = 25

    can_create = False
    can_delete = False
    can_edit = True
    can_view_details = True
