import json
from contextlib import closing
from pathlib import Path
import sqlite3
import struct
import tempfile
import unittest
from unittest.mock import patch

from tools.a08_storage.format import FIELDS, SLOT_SIZE, crc, decode_slot, normalized_crc, salvage_prefix
from tools.a08_storage.receiver import Receiver


def fixture(slot_id=1, committed=True):
    rows = []
    for i in range(200):
        rows.append(dict(zip(FIELDS, [42, 500+i, (1 << 32)+i*50,
            -32768 if i == 7 else 1200+i%20, 1300, 1400, 1500, 1600, 3400,
            -1 if i == 7 else i%2, -19 if i == 7 else 0, 0, 0, 0, 0])))
    payload = bytearray()
    for key in FIELDS:
        values = [r[key] for r in rows]
        minimum = min(values)
        width = (max(values)-minimum).bit_length()
        packed = sum((v-minimum) << (i*width) for i, v in enumerate(values))
        payload += struct.pack("<BBqq", 0, width, minimum, values[0])
        payload += packed.to_bytes((len(values)*width+7)//8, "little")
    data = bytearray(b"\xff" * SLOT_SIZE)
    data[:8] = b"BAPSLT1\0"
    struct.pack_into("<HHIQII", data, 8, 1, 64, SLOT_SIZE, slot_id, 1, 0)
    data[32:48] = bytes(range(16))
    struct.pack_into("<I", data, 48, crc(data[:48]))
    data[56:64] = b"\0" * 8
    length = 40+((len(payload)+3)&~3)+4
    struct.pack_into("<IHHHHIQQI", data, 64, 0x31524342, length, len(rows), len(payload), len(FIELDS),
                     42, 500, 1 << 32, crc(payload))
    struct.pack_into("<I", data, 100, crc(data[64:100]))
    data[104:104+len(payload)] = payload
    if committed:
        data[64+length-4:64+length] = b"\0" * 4
    return bytes(data), rows


class Port:
    def __init__(self):
        self.commands = []

    def write(self, data):
        self.commands.append(data.decode().strip())
        return len(data)


class OfflineStorageTests(unittest.TestCase):
    def test_signed_values_and_long_uptime_roundtrip(self):
        data, rows = fixture()
        self.assertEqual(decode_slot(data)["rows"], rows)
        self.assertEqual(decode_slot(data)["tail"], "blank")

    def test_torn_uncommitted_body_is_not_a_sample(self):
        data, _ = fixture(committed=False)
        result = decode_slot(data)
        self.assertEqual(result["rows"], [])
        self.assertEqual(result["tail"], "uncommitted")

    def test_committed_corruption_and_open_slot_rejected(self):
        data, _ = fixture()
        for offset in (0, 20, 48, 60, 64, 100, 120):
            damaged = bytearray(data)
            damaged[offset] ^= 1
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                decode_slot(bytes(damaged))
        opened = bytearray(data)
        opened[56:60] = b"\xff"*4
        with self.assertRaises(ValueError):
            decode_slot(bytes(opened))
        with self.assertRaises(ValueError):
            decode_slot(data, 2)

    def test_ack_crc_does_not_depend_on_previous_ack_bits(self):
        data, _ = fixture()
        acked = bytearray(data)
        acked[52:56] = b"\0\0\xff\xff"
        self.assertEqual(normalized_crc(data), normalized_crc(bytes(acked)))

    def test_torn_next_header_preserves_verified_prefix_without_ack(self):
        data, rows = fixture()
        offset = decode_slot(data)["used_bytes"]
        damaged = bytearray(data)
        damaged[offset:offset+8] = b"BCR1\x00\x00\xff\xff"
        with self.assertRaises(ValueError):
            decode_slot(bytes(damaged))
        self.assertEqual(salvage_prefix(bytes(damaged), 1)["rows"], rows)

    def status(self, receiver, port):
        receiver.tick(port)
        payload = f"ready=1 oldest=1 max=1 closed=1 free=767 blocked=0 lost=0 boot=42 ms=4294967296 token={receiver.token}"
        receiver.consume(f"BAPLOG STATUS {payload} crc={crc(payload.encode()):08x}", port)

    def test_pre_suspend_status_cannot_create_false_clock_anchor(self):
        now = [0]
        with tempfile.TemporaryDirectory() as path:
            receiver = Receiver(path, clock=lambda: now[0])
            port = Port()
            receiver.tick(port)
            old_token = receiver.token
            now[0] = 3600
            receiver.tick(port)
            receiver.tick(port)
            self.assertNotEqual(old_token, receiver.token)
            payload = f"ready=1 oldest=0 max=0 closed=0 free=768 blocked=0 lost=0 boot=42 ms=100 token={old_token}"
            receiver.consume(f"BAPLOG STATUS {payload} crc={crc(payload.encode()):08x}", port)
            self.assertEqual(receiver.db.execute("SELECT count(*) FROM anchors").fetchone()[0], 0)
            receiver.close()

    def download(self, receiver, port, data):
        for offset in range(0, SLOT_SIZE, 256):
            chunk = data[offset:offset+256]
            receiver.consume(f"BAPLOG DATA id=1 offset={offset} crc={crc(chunk):08x} hex={chunk.hex()}", port)

    def test_durable_before_ack_and_restart_without_duplicates(self):
        data, rows = fixture()
        with tempfile.TemporaryDirectory() as path:
            receiver = Receiver(path)
            port = Port()
            self.status(receiver, port)
            self.download(receiver, port, data)
            self.assertTrue(port.commands[-1].startswith("baplog ack 1 "))
            with closing(sqlite3.connect(Path(path)/"archive.sqlite3")) as check:
                self.assertEqual(check.execute("SELECT count(*) FROM samples").fetchone()[0], len(rows))
                self.assertEqual(check.execute("SELECT data FROM slots").fetchone()[0], data)
            receiver.close()  # ACK reply lost: after restart durable slot is ACKed directly.
            receiver = Receiver(path)
            port = Port()
            self.status(receiver, port)
            self.assertTrue(port.commands[-1].startswith("baplog ack 1 "))
            self.assertEqual(receiver.db.execute("SELECT count(*) FROM samples").fetchone()[0], 200)
            receiver.close()

    def test_transfer_corruption_never_advances_or_acks(self):
        data, _ = fixture()
        with tempfile.TemporaryDirectory() as path:
            receiver = Receiver(path)
            port = Port()
            self.status(receiver, port)
            receiver.consume(f"BAPLOG DATA id=1 offset=0 crc=00000000 hex={data[:256].hex()}", port)
            self.assertEqual(receiver.offset, 0)
            self.assertFalse(any(" ack " in c for c in port.commands))
            receiver.close()

    def test_disk_export_failure_keeps_flash_unacknowledged(self):
        data, _ = fixture()
        with tempfile.TemporaryDirectory() as path:
            receiver = Receiver(path)
            port = Port()
            self.status(receiver, port)
            with patch.object(receiver, "_export", side_effect=OSError("disk full")), self.assertRaises(OSError):
                self.download(receiver, port, data)
            self.assertFalse(any(" ack " in c for c in port.commands))
            receiver.close()

    def test_bad_slot_quarantined_not_acked_later_slots_accessible(self):
        data, _ = fixture()
        data = bytearray(data)
        data[120] ^= 1
        with tempfile.TemporaryDirectory() as path:
            receiver = Receiver(path)
            port = Port()
            self.status(receiver, port)
            self.download(receiver, port, bytes(data))
            self.assertEqual(receiver.after, 1)
            self.assertFalse(any(" ack " in c for c in port.commands))
            self.assertIsNotNone(receiver.db.execute("SELECT error FROM slots").fetchone()[0])
            receiver.close()


if __name__ == "__main__":
    unittest.main()
