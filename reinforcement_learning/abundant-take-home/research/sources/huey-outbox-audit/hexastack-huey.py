from typing import Any, cast

from hexastack_core.ports.lock import LockPort
from hexastack_cqrs.ports.buses import EventBusPort
from hexastack_events.domain.models import CloudEventEnvelope
from hexastack_events.ports.buses import DistributedEventBusPort
from hexastack_events.ports.outbox import (
    OutboxRelayPort,
    OutboxStoragePort,
)


class HueyOutboxRelay(OutboxRelayPort):
    """Huey-backed multi-process outbox worker and periodic task runner.

    Notes/Architectural Intent:
        Executes outbox polling across separate worker processes using Huey,
        decoupling database polling from API request threads.
        Accepts an optional LockPort (e.g. FileLockAdapter) to prevent concurrent
        worker contention.
    """

    def __init__(
        self,
        storage: OutboxStoragePort,
        bus: EventBusPort,
        huey_instance: Any | None = None,
        batch_size: int = 50,
        lock: LockPort | None = None,
    ) -> None:
        self._storage = storage
        self._bus = bus
        self._huey = huey_instance
        self._batch_size = batch_size
        self._lock = lock
        self._is_active: bool = False

    def publish_pending_batch(self, limit: int = 50) -> int:
        """Poll and publish pending outbox records.

        Args:
            limit: Maximum records to process.

        Returns:
            Count of successfully published records.
        """
        if self._lock is not None:
            acquired = self._lock.acquire(blocking=False)
            if not acquired:
                return 0
            try:
                return self._drain_and_publish(limit=limit)
            finally:
                self._lock.release()

        return self._drain_and_publish(limit=limit)

    def _drain_and_publish(self, limit: int = 50) -> int:
        """Internal worker fetching and publishing pending records."""
        records = self._storage.fetch_pending(limit=limit)
        published_count = 0

        for record in records:
            try:
                envelope = CloudEventEnvelope(
                    id=record.id,
                    source=record.source,
                    type=record.event_type,
                    time=record.created_at.isoformat(),
                    datacontenttype="application/json",
                    correlationid=record.correlation_id,
                    tenantid=record.tenant_id,
                    data=record.payload,
                )
                if isinstance(self._bus, DistributedEventBusPort):
                    self._bus.publish_envelope(envelope)
                else:
                    self._bus.publish(cast("Any", envelope))

                self._storage.mark_published(record.id)
                published_count += 1
            except Exception as exc:  # noqa: BLE001
                self._storage.mark_failed(record.id, str(exc))

        return published_count

    def start(self) -> None:
        """Activate Huey outbox periodic runner."""
        self._is_active = True

    def stop(self) -> None:
        """Deactivate Huey outbox periodic runner."""
        self._is_active = False


__all__ = [
    "HueyOutboxRelay",
]
