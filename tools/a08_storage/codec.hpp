#pragma once
#include <cstdint>
#include <cstddef>
#include <cstring>

namespace baplog {
constexpr unsigned BATCH = 200, FIELDS = 15, SLOT_SIZE = 8192, HEADER = 64;
constexpr uint32_t BASE = 0x200000, END = 0x800000, SLOTS = (END-BASE)/SLOT_SIZE;
struct Sample {
    uint64_t sequence, ms;
    uint32_t boot;
    int16_t raw[6];
    int32_t status[5];
    int8_t lid;
};
inline int64_t field(const Sample &s, unsigned f) {
    if (f==0) return s.boot;
    if (f==1) return static_cast<int64_t>(s.sequence);
    if (f==2) return static_cast<int64_t>(s.ms);
    if (f<9) return s.raw[f-3];
    if (f==9) return s.lid;
    return s.status[f-10];
}
inline void put(uint8_t *p, uint64_t x, unsigned n) { for(unsigned i=0;i<n;++i) p[i]=uint8_t(x>>(8*i)); }
inline uint64_t get(const uint8_t *p,unsigned n) { uint64_t x=0;for(unsigned i=0;i<n;++i)x|=uint64_t(p[i])<<(8*i);return x; }
inline unsigned width(uint64_t n) { unsigned w=0;while(n){++w;n>>=1;}return w; }
// Actual domains fit in signed 64 bits. Device uptime/sequence must stay below 2^63.
// No heap, no compressor dictionary; caller owns the bounded output buffer.
inline size_t encode(const Sample *s,unsigned count,uint8_t *out,size_t capacity) {
    if(!count || count>BATCH) return 0;
    size_t pos=0;
    for(unsigned f=0;f<FIELDS;++f) {
        int64_t lo[2]={field(s[0],f),0}, hi[2]={lo[0],0};
        if(count>1) lo[1]=hi[1]=field(s[1],f)-field(s[0],f);
        for(unsigned i=0;i<count;++i) {
            int64_t v=field(s[i],f);
            if(v<lo[0])lo[0]=v;
            if(v>hi[0])hi[0]=v;
            if(i) { v-=field(s[i-1],f);if(v<lo[1])lo[1]=v;if(v>hi[1])hi[1]=v; }
        }
        unsigned widths[2]={width(uint64_t(hi[0]-lo[0])),width(uint64_t(hi[1]-lo[1]))};
        unsigned mode=(count-1)*widths[1]<count*widths[0], w=widths[mode];
        size_t bytes=((count-mode)*w+7)/8;
        if(w>63 || pos+18+bytes>capacity) return 0;
        out[pos]=mode;out[pos+1]=w;put(out+pos+2,uint64_t(lo[mode]),8);put(out+pos+10,uint64_t(field(s[0],f)),8);
        pos+=18;std::memset(out+pos,0,bytes);
        size_t bit=0;
        for(unsigned i=mode;i<count;++i) {
            uint64_t v=uint64_t(field(s[i],f)-(mode?field(s[i-1],f):0)-lo[mode]);
            for(unsigned k=0;k<w;++k,++bit)out[pos+bit/8]|=uint8_t(((v>>k)&1)<<(bit%8));
        }
        pos+=bytes;
    }
    return pos;
}
inline bool matches(const uint8_t *data,size_t size,const Sample *s,unsigned count) {
    size_t pos=0;
    for(unsigned f=0;f<FIELDS;++f) {
        if(pos+18>size)return false;
        unsigned mode=data[pos],w=data[pos+1];
        if(mode>1||w>63)return false;
        int64_t lo=int64_t(get(data+pos+2,8)),prev=int64_t(get(data+pos+10,8));pos+=18;
        size_t bytes=((count-mode)*w+7)/8;
        if(pos+bytes>size || (mode && prev!=field(s[0],f)))return false;
        size_t bit=0;
        for(unsigned i=mode;i<count;++i) {
            uint64_t v=0;
            for(unsigned k=0;k<w;++k,++bit)v|=uint64_t((data[pos+bit/8]>>(bit%8))&1)<<k;
            int64_t value=int64_t(v)+lo+(mode?prev:0);
            if(value!=field(s[i],f))return false;
            prev=value;
        }
        pos+=bytes;
    }
    return pos==size;
}
}
