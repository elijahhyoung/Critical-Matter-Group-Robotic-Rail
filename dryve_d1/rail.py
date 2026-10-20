"""
High-level control of one linear axis on a dryve D1 (Modbus TCP Gateway).

    from dryve_d1 import Rail

    with Rail("169.254.116.59") as rail:
        rail.enable()                       # CiA 402 state machine -> Operation enabled
        rail.home_here()                    # zero = where the carriage is right now
        rail.move_abs(300, speed=100, accel=500)
        rail.move_rel(-50, speed=50, accel=500)
        print(rail.position_mm())

All distances are mm, speeds mm/s, accelerations mm/s^2.  Conversion to the
drive's integer units goes through `Units` (see units.py).

Preconditions on the drive (one-time, in the web UI - see README.md):
  * Communication -> Modbus TCP  ON
  * Drive Profile  -> "Modbus TCP Gateway"
  * Inputs/Outputs -> DI 7 Enable high (switch in UI or 24 V on X2.7)
  * Axis -> Reference method SCP (if you home with `home_here`)
"""
from __future__ import annotations

import time
from typing import Callable, Optional

from .cia402 import CW, DI7_INPUT_BIT, OD, HomingMethod, Mode, State, Status, error_text
from .gateway import DEFAULT_UNIT_ID, D1Gateway, GatewayException
from .units import Units

SampleCallback = Callable[[float, float, Status], None]

# Software ceiling on commanded speed, applied on top of the drive's own
# Max. Velocity (607Fh, 300 mm/s on the Axis page).  Set to 100 on 15 Oct 2026
# after the first sweep got loud above that.  Pass Rail(..., max_speed_mm_s=None)
# to rely on the drive alone.
MAX_SPEED_MM_S = 100.0


class RailError(RuntimeError):
    pass


