"""Strict decoder for the version-1 offline journal. All CRCs use IEEE CRC32."""
import struct
import zlib

SLOT_SIZE = 8192
FIELDS = ("boot", "sequence", "uptime_ms", "lock_raw", "warm_raw", "white_rice_raw",
          "reservation_raw", "ntc_raw", "vdd_raw", "lid_open", "lock_status",
          "warm_status", "white_rice_status", "reservation_status", "ntc_status")


def crc(data):
    return zlib.crc32(data) & 0xffffffff


def normalized_crc(data):
    return crc(data[:52] + b"\xff" * 4 + data[56:])


def decode_payload(data, count):
    if not 1 <= count <= 200:
        raise ValueError("invalid sample count")
    columns = []
    offset = 0
    for _ in FIELDS:
        if offset + 18 > len(data):
            raise ValueError("short field header")
        mode, width, minimum, first = struct.unpack_from("<BBqq", data, offset)
        offset += 18
        if mode not in (0, 1) or width > 63:
            raise ValueError("invalid field encoding")
        size = ((count - mode) * width + 7) // 8
        if offset + size > len(data):
            raise ValueError("short field payload")
        packed = int.from_bytes(data[offset:offset + size], "little")
        offset += size
        values = [first] if mode else []
        for i in range(count - mode):
            value = minimum + ((packed >> (i * width)) & ((1 << width) - 1))
            values.append(value + values[-1] if mode else value)
        columns.append(values)
    if offset != len(data):
        raise ValueError("trailing payload bytes")
    rows = [dict(zip(FIELDS, values)) for values in zip(*columns)]
    for row in rows:
        if not 0 <= row["boot"] <= 0xffffffff or min(row["sequence"], row["uptime_ms"]) < 0:
            raise ValueError("invalid identity/time")
        if row["lid_open"] not in (-1, 0, 1):
            raise ValueError("invalid lid value")
        if any(not -32768 <= row[name] <= 32767 for name in FIELDS[3:9]):
            raise ValueError("invalid ADC code")
        if any(not -(1 << 31) <= row[name] < (1 << 31) for name in FIELDS[10:]):
            raise ValueError("invalid driver status")
    return rows


def decode_slot(data, expected_id=None):
    if len(data) != SLOT_SIZE or data[:8] != b"BAPSLT1\0":
        raise ValueError("invalid slot size/magic")
    version, header, size, slot_id, encoding = struct.unpack_from("<HHIQI", data, 8)
    if (version, header, size, encoding) != (1, 64, SLOT_SIZE, 1):
        raise ValueError("unsupported slot version")
    if not 0 < slot_id < 0xffffffff or (expected_id is not None and slot_id != expected_id):
        raise ValueError("slot identity mismatch")
    if crc(data[:48]) != struct.unpack_from("<I", data, 48)[0] or data[60:64] != b"\0" * 4:
        raise ValueError("invalid/uncommitted slot header")
    if data[56:60] != b"\0" * 4:
        raise ValueError("slot is still open")
    rows = []
    offset = 64
    tail = "blank"
    while offset + 40 <= SLOT_SIZE:
        h = data[offset:offset + 40]
        if h == b"\xff" * 40:
            # A partial write can leave a nonblank payload after an erased header.
            tail = "blank" if all(x == 255 for x in data[offset:]) else "uncommitted"
            break
        magic, length, count, payload_size, fields, boot, first_seq, first_ms, checksum, header_crc = struct.unpack("<IHHHHIQQII", h)
        if magic != 0x31524342 or crc(h[:36]) != header_crc:
            # Do not acknowledge ambiguous corruption. Unlike a known uncommitted
            # record, its end/commit marker cannot safely be located.
            raise ValueError("corrupt record header; preserve slot for inspection")
        if length != 40 + ((payload_size + 3) & ~3) + 4 or offset + length > SLOT_SIZE or fields != len(FIELDS):
            raise ValueError("invalid record bounds")
        if data[offset + length - 4:offset + length] != b"\0" * 4:
            tail = "uncommitted"
            break
        payload = data[offset + 40:offset + 40 + payload_size]
        if crc(payload) != checksum:
            raise ValueError("committed payload CRC mismatch")
        decoded = decode_payload(payload, count)
        if (decoded[0]["boot"], decoded[0]["sequence"], decoded[0]["uptime_ms"]) != (boot, first_seq, first_ms):
            raise ValueError("record identity mismatch")
        if any(r["boot"] != boot for r in decoded):
            raise ValueError("mixed boot record")
        for a, b in zip(decoded, decoded[1:]):
            if b["sequence"] <= a["sequence"] or b["uptime_ms"] <= a["uptime_ms"]:
                raise ValueError("non-monotonic record")
        if rows and decoded[0]["boot"] == rows[-1]["boot"] and decoded[0]["sequence"] <= rows[-1]["sequence"]:
            raise ValueError("out-of-order records")
        rows.extend(decoded)
        offset += length
    return {"slot_id": slot_id, "config_hash": data[32:48].hex(), "rows": rows, "tail": tail,
            "crc": normalized_crc(data), "used_bytes": offset}


def salvage_prefix(data, expected_id):
    """Recover only CRC-verified records; original damaged slot remains unacknowledged."""
    offset = 64
    result = decode_slot(data[:64] + b"\xff" * (SLOT_SIZE-64), expected_id)
    while offset + 40 <= SLOT_SIZE:
        h = data[offset:offset+40]
        if h[:4] != b"BCR1" or crc(h[:36]) != struct.unpack_from("<I", h, 36)[0]:
            break
        length = struct.unpack_from("<H", h, 4)[0]
        if length < 44 or offset + length > SLOT_SIZE or data[offset+length-4:offset+length] != b"\0"*4:
            break
        end = offset + length
        try:
            candidate = decode_slot(data[:end] + b"\xff" * (SLOT_SIZE-end), expected_id)
        except ValueError:
            break
        result = candidate
        offset = end
    result["tail"] = "quarantined_after_verified_prefix"
    return result
