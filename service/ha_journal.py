"""Serialised disk writes for HA raw events, observations, and service records."""

from __future__ import annotations

import asyncio
from datetime import datetime


class HAJournal:
    def __init__(self, hass, raw, observations, records, logger, contract=None, test=None) -> None:
        self.hass = hass
        self.raw = raw
        self.observations = observations
        self.records = records
        self.contract = contract
        self.test = test
        self.logger = logger
        self.lock = asyncio.Lock()

    def archive_raw(self, stream: str, payload: dict[str, object]) -> None:
        if self.raw is not None:
            self.hass.async_create_task(self._write_raw(stream, dict(payload)))

    async def _write_raw(self, stream: str, payload: dict[str, object]) -> None:
        try:
            async with self.lock:
                await self.hass.async_add_executor_job(self.raw.append, stream, payload)
        except (OSError, ValueError) as exc:
            self.logger.warning("Could not archive raw %s event: %s", stream, exc)

    async def write_observation(self, payload: dict[str, object]) -> None:
        try:
            async with self.lock:
                await self.hass.async_add_executor_job(self.observations.append, payload)
        except (OSError, ValueError, TypeError) as exc:
            self.logger.warning("Could not archive normalized observation: %s", exc)

    def archive_contract(self, payloads: list[dict]) -> None:
        if self.contract is not None and payloads:
            self.hass.async_create_task(self._write_contract(payloads))

    async def _write_contract(self, payloads: list[dict]) -> None:
        try:
            async with self.lock:
                for payload in payloads:
                    await self.hass.async_add_executor_job(self.contract.append, payload)
        except (OSError, ValueError, TypeError) as exc:
            self.logger.warning("Could not archive A05 contract records: %s", exc)

    async def write_test(self, payloads: list[dict]) -> bool:
        """Persist one test batch before allowing its single mobile attempt."""
        if self.test is None:
            return False
        try:
            async with self.lock:
                for payload in payloads:
                    await self.hass.async_add_executor_job(self.test.append, payload)
        except (OSError, ValueError, TypeError) as exc:
            self.logger.warning("Could not archive A05 test run: %s", exc)
            return False
        return True

    def archive_service_record(self, category: str, payload: dict[str, object], at: str) -> None:
        if self.records is not None:
            self.hass.async_create_task(self._write_service_record({"category": category, "as_of": at, **payload}))

    async def _write_service_record(self, payload: dict[str, object]) -> None:
        try:
            async with self.lock:
                await self.hass.async_add_executor_job(self.records.append, payload)
        except (OSError, ValueError, TypeError) as exc:
            self.logger.warning("Could not archive service record: %s", exc)

    async def prune_observations(self, now: datetime) -> None:
        try:
            async with self.lock:
                await self.hass.async_add_executor_job(self.observations.prune, now)
                if self.records is not None:
                    await self.hass.async_add_executor_job(self.records.prune, now)
                if self.contract is not None:
                    await self.hass.async_add_executor_job(self.contract.prune, now)
                if self.test is not None:
                    await self.hass.async_add_executor_job(self.test.prune, now)
        except OSError as exc:
            self.logger.warning("Could not prune normalized observations: %s", exc)
