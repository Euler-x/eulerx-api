from sqladmin import ModelView
from sqladmin.filters import AllUniqueStringValuesFilter, BooleanFilter

from app.models.support import SupportMessage, SupportTicket


class SupportTicketAdmin(ModelView, model=SupportTicket):
    name = "Support Ticket"
    name_plural = "Support Tickets"
    icon = "fa-solid fa-ticket"
    category = "Content & Support"

    column_list = [
        SupportTicket.id,
        SupportTicket.user_id,
        SupportTicket.subject,
        SupportTicket.status,
        SupportTicket.priority,
        SupportTicket.created_at,
    ]

    form_excluded_columns = [
        SupportTicket.created_at,
        SupportTicket.updated_at,
        SupportTicket.messages,
        SupportTicket.user,
    ]

    column_searchable_list = [SupportTicket.subject]
    column_sortable_list = [
        SupportTicket.status,
        SupportTicket.priority,
        SupportTicket.created_at,
    ]
    column_filters = [
        AllUniqueStringValuesFilter("status"),
        AllUniqueStringValuesFilter("priority"),
    ]

    column_default_sort = (SupportTicket.created_at, True)
    page_size = 25

    can_create = False
    can_delete = False
    can_edit = True
    can_view_details = True


class SupportMessageAdmin(ModelView, model=SupportMessage):
    name = "Support Message"
    name_plural = "Support Messages"
    icon = "fa-solid fa-comments"
    category = "Content & Support"

    column_list = [
        SupportMessage.id,
        SupportMessage.ticket_id,
        SupportMessage.user_id,
        SupportMessage.is_admin,
        SupportMessage.created_at,
    ]

    form_excluded_columns = [
        SupportMessage.created_at,
        SupportMessage.ticket,
    ]

    column_sortable_list = [SupportMessage.is_admin, SupportMessage.created_at]
    column_filters = [BooleanFilter("is_admin")]

    column_default_sort = (SupportMessage.created_at, True)
    page_size = 25

    can_create = True
    can_delete = False
    can_edit = False
    can_view_details = True
