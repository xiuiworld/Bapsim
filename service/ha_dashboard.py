"""Home Assistant sensor presentation for Bapsim state."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


LABELS = {
    "detection_status": "밥솥 단독 판정", "workflow_status": "보호자 확인 상태",
    "baseline": "개인 기준", "features": "현재 특징",
    "usage_context": "최근 개방의 보드 맥락",
    "initial_routine": "초기 밥솥 사용 시간 설정",
    "initial_routine_result": "초기 사용 시간 적용 결과",
    "notification_delivery": "알림 전달 단계", "matter_input": "실물 밥솥 입력 준비",
    "observation_issue_status": "연결 문제 상태",
    "output_state": "실물 분석 상태", "simulation_output_state": "시뮬레이션 분석 상태",
}

SOURCE_SCOPED = frozenset({
    "baseline", "features", "detection_status", "interval_detection",
    "decline_detection", "workflow_status", "decline_status",
    "observation_issue_status", "notification_delivery", "auxiliary_deferral",
})


class HADashboard:
    def __init__(self, hass, usage_model, routine_updated_at: dict[str, str], schedule_expiry=None,
                 on_output_change=None) -> None:
        self.hass = hass
        self.usage_model = usage_model
        self.routine_updated_at = routine_updated_at
        self.schedule_expiry = schedule_expiry
        self.on_output_change = on_output_change
        self.output_timers: dict[str, object] = {}
        self.output_revisions: dict[str, int] = {}

    def show(self, entity: str, value: object, attributes: dict[str, object] | None = None) -> None:
        label = LABELS.get(entity, entity)
        if entity in SOURCE_SCOPED and attributes and attributes.get("source") == "simulator":
            entity = f"simulation_{entity}"
            label = f"시뮬레이션 {label}"
        elif entity.startswith("simulation_") and entity != "simulation_output_state":
            label = f"시뮬레이션 {LABELS.get(entity.removeprefix('simulation_'), entity)}"
        # HA reserves "unknown" and "unavailable" as entity health states.
        display = {"unknown": "not_observed", "unavailable": "disconnected"}.get(str(value), str(value))
        self.hass.states.async_set(f"sensor.bapsim_{entity}", display, {
            "friendly_name": label, **(attributes or {}),
        })

    def show_output(self, source: str, payload: dict[str, object]) -> None:
        entity = "output_state" if source == "matter" else "simulation_output_state"
        revision = int(payload["revision"])
        if revision <= self.output_revisions.get(entity, -1):
            return
        if entity in self.output_timers:
            self.output_timers.pop(entity)()
        self.output_revisions[entity] = revision
        expires = datetime.fromisoformat(str(payload["valid_until"]).replace("Z", "+00:00"))
        if expires.tzinfo is None:
            raise ValueError("output valid_until requires timezone")

        def expire(_now) -> None:
            if self.output_revisions.get(entity) == revision:
                self.show(entity, "expired", {"source": source, "valid_until": payload["valid_until"],
                                             "last_usage_at": payload.get("last_usage_at")})
                self.output_timers.pop(entity, None)
                if source == "matter" and self.on_output_change is not None:
                    self.on_output_change()

        if expires <= datetime.now(timezone.utc):
            expire(expires)
            return
        self.show(entity, payload["summary"], {**payload, "source": source})
        if source == "matter" and self.on_output_change is not None:
            self.on_output_change()
        if self.schedule_expiry is not None:
            self.output_timers[entity] = self.schedule_expiry(self.hass, expire, expires)

    def close(self) -> None:
        for cancel in self.output_timers.values():
            cancel()
        self.output_timers.clear()

    def show_observation(self, records: list[dict]) -> None:
        """Present only verified live contract records, independent of analysis."""
        rows = [row for row in records if row.get("mode") == "live"]
        if not rows:
            return
        fields = {row["signal"]: row for row in rows}
        latest = rows[-1]
        at = latest.get("source_at") or latest["received_at"]
        valid_until = (datetime.fromisoformat(at.replace("Z", "+00:00")) +
                       timedelta(minutes=3)).isoformat()
        self.show("current_observation", "received", {
            "source": "matter", "device_id": latest["device_id"],
            "observed_at": latest.get("observed_at"), "source_at": latest.get("source_at"),
            "received_at": latest["received_at"], "valid_until": valid_until,
            "fields": {name: {"value": row["value"], "quality": row["quality"]}
                       for name, row in fields.items()},
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
