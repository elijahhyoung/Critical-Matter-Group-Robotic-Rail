"""
CiA 402 (CANopen drive profile) constants as implemented by the dryve D1.

Object indices, controlword commands, statusword bits and the state
decoder.  Everything here is pure data / pure functions.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, IntEnum


# ------------------------------------------------------------ object dict
class OD(IntEnum):
    STORE_PARAMETERS = 0x1010
    ERROR_CODE = 0x603F
    CONTROLWORD = 0x6040
    STATUSWORD = 0x6041
    MODES_OF_OPERATION = 0x6060
    MODES_OF_OPERATION_DISPLAY = 0x6061
    POSITION_ACTUAL = 0x6064
    POSITION_WINDOW = 0x6067
    POSITION_WINDOW_TIME = 0x6068
    VELOCITY_ACTUAL = 0x606C
    TARGET_POSITION = 0x607A
    POSITION_RANGE_LIMIT = 0x607B      # sub1 = min, sub2 = max
    MAX_PROFILE_VELOCITY = 0x607F      # mirrors Axis -> Max. Velocity in the UI
    PROFILE_VELOCITY = 0x6081
    PROFILE_ACCELERATION = 0x6083
    PROFILE_DECELERATION = 0x6084
    QUICK_STOP_DECELERATION = 0x6085
    GEAR_RATIO = 0x6091                # sub1 = motor revs, sub2 = output revs
    FEED_CONSTANT = 0x6092             # sub1 = feed, sub2 = shaft revolutions
    HOMING_METHOD = 0x6098             # read-only on the D1 (set in web UI)
    HOMING_SPEEDS = 0x6099             # sub1 = switch search, sub2 = zero search
    HOMING_ACCELERATION = 0x609A
    SI_UNIT_POSITION = 0x60A8
    SI_UNIT_VELOCITY = 0x60A9
    MAX_ACCELERATION = 0x60C5          # mirrors Axis -> Max. Acceleration in the UI
    DIGITAL_INPUTS = 0x60FD
    DIGITAL_OUTPUTS = 0x60FE
    TARGET_VELOCITY = 0x60FF
    SUPPORTED_DRIVE_MODES = 0x6502
    D1_HOMING_STATUS = 0x2014          # dryve-specific


# 60FDh digital inputs (manual V3.2.3, p. 181): bit 0 = DI 9 negative limit
# switch, bit 1 = DI 8 positive limit switch, bit 3 = Enable, bits 16.. = DI 1..
# DI 10 after the UI polarity setting, so DI 7 = bit 22 (redundant with bit 3).
# Seen live on 8 Oct 2026: 0x00400008.
DI_INPUT_SHIFT = 16
DI7_INPUT_BIT = 1 << (DI_INPUT_SHIFT + 6)


# 603Fh error code -> the E-number the web UI / X9 display shows
ERROR_CODES = {
    0x0000: "no error",
    0x6320: "E01 configuration",
    0x2320: "E02 motor over-current",
    0x5114: "E05 I/O supply low",
    0x3222: "E06 logic supply low",
    0x3112: "E07 logic supply high",
    0x3221: "E08 load supply low",
    0x3211: "E09 load supply high",
    0x4310: "E10 temperature",
    0x8611: "E11 following error",
    0xFF00: "E12 limit switch",
    0x7306: "E13 Hall sensor",
    0x7110: "E21 braking resistor overload",
}


def error_text(code: int) -> str:
    return f"0x{code:04X} {ERROR_CODES.get(code, 'unknown error')}"


class Mode(IntEnum):
    PROFILE_POSITION = 1
    PROFILE_VELOCITY = 3
    HOMING = 6
    CYCLIC_SYNC_POSITION = 8


class HomingMethod(IntEnum):
    """CiA 402 method numbers (D1 maps its UI choices onto these)."""
    LIMIT_SWITCH_NEGATIVE = 17   # LSN
    LIMIT_SWITCH_POSITIVE = 18   # LSP
    SET_CURRENT_POSITION = 37    # SCP


# ------------------------------------------------------------ controlword
class CW:
    SWITCH_ON = 1 << 0
    ENABLE_VOLTAGE = 1 << 1
    QUICK_STOP_N = 1 << 2      # 1 = NOT quick-stopping
    ENABLE_OPERATION = 1 << 3
    NEW_SETPOINT = 1 << 4      # PP: start; Homing: start
    CHANGE_IMMEDIATELY = 1 << 5
    RELATIVE = 1 << 6
    FAULT_RESET = 1 << 7
    HALT = 1 << 8

    DISABLE_VOLTAGE = 0x0000
    SHUTDOWN = 0x0006          # -> Ready to switch on
    SWITCH_ON_CMD = 0x0007     # -> Switched on
    ENABLE_OP = 0x000F         # -> Operation enabled (hold bits 0..3 from now on)
    QUICK_STOP = 0x0002        # bit 2 low -> quick stop
    START_ABS = 0x001F         # ENABLE_OP | NEW_SETPOINT
    START_REL = 0x005F         # ENABLE_OP | NEW_SETPOINT | RELATIVE
    HALT_CMD = 0x010F          # ENABLE_OP | HALT
    RESET_FAULT = 0x0080


# ------------------------------------------------------------- statusword
class SW:
    READY_TO_SWITCH_ON = 1 << 0
    SWITCHED_ON = 1 << 1
    OPERATION_ENABLED = 1 << 2
    FAULT = 1 << 3
    VOLTAGE_ENABLED = 1 << 4
    QUICK_STOP = 1 << 5
    SWITCH_ON_DISABLED = 1 << 6
    WARNING = 1 << 7
    REMOTE = 1 << 9            # manual: mirrors DI 7 'Enable'; read 0 on 8 Oct 2026 while not under bus control
    TARGET_REACHED = 1 << 10
    INTERNAL_LIMIT = 1 << 11
    SETPOINT_ACK = 1 << 12     # PP: set-point acknowledged; Homing: attained
    FOLLOWING_ERROR = 1 << 13  # PP: following error; Homing: homing error


class State(str, Enum):
    NOT_READY = "not ready to switch on"
    SWITCH_ON_DISABLED = "switch on disabled"
    READY_TO_SWITCH_ON = "ready to switch on"
    SWITCHED_ON = "switched on"
    OPERATION_ENABLED = "operation enabled"
    QUICK_STOP_ACTIVE = "quick stop active"
    FAULT_REACTION = "fault reaction active"
    FAULT = "fault"
    UNKNOWN = "unknown"


def decode_state(sw: int) -> State:
    """Standard CiA 402 state table over bits 0-3, 5, 6."""
    low = sw & 0x4F           # bits 0,1,2,3,6
    if low == 0x00:
        return State.NOT_READY
    if low == 0x40:
        return State.SWITCH_ON_DISABLED
    if (sw & 0x6F) == 0x21:
        return State.READY_TO_SWITCH_ON
    if (sw & 0x6F) == 0x23:
        return State.SWITCHED_ON
    if (sw & 0x6F) == 0x27:
        return State.OPERATION_ENABLED
    if (sw & 0x6F) == 0x07:
        return State.QUICK_STOP_ACTIVE
    if low == 0x0F:
        return State.FAULT_REACTION
    if low == 0x08:
        return State.FAULT
    return State.UNKNOWN


@dataclass
class Status:
    raw: int
    state: State
    remote: bool
    target_reached: bool
    setpoint_ack: bool
    following_error: bool
    fault: bool
    warning: bool
    internal_limit: bool

    @classmethod
    def from_word(cls, sw: int) -> "Status":
        return cls(
            raw=sw,
            state=decode_state(sw),
            remote=bool(sw & SW.REMOTE),
            target_reached=bool(sw & SW.TARGET_REACHED),
            setpoint_ack=bool(sw & SW.SETPOINT_ACK),
            following_error=bool(sw & SW.FOLLOWING_ERROR),
            fault=bool(sw & SW.FAULT),
            warning=bool(sw & SW.WARNING),
            internal_limit=bool(sw & SW.INTERNAL_LIMIT),
        )

    def __str__(self) -> str:
        flags = [n for n, v in (
            ("remote", self.remote), ("target_reached", self.target_reached),
            ("setpoint_ack", self.setpoint_ack), ("following_err", self.following_error),
            ("warning", self.warning), ("limit", self.internal_limit)) if v]
        return f"0x{self.raw:04X} {self.state.value} [{' '.join(flags)}]"
