// A07 test-only projection into the existing A05 SnapshotPacked v1 attribute.
// Portable C++17. The caller must publish on the Matter event loop and keep
// origin=test; this module does not perform any Matter or B analysis work.
#pragma once
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include "preprocess.hpp"

namespace a07 {
struct SnapshotMeta {
    uint32_t boot, sequence, counter;
    uint64_t uptime_ms;
    int phase; // model 0=inactive, 1=cooking, 2=warming, 3=unknown
    float score;
    const char *model_version;
};

inline bool encode_test_snapshot(const Result &r, const SnapshotMeta &m,
                                 uint8_t *frame, size_t capacity, size_t &length) {
    length=0;
    if(!frame || !m.model_version || m.phase<0 || m.phase>3 ||
       !std::isfinite(m.score) || m.score<0 || m.score>1) return false;
    const size_t version_length=std::strlen(m.model_version);
    if(!version_length || version_length>64 || capacity<46+version_length) return false;
    for(size_t i=0;i<version_length;i++)
        if(static_cast<unsigned char>(m.model_version[i])>127) return false;
    auto u8=[&](uint8_t v){ frame[length++]=v; };
    auto u16=[&](uint16_t v){ u8(v&255); u8(v>>8); };
    auto u32=[&](uint32_t v){ for(int i=0;i<4;i++) u8(v>>(8*i)); };
    auto u64=[&](uint64_t v){ for(int i=0;i<8;i++) u8(v>>(8*i)); };
    u8('B'); u8('S'); u8('0'); u8('5');
    u16(1); u8(1); // schema v1, explicit test origin
    u32(m.boot); u32(m.sequence); u64(m.uptime_ms); u32(m.counter);
    const bool lid_good=r.lid_good && (r.lid==0 || r.lid==1);
    u8(lid_good ? static_cast<uint8_t>(r.lid) : 255);
    const bool temp_good=r.surface_good && std::isfinite(r.surface_c) &&
                         r.surface_c>=-20 && r.surface_c<=130;
    const int16_t centi=temp_good ? static_cast<int16_t>(std::lround(r.surface_c*100)) : static_cast<int16_t>(-32768);
    u16(static_cast<uint16_t>(centi));
    for(int j=0;j<4;j++) u8(r.led_quality[j]==1 && r.led[j]>=1 && r.led[j]<=3 ? r.led[j] : 0);
    u8(m.phase==3 ? 0 : static_cast<uint8_t>(m.phase+1));
    u8(lid_good ? 1 : 0); u8(temp_good ? 1 : 2);
    for(int j=0;j<4;j++) u8(r.led_quality[j]==1 && r.led[j]>=1 && r.led[j]<=3 ? 1 : 0);
    u8(m.phase==3 ? 0 : 1);
    u8(1); // edge_model; never claim rule or live qualification
    u16(m.phase==3 ? 65535 : static_cast<uint16_t>(std::lround(m.score*10000)));
    u8(static_cast<uint8_t>(version_length));
    std::memcpy(frame+length,m.model_version,version_length);
    length+=version_length;
    return true;
}
} // namespace a07
