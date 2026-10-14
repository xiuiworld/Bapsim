// Included in the generated A08 app after sBootId and sSensors, not in firmware/.
namespace offline_storage {
using namespace ::baplog;
static_assert(DT_REG_ADDR(DT_NODELABEL(slot1_partition))+DT_REG_SIZE(DT_NODELABEL(slot1_partition))<=BASE,
              "Offline log overlaps the OTA slot");
enum State : uint8_t { UNKNOWN, BLANK, OPEN, CLOSED, ACKED, BLOCKED };
struct Entry { uint32_t id; State state; };
struct Request { const shell *console; uint32_t id,offset,crc; uint8_t type; };
static Entry entries[SLOTS];
static Sample batches[2][BATCH];
static atomic_t batch_state[2], lost;
static uint8_t buffer[SLOT_SIZE], scratch[256];
static uint32_t maximum=0, cursor=0, used=HEADER, records=0;
static int current=-1;
static bool initialized=false;
static const device *memory=DEVICE_DT_GET(DT_NODELABEL(mx25r64));
K_MSGQ_DEFINE(commands,sizeof(Request),8,4);

static uint32_t address(unsigned slot) { return BASE+slot*SLOT_SIZE; }
static bool blank(const uint8_t *p,size_t n) { for(size_t i=0;i<n;++i)if(p[i]!=255)return false;return true; }
static bool erased(unsigned slot) {
    for(unsigned off=0;off<SLOT_SIZE;off+=sizeof(scratch)) {
        if(flash_read(memory,address(slot)+off,scratch,sizeof(scratch)) || !blank(scratch,sizeof(scratch)))return false;
    }
    return true;
}
static bool write_checked(uint32_t off,const uint8_t *data,size_t size) {
    if(off<BASE || off+size>END || flash_write(memory,off,data,size))return false;
    for(size_t i=0;i<size;i+=sizeof(scratch)) {
        size_t n=size-i<sizeof(scratch)?size-i:sizeof(scratch);
        if(flash_read(memory,off+i,scratch,n) || std::memcmp(data+i,scratch,n))return false;
    }
    return true;
}
static bool zero_word(uint32_t off) { const uint8_t zero[4]={};return write_checked(off,zero,4); }
static bool header_valid(const uint8_t *h) {
    return !std::memcmp(h,"BAPSLT1\0",8) && get(h+8,2)==1 && get(h+10,2)==HEADER &&
        get(h+12,4)==SLOT_SIZE && get(h+16,8)>0 && get(h+16,8)<UINT32_MAX &&
        get(h+24,4)==1 && crc32_ieee(h,48)==get(h+48,4) && get(h+60,4)==0;
}
static bool close_current() {
    if(current<0)return true;
    bool ok=zero_word(address(current)+56);
    entries[current].state=ok?CLOSED:BLOCKED;
    current=-1;
    return ok;
}
static bool allocate() {
    if(maximum>=UINT32_MAX-1)return false;
    for(unsigned attempt=0;attempt<SLOTS;++attempt) {
        int candidate=-1;
        for(unsigned i=0;i<SLOTS;++i) {
            unsigned slot=(cursor+i)%SLOTS;
            if(entries[slot].state==BLANK){candidate=int(slot);break;}
        }
        if(candidate<0) {
            uint32_t oldest=UINT32_MAX;
            for(unsigned i=0;i<SLOTS;++i)if(entries[i].state==ACKED && entries[i].id<oldest && entries[i].id!=maximum) {
                oldest=entries[i].id;candidate=int(i);
            }
        }
        if(candidate<0)return false;
        if(entries[candidate].state==ACKED && flash_erase(memory,address(candidate),SLOT_SIZE)) {
            entries[candidate].state=BLOCKED;continue;
        }
        // Never erase unknown contents, including a torn/unrecognized slot header.
        if(!erased(candidate)){entries[candidate].state=BLOCKED;continue;}
        std::memset(buffer,255,HEADER);
        std::memcpy(buffer,"BAPSLT1\0",8);
        put(buffer+8,1,2);put(buffer+10,HEADER,2);put(buffer+12,SLOT_SIZE,4);
        put(buffer+16,maximum+1,8);put(buffer+24,1,4);put(buffer+28,0,4);
        std::memcpy(buffer+32,BAPLOG_CONFIG_HASH,16);
        put(buffer+48,crc32_ieee(buffer,48),4);
        if(!write_checked(address(candidate),buffer,60) || !zero_word(address(candidate)+60)) {
            entries[candidate].state=BLOCKED;continue;
        }
        entries[candidate]={++maximum,OPEN};current=candidate;cursor=(candidate+1)%SLOTS;
        used=HEADER;records=0;
        return true;
    }
    return false;
}
static bool save(const Sample *samples,unsigned count) {
    // Allocate before encoding: both operations share a bounded scratch buffer.
    if(current<0 && !allocate())return false;
    size_t n=encode(samples,count,buffer+40,SLOT_SIZE-HEADER-44);
    if(!n) {
        if(count<2)return false;
        unsigned half=count/2;
        return save(samples,half) && save(samples+half,count-half);
    }
    if(!matches(buffer+40,n,samples,count))return false;
    unsigned length=40+((n+3)&~3u)+4;
    if(used+length>SLOT_SIZE) {
        if(!close_current() || !allocate())return false;
        return save(samples,count);
    }
    put(buffer,0x31524342,4);put(buffer+4,length,2);put(buffer+6,count,2);
    put(buffer+8,n,2);put(buffer+10,FIELDS,2);put(buffer+12,samples[0].boot,4);
    put(buffer+16,samples[0].sequence,8);put(buffer+24,samples[0].ms,8);
    put(buffer+32,crc32_ieee(buffer+40,n),4);put(buffer+36,crc32_ieee(buffer,36),4);
    std::memset(buffer+40+n,255,length-40-n);
    uint32_t offset=address(current)+used;
    if(!write_checked(offset,buffer,length-4) || !zero_word(offset+length-4)) {
        close_current();return false;
    }
    used+=length;
    if(++records>=6)return close_current(); // Make archived blocks available within about a minute.
    return true;
}
static void initialize() {
    if(!device_is_ready(memory))return;
    for(unsigned slot=0;slot<SLOTS;++slot) {
        if(flash_read(memory,address(slot),buffer,HEADER)){entries[slot].state=BLOCKED;continue;}
        if(blank(buffer,HEADER)){entries[slot].state=BLANK;continue;}
        if(!header_valid(buffer)){entries[slot].state=BLOCKED;continue;}
        uint32_t id=get(buffer+16,8);
        for(unsigned j=0;j<slot;++j)if(entries[j].id==id) {
            LOG_ERR("A08 STORE duplicate_id=%u; logging disabled",id);return;
        }
        if(id>maximum)maximum=id;
        if(get(buffer+56,4)!=0 && !zero_word(address(slot)+56)) {entries[slot]={id,BLOCKED};continue;}
        entries[slot]={id,get(buffer+52,4)==0?ACKED:CLOSED};
    }
    initialized=true;
    LOG_INF("A08 STORE ready slots=%u max_id=%u batch=%u",SLOTS,maximum,BATCH);
}
static int find(uint32_t id) {
    for(unsigned i=0;i<SLOTS;++i)if(entries[i].id==id && (entries[i].state==CLOSED||entries[i].state==ACKED))return int(i);
    return -1;
}
static void respond(const Request &r) {
    if(r.type==0) {
        uint32_t oldest=UINT32_MAX, closed=0,free=0,blocked=0;
        for(const auto &entry:entries) {
            if(entry.state==CLOSED){++closed;if(entry.id>r.id && entry.id<oldest)oldest=entry.id;}
            if(entry.state==BLANK||entry.state==ACKED)++free;
            if(entry.state==BLOCKED)++blocked;
        }
        char line[240];
        int length=snprintf(line,sizeof(line),"ready=%u oldest=%u max=%u closed=%u free=%u blocked=%u lost=%u boot=%u ms=%llu token=%u",
            unsigned(initialized),oldest==UINT32_MAX?0:oldest,maximum,closed,free,blocked,unsigned(atomic_get(&lost)),sBootId,
            static_cast<unsigned long long>(k_uptime_get()),r.offset);
        if(length>0 && unsigned(length)<sizeof(line))
            shell_print(r.console,"BAPLOG STATUS %s crc=%08x",line,crc32_ieee(reinterpret_cast<const uint8_t *>(line),length));
        return;
    }
    int slot=find(r.id);
    if(slot<0){shell_print(r.console,"BAPLOG ERROR id=%u reason=not_closed",r.id);return;}
    if(r.type==1) {
        if(r.offset>=SLOT_SIZE || r.offset%256){shell_print(r.console,"BAPLOG ERROR id=%u reason=offset",r.id);return;}
        if(flash_read(memory,address(slot)+r.offset,scratch,256)){shell_print(r.console,"BAPLOG ERROR id=%u reason=read",r.id);return;}
        char hex[513];const char digits[]="0123456789abcdef";
        for(unsigned i=0;i<256;++i){hex[i*2]=digits[scratch[i]>>4];hex[i*2+1]=digits[scratch[i]&15];}hex[512]=0;
        shell_print(r.console,"BAPLOG DATA id=%u offset=%u crc=%08x hex=%s",r.id,r.offset,crc32_ieee(scratch,256),hex);
        return;
    }
    if(flash_read(memory,address(slot),buffer,SLOT_SIZE)){shell_print(r.console,"BAPLOG ERROR id=%u reason=read",r.id);return;}
    std::memset(buffer+52,255,4); // Idempotent even after a torn ACK write.
    if(crc32_ieee(buffer,SLOT_SIZE)!=r.crc){shell_print(r.console,"BAPLOG ERROR id=%u reason=crc",r.id);return;}
    if(!zero_word(address(slot)+52)){shell_print(r.console,"BAPLOG ERROR id=%u reason=ack_write",r.id);return;}
    entries[slot].state=ACKED;
    shell_print(r.console,"BAPLOG ACK id=%u crc=%08x",r.id,r.crc);
}
static bool number(const char *text,uint32_t &value,int base=10) {
    if(!text||!*text||*text=='-')return false;
    char *end=nullptr;errno=0;unsigned long n=strtoul(text,&end,base);
    if(errno || *end || n>UINT32_MAX)return false;
    value=uint32_t(n);return true;
}
static int command(const shell *console,size_t argc,char **argv) {
    Request request{};request.console=console;
    if(!strcmp(argv[0],"status")) {
        request.type=0;
        if(argc>1 && !number(argv[1],request.id))return -EINVAL;
        if(argc>2 && !number(argv[2],request.offset))return -EINVAL;
    }
    else {
        if(argc!=3||!number(argv[1],request.id))return -EINVAL;
        request.type=!strcmp(argv[0],"get")?1:2;
        if(!number(argv[2],request.type==1?request.offset:request.crc,request.type==1?10:16))return -EINVAL;
    }
    if(k_msgq_put(&commands,&request,K_NO_WAIT)){shell_print(console,"BAPLOG BUSY");return -EBUSY;}
    return 0;
}
SHELL_STATIC_SUBCMD_SET_CREATE(baplog_commands,
    SHELL_CMD_ARG(status,NULL,"Read logging status [after_id token]",command,1,2),
    SHELL_CMD_ARG(get,NULL,"Read closed slot: id offset",command,3,0),
    SHELL_CMD_ARG(ack,NULL,"Acknowledge durable slot: id normalized_crc32",command,3,0),
    SHELL_SUBCMD_SET_END);
SHELL_CMD_REGISTER(baplog,&baplog_commands,"Offline sensor archive",NULL);

static void push(uint32_t boot,uint64_t sequence,uint64_t ms,const int16_t *raw,int16_t supply,int lid,const int *status) {
    static int active=-1;static unsigned count=0;
    if(active<0)for(int i=0;i<2;++i)if(atomic_cas(&batch_state[i],0,1)){active=i;break;}
    if(active<0){atomic_inc(&lost);return;}
    auto &sample=batches[active][count];sample.boot=boot;sample.sequence=sequence;sample.ms=ms;
    for(unsigned i=0;i<5;++i){sample.raw[i]=raw[i];sample.status[i]=status[i];}
    sample.raw[5]=supply;sample.lid=lid;
    if(++count==BATCH){atomic_set(&batch_state[active],2);active=-1;count=0;}
}
static void worker(void*,void*,void*) {
    unsigned failures=0;
    for(unsigned variant=0;variant<4;++variant) {
        for(unsigned i=0;i<BATCH;++i) {
            auto &s=batches[0][i];s.boot=42;s.sequence=0x100000000ULL+i;s.ms=0x100000000ULL+i*50;
            for(unsigned j=0;j<6;++j)s.raw[j]=variant==0?0:variant==1?int16_t(i%2?-32768:32767):int16_t((i*7919+j*113)%4096);
            for(unsigned j=0;j<5;++j)s.status[j]=variant==3?-19:0;
            s.lid=variant==3?-1:int8_t(i%2);
        }
        size_t n=encode(batches[0],BATCH,buffer,sizeof(buffer));
        if(!n||!matches(buffer,n,batches[0],BATCH)||matches(buffer,n-1,batches[0],BATCH))++failures;
    }
    // All slots unacknowledged: allocation must fail without touching flash.
    for(auto &entry:entries)entry={1,CLOSED};
    if(allocate())++failures;
    std::memset(entries,0,sizeof(entries));
    LOG_INF("A08 STORE selftest failures=%u",failures);
    if(failures)return;
    initialize();
    while(true) {
        // Select the oldest ready batch; producer always remains nonblocking.
        int selected=-1;
        for(int i=0;i<2;++i)if(atomic_get(&batch_state[i])==2 &&
            (selected<0 || batches[i][0].sequence<batches[selected][0].sequence))selected=i;
        if(selected>=0 && atomic_cas(&batch_state[selected],2,3)) {
            if(!initialized||!save(batches[selected],BATCH)) {
                atomic_add(&lost,BATCH);LOG_ERR("A08 STORE not_saved first_seq=%llu lost=%u",static_cast<unsigned long long>(batches[selected][0].sequence),unsigned(atomic_get(&lost)));
            }
            atomic_set(&batch_state[selected],0);
        }
        Request request;
        if(!k_msgq_get(&commands,&request,K_NO_WAIT))respond(request);
        k_sleep(K_MSEC(5));
    }
}
K_THREAD_DEFINE(baplog_thread,4096,worker,nullptr,nullptr,nullptr,10,0,0);
}
