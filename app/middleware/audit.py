import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog


async def log_audit(
    db: AsyncSession,
    *,
    user_id: uuid.UUID | None,
    action: str,
    resource_type: str = None,
    resource_id: str = None,
    details: dict = None,
    ip_address: str = None,
) -> None:
    """Create an AuditLog record and add it to the session.

    The caller is responsible for committing the session so that the audit
    entry is persisted atomically with whatever business-logic changes
    triggered the audit event.

    Args:
        db:            The active async database session.
        user_id:       UUID of the acting user, or None for system-initiated
                       actions.
        action:        Short label describing the action taken, e.g.
                       "toggle_admin", "update_subscription", "trading_halt",
                       "ban_user".
        resource_type: Category of the affected resource, e.g. "user",
                       "subscription", "strategy".
        resource_id:   String identifier of the affected resource.
        details:       Arbitrary dict with extra context such as before/after
                       values.
        ip_address:    IPv4 or IPv6 address of the request originator
                       (max 45 chars to accommodate IPv6 with brackets).
    """
    audit_entry = AuditLog(
        user_id=user_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        details=details,
        ip_address=ip_address,
    )
    db.add(audit_entry)
