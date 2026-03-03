from sqladmin import ModelView
from sqladmin.filters import AllUniqueStringValuesFilter

from app.models.audit_log import AuditLog


class AuditLogAdmin(ModelView, model=AuditLog):
    name = "Audit Log"
    name_plural = "Audit Logs"
    icon = "fa-solid fa-clipboard-list"
    category = "System"

    column_list = [
        AuditLog.id,
        AuditLog.user_id,
        AuditLog.action,
        AuditLog.resource_type,
        AuditLog.resource_id,
        AuditLog.ip_address,
        AuditLog.created_at,
    ]

    column_searchable_list = [
        AuditLog.action,
        AuditLog.resource_type,
        AuditLog.resource_id,
        AuditLog.ip_address,
    ]
    column_sortable_list = [
        AuditLog.action,
        AuditLog.resource_type,
        AuditLog.created_at,
    ]
    column_filters = [
        AllUniqueStringValuesFilter("action"),
        AllUniqueStringValuesFilter("resource_type"),
    ]

    column_default_sort = (AuditLog.created_at, True)
    page_size = 50

    can_create = False
    can_delete = False
    can_edit = False
    can_view_details = True
