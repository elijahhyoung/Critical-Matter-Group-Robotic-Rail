#!/usr/bin/env python3
"""
run_both.py - run the rail sweep (display_sweep.py) and the arm dance
(Dance_Cycle.py) at the same time.  No programming needed: the two scripts
keep their own SETTINGS blocks; this one only starts them together, repeats
the dance while the sweep runs, and stops both if one of them fails.

HOW TO USE
  1. Edit display_sweep.py (the rail: LOW, HIGH, SPEED, HOURS) and
     Dance_Cycle.py (the arm) as usual.  With the arm riding on the carriage
     keep the rail gentle: 50 mm/s and 25 mm/s^2 have been fine.
  2. Arm at its START pose, carriage at the ZERO END of the rail (the end
     WITHOUT the motor).
  3. Run it:        python run_both.py
     The sweep ends and hours can be given here instead of editing the file,
     exactly like display_sweep.py:
                    python run_both.py 200 2000        # ends in mm
                    python run_both.py 200 2000 4      # ... for 4 hours (0 = until Ctrl-C)

WHAT HAPPENS
  * You are asked once whether the arm and the carriage are in place.
  * display_sweep.py starts (its zero-end question is answered for it).  As
    soon as it reports the carriage homed and sweeping, Dance_Cycle.py starts.
  * Each time the dance finishes (arm back at START) it is started again,
    until the sweep's HOURS are up.  The sweep then parks the carriage at 0,
    the dance that is under way finishes, and this script exits.
  * Everything both scripts print is shown here with an [arm] or [rail] prefix.
  * Ctrl-C stops both; each script does its own controlled stop.  If one script
    fails, the other is sent Ctrl-C as well.  (Dance_Cycle.py prints a Python
    traceback after a Ctrl-C; that is normal, the arm has already been stopped.)
  * No collision checking of any kind is done.  Make sure the dancing arm
    cannot reach anything along the whole sweep before running this.
  * Same long-run notes as display_sweep.py: laptop plugged in, lid open.

NETWORK
  The arm (192.168.1.x) and the D1 (169.254.x.x, link-local) are on different
  subnets.  The laptop needs a route to both: two Ethernet adapters, a second
  address on one adapter, or the D1 given a Manual IP in the arm's subnet on
  its Communication page.  Ping both addresses before running.

Dance_Cycle.py needs the xArm Python SDK:   pip install xarm-python-sdk
"""

# =============================== SETTINGS ====================================
ARM_SCRIPT = "Dance_Cycle.py"           # the arm script, relative to this file
RAIL_SCRIPT = "display_sweep.py"        # the rail script, relative to this file

REPEAT_DANCE = True           # True: dance again and again while the sweep runs.  False: dance once.
PAUSE_BETWEEN_DANCES = 0.0    # s   rest at START between two dances (connecting takes a few s anyway)

MAX_RAIL_SPEED = 100          # mm/s    refuse display_sweep.py settings faster than this (arm on board)
MAX_RAIL_ACCEL = 100          # mm/s^2  refuse display_sweep.py settings harder than this

RAIL_READY_LINE = "start = 0.00 mm"     # display_sweep.py prints this once homed; the arm starts then
CLEANUP_TIMEOUT = 30.0        # s to let the scripts stop on their own after Ctrl-C before killing them
# =============================================================================

import importlib.util
import math
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent


def leg_time(dist_mm, speed, accel):
    """Seconds for a rest-to-rest move of dist_mm at speed/accel (trapezoid)."""
    d = abs(dist_mm)
    if d == 0:
        return 0.0
    if d >= speed * speed / accel:
        return d / speed + speed / accel
    return 2.0 * math.sqrt(d / accel)


def load_settings(path, what):
    """Import a script as a module just to read its SETTINGS (its main() does not run)."""
    spec = importlib.util.spec_from_file_location(path.stem, path)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except ImportError as e:
        print(f"{what} ({path.name}) cannot be imported: {e}")
        if "xarm" in str(e):
            print("  Install the xArm SDK first:   pip install xarm-python-sdk")
        return None
    return mod


