"""Track HA services installed by one Bapsim integration instance."""

from __future__ import annotations


class HAServiceRegistry:
    def __init__(self, hass, domain: str) -> None:
        self.hass = hass
        self.domain = domain
        self.names: set[str] = set()

    def register(self, name: str, handler) -> None:
        self.hass.services.async_register(self.domain, name, handler)
        self.names.add(name)

    def close(self) -> None:
        for name in tuple(self.names):
            self.hass.services.async_remove(self.domain, name)
            self.names.remove(name)
