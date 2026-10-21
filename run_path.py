#!/usr/bin/env python3
"""
run_path.py - move the rail carriage through a list of stops.  No programming needed.

HOW TO USE
  1. Edit the SETTINGS block below: the stops, the speed, the acceleration.
  2. Make sure the carriage is at the ZERO END of the rail: the end WITHOUT
     the motor.  Every run finishes there, so it normally already is; if
     someone moved it while the drive was off, push it back first (motor is
     unpowered between runs).
  3. Run it:        python run_path.py
     Stops can also be given on the command line instead of editing the file:
                    python run_path.py 200 500 100

The carriage position when the script starts becomes 0 mm (the drive has no
encoder, so it forgets its position whenever it is powered down or disabled
anyway).  Every stop is measured from there, and the carriage only ever
travels away from that end (0 mm is the end stop, so stops must be between 0
and MAX_POSITION).  The D1 also refuses any target beyond its own Available
Stroke (Axis page), so that number must be at least as large as the stops.
Ctrl-C stops the carriage with a controlled deceleration.
"""

# =============================== SETTINGS ====================================
HOST = "169.254.116.59"   # the IP that scrolls on the D1's X9 display

STOPS = [200, 500, 1500]   # mm from the start, visited in this order
SPEED = 100                # mm/s    how fast to travel between stops
ACCEL = 300               # mm/s^2  how quickly to speed up and slow down
PAUSE = 1.0               # seconds to wait at each stop
RETURN_TO_START = True    # leave True: the carriage parks at 0 so the next run starts zeroed

MAX_SPEED = 300           # mm/s    the script refuses anything faster
MAX_ACCEL = 500           # mm/s^2  the script refuses anything harder
MAX_POSITION = 2500        # mm      furthest stop allowed (rail is 3000 mm; keep a margin)
# =============================================================================

import sys
import time

from dryve_d1 import GatewayError, Rail, RailError


def main() -> int:
    stops = [float(a) for a in sys.argv[1:]] or [float(s) for s in STOPS]

    # ---- sanity checks before anything is powered -------------------------
    problems = []
    if not stops:
        problems.append("STOPS is empty")
    for s in stops:
        if not 0 <= s <= MAX_POSITION:
            problems.append(f"stop {s:g} mm is outside 0..{MAX_POSITION} mm")
    if SPEED <= 0 or SPEED > MAX_SPEED:
        problems.append(f"SPEED {SPEED:g} mm/s must be between 0 and MAX_SPEED ({MAX_SPEED:g})")
    if ACCEL <= 0 or ACCEL > MAX_ACCEL:
        problems.append(f"ACCEL {ACCEL:g} mm/s^2 must be between 0 and MAX_ACCEL ({MAX_ACCEL:g})")
    if problems:
        print("Not moving. Fix these settings first:")
        for p in problems:
            print("  -", p)
        return 1

    print(f"stops: {' -> '.join(f'{s:g}' for s in stops)} mm   speed {SPEED:g} mm/s   accel {ACCEL:g} mm/s^2")
    rail = Rail(HOST, max_speed_mm_s=MAX_SPEED, poll_s=0.02, log=lambda m: None)
    rc = 0
    homed = False
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
        print("start = 0.00 mm")

        for s in stops:
            t = rail.move_abs(s, SPEED, ACCEL)
            print(f"  at {rail.position_mm():7.2f} mm   ({t:.1f} s)")
            time.sleep(PAUSE)
        if RETURN_TO_START and abs(rail.position_mm()) > 0.5:
            t = rail.move_abs(0, SPEED, ACCEL)
            print(f"  back at {rail.position_mm():.2f} mm   ({t:.1f} s)")
        print("done")

    except KeyboardInterrupt:
        print("\nCtrl-C: stopping")
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
        try:
            if rail.gw.connected:
                if homed and abs(rail.position_mm()) > 1.0:
                    print(f"\nNOTE: carriage is at {rail.position_mm():.1f} mm, NOT at the zero end. "
                          "Move it back before the next run (the drive forgets its position when disabled).")
                rail.disable()     # motor current off; the 0 mm reference is forgotten
        except Exception:  # noqa: BLE001
            pass
        rail.close()
    return rc


if __name__ == "__main__":
    sys.exit(main())
