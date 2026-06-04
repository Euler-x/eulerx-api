"""Cross-database compatible UUID type.

Uses CHAR(36) storage which works on both PostgreSQL and SQLite,
while transparently converting to/from Python uuid.UUID objects.
"""

import uuid as uuid_mod

from sqlalchemy.types import CHAR, TypeDecorator


class GUID(TypeDecorator):
    """Platform-independent UUID type.

    Uses CHAR(36) to store UUIDs as strings, compatible with SQLite,
    PostgreSQL, and all other backends.
    """

    impl = CHAR(36)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None:
            if isinstance(value, uuid_mod.UUID):
                return str(value)
            uuid_mod.UUID(value)  # validate format
            return value
        return value

    def process_result_value(self, value, dialect):
        if value is not None:
            if isinstance(value, uuid_mod.UUID):
                return value
            return uuid_mod.UUID(str(value))
        return value
