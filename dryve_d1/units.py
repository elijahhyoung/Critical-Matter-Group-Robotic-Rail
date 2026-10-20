"""
Unit scaling between millimetres and the D1's internal "user units".

The D1 reports/accepts positions, velocities and accelerations in integer
user units whose meaning is defined by three objects:

    6092h:01  Feed constant   - travel per  6092h:02  revolutions of the OUTPUT
                                shaft, i.e. after the 6091h gearbox (CiA 402)
    60A8h     SI unit position  (CiA 303-2 encoding, e.g. 0xFB010000 = 10^-5 m = 0.01 mm)
    60A9h     SI unit velocity  (e.g. 0xFB010300 = 0.01 mm/s, 0x00B44700 = rev/min)

The web UI shows positions with two decimals, and the manual's worked example
("positioning in the 0.01 mm range ... feed = 70 mm x 100 = 7000") uses
0.01 mm units, so that is the default assumption.  `Units.from_drive()` reads
the objects and overrides the defaults with whatever the drive actually says.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .cia402 import OD

_SI_UNIT_NAMES = {
    0x01: "m",
    0x03: "s",
    0x10: "rad",
    0x41: "deg",
    0x47: "min",
    0x48: "h",
    0xB4: "rev",
}


def _prefix_exponent(byte: int) -> int:
    """Signed 8-bit power-of-ten prefix (0xFD -> -3)."""
    return byte - 256 if byte > 127 else byte


def decode_si_position(value: int) -> tuple[str, Optional[float]]:
    """Return (human text, units_per_mm or None if not a length)."""
    prefix = _prefix_exponent((value >> 24) & 0xFF)
    unit = (value >> 16) & 0xFF
    name = _SI_UNIT_NAMES.get(unit, f"unit 0x{unit:02X}")
    text = f"10^{prefix} {name}"
    if unit == 0x01:            # metre
        return text, 10.0 ** (-3 - prefix)
    return text, None


def decode_si_velocity(value: int) -> tuple[str, Optional[float], str]:
    """Return (text, units_per_mm_per_s or None, kind) where kind is
    'linear', 'rotary' or 'unknown'."""
    prefix = _prefix_exponent((value >> 24) & 0xFF)
    num = (value >> 16) & 0xFF
    den = (value >> 8) & 0xFF
    nname = _SI_UNIT_NAMES.get(num, f"unit 0x{num:02X}")
    dname = _SI_UNIT_NAMES.get(den, f"unit 0x{den:02X}") if den else ""
    text = f"10^{prefix} {nname}" + (f"/{dname}" if dname else "")
    per_time = {0x03: 1.0, 0x47: 60.0, 0x48: 3600.0}.get(den)
    if num == 0x01 and per_time is not None:
        return text, 10.0 ** (-3 - prefix) * per_time, "linear"
    if num == 0xB4:
        return text, None, "rotary"
    return text, None, "unknown"


@dataclass
class Units:
    """Scale factors.  `*_per_mm*` = drive units per millimetre (etc.)."""

    pos_per_mm: float = 100.0          # default: 0.01 mm resolution
    vel_per_mm_s: float = 100.0        # default: 0.01 mm/s
    acc_per_mm_s2: float = 100.0       # default: 0.01 mm/s^2
    mm_per_output_rev: Optional[float] = None   # 6092h: Feed Rate on the Axis page
    mm_per_motor_rev: Optional[float] = None    # ... divided by the 6091h gear ratio
    notes: list[str] = field(default_factory=list)

    # ---------------------------------------------------------- conversions
    def pos_to_units(self, mm: float) -> int:
        return int(round(mm * self.pos_per_mm))

    def pos_to_mm(self, units: int) -> float:
        return units / self.pos_per_mm

    def vel_to_units(self, mm_s: float) -> int:
        return max(1, int(round(mm_s * self.vel_per_mm_s)))

    def vel_to_mm_s(self, units: int) -> float:
        return units / self.vel_per_mm_s

    def acc_to_units(self, mm_s2: float) -> int:
        return max(1, int(round(mm_s2 * self.acc_per_mm_s2)))

    # -------------------------------------------------------------- probing
    @classmethod
    def from_drive(cls, gw, *, gear_ratio_hint: float = 5.0,
                   feed_mm_per_output_rev_hint: float = 144.0) -> "Units":
        """Read 60A8h / 6091h / 6092h / 60A9h and build a Units object.  Anything
        the drive does not expose keeps the default and is listed in `notes`.

        The D1 follows CiA 402 here: 6092h is the feed per revolution of the
        *output* (driving) shaft - the Feed Rate you typed on the Axis page -
        and the gearbox is separate in 6091h (motor revs : output revs).  The
        saved config shows exactly that: feedNumerator 14399 / 1, gear 5000 / 1000.
        """
        u = cls()
        # position unit
        try:
            raw = gw.read_u32(OD.SI_UNIT_POSITION)
            text, per_mm = decode_si_position(raw)
            if per_mm:
                u.pos_per_mm = per_mm
                u.notes.append(f"60A8h = 0x{raw:08X} -> position unit {text} ({per_mm:g} units/mm)")
            else:
                u.notes.append(f"60A8h = 0x{raw:08X} -> {text}: not a length, keeping {u.pos_per_mm:g} units/mm")
        except Exception as e:  # noqa: BLE001
            u.notes.append(f"60A8h not readable ({e}); assuming {u.pos_per_mm:g} units/mm")

        # gearbox 6091h (motor revs : output revs; the Motor page 'Gear' setting)
        gear = gear_ratio_hint
        gear_text = f"6091h not readable; assuming {gear_ratio_hint:g}:1"
        try:
            gin = gw.read_u32(OD.GEAR_RATIO, 1)
            gout = gw.read_u32(OD.GEAR_RATIO, 2) or 1
            if gin:
                gear = gin / gout
                gear_text = f"6091h = {gin}:{gout}"
        except Exception as e:  # noqa: BLE001
            gear_text = f"6091h not readable ({e}); assuming {gear_ratio_hint:g}:1"

        # feed constant 6092h -> mm per OUTPUT rev; / gear -> mm per motor rev
        try:
            feed = gw.read_u32(OD.FEED_CONSTANT, 1)
            revs = gw.read_u32(OD.FEED_CONSTANT, 2) or 1
            u.mm_per_output_rev = (feed / u.pos_per_mm) / revs
            u.notes.append(f"6092h = {feed} units / {revs} rev -> {u.mm_per_output_rev:.3f} mm per output rev")
        except Exception as e:  # noqa: BLE001
            u.mm_per_output_rev = feed_mm_per_output_rev_hint
            u.notes.append(f"6092h not readable ({e}); assuming {u.mm_per_output_rev:.3f} mm per output rev")
        u.mm_per_motor_rev = u.mm_per_output_rev / gear
        u.notes.append(f"{gear_text} -> gear {gear:g}:1 -> {u.mm_per_motor_rev:.3f} mm per motor rev")

        # velocity unit
        try:
            raw = gw.read_u32(OD.SI_UNIT_VELOCITY)
            text, per_mm_s, kind = decode_si_velocity(raw)
            if kind == "linear" and per_mm_s:
                u.vel_per_mm_s = per_mm_s
                u.acc_per_mm_s2 = per_mm_s
                u.notes.append(f"60A9h = 0x{raw:08X} -> velocity unit {text} ({per_mm_s:g} units per mm/s)")
            elif kind == "rotary" and u.mm_per_motor_rev:
                prefix = _prefix_exponent((raw >> 24) & 0xFF)
                den = (raw >> 8) & 0xFF
                per_time = {0x03: 1.0, 0x47: 60.0}.get(den, 60.0)
                u.vel_per_mm_s = (1.0 / u.mm_per_motor_rev) * per_time * 10.0 ** (-prefix)
                u.acc_per_mm_s2 = u.vel_per_mm_s
                u.notes.append(f"60A9h = 0x{raw:08X} -> velocity unit {text} (rotary) -> {u.vel_per_mm_s:.4g} units per mm/s")
            else:
                u.notes.append(f"60A9h = 0x{raw:08X} -> {text}: undecoded, keeping {u.vel_per_mm_s:g} units per mm/s")
        except Exception as e:  # noqa: BLE001
            u.notes.append(f"60A9h not readable ({e}); assuming {u.vel_per_mm_s:g} units per mm/s")
        return u

    def describe(self) -> str:
        lines = [
            f"position : {self.pos_per_mm:g} units/mm   (1 unit = {1/self.pos_per_mm:g} mm)",
            f"velocity : {self.vel_per_mm_s:g} units per mm/s",
            f"accel    : {self.acc_per_mm_s2:g} units per mm/s^2",
        ]
        if self.mm_per_output_rev:
            lines.append(f"feed     : {self.mm_per_output_rev:.3f} mm per output (pulley) revolution")
        if self.mm_per_motor_rev:
            lines.append(f"motor    : {self.mm_per_motor_rev:.3f} mm per motor revolution")
        return "\n".join(lines + ["  - " + n for n in self.notes])
