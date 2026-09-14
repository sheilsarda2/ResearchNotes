from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    Integer,
    String,
    Text,
    select,
    update,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from hexastack_events.domain.models import OutboxRecord, OutboxStatus
from hexastack_events.ports.outbox import OutboxStoragePort


class OutboxEventMixin:
    """SQLAlchemy declarative mixin providing columns for transactional outbox tables.

    Notes/Architectural Intent:
        Allows applications to embed outbox storage schema into their existing
        database schema / migrations or use the standalone OutboxEventBaseModel.
    """

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(128), index=True)
    source: Mapped[str] = mapped_column(String(255))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(
        String(32), index=True, default=OutboxStatus.PENDING.value
    )
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    correlation_id: Mapped[str | None] = mapped_column(
        String(64), index=True, nullable=True
    )
    tenant_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    def to_domain(self) -> OutboxRecord:
        """Convert SQLAlchemy row to domain OutboxRecord."""
        return OutboxRecord(
            id=self.id,
            event_type=self.event_type,
            source=self.source,
            payload=self.payload,
            status=OutboxStatus(self.status),
            retry_count=self.retry_count,
            correlation_id=self.correlation_id,
            tenant_id=self.tenant_id,
            created_at=self.created_at,
            published_at=self.published_at,
            last_error=self.last_error,
        )


class Base(DeclarativeBase):
    """Default DeclarativeBase for standalone outbox table."""


class OutboxEventBaseModel(Base, OutboxEventMixin):
    """Default standalone SQLAlchemy model for the outbox_events table."""

    __tablename__ = "outbox_events"


class SqlAlchemyOutboxStorage(OutboxStoragePort):
    """SQLAlchemy adapter implementing OutboxStoragePort against relational databases.

    Notes/Architectural Intent:
        Enables transactional outbox staging inside the active database transaction.
        When running alongside SqlAlchemyUnitOfWork, outbox events commit atomically
        with domain state mutations.
    """

    def __init__(
        self,
        session_factory: Callable[[], Session] | Session,
        model_cls: type[Any] = OutboxEventBaseModel,
    ) -> None:
        self._session_or_factory = session_factory
        self._model_cls = model_cls

    def _get_session(self) -> Session:
        """Helper to get or create an active Session."""
        if isinstance(self._session_or_factory, Session):
            return self._session_or_factory
        return self._session_or_factory()

    def fetch_pending(self, limit: int = 50) -> list[OutboxRecord]:
        """Fetch pending outbox records ready for relaying."""
        session = self._get_session()
        stmt = (
            select(self._model_cls)
            .where(
                self._model_cls.status.in_(
                    [OutboxStatus.PENDING.value, OutboxStatus.FAILED.value]
                )
            )
            .where(self._model_cls.retry_count < 5)
            .order_by(self._model_cls.created_at.asc())
            .limit(limit)
        )
        results = session.scalars(stmt).all()
        return [row.to_domain() for row in results]

    def mark_failed(self, record_id: str, error_message: str) -> None:
        """Increment retry count and mark as FAILED."""
        session = self._get_session()
        stmt = (
            update(self._model_cls)
            .where(self._model_cls.id == record_id)
            .values(
                status=OutboxStatus.FAILED.value,
                retry_count=self._model_cls.retry_count + 1,
                last_error=error_message,
            )
        )
        session.execute(stmt)
        session.flush()

    def mark_published(self, record_id: str) -> None:
        """Mark record as PUBLISHED."""
        session = self._get_session()
        now = datetime.now(UTC)
        stmt = (
            update(self._model_cls)
            .where(self._model_cls.id == record_id)
            .values(
                status=OutboxStatus.PUBLISHED.value,
                published_at=now,
                last_error=None,
            )
        )
        session.execute(stmt)
        session.flush()

    def save(self, record: OutboxRecord) -> None:
        """Persist a single outbox record in the database."""
        session = self._get_session()
        row = self._model_cls(
            id=record.id,
            event_type=record.event_type,
            source=record.source,
            payload=record.payload,
            status=record.status.value,
            retry_count=record.retry_count,
            correlation_id=record.correlation_id,
            tenant_id=record.tenant_id,
            created_at=record.created_at,
            published_at=record.published_at,
            last_error=record.last_error,
        )
        session.add(row)
        session.flush()

    def save_all(self, records: list[OutboxRecord]) -> None:
        """Persist multiple outbox records in a batch."""
        session = self._get_session()
        rows = [
            self._model_cls(
                id=r.id,
                event_type=r.event_type,
                source=r.source,
                payload=r.payload,
                status=r.status.value,
                retry_count=r.retry_count,
                correlation_id=r.correlation_id,
                tenant_id=r.tenant_id,
                created_at=r.created_at,
                published_at=r.published_at,
                last_error=r.last_error,
            )
            for r in records
        ]
        session.add_all(rows)
        session.flush()


__all__ = [
    "OutboxEventBaseModel",
    "OutboxEventMixin",
    "SqlAlchemyOutboxStorage",
]
