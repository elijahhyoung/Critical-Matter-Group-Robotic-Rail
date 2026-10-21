#!/usr/bin/env python3
"""
display_sweep.py - shuttle the carriage back and forth between two positions
for hours (a running display, or a soak test).  No programming needed.

HOW TO USE
  1. Edit the SETTINGS block below: the two ends, the speed, how many hours.
  2. Make sure the carriage is at the ZERO END of the rail: the end WITHOUT
     the motor.  Every run finishes there, so it normally already is; if
     someone moved it while the drive was off, push it back first (motor is
     unpowered between runs).
  3. Run it:        python display_sweep.py
     The ends and the duration can also be given on the command line:
                    python display_sweep.py 200 2000      # ends in mm, HOURS from the file
                    python display_sweep.py 200 2000 4    # ... and run for 4 hours

The carriage position when the script starts becomes 0 mm (same zero
convention as run_path.py: the drive has no encoder and forgets its position
whenever it is disabled).  The carriage goes to LOW, then LOW -> HIGH -> LOW
-> ... until HOURS have passed or you press Ctrl-C.  It finishes the leg it
is on, then parks back at 0 mm.  One line is printed per leg so you can see
it is alive.

Long-run notes
  * The laptop must not sleep.  The script asks Windows / macOS to stay awake
    while it runs, but closing the lid still sleeps most laptops: plug it in
    and leave the lid open (or set "When I close the lid" to "Do nothing").
    If the link drops anyway, the script reconnects and carries on, provided
    the drive was never disabled in the meantime (the drive finishes the leg
    it was on by itself).
  * A drive fault (E-number on the X9 display) stops the run and is NOT
    retried.  Fix the cause, push the carriage to the zero end, run again.
  * No encoder means skipped steps go unnoticed and positions drift.  Keep
    LOW and HIGH well inside the rail and glance at the carriage now and then.
"""

# =============================== SETTINGS ====================================
HOST = "169.254.116.59"   # the IP that scrolls on the D1's X9 display

LOW = 0                # mm from the zero end: one end of the sweep
HIGH = 1850               # mm from the zero end: the other end
SPEED = 50              # mm/s    (100 is comfortable; the rail gets loud above that)
ACCEL = 25               # mm/s^2  how quickly to speed up and slow down
PAUSE = 1              # seconds to rest at each end before turning around
HOURS = 0.1               # how long to keep sweeping; 0 = until Ctrl-C
RETURN_TO_START = True    # leave True: the carriage parks at 0 so the next run starts zeroed

MAX_SPEED = 300           # mm/s    the script refuses anything faster
MAX_ACCEL = 500           # mm/s^2  the script refuses anything harder
MAX_POSITION = 2500       # mm      furthest position allowed (rail is 3000 mm; keep a margin)
# =============================================================================

import datetime as dt
import sys
import time

from dryve_d1 import GatewayError, Rail, RailError, State

RECONNECT_TRIES = 12      # attempts after the link drops, RECONNECT_WAIT s apart (about a minute)
RECONNECT_WAIT = 5.0


def now() -> str:
    return dt.datetime.now().strftime("%H:%M:%S")


def keep_awake():
    """Ask the OS not to sleep while the sweep runs.  Returns an undo function."""
    if sys.platform == "win32":
        import ctypes
        es_continuous, es_system_required = 0x80000000, 0x00000001
        k32 = ctypes.windll.kernel32
        k32.SetThreadExecutionState.argtypes = [ctypes.c_uint]
        k32.SetThreadExecutionState.restype = ctypes.c_uint
        k32.SetThreadExecutionState(es_continuous | es_system_required)
        return lambda: k32.SetThreadExecutionState(es_continuous)
    if sys.platform == "darwin":
        import os
        import subprocess
        try:
            p = subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())])
            return p.terminate
        except OSError:
            pass
    return lambda: None


def reconnect(rail: Rail) -> bool:
    """Re-open a dropped link.  True if the drive is still enabled, i.e. the
    0 mm reference survived and the sweep can continue."""
    rail.close()
    for attempt in range(1, RECONNECT_TRIES + 1):
        time.sleep(RECONNECT_WAIT)
        try:
            rail.connect()
            st = rail.status()
        except (OSError, GatewayError) as e:
            print(f"{now()}  reconnect {attempt}/{RECONNECT_TRIES} failed: {e}")
            rail.close()
            continue
        if st.state == State.OPERATION_ENABLED and not st.fault:
            print(f"{now()}  reconnected: drive still enabled, carriage at {rail.position_mm():.1f} mm")
            return True
        print(f"{now()}  reconnected, but the drive is '{st.state.value}': the 0 mm reference is lost")
        return False
    return False


