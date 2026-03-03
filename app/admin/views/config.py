from sqladmin import ModelView

from app.models.admin_config import AdminConfig


class AdminConfigAdmin(ModelView, model=AdminConfig):
    name = "Admin Config"
    name_plural = "Admin Configs"
    icon = "fa-solid fa-gear"
    category = "System"

    column_list = [
        AdminConfig.id,
        AdminConfig.key,
        AdminConfig.description,
        AdminConfig.updated_at,
    ]

    column_searchable_list = [AdminConfig.key, AdminConfig.description]
    column_sortable_list = [AdminConfig.key, AdminConfig.updated_at]

    column_default_sort = (AdminConfig.key, False)
    page_size = 25

    can_create = True
    can_delete = True
    can_edit = True
    can_view_details = True
