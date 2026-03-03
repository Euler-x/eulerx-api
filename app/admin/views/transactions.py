from sqladmin import ModelView
from sqladmin.filters import AllUniqueStringValuesFilter

from app.models.transaction import Transaction


class TransactionAdmin(ModelView, model=Transaction):
    name = "Transaction"
    name_plural = "Transactions"
    icon = "fa-solid fa-money-bill-transfer"
    category = "Financial"

    column_list = [
        Transaction.id,
        Transaction.user_id,
        Transaction.category,
        Transaction.amount,
        Transaction.asset,
        Transaction.status,
        Transaction.tx_hash,
        Transaction.created_at,
    ]

    column_searchable_list = [Transaction.tx_hash, Transaction.asset]
    column_sortable_list = [
        Transaction.category,
        Transaction.amount,
        Transaction.status,
        Transaction.created_at,
    ]
    column_filters = [
        AllUniqueStringValuesFilter("category"),
        AllUniqueStringValuesFilter("status"),
        AllUniqueStringValuesFilter("asset"),
    ]

    column_default_sort = (Transaction.created_at, True)
    page_size = 25

    can_create = False
    can_delete = False
    can_edit = False
    can_view_details = True
