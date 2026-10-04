"""Serialise retained Bapsim status and controls on Home Assistant's MQTT service."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone


class HAMQTTOutput:
    def __init__(self, hass, output, controls, show, logger) -> None:
        self.hass = hass
        self.output = output
        self.controls = controls
        self.show = show
        self.logger = logger
        self.lock = asyncio.Lock()
        self.last_discovery_at = None

    async def send_state(self, source: str, payload: dict[str, object]) -> None:
        async with self.lock:
            if payload["revision"] < self.output.revision:
                return
            await self._send_current(source, payload)

    async def _send_current(self, source: str, payload: dict[str, object]) -> None:
        if not self.hass.services.has_service("mqtt", "publish"):
            self.show("mqtt_output", "broker_unavailable", {"topic": self.output.topic(source, "state")})
            return
        state_topic = self.output.topic(source, "state")
        availability_topic = self.output.topic(source, "availability")
        try:
            publish_discovery = source == "matter" and (
                self.last_discovery_at is None or
                datetime.now(timezone.utc) - self.last_discovery_at >= timedelta(hours=1)
            )
            if publish_discovery:
                discovery = {
                    "name": "밥심 현재 안내", "unique_id": f"bapsim_{self.output.household_id}_summary",
                    "state_topic": state_topic, "value_template": "{{ value_json.summary }}",
                    "json_attributes_topic": state_topic, "availability_topic": availability_topic,
                    "expire_after": 180,
                }
                await self.hass.services.async_call("mqtt", "publish", {
                    "topic": f"homeassistant/sensor/bapsim_{self.output.household_id}/config",
                    "payload": json.dumps(discovery, ensure_ascii=False), "qos": 1, "retain": True,
                }, blocking=True)
                self.last_discovery_at = datetime.now(timezone.utc)
            for topic, value in ((availability_topic, "online" if payload["observation"] != "unavailable" else "offline"),
                                 (state_topic, json.dumps(payload, ensure_ascii=False))):
                await self.hass.services.async_call("mqtt", "publish", {
                    "topic": topic, "payload": value, "qos": 1, "retain": True,
                }, blocking=True)
        except Exception:
            self.logger.exception("Bapsim MQTT status publish failed")
            self.show("mqtt_output", "failed", {"topic": state_topic})
        else:
            self.show("mqtt_output", "published", {"topic": state_topic, "revision": payload["revision"]})

    async def send_controls(self) -> None:
        async with self.lock:
            try:
                await self.hass.services.async_call("mqtt", "publish", {
                    "topic": self.output.topic("matter", "notification_controls"),
                    "payload": json.dumps({"schema_version": "0.1.0", "household_id": self.output.household_id,
                                           "revision": self.controls.revision, "pauses": self.controls.pauses,
                                           "acknowledgements": self.controls.acknowledgements}, ensure_ascii=False),
                    "qos": 1, "retain": True,
                }, blocking=True)
            except Exception:
                self.logger.exception("Bapsim MQTT controls publish failed")
