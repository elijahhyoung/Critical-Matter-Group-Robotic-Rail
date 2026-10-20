"""
Modbus TCP *Gateway* transport for the igus dryve D1.

The D1 does not expose a normal Modbus register map.  Instead it tunnels
CANopen SDO (object-dictionary) reads/writes inside Modbus function code 43
(0x2B) / MEI type 13 (0x0D) -- this is the "Modbus TCP Gateway" described in
the dryve D1 manual, chapter "Modbus TCP Gateway".

Telegram layout (all numbers are byte offsets):

    0-1   Transaction ID   (big-endian, echoed back by the D1)
    2-3   Protocol ID      (always 0)
    4-5   Length           (big-endian; bytes that follow = 13 + n_data)
    6     Unit ID          ("Unit Identifier" on the Communication page; this D1 is set to 255)
    7     Function code    0x2B (43)
    8     MEI type         0x0D (13)
    9     Protocol control 0 = read, 1 = write
    10    reserved         0
    11    Node ID          0
    12-13 Object index     big-endian (e.g. 0x60 0x40 for 6040h)
    14    Sub-index
    15-17 reserved         0
    18    Byte count       number of data bytes (1..4)
    19..  Data             LITTLE-endian (byte 19 = LSB)

Errors come back with byte 7 = 0xAB (0x2B + 0x80) and the exception code in byte 8.

Only the Python standard library is used.
"""
from __future__ import annotations

import socket
import struct
import threading
from dataclasses import dataclass
from typing import Optional

FUNCTION_CODE = 0x2B
MEI_TYPE = 0x0D
EXCEPTION_FLAG = 0x80
DEFAULT_UNIT_ID = 255          # must match "Unit Identifier" on the D1 Communication page

MODBUS_EXCEPTIONS = {
    0x01: "Illegal function",
    0x02: "Illegal data address (object/sub-index does not exist)",
    0x03: "Illegal data value (wrong length or out of range)",
    0x04: "Slave device failure",
    0x05: "Acknowledge",
    0x06: "Slave device busy",
    0x0A: "Gateway path unavailable",
    0x0B: "Gateway target device failed to respond",
}


class GatewayError(RuntimeError):
    """Transport-level or framing problem."""


class GatewayException(GatewayError):
    """The D1 answered with a Modbus exception (byte 7 = 0xAB)."""

    def __init__(self, index: int, subindex: int, code: int):
        self.index = index
        self.subindex = subindex
        self.code = code
        desc = MODBUS_EXCEPTIONS.get(code, "unknown")
        super().__init__(
            f"D1 rejected access to {index:04X}h:{subindex:02X} "
            f"(exception 0x{code:02X}: {desc})"
        )


@dataclass
class Response:
    transaction_id: int
    unit_id: int
    is_write: bool
    index: int
    subindex: int
    data: bytes


