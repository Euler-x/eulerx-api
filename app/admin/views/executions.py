from sqladmin import ModelView
from sqladmin.filters import AllUniqueStringValuesFilter

from app.models.execution import Execution


class ExecutionAdmin(ModelView, model=Execution):
    name = "Execution"
    name_plural = "Executions"
    icon = "fa-solid fa-bolt"
    category = "Trading"

    column_list = [
        Execution.id,
        Execution.user_id,
        Execution.strategy_id,
        Execution.direction,
        Execution.order_type,
        Execution.entry_price,
        Execution.exit_price,
        Execution.quantity,
        Execution.leverage,
        Execution.pnl,
        Execution.status,
        Execution.created_at,
    ]

    column_searchable_list = [Execution.tx_hash]
    column_sortable_list = [
        Execution.direction,
        Execution.status,
        Execution.pnl,
        Execution.created_at,
    ]
    column_filters = [
        AllUniqueStringValuesFilter("status"),
        AllUniqueStringValuesFilter("direction"),
        AllUniqueStringValuesFilter("order_type"),
    ]

    column_default_sort = (Execution.created_at, True)
    page_size = 25

    can_create = False
    can_delete = False
    can_edit = False
    can_view_details = True
