"""MQTT transport and HA notification delivery for the standalone worker."""

from __future__ import annotations

import json
import logging
import os
import queue
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import paho.mqtt.client as mqtt

from service.standalone import AnalysisService


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
LOG = logging.getLogger("bapsim.worker")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def main() -> None:
    household = os.environ.get("BAPSIM_HOUSEHOLD_ID", "household_01")
    host = os.environ.get("BAPSIM_MQTT_HOST", "mosquitto")
    port = int(os.environ.get("BAPSIM_MQTT_PORT", "1883"))
    username = os.environ.get("BAPSIM_MQTT_USERNAME")
    password = os.environ.get("BAPSIM_MQTT_PASSWORD")
    ha_url = os.environ.get("BAPSIM_HA_URL", "http://homeassistant:8123").rstrip("/")
    ha_token = os.environ.get("BAPSIM_HA_TOKEN", "")
    mobile = os.environ.get("BAPSIM_NOTIFY_SERVICE", "")
    inbound: queue.Queue[tuple[str, str, bool]] = queue.Queue()
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"bapsim-{household}")
    if username:
        client.username_pw_set(username, password)
    live_topic = f"bapsim/v1/{household}/worker_availability"
    client.will_set(live_topic, "offline", qos=1, retain=True)

    def publish(topic: str, payload: str, retain: bool) -> None:
        result = client.publish(topic, payload, qos=1, retain=retain)
        if result.rc != mqtt.MQTT_ERR_SUCCESS:
            LOG.warning("MQTT publish failed: %s rc=%s", topic, result.rc)

    def ha_call(domain: str, service: str, body: dict[str, object]) -> str:
        if not ha_token:
            LOG.warning("HA token missing; notification remains pending")
            return "failed"
        request = Request(f"{ha_url}/api/services/{domain}/{service}",
                          data=json.dumps(body, ensure_ascii=False).encode(),
                          headers={"Authorization": f"Bearer {ha_token}",
                                   "Content-Type": "application/json"}, method="POST")
        try:
            with urlopen(request, timeout=10) as response:
                return "ha_accepted" if response.status in {200, 201} else "uncertain"
        except HTTPError as exc:
            LOG.warning("HA notification HTTP error: %s", exc.code)
            return "failed" if exc.code in {400, 401, 403, 404} else "uncertain"
        except (OSError, URLError) as exc:
            LOG.warning("HA notification call failed: %s", exc)
            reason = exc.reason if isinstance(exc, URLError) else exc
            return "failed" if isinstance(reason, ConnectionRefusedError) else "uncertain"

    def notify(update) -> bool:
        resumed = update.action == "usage_resumed_requested"
        reminder = update.action == "reminder_requested"
        issue = update.action == "observation_issue_requested"
        notification_id = "bapsim_" + str(update.notification_id or update.incident_id)
        if update.source == "simulator":
            return ha_call("persistent_notification", "create", {
                "title": "밥심 시뮬레이션", "message": (
                    "밥솥 연결 상태를 확인하기 어렵습니다." if issue else
                    "밥솥을 다시 사용한 기록이 확인되었습니다." if resumed else
                    "다음 예상 사용 시점에도 사용 기록이 없습니다." if reminder else
                    "시뮬레이션에서 사용 변화가 관측되었습니다."),
                "notification_id": notification_id,
            })
        title = ("밥심 — 연결 상태 확인" if issue else "밥심 — 사용 재개" if resumed else "밥심 — 최근 사용 감소"
                 if "DECLINING_USAGE" in update.reason_codes else "밥심 — 안부 확인 필요")
        message = ("현재 밥솥 사용 기록을 확인하기 어려워요. 연결 상태를 확인해 주세요." if issue else
                   "밥솥을 다시 사용하신 기록이 확인됐어요." if resumed else
                   "다음 예상 사용 시점에도 밥솥 사용 기록이 없습니다. 안부를 확인해 보세요."
                   if reminder else "평소와 다른 밥솥 사용 기록이 있습니다. 안부를 확인해 보세요.")
        local_result = ha_call("persistent_notification", "create", {
            "title": title, "message": message,
            "notification_id": notification_id,
        })
        if local_result != "ha_accepted":
            return local_result
        if mobile:
            return ha_call("notify", mobile, {"title": title, "message": message})
        return "ha_accepted"

    service = AnalysisService(household, Path(os.environ.get("BAPSIM_STATE_FILE", "/data/state.json")),
                              publish, notify,
                              quiet_start=os.environ.get("BAPSIM_QUIET_START", "22:00"),
                              quiet_end=os.environ.get("BAPSIM_QUIET_END", "08:00"))

    def on_connect(client, userdata, flags, reason_code, properties) -> None:
        if reason_code.is_failure:
            LOG.error("MQTT connection rejected: %s", reason_code)
            return
        for prefix in (f"bapsim/v1/{household}", f"bapsim/simulation/v1/{household}"):
            client.subscribe(f"{prefix}/observations", qos=1)
        client.subscribe(f"bapsim/v1/{household}/control", qos=1)
        client.subscribe(f"bapsim/v1/{household}/routine_config", qos=1)
        publish(live_topic, "online", True)
        publish(f"homeassistant/sensor/bapsim_{household}/config", json.dumps({
            "name": "밥심 현재 안내", "unique_id": f"bapsim_{household}_summary",
            "state_topic": f"bapsim/v1/{household}/state",
            "value_template": "{{ value_json.summary }}",
            "json_attributes_topic": f"bapsim/v1/{household}/state",
            "availability_topic": live_topic, "expire_after": 180,
        }, ensure_ascii=False), True)
        service._emit_controls()

    def on_message(client, userdata, message) -> None:
        inbound.put((message.topic, message.payload.decode("utf-8"), bool(message.retain)))

    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(host, port, keepalive=60)
    client.loop_start()
    next_tick = time.monotonic()
    try:
        while True:
            try:
                topic, raw, retained = inbound.get(timeout=1)
                if retained and not topic.endswith("/routine_config"):
                    continue
                payload = json.loads(raw)
                if not isinstance(payload, dict):
                    raise ValueError("MQTT payload must be an object")
                if topic.endswith("/routine_config"):
                    service.process_routine_config(payload)
                elif topic.endswith("/control"):
                    service.process_control(payload, utc_now())
                elif topic.endswith("/observations"):
                    service.process_event(payload, utc_now())
            except queue.Empty:
                pass
            except (ValueError, KeyError, TypeError) as exc:
                LOG.warning("Ignored invalid MQTT message: %s", exc)
            if time.monotonic() >= next_tick:
                service.tick(utc_now())
                next_tick = time.monotonic() + 60
    finally:
        publish(live_topic, "offline", True)
        client.disconnect()
        client.loop_stop()


if __name__ == "__main__":
    main()
