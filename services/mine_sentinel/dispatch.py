"""MineSentinel report dispatch orchestration."""

from __future__ import annotations

from collections.abc import Callable
from io import BytesIO
from pathlib import Path

from .delivery import MineSentinelDelivery
from .models import ObservationRecord
from .routing import MineSentinelTargetRouter


class MineSentinelReportDispatcher:
    """Send report text/files to the sessions derived from observation records."""

    def __init__(
        self,
        delivery: MineSentinelDelivery,
        router: MineSentinelTargetRouter,
        error_sink: Callable[[str], None] | None = None,
    ):
        self.delivery = delivery
        self.router = router
        self.error_sink = error_sink

    def records_by_session(
        self,
        records: list[ObservationRecord],
        include_server_targets: bool = True,
        include_report_targets: bool = True,
    ) -> dict[str, list[ObservationRecord]]:
        return self.router.records_by_session(
            records,
            include_server_targets=include_server_targets,
            include_report_targets=include_report_targets,
        )

    async def send_to_target_sessions(
        self,
        text: str,
        records: list[ObservationRecord],
        current_session: str = "",
        include_server_targets: bool = True,
        include_report_targets: bool = True,
        image: BytesIO | None = None,
        file_path: Path | None = None,
    ):
        for umo in self.router.sessions_for_records(
            records,
            current_session,
            include_server_targets=include_server_targets,
            include_report_targets=include_report_targets,
        ):
            await self.send_report(umo, text, image=image, file_path=file_path)

    async def send_message(
        self,
        umo: str,
        text: str,
        file_path: Path | None = None,
    ) -> bool:
        sent = await self.delivery.send_message(umo, text, file_path)
        if not sent:
            self._capture_delivery_error()
        return sent

    async def send_report(
        self,
        umo: str,
        text: str,
        image: BytesIO | None = None,
        file_path: Path | None = None,
    ) -> bool:
        send_report = getattr(self.delivery, "send_report", None)
        if callable(send_report):
            sent = await send_report(umo, text, image, file_path)
        else:
            sent = await self.delivery.send_message(umo, text, file_path)
        if not sent:
            self._capture_delivery_error()
        return sent

    async def send_file(self, umo: str, file_path: Path):
        sent = await self.delivery.send_file(umo, file_path)
        if not sent:
            self._capture_delivery_error()

    def _capture_delivery_error(self):
        if self.error_sink and self.delivery.last_error:
            self.error_sink(self.delivery.last_error)