class Child:
    """One of the two scripts, running as a separate process with its output relayed."""

    def __init__(self, tag, path, args=(), stdin_text=None, on_line=None):
        self.tag = tag
        self.proc = subprocess.Popen(
            [sys.executable, "-u", path.name, *args],
            cwd=path.parent,
            stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        if stdin_text is not None:
            self.proc.stdin.write(stdin_text.encode())
            self.proc.stdin.close()
        self.on_line = on_line
        self.thread = threading.Thread(target=self._pump, name=tag, daemon=True)
        self.thread.start()

    def _pump(self):
        for raw in self.proc.stdout:
            line = raw.decode(errors="replace").rstrip("\r\n")
            print(f"[{self.tag}] {line}", flush=True)
            if self.on_line:
                self.on_line(line)

    def poll(self):
        return self.proc.poll()

    def terminate(self):
        try:
            self.proc.terminate()
        except OSError:
            pass


def interrupt_everyone():
    """Send Ctrl-C to every process on this console: both scripts and this one."""
    try:
        if sys.platform == "win32":
            os.kill(0, signal.CTRL_C_EVENT)
        else:
            os.killpg(os.getpgrp(), signal.SIGINT)
    except OSError as e:
        print(f"[run_both] could not send Ctrl-C to the scripts ({e}); they will be killed "
              f"after {CLEANUP_TIMEOUT:g} s", flush=True)


def say(msg):
    print(f"[run_both] {msg}", flush=True)


def main() -> int:
    arm_path = (HERE / ARM_SCRIPT).resolve()
    rail_path = (HERE / RAIL_SCRIPT).resolve()
    for what, p in (("arm script", arm_path), ("rail script", rail_path)):
        if not p.is_file():
            print(f"{what} not found: {p}   (ARM_SCRIPT / RAIL_SCRIPT in the SETTINGS block)")
            return 1

    # ---- read both scripts' settings, check the rail numbers, show the plan ----
    arm = load_settings(arm_path, "arm script")
    rail = load_settings(rail_path, "rail script")
    if arm is None or rail is None:
        return 1
    try:
        args = [float(a) for a in sys.argv[1:]]
    except ValueError:
        args = None
    if args is None or len(args) not in (0, 2, 3):
        print("usage: python run_both.py [LOW HIGH [HOURS]]     (numbers, in mm and hours)")
        return 1
    low, high = (args[0], args[1]) if args else (float(rail.LOW), float(rail.HIGH))
    hours = args[2] if len(args) == 3 else float(rail.HOURS)
    speed, accel, pause = float(rail.SPEED), float(rail.ACCEL), float(rail.PAUSE)
    problems = []
    for name, v in (("LOW", low), ("HIGH", high)):
        if not 0 <= v <= rail.MAX_POSITION:
            problems.append(f"{name} {v:g} mm is outside 0..{rail.MAX_POSITION:g} mm")
    if low >= high:
        problems.append(f"LOW ({low:g}) must be smaller than HIGH ({high:g})")
    if speed <= 0 or speed > MAX_RAIL_SPEED:
        problems.append(f"{rail_path.name} SPEED {speed:g} mm/s must be between 0 and {MAX_RAIL_SPEED:g} "
                        "(MAX_RAIL_SPEED here: the arm is riding on the carriage)")
    if accel <= 0 or accel > MAX_RAIL_ACCEL:
        problems.append(f"{rail_path.name} ACCEL {accel:g} mm/s^2 must be between 0 and {MAX_RAIL_ACCEL:g} "
                        "(MAX_RAIL_ACCEL here)")
    if hours < 0:
        problems.append("HOURS must not be negative (0 = until Ctrl-C)")
    if PAUSE_BETWEEN_DANCES < 0:
        problems.append("PAUSE_BETWEEN_DANCES must not be negative")
    if problems:
        print("Not moving. Fix these settings first:")
        for p in problems:
            print("  -", p)
        return 1

    t_in = float(getattr(arm, "TRANSITION_TIME", 0.0))
    t_dance = float(getattr(arm, "DANCE_TIME", 0.0))
    t_out = float(getattr(arm, "RETURN_TIME", 0.0))
    dance_s = t_in + t_dance + t_out
    leg_s = leg_time(high - low, speed, accel)
    print(f"rail: {rail_path.name}   sweep {low:g} <-> {high:g} mm   speed {speed:g} mm/s   "
          f"accel {accel:g} mm/s^2   pause {pause:g} s   one leg about {leg_s:.0f} s   "
          + (f"for {hours:g} h" if hours else "until Ctrl-C"))
    print(f"arm : {arm_path.name}   {t_in:g} s in + {t_dance:g} s dance + {t_out:g} s out = {dance_s:g} s, "
          + ("repeated while the sweep runs" if REPEAT_DANCE else "once"))
    if REPEAT_DANCE and hours and dance_s:
        n = hours * 3600 / (dance_s + PAUSE_BETWEEN_DANCES + 5)      # + 5 s to connect each time
        print(f"      roughly {n:.0f} dance{'' if round(n) == 1 else 's'} in {hours:g} h")
    print("Zero convention: the carriage must be at the ZERO END (the end WITHOUT the motor) before every run.")
    answer = input("Arm at its START pose, carriage at the zero end, nothing in the way? [y/N] ")
    if not answer.strip().lower().startswith("y"):
        print("Set things up, then run again.")
        return 2

    # ---- run ----
    stop_requested = threading.Event()
    rail_ready = threading.Event()

    def on_rail_line(line):
        if RAIL_READY_LINE in line:
            rail_ready.set()

    signal.signal(signal.SIGINT, lambda *_: stop_requested.set())
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, lambda *_: stop_requested.set())
    if sys.platform == "win32":
        # A process started from some launchers (VS Code tasks, wrappers) inherits "ignore
        # Ctrl-C"; switch it back on here so Ctrl-C reaches this script and, through
        # inheritance, both scripts it starts.
        import ctypes
        ctypes.windll.kernel32.SetConsoleCtrlHandler(None, False)

    running = {}
    rail_args = [f"{a:g}" for a in args]
    running["rail"] = Child("rail", rail_path, rail_args, stdin_text="y\n", on_line=on_rail_line)
    say("rail script started; the arm starts once the carriage is homed and sweeping")
    rail_done = False
    arm_done = False
    dances = 0
    next_dance_at = 0.0
    failed = False
    deadline = None
    while running:
        time.sleep(0.2)
        for tag, child in list(running.items()):
            rc = child.poll()
            if rc is None:
                continue
            del running[tag]
            if rc == 0:
                if tag == "rail":
                    rail_done = True
                    say("rail script finished (carriage parked)"
                        + ("; letting the current dance finish" if "arm" in running else ""))
                else:
                    dances += 1
                    if REPEAT_DANCE and not rail_done and not stop_requested.is_set():
                        say(f"dance {dances} finished; next one in {PAUSE_BETWEEN_DANCES:g} s")
                        next_dance_at = time.monotonic() + PAUSE_BETWEEN_DANCES
                    else:
                        arm_done = True
                        say(f"dance {dances} finished"
                            + ("; waiting for the sweep to finish (Ctrl-C ends it early)"
                               if "rail" in running else ""))
            elif stop_requested.is_set():
                say(f"{tag} script stopped (exit code {rc})")
            else:
                failed = True
                say(f"{tag} script FAILED (exit code {rc})")
                if running:
                    say("sending Ctrl-C to the other script")
                    interrupt_everyone()
                    stop_requested.set()
        if ("arm" not in running and not arm_done and not stop_requested.is_set()
                and "rail" in running and rail_ready.is_set() and time.monotonic() >= next_dance_at):
            running["arm"] = Child("arm", arm_path)
            say(f"dance {dances + 1} started")
        if stop_requested.is_set() and running:
            if deadline is None:
                deadline = time.monotonic() + CLEANUP_TIMEOUT
                say("stopping: waiting for the scripts to finish their controlled stops")
            elif time.monotonic() > deadline:
                for tag, child in running.items():
                    say(f"{tag} script did not stop in {CLEANUP_TIMEOUT:g} s: killing it")
                    child.terminate()
                    failed = True
                running.clear()

    if failed:
        say("stopped because a script failed. If the carriage is not at the zero end, "
            "push it back before the next run.")
        return 1
    if stop_requested.is_set():
        say(f"stopped by Ctrl-C after {dances} complete dance{'' if dances == 1 else 's'}. "
            "If the carriage is not at the zero end, push it back before the next run.")
        return 130
    if dances == 0:
        say("the rail finished before the arm ever started; nothing danced")
        return 1
    say(f"done: {dances} dances, arm back at START, carriage parked")
    return 0


if __name__ == "__main__":
    sys.exit(main())