def main() -> int:
    try:
        args = [float(a) for a in sys.argv[1:]]
    except ValueError:
        args = [None] * 99
    if len(args) not in (0, 2, 3):
        print("usage: python display_sweep.py [LOW HIGH [HOURS]]     (numbers, in mm and hours)")
        return 1
    low, high = (args[0], args[1]) if args else (float(LOW), float(HIGH))
    hours = args[2] if len(args) == 3 else float(HOURS)

    # ---- sanity checks before anything is powered -------------------------
    problems = []
    for name, v in (("LOW", low), ("HIGH", high)):
        if not 0 <= v <= MAX_POSITION:
            problems.append(f"{name} {v:g} mm is outside 0..{MAX_POSITION} mm")
    if low >= high:
        problems.append(f"LOW ({low:g}) must be smaller than HIGH ({high:g})")
    if SPEED <= 0 or SPEED > MAX_SPEED:
        problems.append(f"SPEED {SPEED:g} mm/s must be between 0 and MAX_SPEED ({MAX_SPEED:g})")
    if ACCEL <= 0 or ACCEL > MAX_ACCEL:
        problems.append(f"ACCEL {ACCEL:g} mm/s^2 must be between 0 and MAX_ACCEL ({MAX_ACCEL:g})")
    if PAUSE < 0:
        problems.append("PAUSE must not be negative")
    if hours < 0:
        problems.append("HOURS must not be negative (0 = until Ctrl-C)")
    if problems:
        print("Not moving. Fix these settings first:")
        for p in problems:
            print("  -", p)
        return 1

    print(f"sweep {low:g} <-> {high:g} mm   speed {SPEED:g} mm/s   accel {ACCEL:g} mm/s^2   "
          f"pause {PAUSE:g} s   " + (f"for {hours:g} h" if hours else "until Ctrl-C"))
    undo_awake = keep_awake()
    rail = Rail(HOST, max_speed_mm_s=MAX_SPEED, poll_s=0.02, log=lambda m: None)
    rc = 0
    homed = False
    legs = 0
    try:
        rail.connect()
        rail.require_remote()          # DI 7 "Enable" must be on
        rail.enable()                  # motor gets current; refuses if the bus is not in control

        print("Zero convention: the carriage must be at the ZERO END (the end WITHOUT the motor) before every run.")
        print("(Runs finish there. If it was moved while the drive was off, push it back first.)")
        answer = input("Is the carriage at the zero end (no-motor end) right now? [y/N] ").strip().lower()
        if not answer.startswith("y"):
            print("Put it there, then run again.")
            return 2
        rail.home_here()               # wherever the carriage is right now becomes 0 mm
        homed = True

        t0 = time.monotonic()
        deadline = t0 + hours * 3600 if hours else None
        if deadline:
            stop_at = dt.datetime.now() + dt.timedelta(hours=hours)
            print(f"{now()}  start = 0.00 mm, stopping after {stop_at:%H:%M} ({hours:g} h). Ctrl-C stops earlier.")
        else:
            print(f"{now()}  start = 0.00 mm, sweeping until Ctrl-C")

        target = low
        while deadline is None or time.monotonic() < deadline:
            try:
                if abs(rail.position_mm() - target) <= 0.5:
                    t = 0.0        # already there (the first leg when LOW = 0)
                else:
                    t = rail.move_abs(target, SPEED, ACCEL)
            except (OSError, GatewayError) as e:
                print(f"{now()}  lost the link to the D1 ({e}); reconnecting")
                if reconnect(rail):
                    continue           # redo this leg from wherever the carriage is now
                raise RailError("the link dropped and the drive is no longer enabled, so the 0 mm "
                                "reference is gone. Push the carriage back to the zero end before "
                                "the next run.") from e
            legs += 1
            elapsed = dt.timedelta(seconds=int(time.monotonic() - t0))
            print(f"{now()}  leg {legs:4d}   at {rail.position_mm():7.1f} mm   ({t:5.1f} s)   running {elapsed}")
            target = high if target == low else low
            time.sleep(PAUSE)

        print(f"{now()}  time is up after {legs} legs")
        if RETURN_TO_START and abs(rail.position_mm()) > 0.5:
            t = rail.move_abs(0, SPEED, ACCEL)
            print(f"  back at {rail.position_mm():.2f} mm   ({t:.1f} s)")
        print("done")

    except KeyboardInterrupt:
        print(f"\n{now()}  Ctrl-C after {legs} legs: stopping")
        try:
            rail.stop()
        except Exception:  # noqa: BLE001
            pass
        rc = 130
    except RailError as e:
        print(f"\nERROR: {e}")
        rc = 1
    except (OSError, GatewayError) as e:
        print(f"\nCannot talk to the D1 at {HOST}: {e}")
        print("  Is it powered?  Communication page -> Modbus TCP Gateway ON?  Right IP?")
        rc = 2
    finally:
        undo_awake()
        try:
            if rail.gw.connected:
                if homed and abs(rail.position_mm()) > 1.0:
                    print(f"\nNOTE: carriage is at {rail.position_mm():.1f} mm, NOT at the zero end. "
                          "Move it back before the next run (the drive forgets its position when disabled).")
                rail.disable()         # motor current off; the 0 mm reference is forgotten
            elif homed:
                print("\nNOTE: the link to the D1 is down, so the carriage could not be parked. "
                      "Push it back to the zero end before the next run.")
        except Exception:  # noqa: BLE001
            pass
        rail.close()
    return rc


if __name__ == "__main__":
    sys.exit(main())
