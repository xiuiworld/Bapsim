"""Expose one explicitly read Matter attribute as a diagnostic test sensor.

Uses the existing HA Matter client: no new fabric, credentials or open port.
This is a version-bound A04 probe, not the production B adapter.
"""
import asyncio
from datetime import timedelta
import logging

import voluptuous as vol

from homeassistant.components.sensor import PLATFORM_SCHEMA, SensorEntity
from homeassistant.helpers import config_validation as cv

_LOGGER = logging.getLogger(__name__)
SCAN_INTERVAL = timedelta(seconds=30)
PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Required("matter_entry_id"): cv.string,
    vol.Required("node_id"): cv.positive_int,
    vol.Required("attribute_path"): vol.Match(r"^\d+/\d+/\d+$"),
    vol.Optional("name", default="A04 Board Report Counter"): cv.string,
})


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    async_add_entities([MatterProbe(hass, config)], update_before_add=True)


class MatterProbe(SensorEntity):
    """Copy the board's counter without generating or incrementing a value."""

    _attr_should_poll = True
    _attr_icon = "mdi:counter"
    _attr_available = False

    def __init__(self, hass, config):
        self.hass = hass
        self._entry_id = config["matter_entry_id"]
        self._node_id = config["node_id"]
        self._path = config["attribute_path"]
        self._attr_name = config["name"]
        self._attr_unique_id = f"a04_{self._entry_id}_{self._node_id}_{self._path}"
        self._attr_extra_state_attributes = {
            "input_kind": "test",
            "transport": "matter",
            "node_id": self._node_id,
            "attribute_path": self._path,
            "read_method": "explicit_read_30s",
        }

    async def async_update(self):
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        data = getattr(entry, "runtime_data", None) if entry is not None else None
        if data is None:
            self._attr_available = False
            return
        try:
            async with asyncio.timeout(20):
                values = await data.adapter.matter_client.read_attribute(self._node_id, self._path)
            value = values.get(self._path)
            if type(value) is not int or not 0 <= value <= 0xFFFFFFFF:
                raise ValueError("Board counter is absent or is not uint32")
        except Exception as err:
            # Transport and unsupported-path failures are unavailable, never zero.
            if self._attr_available:
                _LOGGER.warning("A04 attribute read unavailable: %s", err)
            self._attr_available = False
            return
        self._attr_native_value = value
        self._attr_available = True
