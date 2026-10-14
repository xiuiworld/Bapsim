"""Export isolated synthetic records through the existing analysis/workflow core.

This is recorded playback, never HA state injection or phone delivery evidence.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory

from service.standalone import AnalysisService
from service.analysis_policy import assess_personal_use
from tools.simulate_notification_flow import at, SOURCE, DEVICE


def build() -> dict:
    with TemporaryDirectory(prefix="bapsim-caregiver-replay-") as directory:
        sent = []
        service = AnalysisService("demo_household", Path(directory) / "state.json",
                                  lambda *args: None,
                                  lambda update: sent.append(update) or True)
        frames = []
        last_decisions = {}
        lid_open = False

        def event(day, hour, minute, kind):
            nonlocal lid_open
            now = at(day, hour, minute)
            service.process_event({"source": SOURCE, "device_id": DEVICE,
                                   "event_type": kind, "observed_at": now,
                                   "availability": "online"}, now)
            if kind in {"lid_opened", "lid_closed"}:
                lid_open = kind == "lid_opened"


        def frame(day, hour, minute, title, page="home"):
            now = at(day, hour, minute)
            decisions = assess_personal_use(deepcopy(service.model), SOURCE, DEVICE, now, online=True, away=False)
            service.evaluate(SOURCE, DEVICE, now)
            out = service._state_payload(SOURCE, DEVICE, now)
            out["source"] = SOURCE
            profile = service.model.profile(SOURCE, DEVICE, now)
            states = {
                "sensor.bapsim_output_state": {"state": out["summary"], "attributes": out},
                "sensor.bapsim_baseline": {"state": "recorded", "attributes": profile},
                "sensor.bapsim_current_observation": {"state": "received", "attributes": {
                    "source": SOURCE, "device_id": DEVICE, "received_at": now,
                    "valid_until": (datetime.fromisoformat(now) + timedelta(minutes=3)).isoformat(),
                    "fields": {"lid_open": {"value": lid_open, "quality": "good"},
                               "device_connection": {"value": "online", "quality": "good"}}}},
                "sensor.bapsim_notification_delivery": {"state": "simulation", "attributes": {}},
            }
            for decision in decisions.windows:
                # An unused lunch/night slot must not replace the last relevant window result.
                if decision["routine_status"] == "no_expectation":
                    continue
                last_decisions["sensor.bapsim_detection_status"] = {"state": decision["routine_status"], "attributes": decision}
            if decisions.interval:
                last_decisions["sensor.bapsim_interval_detection"] = {"state": decisions.interval["routine_status"], "attributes": decisions.interval}
            states.update(last_decisions)
            frames.append(deepcopy({"at": now, "title": title, "page": page, "states": states}))

        event(0, 0, 0, "device_online")
        frame(0, 0, 0, "합성 기록 시작")
        # A household that uses the cooker morning and evening, not a claim of meals.
        offsets = [-8, 4, -3, 9, 1, -6, 7, -4, 5, 0, -9, 6, -2, 3]
        for day, offset in enumerate(offsets):
            for center, shift in [(8 * 60, offset), (19 * 60, -offset)]:
                opened = center + shift
                closed = opened + 2 + day % 3
                event(day, *divmod(opened, 60), "lid_opened")
                event(day, *divmod(closed, 60), "lid_closed")
            frame(day, 21, 15, f"{day+1}일째 · 아침과 저녁 사용 기록")
        # Start with a learned baseline and a normal use on the same morning.
        event(14, 8, 3, "lid_opened")
        frame(14, 8, 3, "오늘 아침에도 평소처럼 사용했어요")
        start_index = len(frames) - 1
        event(14, 8, 6, "lid_closed")
        frame(14, 8, 6, "아침 사용 후 뚜껑을 닫았어요")
        cursor = datetime.fromisoformat(at(14, 8, 30))
        while not sent:
            cursor += timedelta(minutes=30)
            assert cursor <= datetime.fromisoformat(at(16, 16))
            day = (cursor.date() - datetime.fromisoformat(at(0)).date()).days
            frame(day, cursor.hour, cursor.minute, "시간 흐름", "home")
        request_index = len(frames) - 1
        frames[-1]['title'] = '안부 확인 필요'
        frames[-1]['wait_for_response'] = True
        assert len(sent) == 1 and sent[0].action == "notify_requested"
        incident = sent[0].incident_id
        responses = {}
        for response, title in [("checking", "보호자가 확인 중"), ("resolved", "보호자가 안부 확인 완료")]:
            cursor += timedelta(minutes=1)
            now = cursor.isoformat()
            update = service.gap.caregiver_response(SOURCE, DEVICE, response, now, incident)
            assert update is not None
            day = (cursor.date() - datetime.fromisoformat(at(0)).date()).days
            frame(day, cursor.hour, cursor.minute, title, "history")
            responses[response] = len(frames) - 1
            frames[-1]['wait_for_response'] = response == 'checking'
        frames[request_index]['responses'] = responses.copy()
        frames[responses['checking']]['responses'] = {'resolved': responses['resolved']}
        cursor += timedelta(minutes=10)
        day = (cursor.date() - datetime.fromisoformat(at(0)).date()).days
        event(day, cursor.hour, cursor.minute, "lid_opened")
        frame(day, cursor.hour, cursor.minute, "이후 개방 기록 · 최근 사용 시각이 갱신됩니다")
        return {"schema_version": 2, "source": SOURCE, "mode": "recorded_replay",
                "start_index": start_index, "request_index": request_index,
                "response_indices": responses,
                "real_push_sent": False, "delivery": "simulated receiver only",
                "caregiver_responses": "recorded workflow calls, not HA service calls",
                "frames": frames}


if __name__ == "__main__":
    path = Path(__file__).resolve().parents[1] / "infrastructure/home_assistant/config/www/bapsim-caregiver-replay.json"
    result = build()
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{len(result['frames'])} isolated simulator frames: {path}")