class Rail:
    def __init__(self, host: str, port: int = 502, unit_id: int = DEFAULT_UNIT_ID, *,
                 units: Optional[Units] = None, poll_s: float = 0.05,
                 max_speed_mm_s: Optional[float] = MAX_SPEED_MM_S,
                 log: Optional[Callable[[str], None]] = None, debug: bool = False):
        self.gw = D1Gateway(host, port, unit_id, debug=debug)
        self.max_speed_mm_s = max_speed_mm_s
        self.units: Units = units or Units()
        self._units_given = units is not None
        self.poll_s = poll_s
        self._t0 = time.monotonic()
        self._log = log or self._default_log
        self._enabled = False
        self._position_window_units: Optional[int] = None
        self.drive_max_speed_mm_s: Optional[float] = None     # 607Fh, if the firmware exposes it
        self.drive_max_accel_mm_s2: Optional[float] = None    # 60C5h

    # ------------------------------------------------------------- basics
    def _default_log(self, msg: str) -> None:
        print(f"[{time.monotonic() - self._t0:7.2f}s] {msg}")

    def connect(self) -> "Rail":
        self.gw.connect()
        if not self._units_given:
            self.units = Units.from_drive(self.gw)
        try:
            self._position_window_units = self.gw.read_i32(OD.POSITION_WINDOW)
        except Exception:  # noqa: BLE001
            self._position_window_units = None
        try:
            self.drive_max_speed_mm_s = self.units.vel_to_mm_s(self.gw.read_u32(OD.MAX_PROFILE_VELOCITY))
            self.drive_max_accel_mm_s2 = self.gw.read_u32(OD.MAX_ACCELERATION) / self.units.acc_per_mm_s2
        except Exception:  # noqa: BLE001
            pass
        return self

    def close(self) -> None:
        self.gw.close()

    def __enter__(self) -> "Rail":
        return self.connect()

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is not None and self.gw.connected:
            try:
                self.stop()
            except Exception:  # noqa: BLE001
                pass
        self.close()

    # ------------------------------------------------------------- reads
    def status(self) -> Status:
        return Status.from_word(self.gw.read_u16(OD.STATUSWORD))

    def position_mm(self) -> float:
        return self.units.pos_to_mm(self.gw.read_i32(OD.POSITION_ACTUAL))

    def mode(self) -> int:
        return self.gw.read_i8(OD.MODES_OF_OPERATION_DISPLAY)

    def error_code(self) -> int:
        try:
            return self.gw.read_u16(OD.ERROR_CODE)
        except Exception:  # noqa: BLE001
            return -1

    def is_homed(self) -> Optional[bool]:
        """D1-specific object 2014h; None if the firmware does not expose it."""
        try:
            return bool(self.gw.read_u16(OD.D1_HOMING_STATUS))
        except Exception:  # noqa: BLE001
            return None

    def dump(self) -> dict:
        """Read the objects that matter for commissioning.  Never moves anything."""
        out: dict = {}
        items = [
            ("statusword 6041h", lambda: str(self.status())),
            ("mode display 6061h", lambda: self.gw.read_i8(OD.MODES_OF_OPERATION_DISPLAY)),
            ("position actual 6064h", lambda: f"{self.gw.read_i32(OD.POSITION_ACTUAL)} units = {self.position_mm():.2f} mm"),
            ("velocity actual 606Ch", lambda: self.gw.read_i32(OD.VELOCITY_ACTUAL)),
            ("target position 607Ah", lambda: self.gw.read_i32(OD.TARGET_POSITION)),
            ("profile velocity 6081h", lambda: self.gw.read_u32(OD.PROFILE_VELOCITY)),
            ("profile accel 6083h", lambda: self.gw.read_u32(OD.PROFILE_ACCELERATION)),
            ("profile decel 6084h", lambda: self.gw.read_u32(OD.PROFILE_DECELERATION)),
            ("quick-stop decel 6085h", lambda: self.gw.read_u32(OD.QUICK_STOP_DECELERATION)),
            ("max velocity 607Fh", lambda: self.gw.read_u32(OD.MAX_PROFILE_VELOCITY)),
            ("max acceleration 60C5h", lambda: self.gw.read_u32(OD.MAX_ACCELERATION)),
            ("gear ratio 6091h:1/:2", lambda: f"{self.gw.read_u32(OD.GEAR_RATIO, 1)} : {self.gw.read_u32(OD.GEAR_RATIO, 2)}"),
            ("feed constant 6092h:1", lambda: self.gw.read_u32(OD.FEED_CONSTANT, 1)),
            ("shaft revolutions 6092h:2", lambda: self.gw.read_u32(OD.FEED_CONSTANT, 2)),
            ("SI unit position 60A8h", lambda: f"0x{self.gw.read_u32(OD.SI_UNIT_POSITION):08X}"),
            ("SI unit velocity 60A9h", lambda: f"0x{self.gw.read_u32(OD.SI_UNIT_VELOCITY):08X}"),
            ("homing method 6098h", lambda: self.gw.read_i8(OD.HOMING_METHOD)),
            ("homing speed switch 6099h:1", lambda: self.gw.read_u32(OD.HOMING_SPEEDS, 1)),
            ("homing speed zero 6099h:2", lambda: self.gw.read_u32(OD.HOMING_SPEEDS, 2)),
            ("position window 6067h", lambda: self.gw.read_i32(OD.POSITION_WINDOW)),
            ("position range min 607Bh:1", lambda: self.gw.read_i32(OD.POSITION_RANGE_LIMIT, 1)),
            ("position range max 607Bh:2", lambda: self.gw.read_i32(OD.POSITION_RANGE_LIMIT, 2)),
            ("homing status 2014h (D1)", lambda: self.gw.read_u16(OD.D1_HOMING_STATUS)),
            ("error code 603Fh", lambda: error_text(self.gw.read_u16(OD.ERROR_CODE))),
            ("digital inputs 60FDh", lambda: f"0x{self.gw.read_u32(OD.DIGITAL_INPUTS):08X}"),
        ]
        for name, fn in items:
            try:
                out[name] = fn()
            except GatewayException as e:
                out[name] = f"<not available: {e.code:#04x}>"
            except Exception as e:  # noqa: BLE001
                out[name] = f"<error: {e}>"
        return out

    # ------------------------------------------------------- state machine
    def di7_enable(self) -> Optional[bool]:
        """DI 7 'Enable' as the drive sees it (after the UI polarity setting):
        60FDh bit 22, DI1 being bit 16.  None if the object is not readable."""
        try:
            return bool(self.gw.read_u32(OD.DIGITAL_INPUTS) & DI7_INPUT_BIT)
        except Exception:  # noqa: BLE001
            return None

    def require_remote(self) -> Status:
        """Raise unless DI 7 'Enable' is high.

        The manual says statusword bit 9 'Remote' mirrors DI 7, but on 8 Oct
        2026 it read 0 with DI 7 high (60FDh bit 22 and bit 3 'Enable' set) while
        the Drive Profile was not acting as Modbus TCP Gateway, so 60FDh is the
        primary source and bit 9 only the fallback when 60FDh cannot be read.
        """
        st = self.status()
        di7 = self.di7_enable()
        enabled = st.remote if di7 is None else di7
        if not enabled:
            where = "statusword bit 9 is low" if di7 is None else "60FDh bit 22 is low"
            raise RailError(
                f"DI 7 'Enable' is not set ({where}).  Switch it on (Inputs/Outputs page, "
                "DI 7 level) or put 24 V on X2.7, then retry.")
        return st

    def _wait_state(self, wanted: set[State], timeout: float) -> Status:
        deadline = time.monotonic() + timeout
        while True:
            st = self.status()
            if st.state in wanted:
                return st
            if st.fault:
                raise RailError(f"drive in FAULT ({error_text(self.error_code())}); call fault_reset()")
            if time.monotonic() > deadline:
                raise RailError(f"timeout waiting for {[w.value for w in wanted]}, got {st}")
            time.sleep(self.poll_s)

    def fault_reset(self) -> None:
        st = self.require_remote()
        if not st.fault:
            return
        self._log(f"fault reset ({error_text(self.error_code())})")
        self.gw.write_u16(OD.CONTROLWORD, 0, CW.RESET_FAULT)
        time.sleep(0.2)
        self.gw.write_u16(OD.CONTROLWORD, 0, CW.SHUTDOWN)
        self._wait_state({State.SWITCH_ON_DISABLED, State.READY_TO_SWITCH_ON,
                          State.SWITCHED_ON, State.OPERATION_ENABLED}, 5.0)
        self._enabled = False

    def controlword(self) -> int:
        return self.gw.read_u16(OD.CONTROLWORD)

    def controlword_in_effect(self) -> Optional[bool]:
        """Passive check that the drive obeys the bus.

        False when the drive reports Operation enabled although the last
        controlword did not ask for it: DI 7 enabled the power stage on its own,
        which is what a non-bus Drive Profile (Binary, Tipp/Teach, ...) does.
        Seen on the real D1 on 8 Oct 2026: 6040h = 0x0006 after a Shutdown, state
        still 0x0427.  None when nothing can be concluded from the idle state.
        """
        st = self.status()
        cw = self.controlword() & 0x0F
        if st.state == State.OPERATION_ENABLED:
            return cw == CW.ENABLE_OP
        if st.state == State.READY_TO_SWITCH_ON and cw == CW.SHUTDOWN:
            return True                      # 8 Oct 2026: 0x0621 after a stored Shutdown took effect
        if st.state == State.SWITCHED_ON and cw == CW.SWITCH_ON_CMD:
            return True
        return None

    def enable(self, timeout: float = 5.0) -> Status:
        """Shutdown -> Switch on -> Enable operation.  Motor gets current here.

        If the drive is already in Operation enabled without a bus command (DI 7
        did it), a Shutdown is sent first to prove the controlword is obeyed; a
        drive that stays enabled is not under bus control and RailError is raised
        before any move is attempted.
        """
        st = self.require_remote()
        if st.fault:
            self.fault_reset()
            st = self.status()
        if st.state == State.OPERATION_ENABLED:
            if (self.controlword() & 0x0F) == CW.ENABLE_OP:
                self._enabled = True            # enabled over the bus (this or an earlier run)
                return st
            self._log("enable: drive is enabled without a bus command - checking that it obeys the controlword")
            self.gw.write_u16(OD.CONTROLWORD, 0, CW.SHUTDOWN)
            deadline = time.monotonic() + min(1.0, timeout)
            while True:
                st = self.status()
                if st.state != State.OPERATION_ENABLED:
                    break
                if time.monotonic() > deadline:
                    raise RailError(
                        "the D1 ignores the controlword (a Shutdown left it in Operation enabled), so "
                        "the bus is not in control: the Drive Profile is not acting as 'Modbus TCP "
                        "Gateway'.  The dropdown only changes with DI 7 'Enable' low and Modbus TCP "
                        "switched OFF - follow the 'Changing the Drive Profile' steps in README.md "
                        "in that order, then run again.")
                time.sleep(self.poll_s)
        self._log(f"enable: start state {st.state.value}")
        if st.state in (State.NOT_READY, State.SWITCH_ON_DISABLED, State.QUICK_STOP_ACTIVE,
                        State.UNKNOWN):
            self.gw.write_u16(OD.CONTROLWORD, 0, CW.SHUTDOWN)
            st = self._wait_state({State.READY_TO_SWITCH_ON}, timeout)
        if st.state == State.READY_TO_SWITCH_ON:
            self.gw.write_u16(OD.CONTROLWORD, 0, CW.SWITCH_ON_CMD)
            st = self._wait_state({State.SWITCHED_ON}, timeout)
        if st.state == State.SWITCHED_ON:
            self.gw.write_u16(OD.CONTROLWORD, 0, CW.ENABLE_OP)
            st = self._wait_state({State.OPERATION_ENABLED}, timeout)
        if st.state != State.OPERATION_ENABLED:
            raise RailError(f"could not reach Operation enabled: {st}")
        self._enabled = True
        self._log("enable: operation enabled (motor energised)")
        return st

    def disable(self) -> None:
        """Back to 'Ready to switch on' - motor current off (UI DI7 still decides holding)."""
        self.gw.write_u16(OD.CONTROLWORD, 0, CW.SHUTDOWN)
        self._enabled = False
        self._log("disabled")

    def stop(self) -> None:
        """Controlled stop with profile deceleration (HALT bit)."""
        self.gw.write_u16(OD.CONTROLWORD, 0, CW.HALT_CMD)
        time.sleep(0.05)
        self.gw.write_u16(OD.CONTROLWORD, 0, CW.ENABLE_OP)

    def quick_stop(self) -> None:
        """Emergency decel (6085h).  Leaves the drive in Quick-stop active; call enable() to resume."""
        self.gw.write_u16(OD.CONTROLWORD, 0, CW.QUICK_STOP)
        self._enabled = False

    # --------------------------------------------------------------- modes
    def set_mode(self, mode: int, timeout: float = 1.0) -> None:
        self.gw.write_i8(OD.MODES_OF_OPERATION, 0, int(mode))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.gw.read_i8(OD.MODES_OF_OPERATION_DISPLAY) == int(mode):
                return
            time.sleep(0.02)
        # the D1 gateway sometimes lags on 6061h; the set-point handshake will catch a real problem
        self._log(f"warning: mode display did not confirm mode {mode} within {timeout}s")

    def set_profile(self, speed_mm_s: float, accel_mm_s2: float,
                    decel_mm_s2: Optional[float] = None) -> None:
        ceilings = [c for c in (self.max_speed_mm_s, self.drive_max_speed_mm_s) if c]
        if ceilings and speed_mm_s > min(ceilings):
            self._log(f"warning: {speed_mm_s:g} mm/s exceeds the {min(ceilings):g} mm/s ceiling "
                      f"(software limit {self.max_speed_mm_s}, drive Max. Velocity "
                      f"{self.drive_max_speed_mm_s}) - clamping")
            speed_mm_s = min(ceilings)
        if self.drive_max_accel_mm_s2 and accel_mm_s2 > self.drive_max_accel_mm_s2:
            self._log(f"warning: {accel_mm_s2:g} mm/s^2 exceeds the drive's Max. Acceleration "
                      f"{self.drive_max_accel_mm_s2:g} - clamping")
            accel_mm_s2 = self.drive_max_accel_mm_s2
            decel_mm_s2 = min(decel_mm_s2, accel_mm_s2) if decel_mm_s2 else None
        self.gw.write_u32(OD.PROFILE_VELOCITY, 0, self.units.vel_to_units(speed_mm_s))
        self.gw.write_u32(OD.PROFILE_ACCELERATION, 0, self.units.acc_to_units(accel_mm_s2))
        self.gw.write_u32(OD.PROFILE_DECELERATION, 0,
                          self.units.acc_to_units(decel_mm_s2 if decel_mm_s2 else accel_mm_s2))

    def set_position_limits(self, min_mm: float, max_mm: float) -> bool:
        """Software end positions (607Bh).  Returns False if the drive refuses."""
        try:
            self.gw.write_i32(OD.POSITION_RANGE_LIMIT, 1, self.units.pos_to_units(min_mm))
            self.gw.write_i32(OD.POSITION_RANGE_LIMIT, 2, self.units.pos_to_units(max_mm))
            return True
        except GatewayException as e:
            self._log(f"position limits not accepted ({e}); set 'Available Stroke' in the UI instead")
            return False

    # -------------------------------------------------------------- homing
    def home_here(self, timeout: float = 5.0) -> None:
        """Declare the current carriage position to be 0 mm (CiA method 37 / D1 'SCP').

        Refuses to run if the drive is configured for a limit-switch homing
        method, because with no switches wired that would drive into the end
        stop.  Set Axis -> Reference -> SCP in the web UI first.
        """
        if not self._enabled:
            self.enable()
        self.set_mode(Mode.HOMING)
        try:
            self.gw.write_i8(OD.HOMING_METHOD, 0, HomingMethod.SET_CURRENT_POSITION)
        except GatewayException:
            pass                                  # read-only on current D1 firmware
        method = self.gw.read_i8(OD.HOMING_METHOD)
        if method != HomingMethod.SET_CURRENT_POSITION:
            self.set_mode(Mode.PROFILE_POSITION)
            raise RailError(
                f"drive homing method is {method}, not SCP(37).  With no limit switches wired, "
                "switch Drive Profile back to Binary, set Axis -> Reference -> SCP, then "
                "return to Modbus TCP Gateway.")
        self.gw.write_u16(OD.CONTROLWORD, 0, CW.ENABLE_OP)
        self.gw.write_u16(OD.CONTROLWORD, 0, CW.START_ABS)      # bit 4 = start homing
        deadline = time.monotonic() + timeout
        try:
            while True:
                st = self.status()
                if st.setpoint_ack and st.target_reached:        # homing attained
                    break
                if st.fault or st.following_error:
                    raise RailError(f"homing failed: {st} {error_text(self.error_code())}")
                if time.monotonic() > deadline:
                    raise RailError(
                        f"homing not confirmed within {timeout}s: {st}.  If the state never changed, "
                        "the drive is ignoring the controlword: Drive Profile must be 'Modbus TCP "
                        "Gateway' (README.md, 'Changing the Drive Profile').")
                time.sleep(self.poll_s)
        finally:
            self.gw.write_u16(OD.CONTROLWORD, 0, CW.ENABLE_OP)
        self.set_mode(Mode.PROFILE_POSITION)
        self._log(f"homed here: position now {self.position_mm():.2f} mm")

    # --------------------------------------------------------------- moves
    def _reached_window_units(self) -> int:
        w = self._position_window_units or 0
        return max(w, self.units.pos_to_units(1.0))             # never tighter than 1 mm

    def _move(self, target_units: int, relative: bool, speed: float, accel: float,
              decel: Optional[float], wait: bool, timeout: Optional[float],
              on_sample: Optional[SampleCallback]) -> float:
        if not self._enabled:
            self.enable()
        self.set_mode(Mode.PROFILE_POSITION)
        self.set_profile(speed, accel, decel)
        self.gw.write_i32(OD.TARGET_POSITION, 0, target_units)
        start_pos = self.gw.read_i32(OD.POSITION_ACTUAL)
        self.status()                                            # round-trip barrier
        time.sleep(0.01)

        abs_target = start_pos + target_units if relative else target_units
        deadline = time.monotonic() + 1.0
        while self.status().setpoint_ack:                        # previous start still acknowledged?
            self.gw.write_u16(OD.CONTROLWORD, 0, CW.ENABLE_OP)
            if time.monotonic() > deadline:
                raise RailError("drive still acknowledging a previous set-point; call stop() first")
            time.sleep(0.01)
        t_start = time.monotonic()
        self.gw.write_u16(OD.CONTROLWORD, 0, CW.START_REL if relative else CW.START_ABS)
        try:
            deadline = time.monotonic() + 1.0
            while True:                                          # wait for set-point acknowledge
                st = self.status()
                if st.setpoint_ack or not st.target_reached:
                    break
                if st.fault:
                    raise RailError(f"fault on start: {st} {error_text(self.error_code())}")
                if time.monotonic() > deadline:
                    raise RailError(
                        f"start not acknowledged: {st}.  Is Drive Profile = 'Modbus TCP Gateway' "
                        "and the drive in Operation enabled?")
                time.sleep(0.01)
        finally:
            self.gw.write_u16(OD.CONTROLWORD, 0, CW.ENABLE_OP)   # release start bit
        if not wait:
            return 0.0

        dist_mm = abs(self.units.pos_to_mm(abs_target - start_pos))
        if timeout is None:
            timeout = 1.5 * (dist_mm / max(speed, 1e-6) + 2 * speed / max(accel, 1e-6)) + 3.0
        self.wait_until_reached(abs_target, timeout, on_sample)
        return time.monotonic() - t_start

    def wait_until_reached(self, target_units: int, timeout: float,
                           on_sample: Optional[SampleCallback] = None) -> None:
        window = self._reached_window_units()
        deadline = time.monotonic() + timeout
        while True:
            st = self.status()
            pos = self.gw.read_i32(OD.POSITION_ACTUAL)
            if on_sample:
                on_sample(time.monotonic() - self._t0, self.units.pos_to_mm(pos), st)
            if st.fault or st.following_error:
                raise RailError(f"move aborted: {st} {error_text(self.error_code())}")
            if st.target_reached and abs(pos - target_units) <= window:
                return
            if time.monotonic() > deadline:
                raise RailError(
                    f"timeout after {timeout:.1f}s: at {self.units.pos_to_mm(pos):.2f} mm, "
                    f"target {self.units.pos_to_mm(target_units):.2f} mm, {st}")
            time.sleep(self.poll_s)

    def move_abs(self, mm: float, speed: float, accel: float, decel: Optional[float] = None,
                 *, wait: bool = True, timeout: Optional[float] = None,
                 on_sample: Optional[SampleCallback] = None) -> float:
        """Move to absolute position.  Returns elapsed seconds when wait=True."""
        return self._move(self.units.pos_to_units(mm), False, speed, accel, decel,
                          wait, timeout, on_sample)

    def move_rel(self, mm: float, speed: float, accel: float, decel: Optional[float] = None,
                 *, wait: bool = True, timeout: Optional[float] = None,
                 on_sample: Optional[SampleCallback] = None) -> float:
        """Move by a relative distance (sign = direction)."""
        return self._move(self.units.pos_to_units(mm), True, speed, accel, decel,
                          wait, timeout, on_sample)
