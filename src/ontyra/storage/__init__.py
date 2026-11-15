"""SurrealDB driver layer. The only package that imports the `surrealdb` SDK."""

from ontyra.storage.database import Capabilities, Database, capabilities_for
from ontyra.storage.repository import SurrealStore
from ontyra.storage.schema import SchemaGenerator, SchemaManager

__all__ = [
    "Capabilities",
    "Database",
    "SchemaGenerator",
    "SchemaManager",
    "SurrealStore",
    "capabilities_for",
]
