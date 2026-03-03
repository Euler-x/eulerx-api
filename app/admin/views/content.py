from sqladmin import ModelView
from sqladmin.filters import AllUniqueStringValuesFilter, BooleanFilter

from app.models.content import LearningContent


class LearningContentAdmin(ModelView, model=LearningContent):
    name = "Learning Content"
    name_plural = "Learning Content"
    icon = "fa-solid fa-graduation-cap"
    category = "Content & Support"

    column_list = [
        LearningContent.id,
        LearningContent.title,
        LearningContent.category,
        LearningContent.content_type,
        LearningContent.display_order,
        LearningContent.is_published,
        LearningContent.created_at,
    ]

    form_excluded_columns = [
        LearningContent.created_at,
        LearningContent.updated_at,
    ]

    column_searchable_list = [LearningContent.title]
    column_sortable_list = [
        LearningContent.title,
        LearningContent.category,
        LearningContent.content_type,
        LearningContent.display_order,
        LearningContent.is_published,
        LearningContent.created_at,
    ]
    column_filters = [
        AllUniqueStringValuesFilter("category"),
        AllUniqueStringValuesFilter("content_type"),
        BooleanFilter("is_published"),
    ]

    column_default_sort = (LearningContent.display_order, False)
    page_size = 25

    can_create = True
    can_delete = True
    can_edit = True
    can_view_details = True
