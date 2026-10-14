uint32_t sBootId=0;
static a08::Pipeline sPipeline;
static a08::Sensors sSensors;
K_MUTEX_DEFINE(sPipelineMutex);
K_SEM_DEFINE(sStartSensors,0,1);
static bool sPublishQueued=false;
static uint8_t sFrame[110], sJournal[a05::History::MAX_BYTES];
// Acquisition owns the ADC and never waits for the model, Matter or logging.
// A dropped queue entry remains a sequence gap; the existing quality gate rejects it.
struct Acquisition {
    a07::Sample sample;
    int16_t raw[5], supply;
    int status[5];
    int32_t mv[5], supply_mv;
    uint32_t min_ms, max_ms, gaps, read_us, late_ms, dropped;
};
K_MSGQ_DEFINE(sAcquired,sizeof(Acquisition),64,8);

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

void ProcessSensors(void *,void *,void *)
{
    Acquisition item{};
    while(true) {
        k_msgq_get(&sAcquired,&item,K_FOREVER);
        const auto &sample=item.sample;
        if(a08::RAW_UART_ENABLED) {
            LOG_INF("A08 SAMPLE boot=%u seq=%u ms=%u raw=%d,%d,%d,%d,%d supply=%d lid=%d",
                sample.boot,unsigned(sample.sequence),unsigned(sample.ms),
                item.raw[0],item.raw[1],item.raw[2],item.raw[3],item.raw[4],item.supply,int(sample.lid));
        }
        if(sample.sequence%200==0) {
            LOG_INF("A08 ADC raw=%d,%d,%d,%d,%d status=%d,%d,%d,%d,%d",
                item.raw[0],item.raw[1],item.raw[2],item.raw[3],item.raw[4],
                item.status[0],item.status[1],item.status[2],item.status[3],item.status[4]);
            LOG_INF("A08 ADC mv=%d,%d,%d,%d,%d",item.mv[0],item.mv[1],item.mv[2],item.mv[3],item.mv[4]);
            LOG_INF("A08 SUPPLY raw=%d mv=%d temperature_enabled=%u",item.supply,item.supply_mv,a08::TEMPERATURE_ENABLED);
        }
        k_mutex_lock(&sPipelineMutex,K_FOREVER);
        const bool report=sPipeline.push(sample);
        if(report && !sPublishQueued) {
            sPublishQueued=true;
            if(PlatformMgr().ScheduleWork(PublishSensors,0)!=CHIP_NO_ERROR) sPublishQueued=false;
        }
        k_mutex_unlock(&sPipelineMutex);
        if(sample.sequence%200==199) {
            LOG_INF("A08 TIMING samples=200 min_ms=%u max_ms=%u gaps=%u",item.min_ms,item.max_ms,item.gaps);
            LOG_INF("A08 ACQUIRE read_max_us=%u late_max_ms=%u queue_dropped=%u",item.read_us,item.late_ms,item.dropped);
        }
    }
}
// Acquisition has priority 0; processing and Matter share priority 1.
// Model execution must never time-slice with the physical sample clock.
K_THREAD_DEFINE(a08_processing,6144,ProcessSensors,nullptr,nullptr,nullptr,1,0,0);

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
    const bool calibrated=sPipeline.reset(a08::CAL,a08::TRUSTED_LED_MASK,a08::LIVE_OBSERVATIONS,a08::LIVE_EDGE_OUTPUT);
    const int errors=sSensors.init();
    LOG_INF("A08 ADC config gain=1/4 reference_mv=%u resolution=12",adc_ref_internal(DEVICE_DT_GET(DT_NODELABEL(adc))));
    LOG_INF("A08 START preprocess=%s model=%s calibration=%s confirmed=%u cal_valid=%u driver_errors=%d",
        a07::PREPROCESS_VERSION,A07_MODEL_VERSION,a08::CAL_VERSION,a08::SENSORS_CONFIRMED,calibrated,errors);
    LOG_INF("A08 CONTACT enabled=%u high_is_open=%u pin=P1.10",a08::CONTACT_ENABLED,a08::HIGH_IS_OPEN);
    LOG_INF("A08 POLICY trusted_led_mask=%u raw_uart=%u",a08::TRUSTED_LED_MASK,a08::RAW_UART_ENABLED);
    uint64_t sequence=0;
    uint64_t previous=0, min_period=UINT64_MAX, max_period=0;
    unsigned gaps=0;
    uint32_t read_max_us=0,late_max_ms=0,dropped=0;
    int64_t next=k_uptime_get();
    while(true) {
        a07::Sample sample{};
        sample.ms=k_uptime_get(); sample.sequence=sequence++; sample.boot=sBootId;
        const uint32_t late=sample.ms>uint64_t(next) ? sample.ms-next : 0;
        if(late>late_max_ms) late_max_ms=late;
        if(sequence>1) {
            const uint64_t period=sample.ms-previous;
            if(period<min_period) min_period=period;
            if(period>max_period) max_period=period;
            gaps+=period<40 || period>60;
        }
        previous=sample.ms;
        const uint32_t read_start=k_cycle_get_32();
        sSensors.read(sample);
        const uint32_t read_us=k_cyc_to_us_floor32(k_cycle_get_32()-read_start);
        if(read_us>read_max_us) read_max_us=read_us;
        Acquisition item{};
        item.sample=sample;
        memcpy(item.raw,sSensors.raw_codes,sizeof(item.raw));
        memcpy(item.status,sSensors.read_status,sizeof(item.status));
        memcpy(item.mv,sSensors.millivolts,sizeof(item.mv));
        item.supply=sSensors.supply_raw; item.supply_mv=sSensors.supply_mv;
        item.min_ms=min_period; item.max_ms=max_period; item.gaps=gaps;
        item.read_us=read_max_us; item.late_ms=late_max_ms; item.dropped=dropped;
        if(k_msgq_put(&sAcquired,&item,K_NO_WAIT)) ++dropped;
        if(sequence%200==0) {
            min_period=UINT64_MAX; max_period=0; gaps=0;
            read_max_us=late_max_ms=0;
        }
        next+=50;
        if(next<=k_uptime_get()) next=k_uptime_get()+50; // no fabricated catch-up samples
        k_sleep(K_TIMEOUT_ABS_MS(next));
    }
}
K_THREAD_DEFINE(a08_thread,6144,SensorThread,nullptr,nullptr,nullptr,0,0,0);
