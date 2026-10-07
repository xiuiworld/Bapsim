uint32_t sBootId=0;
static a08::Pipeline sPipeline;
static a08::Sensors sSensors;
K_MUTEX_DEFINE(sPipelineMutex);
K_SEM_DEFINE(sStartSensors,0,1);
static bool sPublishQueued=false;
static uint8_t sFrame[110], sJournal[a05::History::MAX_BYTES];

void PublishSensors(intptr_t)
{
    k_mutex_lock(&sPipelineMutex,K_FOREVER);
    const size_t n=sPipeline.length;
    memcpy(sFrame,sPipeline.frame,n);
    const size_t h=sPipeline.history.encode(sJournal,sizeof(sJournal));
    const auto count=sPipeline.reports();
    sPublishQueued=false;
    k_mutex_unlock(&sPipelineMutex);
    if(!n) return;
    const auto packed=Clusters::NordicDevKit::Attributes::SnapshotPacked::Set(1,ByteSpan(sFrame,n));
    const auto journal=Clusters::NordicDevKit::Attributes::SnapshotHistory::Set(1,ByteSpan(sJournal,h));
    if(packed==Protocols::InteractionModel::Status::Success && journal==Protocols::InteractionModel::Status::Success)
        Clusters::NordicDevKit::Attributes::ReportCounter::Set(1,count);
    // SnapshotPacked is authoritative; invalid contact has no BooleanState null.
    if(sFrame[27]<=1) {
        if(auto contact=Clusters::BooleanState::FindClusterOnEndpoint(1)) contact->SetStateValue(sFrame[27]==0);
    }
    const int16_t temp=static_cast<int16_t>(sys_get_le16(sFrame+28));
    if(temp==INT16_MIN) Clusters::TemperatureMeasurement::Attributes::MeasuredValue::SetNull(2);
    else Clusters::TemperatureMeasurement::Attributes::MeasuredValue::Set(2,temp);
    LOG_INF("A08 R c=%u p=%u h=%u l=%u f=%u t=%d led=%u,%u,%u,%u",
        count,unsigned(packed),unsigned(journal),sFrame[27],sFrame[34],temp,sFrame[30],sFrame[31],sFrame[32],sFrame[33]);
}

void SensorThread(void *,void *,void *)
{
    k_sem_take(&sStartSensors,K_FOREVER);
    k_sleep(K_SECONDS(12));
    // The SDK minimal UART logger blocks while emitting long packet traces.
    // Keep Matter errors, suppress per-packet progress/detail in the product loop.
    chip::Logging::SetLogRedirectCallback([](const char *module,uint8_t category,const char *msg,va_list args) {
        if(category==chip::Logging::kLogCategory_Error)
            chip::Logging::Platform::LogV(module,category,msg,args);
    });
    // Same compiled pipeline tests run before real acquisition, never published.
    const unsigned failures=a08::selftest(sPipeline);
    LOG_INF("A08 INTEGRATION END failures=%u",failures);
    if(failures) { LOG_ERR("A08 acquisition blocked: selftest failed"); return; }
    const bool calibrated=sPipeline.reset(a08::CAL);
    const int errors=sSensors.init();
    LOG_INF("A08 ADC config gain=1/4 reference_mv=%u resolution=12",adc_ref_internal(DEVICE_DT_GET(DT_NODELABEL(adc))));
    LOG_INF("A08 START preprocess=%s model=%s calibration=%s confirmed=%u cal_valid=%u driver_errors=%d",
        a07::PREPROCESS_VERSION,A07_MODEL_VERSION,a08::CAL_VERSION,a08::SENSORS_CONFIRMED,calibrated,errors);
    LOG_INF("A08 CONTACT enabled=%u high_is_open=%u pin=P1.10",a08::CONTACT_ENABLED,a08::HIGH_IS_OPEN);
    uint64_t sequence=0;
    uint64_t previous=0, min_period=UINT64_MAX, max_period=0;
    unsigned gaps=0;
    int64_t next=k_uptime_get();
    while(true) {
        a07::Sample sample{};
        sample.ms=k_uptime_get(); sample.sequence=sequence++; sample.boot=sBootId;
        if(sequence>1) {
            const uint64_t period=sample.ms-previous;
            if(period<min_period) min_period=period;
            if(period>max_period) max_period=period;
            gaps+=period<40 || period>60;
        }
        previous=sample.ms;
        sSensors.read(sample);
        // Deferred UART logging: acquisition never waits for a diagnostic line.
        // Record every 20 Hz sample for later replay without invented observations.
        {
            LOG_INF("A08 SAMPLE boot=%u seq=%u ms=%u raw=%d,%d,%d,%d,%d supply=%d lid=%d",
                sBootId,unsigned(sample.sequence),unsigned(sample.ms),
                sSensors.raw_codes[0],sSensors.raw_codes[1],sSensors.raw_codes[2],
                sSensors.raw_codes[3],sSensors.raw_codes[4],sSensors.supply_raw,int(sample.lid));
        }
        if(sequence%200==1) {
            LOG_INF("A08 ADC raw=%d,%d,%d,%d,%d status=%d,%d,%d,%d,%d",
                sSensors.raw_codes[0],sSensors.raw_codes[1],sSensors.raw_codes[2],sSensors.raw_codes[3],sSensors.raw_codes[4],
                sSensors.read_status[0],sSensors.read_status[1],sSensors.read_status[2],sSensors.read_status[3],sSensors.read_status[4]);
            LOG_INF("A08 ADC mv=%d,%d,%d,%d,%d",sSensors.millivolts[0],sSensors.millivolts[1],
                sSensors.millivolts[2],sSensors.millivolts[3],sSensors.millivolts[4]);
            LOG_INF("A08 SUPPLY raw=%d mv=%d temperature_enabled=%u",sSensors.supply_raw,sSensors.supply_mv,a08::TEMPERATURE_ENABLED);
        }
        k_mutex_lock(&sPipelineMutex,K_FOREVER);
        const bool report=sPipeline.push(sample);
        if(report && !sPublishQueued) {
            sPublishQueued=true;
            if(PlatformMgr().ScheduleWork(PublishSensors,0)!=CHIP_NO_ERROR) sPublishQueued=false;
        }
        k_mutex_unlock(&sPipelineMutex);
        if(sequence%200==0) {
            LOG_INF("A08 TIMING samples=200 min_ms=%u max_ms=%u gaps=%u",unsigned(min_period),unsigned(max_period),gaps);
            min_period=UINT64_MAX; max_period=0; gaps=0;
        }
        next+=50;
        if(next<=k_uptime_get()) next=k_uptime_get()+50; // no fabricated catch-up samples
        k_sleep(K_TIMEOUT_ABS_MS(next));
    }
}
K_THREAD_DEFINE(a08_thread,6144,SensorThread,nullptr,nullptr,nullptr,0,0,0);
