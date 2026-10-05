uint32_t sReportCounter = 0, sObservationSequence = 0, sBootId = 0;
uint8_t sScenario = 0;
bool sLidOpen = true;
k_timer sProbeTimer;
static a05::History sHistory;
static uint8_t sHistoryBytes[a05::History::MAX_BYTES];

void PublishSnapshot(bool advance)
{
    if (advance) sScenario = (sReportCounter / 1) % 4;
    ++sReportCounter;
    ++sObservationSequence;
    uint8_t frame[110] = {'B', 'S', '0', '5'};
    size_t n = 4;
    auto u8 = [&](uint8_t v) { frame[n++] = v; };
    auto u16 = [&](uint16_t v) { sys_put_le16(v, frame + n); n += 2; };
    auto u32 = [&](uint32_t v) { sys_put_le32(v, frame + n); n += 4; };
    auto u64 = [&](uint64_t v) { sys_put_le64(v, frame + n); n += 8; };
    const int16_t temperature = sScenario == 2 ? INT16_MIN : 2150 + sScenario * 50;
    const uint8_t phase = sScenario == 0 ? 2 : sScenario == 1 ? 3 : 0;
    const uint8_t quality[7] = {1, static_cast<uint8_t>(sScenario == 2 ? 2 : 1),
        1, 1, 1, static_cast<uint8_t>(sScenario == 3 ? 3 : 1),
        static_cast<uint8_t>(sScenario == 2 ? 0 : sScenario == 3 ? 3 : 1)};
    const char * version = sScenario == 3 ? "" : "test-fixture-only";
    u16(1); u8(1); // schema version, test origin
    u32(sBootId); u32(sObservationSequence); u64(k_uptime_get()); u32(sReportCounter);
    u8(sLidOpen); u16(static_cast<uint16_t>(temperature));
    u8(2); u8(sScenario == 1 ? 2 : 1); u8(2); u8(sScenario == 3 ? 0 : 3); u8(phase);
    for (uint8_t q : quality) u8(q);
    u8(sScenario == 3 ? 0 : 2); u16(UINT16_MAX);
    const size_t length = strlen(version);
    u8(length);
    memcpy(frame + n, version, length); n += length;
    // All snapshot fields are committed in one Matter attribute write on the
    // Matter event loop. Standard attributes remain separate projections.
    auto status = Clusters::NordicDevKit::Attributes::SnapshotPacked::Set(1, ByteSpan(frame, n));
    sHistory.append(frame, n);
    const size_t historySize=sHistory.encode(sHistoryBytes, sizeof(sHistoryBytes));
    auto historyStatus=Clusters::NordicDevKit::Attributes::SnapshotHistory::Set(1, ByteSpan(sHistoryBytes, historySize));
    if(historyStatus != Protocols::InteractionModel::Status::Success)
        LOG_ERR("A05 history publish failed status=%u", static_cast<unsigned>(historyStatus));
    Clusters::NordicDevKit::Attributes::ReportCounter::Set(1, sReportCounter);
    if (auto contact = Clusters::BooleanState::FindClusterOnEndpoint(1)) contact->SetStateValue(!sLidOpen);
    if (sScenario == 2) Clusters::TemperatureMeasurement::Attributes::MeasuredValue::SetNull(2);
    else Clusters::TemperatureMeasurement::Attributes::MeasuredValue::Set(2, temperature);
    LOG_INF("A05 TEST boot=%u counter=%u sequence=%u scenario=%u bytes=%u status=%u",
        sBootId, sReportCounter, sObservationSequence, sScenario, static_cast<unsigned>(n), static_cast<unsigned>(status));
    LOG_HEXDUMP_INF(frame, n, "A05 SnapshotPacked test bytes");
}

void ProbeTick(k_timer *)
{
    PlatformMgr().ScheduleWork([](intptr_t) { PublishSnapshot(true); }, 0);
}
