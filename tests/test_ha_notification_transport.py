"""An operational notification opens the same detail screen as the installed dashboard."""

import asyncio
from unittest import TestCase

from service.ha_notification_transport import send_mobile
from test_ha_service_integration import FakeHass


class HANotificationTransportTests(TestCase):
    def test_live_notification_keeps_actions_and_opens_status_detail(self):
        async def scenario():
            hass = FakeHass()
            await send_mobile(hass, "mobile_app_caregiver", "안부 확인", "사용 변화",
                              "notify_requested", "matter", "cooker_01", "case-1")
            domain, target, payload = hass.services.calls[-1]
            self.assertEqual((domain, target), ("notify", "mobile_app_caregiver"))
            self.assertEqual(payload["data"]["url"], "/bapsim-lab/status")
            self.assertEqual(payload["data"]["clickAction"], "/bapsim-lab/status")
            self.assertEqual(len(payload["data"]["actions"]), 2)
            self.assertIn("case-1", payload["data"]["actions"][0]["action"])

        asyncio.run(scenario())
