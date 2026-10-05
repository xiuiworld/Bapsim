// Bounded raw SnapshotPacked journal. Call only on the Matter event loop.
#pragma once
#include <cstddef>
#include <cstdint>
#include <cstring>
namespace a05 {
class History {
    uint8_t frames[8][110]{};
    uint8_t sizes[8]{};
    unsigned head=0, count=0;
public:
    static constexpr size_t MAX_BYTES=4+8*111;
    bool append(const uint8_t *frame, size_t n) {
        if(!frame || n<46 || n>110) return false;
        std::memcpy(frames[head],frame,n); sizes[head]=static_cast<uint8_t>(n);
        head=(head+1)%8; if(count<8) ++count;
        return true;
    }
    size_t encode(uint8_t *out, size_t capacity) const {
        if(!out || capacity<MAX_BYTES) return 0;
        out[0]='B'; out[1]='H'; out[2]=1; out[3]=static_cast<uint8_t>(count);
        size_t pos=4;
        for(unsigned i=0;i<count;++i) {
            unsigned j=(head+8-count+i)%8;
            out[pos++]=sizes[j]; std::memcpy(out+pos,frames[j],sizes[j]); pos+=sizes[j];
        }
        return pos;
    }
};
} // namespace a05
