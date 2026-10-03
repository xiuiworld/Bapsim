"""Home Assistant sensor presentation for Bapsim state."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


LABELS = {
    "detection_status": "밥솥 단독 판정", "workflow_status": "보호자 확인 상태",
    "baseline": "개인 기준", "features": "현재 특징",
    "initial_routine": "초기 밥솥 사용 시간 설정",
    "notification_delivery": "알림 전달 단계", "matter_input": "실물 밥솥 입력 준비",
    "observation_issue_status": "연결 문제 상태",
    "output_state": "실물 분석 상태", "simulation_output_state": "시뮬레이션 분석 상태",
}


class HADashboard:
    def __init__(self, hass, usage_model, routine_updated_at: dict[str, str]) -> None:
        self.hass = hass
        self.usage_model = usage_model
        self.routine_updated_at = routine_updated_at

    def show(self, entity: str, value: object, attributes: dict[str, object] | None = None) -> None:
        # HA reserves "unknown" and "unavailable" as entity health states.
        display = {"unknown": "not_observed", "unavailable": "disconnected"}.get(str(value), str(value))
        self.hass.states.async_set(f"sensor.bapsim_{entity}", display, {
            "friendly_name": LABELS.get(entity, entity), **(attributes or {}),
        })

    def show_initial_routines(self, as_of: datetime | None = None) -> None:
        now = as_of or datetime.now(timezone.utc)
        routines = []
        for source, device_id in self.usage_model.device_keys:
            if source != "matter":
                continue
            windows = self.usage_model.initial_routine(source, device_id)
            if not windows:
                continue
            updated_at = self.routine_updated_at.get(device_id)
            try:
                updated = datetime.fromisoformat(updated_at) if updated_at else None
                review_due = updated is None or now - updated.astimezone(timezone.utc) >= timedelta(days=28)
            except (ValueError, TypeError):
                review_due = True
            routines.append({"device_id": device_id, "windows": windows,
                             "updated_at": updated_at, "review_due": review_due})
        self.show("initial_routine", "review_due" if any(item["review_due"] for item in routines)
                  else "configured" if routines else "not_configured", {"routines": routines})
