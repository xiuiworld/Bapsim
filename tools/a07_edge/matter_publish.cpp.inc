// A07_SYNTHETIC_MATTER: explicitly started board replay, never live-qualified.
uint32_t sReportCounter = 0, sObservationSequence = 0, sBootId = 0;
k_timer sProbeTimer;
bool sA07ReplayActive = false;
unsigned sA07Stage = 0;
static a07::Preprocessor sA07Processor;
static a05::History sHistory;
static uint8_t sHistoryBytes[a05::History::MAX_BYTES];

void PublishA07Stage()
{
    if (!sA07ReplayActive || sA07Stage >= ARRAY_SIZE(a07_stages)) return;
    const auto &stage = a07_stages[sA07Stage];
    if (!sA07Processor.reset(stage.cal)) {
        LOG_ERR("A07 TEST invalid calibration stage=%u", sA07Stage);
        sA07ReplayActive = false; k_timer_stop(&sProbeTimer); return;
    }
    a07::Result result{};
    bool ready = false;
    for (unsigned i = 0; i < stage.count; ++i) {
        const auto &raw = stage.raw[i];
        a07::Sample sample{};
        sample.ms = raw.ms; sample.sequence = raw.sequence;
        sample.boot = raw.boot; sample.lid = raw.lid;
        for (int j = 0; j < 5; ++j)
            sample.adc[j] = raw.adc[j] == 65535 ? NAN : raw.adc[j];
        ready = sA07Processor.push(sample, result);
    }
    float score = 0;
    const int prediction = ready ? a07_predict(result.x, result.valid, &score) : 3;
    if (!ready || prediction != stage.expected) {
        LOG_ERR("A07 TEST prediction mismatch stage=%u expected=%d actual=%d",
                sA07Stage, stage.expected, prediction);
        sA07ReplayActive = false; k_timer_stop(&sProbeTimer); return;
    }
    const uint32_t next = sReportCounter + 1;
    uint8_t frame[110]; size_t length = 0;
    a07::SnapshotMeta meta{sBootId, sObservationSequence + 1, next,
                           static_cast<uint64_t>(k_uptime_get()),
                           prediction, score, A07_MODEL_VERSION};
    if (!a07::encode_test_snapshot(result, meta, frame, sizeof(frame), length)) {
        LOG_ERR("A07 TEST SnapshotPacked encoding rejected stage=%u", sA07Stage);
        sA07ReplayActive = false; k_timer_stop(&sProbeTimer); return;
    }
    auto status = Clusters::NordicDevKit::Attributes::SnapshotPacked::Set(1, ByteSpan(frame, length));
    if (status != Protocols::InteractionModel::Status::Success) {
        LOG_ERR("A07 TEST Matter SnapshotPacked failed stage=%u status=%u",
                sA07Stage, static_cast<unsigned>(status));
        return; // same stage may be retried; never advance the report counter
    }
    sReportCounter = next; sObservationSequence = meta.sequence;
    sHistory.append(frame, length);
    const size_t historySize=sHistory.encode(sHistoryBytes, sizeof(sHistoryBytes));
    auto historyStatus=Clusters::NordicDevKit::Attributes::SnapshotHistory::Set(1, ByteSpan(sHistoryBytes, historySize));
    if(historyStatus != Protocols::InteractionModel::Status::Success)
        LOG_ERR("A07 history publish failed status=%u", static_cast<unsigned>(historyStatus));
    Clusters::NordicDevKit::Attributes::ReportCounter::Set(1, next);
    if (result.lid_good) {
        if (auto contact = Clusters::BooleanState::FindClusterOnEndpoint(1))
            contact->SetStateValue(result.lid == 0);
    }
    if (result.surface_good)
        Clusters::TemperatureMeasurement::Attributes::MeasuredValue::Set(
            2, static_cast<int16_t>(std::lround(result.surface_c * 100)));
    else
        Clusters::TemperatureMeasurement::Attributes::MeasuredValue::SetNull(2);
    LOG_INF("A07 TEST synthetic=%s model=%s phase=%d boot=%u counter=%u sequence=%u bytes=%u",
            stage.name, A07_MODEL_VERSION, prediction, sBootId, next,
            sObservationSequence, static_cast<unsigned>(length));
    ++sA07Stage;
    if (sA07Stage >= ARRAY_SIZE(a07_stages)) {
        sA07ReplayActive = false;
        k_timer_stop(&sProbeTimer);
        LOG_INF("A07 TEST END stages=%u", sA07Stage);
    }
}

void ProbeTick(k_timer *)
{
    PlatformMgr().ScheduleWork([](intptr_t) { PublishA07Stage(); }, 0);
}

void StartA07Replay()
{
    if (sA07ReplayActive) return;
    sA07Stage = 0;
    sA07ReplayActive = true;
    LOG_INF("A07 TEST BEGIN synthetic=1 model=%s stages=%u", A07_MODEL_VERSION,
            static_cast<unsigned>(ARRAY_SIZE(a07_stages)));
    k_timer_start(&sProbeTimer, K_SECONDS(2), K_SECONDS(15));
}