class D1Gateway:
    """Low-level object-dictionary access.  Thread-safe (one request at a time)."""

    def __init__(self, host: str, port: int = 502, unit_id: int = DEFAULT_UNIT_ID,
                 timeout: float = 2.0, debug: bool = False):
        self.host = host
        self.port = port
        self.unit_id = unit_id
        self.timeout = timeout
        self.debug = debug
        self._sock: Optional[socket.socket] = None
        self._tid = 0
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ life
    def connect(self) -> "D1Gateway":
        s = socket.create_connection((self.host, self.port), timeout=self.timeout)
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self._sock = s
        return self

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None

    @property
    def connected(self) -> bool:
        return self._sock is not None

    def __enter__(self) -> "D1Gateway":
        return self.connect()

    def __exit__(self, *exc) -> None:
        self.close()

    # ------------------------------------------------------------- framing
    def _build(self, write: bool, index: int, subindex: int, data: bytes, nbytes: int) -> bytes:
        self._tid = (self._tid + 1) & 0xFFFF
        pdu = bytes([
            FUNCTION_CODE, MEI_TYPE,
            0x01 if write else 0x00,
            0x00, 0x00,
            (index >> 8) & 0xFF, index & 0xFF,
            subindex & 0xFF,
            0x00, 0x00, 0x00,
            nbytes & 0xFF,
        ]) + data
        mbap = struct.pack(">HHHB", self._tid, 0, 1 + len(pdu), self.unit_id)
        return mbap + pdu

    def _recv_exact(self, n: int) -> bytes:
        assert self._sock is not None
        buf = bytearray()
        while len(buf) < n:
            chunk = self._sock.recv(n - len(buf))
            if not chunk:
                raise GatewayError("connection closed by D1")
            buf += chunk
        return bytes(buf)

    def _xfer(self, frame: bytes) -> Response:
        if self._sock is None:
            raise GatewayError("not connected")
        with self._lock:
            if self.debug:
                print("TX", list(frame))
            self._sock.sendall(frame)
            try:
                head = self._recv_exact(6)
            except socket.timeout:
                raise GatewayError(
                    f"timeout waiting for D1 at {self.host}:{self.port} - is Modbus TCP "
                    "switched ON on the Communication page?") from None
            tid, _pid, length = struct.unpack(">HHH", head)
            body = self._recv_exact(length)
            if self.debug:
                print("RX", list(head + body))

        resp = head + body
        if len(resp) < 9:
            raise GatewayError(f"short response: {list(resp)}")
        func = resp[7]
        if func == (FUNCTION_CODE | EXCEPTION_FLAG):
            raise GatewayException(
                (frame[12] << 8) | frame[13], frame[14], resp[8])
        if func != FUNCTION_CODE:
            raise GatewayError(f"unexpected function code 0x{func:02X} in {list(resp)}")
        if len(resp) < 19:
            raise GatewayError(f"truncated gateway response: {list(resp)}")
        return Response(
            transaction_id=tid,
            unit_id=resp[6],
            is_write=bool(resp[9]),
            index=(resp[12] << 8) | resp[13],
            subindex=resp[14],
            data=resp[19:19 + resp[18]],
        )

    # ------------------------------------------------------------- raw I/O
    def read(self, index: int, subindex: int = 0, nbytes: int = 4) -> bytes:
        """Read `nbytes` (1..4) of an object.  Returns little-endian bytes,
        zero-padded to `nbytes` (the D1 may return fewer bytes for 16-bit objects)."""
        if nbytes not in (1, 2, 3, 4):
            raise ValueError("nbytes must be 1..4")
        resp = self._xfer(self._build(False, index, subindex, b"", nbytes))
        if resp.index != index or resp.subindex != subindex:
            raise GatewayError(
                f"response for {resp.index:04X}h:{resp.subindex} but asked {index:04X}h:{subindex}")
        return (resp.data + b"\x00" * nbytes)[:nbytes]

    def write(self, index: int, subindex: int, data: bytes) -> None:
        if not 1 <= len(data) <= 4:
            raise ValueError("data must be 1..4 bytes")
        resp = self._xfer(self._build(True, index, subindex, bytes(data), len(data)))
        if resp.index != index or resp.subindex != subindex:
            raise GatewayError(
                f"write ack for {resp.index:04X}h:{resp.subindex} but wrote {index:04X}h:{subindex}")

    # --------------------------------------------------------- typed helpers
    def read_u8(self, index: int, sub: int = 0) -> int:
        return self.read(index, sub, 1)[0]

    def read_i8(self, index: int, sub: int = 0) -> int:
        return struct.unpack("<b", self.read(index, sub, 1))[0]

    def read_u16(self, index: int, sub: int = 0) -> int:
        return struct.unpack("<H", self.read(index, sub, 2))[0]

    def read_i16(self, index: int, sub: int = 0) -> int:
        return struct.unpack("<h", self.read(index, sub, 2))[0]

    def read_u32(self, index: int, sub: int = 0) -> int:
        return struct.unpack("<I", self.read(index, sub, 4))[0]

    def read_i32(self, index: int, sub: int = 0) -> int:
        return struct.unpack("<i", self.read(index, sub, 4))[0]

    def write_u8(self, index: int, sub: int, value: int) -> None:
        self.write(index, sub, struct.pack("<B", value & 0xFF))

    def write_i8(self, index: int, sub: int, value: int) -> None:
        self.write(index, sub, struct.pack("<b", value))

    def write_u16(self, index: int, sub: int, value: int) -> None:
        self.write(index, sub, struct.pack("<H", value & 0xFFFF))

    def write_u32(self, index: int, sub: int, value: int) -> None:
        self.write(index, sub, struct.pack("<I", value & 0xFFFFFFFF))

    def write_i32(self, index: int, sub: int, value: int) -> None:
        self.write(index, sub, struct.pack("<i", int(value)))
