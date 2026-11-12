from typing import Any

from sqlalchemy import JSON, TypeDecorator


class EmbeddingVector(TypeDecorator[list[float] | None]):
    """Use pgvector in PostgreSQL and JSON in lightweight test databases."""

    impl = JSON
    cache_ok = True

    def __init__(self, dimensions: int, *args: Any, **kwargs: Any) -> None:
        self.dimensions = dimensions
        super().__init__(*args, **kwargs)

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            from pgvector.sqlalchemy import Vector

            return dialect.type_descriptor(Vector(self.dimensions))
        return dialect.type_descriptor(JSON())

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return [float(item) for item in value]
