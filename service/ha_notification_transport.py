"""Home Assistant service calls and caregiver-facing notification copy."""

from __future__ import annotations


def notification_copy(action: str, reason_codes: tuple[str, ...]) -> tuple[str, str]:
    issue = action == "observation_issue_requested"
    resumed = action == "usage_resumed_requested"
    reminder = action == "reminder_requested"
    declining = "DECLINING_USAGE" in reason_codes
    title = ("밥심 — 연결 상태 확인" if issue else "밥심 — 사용 재개" if resumed else
             "밥심 — 최근 사용 감소" if declining else "밥심 — 안부 확인 필요")
    message = ("현재 밥솥 사용 기록을 확인하기 어려워요. 연결 상태를 확인해 주세요." if issue else
               "밥솥을 다시 사용하신 기록이 확인됐어요." if resumed else
               "최근 밥솥 사용 기록이 평소보다 줄었습니다. 안부를 확인해 보세요." if declining else
               "다음 예상 사용 시점에도 밥솥 사용 기록이 없습니다. 안부를 확인해 보세요."
               if reminder else "평소와 다른 밥솥 사용 패턴이 관측되었습니다. 안부를 확인해 보세요.")
    return title, message


def mobile_actions(action: str, source: str, device_id: str, incident_id: str) -> list[dict[str, str]]:
    if action in {"usage_resumed_requested", "observation_issue_requested"}:
        return []
    return [
        {"action": f"BAPSIM|{response}|{source}|{device_id}|{incident_id}", "title": label}
        for response, label in (("checking", "확인 중"), ("resolved", "안부 확인 완료"))
    ]


async def send_local(hass: object, notification_id: str, title: str, message: str) -> None:
    await hass.services.async_call("persistent_notification", "create", {
        "title": title, "message": message, "notification_id": "bapsim_" + notification_id,
    }, blocking=True)


async def send_mobile(hass: object, target: str, title: str, message: str,
                      action: str, source: str, device_id: str, incident_id: str) -> None:
    await hass.services.async_call("notify", target, {
        "title": title, "message": message,
        "data": {"actions": mobile_actions(action, source, device_id, incident_id)},
    }, blocking=True)
