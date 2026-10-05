"""HA YAML platform entry point; bundled with the tested raw codec for install."""
import asyncio
from datetime import timedelta
import logging
import time
import voluptuous as vol
from homeassistant.components.sensor import PLATFORM_SCHEMA, SensorEntity
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, CoordinatorEntity, UpdateFailed

_LOGGER = logging.getLogger(__name__)
PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Required("matter_entry_id"): cv.string,
    vol.Required("node_id"): cv.positive_int,
    # Retain A04 configuration compatibility; A05 reads the fixed versioned path.
    vol.Optional("attribute_path"): cv.string,
    vol.Optional("name"): cv.string,
    vol.Optional("snapshot_history", default=False): cv.boolean,
})


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    coordinator = SnapshotCoordinator(hass, config)
    await coordinator.async_refresh()
    fields = ["ReportCounter", "BootId", "ObservationSequence", "UptimeMs", "SchemaVersion",
              *SIGNALS, "InferenceMethod", "ModelVersion", "InferenceScore"]
    async_add_entities([SnapshotSensor(coordinator, field) for field in fields])


class SnapshotCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, config):
        super().__init__(hass, _LOGGER, name="Bapsim A05 raw snapshot", update_interval=timedelta(seconds=5))
        self.entry_id = config["matter_entry_id"]
        self.node_id = config["node_id"]
        self.tracker = Continuity()
        self.last_counter_key = None
        self.last_advance = None
        self.discontinuity = "baseline"
        self.last_gap = None
        self.history_enabled = config.get("snapshot_history", False)

    async def _async_update_data(self):
        try:
            entry = self.hass.config_entries.async_get_entry(self.entry_id)
            runtime = getattr(entry, "runtime_data", None)
            if runtime is None:
                raise ValueError("Matter integration is not ready")
            async with asyncio.timeout(10):
                path = HISTORY_PATH if self.history_enabled else PATH
                values = await runtime.adapter.matter_client.read_attribute(self.node_id, path)
            if self.history_enabled:
                reports, test_input = decode_history(values.get(path))
                snapshot = reports[-1]
            else:
                snapshot, test_input = decode(values.get(path))
                reports = [snapshot]
            prior = self.tracker.previous
            unseen = [snapshot]
            if self.history_enabled and prior is not None and snapshot["BootId"] == prior["BootId"]:
                if snapshot["ReportCounter"] >= prior["ReportCounter"]:
                    unseen = [r for r in reports if r["ReportCounter"] > prior["ReportCounter"]]
            continuity = "duplicate"
            for report in unseen:
                before = self.tracker.previous
                if (before is not None and report["BootId"] == before["BootId"] and
                        report["ReportCounter"] > before["ReportCounter"] + 1):
                    self.last_gap = dict(boot_id=report["BootId"],
                        first_missing=before["ReportCounter"] + 1,
                        last_missing=report["ReportCounter"] - 1,
                        count=report["ReportCounter"] - before["ReportCounter"] - 1)
                continuity = self.tracker.accept(report)
                if continuity not in ("continuous", "duplicate"):
                    self.discontinuity = continuity
            key = (snapshot["BootId"], snapshot["ReportCounter"])
            now = time.monotonic()
            if self.last_counter_key != key:
                self.last_counter_key, self.last_advance = key, now
            elif self.last_advance is not None and now - self.last_advance > 180:
                raise ValueError("Board report counter is stale")
            return dict(snapshot=snapshot, test_input=test_input, continuity=continuity,
                        reports=reports)
        except Exception as err:
            # Keep the last validated cursor across a failed read. Availability
            # still fails closed, but reconnect must detect retained vs lost
            # reports and reboot instead of silently treating it as first use.
            self.discontinuity = "read_error"
            raise UpdateFailed(f"Snapshot read/validation failed: {err}") from err


class SnapshotSensor(CoordinatorEntity, SensorEntity):
    _attr_icon = "mdi:chip"

    def __init__(self, coordinator, field):
        super().__init__(coordinator)
        self.field = field
        self._attr_name = f"A05 {field}"
        self._attr_unique_id = f"a05_{coordinator.entry_id}_{coordinator.node_id}_{field}"

    @property
    def native_value(self):
        if not self.coordinator.data:
            return None
        snapshot = self.coordinator.data["snapshot"]
        if self.field in SIGNALS and snapshot["SignalQuality"][self.field] != 1:
            return None
        return snapshot[self.field]

    @property
    def extra_state_attributes(self):
        if not self.coordinator.data:
            return {}
        data = self.coordinator.data
        snapshot = data["snapshot"]
        attributes = dict(
            input_kind="test" if data["test_input"] else "live",
            transport="matter", matter_entry_id=self.coordinator.entry_id,
            node_id=self.coordinator.node_id, attribute_path=PATH,
            read_method="buffered_read_5s" if self.coordinator.history_enabled else "explicit_read_5s",
            schema_version=snapshot["SchemaVersion"],
            boot_id=snapshot["BootId"], observation_sequence=snapshot["ObservationSequence"],
            report_counter=snapshot["ReportCounter"], continuity=data["continuity"],
            last_discontinuity=self.coordinator.discontinuity,
            last_gap=self.coordinator.last_gap,
        )
        if self.field in SIGNALS:
            attributes["quality"] = ("unknown", "good", "error", "unsupported")[snapshot["SignalQuality"][self.field]]
            attributes["raw_value"] = snapshot[self.field]
        if self.field == "ReportCounter":
            attributes["raw_snapshot"] = snapshot
            if self.coordinator.history_enabled:
                attributes["history_path"] = HISTORY_PATH
                attributes["raw_history"] = data["reports"]
        return attributes
