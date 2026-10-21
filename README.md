# igus 7th-axis rail: `run_path.py`

Moves the carriage of the lab's igus **ZLW-20200** linear rail (about 3 m long,
NEMA 24 stepper with a 5:1 gearbox) through a list of stops. The rail is driven
by an igus **dryve D1** motor controller, and this script talks to the D1 over
an Ethernet cable. No programming is needed: edit the numbers at the top of
`run_path.py` (a list of stops) or `display_sweep.py` (back and forth for
hours) and run it. `run_both.py` runs the sweep together with the xArm6 dance
in `Dance_Cycle.py` (see "Running the arm dance and the rail sweep together").

```
igus_rail/
├── README.md          <- this file: setup from scratch, running, troubleshooting
├── run_path.py        <- THE script. Edit the SETTINGS block at the top, run it.
├── display_sweep.py   <- same idea, but shuttles LOW <-> HIGH for hours (display / soak run)
├── Dance_Cycle.py     <- the xArm6 dance on its own (needs the xArm SDK, see requirements.txt)
├── run_both.py        <- the show: display_sweep.py and Dance_Cycle.py at the same time
├── dryve_d1/          <- driver package that run_path.py imports (plain Python, nothing to install)
│   ├── gateway.py     <- Modbus TCP Gateway telegrams over a TCP socket
│   ├── cia402.py      <- object indices, control/status words, drive states
│   ├── units.py       <- mm <-> drive-unit scaling
│   └── rail.py        <- Rail: enable / home / move / stop
├── config/            <- the D1's saved configuration; Load it if the D1 loses its settings
└── requirements.txt   <- only the xArm SDK; the rail itself needs nothing beyond the standard library
```

## Read this first: safety

- **No limit switches are wired.** Nothing stops the carriage at the ends of
  the rail except the mechanical hard stops. Keep every stop below
  `MAX_POSITION` (2500 mm) and keep a hand near the STOP button on the 24 V
  supply.
- **The drive has no encoder.** It forgets where the carriage is every time
  the motor is disabled or the D1 is powered off. Every run therefore starts by
  declaring the carriage's current position to be 0 mm, which only works if the
  carriage really is at the zero end: **the end of the rail without the
  motor**. See "Which end is zero" below.
- **Ctrl-C** stops the carriage with a controlled deceleration and cuts the
  motor current.


## What you need

- **Hardware:** the rail, the dryve D1 with its 24 V supply, and one Ethernet
  cable from the D1's **X8** port to your computer. A plain cable straight into
  the laptop is fine; no switch or router is needed.
- **Computer:** Windows, macOS or Linux with an Ethernet port (a USB-to-Ethernet
  adapter works).
- **Software:** **Python 3.9 or newer.** For the rail alone nothing else: no
  `pip install`, no drivers. The arm scripts (`Dance_Cycle.py`, `run_both.py`)
  also need the xArm SDK: `pip install xarm-python-sdk`.
- **This folder:** clone or download it. `run_path.py` and the `dryve_d1/`
  folder must stay next to each other.

## Setup, step by step

### 1. Power and wiring (already done; just check)

| D1 terminal | Connected to |
|---|---|
| X1.1 and X1.3 | +24 V (load and logic) |
| X1.2 | 0 V |
| X2.11 / X2.12 | +24 V / 0 V (I/O supply; the D1 will not move without it) |
| X5.1 to X5.4 | motor: white, brown, blue, black |
| X8 | Ethernet to your computer |

Switch the 24 V supply on. The D1's small display (**X9**) lights up and, once
the controller has booted, starts scrolling text.

### 2. Plug in the Ethernet cable and read the D1's IP address

Connect the cable from **X8** on the D1 to your computer's Ethernet port.

The D1 is set to **Automatic IP**. With only your laptop on the cable there is
no router to hand out addresses, so the D1 gives itself a *link-local* address
of the form `1169.254.116.59`. **That address scrolls on the X9 display.**

### 3. Get your computer onto the same network

Your computer needs an address in the same `169.254.116.59`. Type into Chrome or other browser.



### 4. Confirm you can reach the D1

Three quick checks, in order. Each one tells you something different.
Replace the address with the one on the display.

1. **Ping** (PowerShell or Terminal). Replies mean the cable and the addresses
   are fine.

   ```
   ping 169.254.116.59
   ```

2. **Web UI.** Open `http://169.254.116.59/` in a browser. The dryve D1 pages
   should appear. You will use them in step 5.

3. **Modbus port 502**, which is what `run_path.py` actually uses.

   ```
   Test-NetConnection 169.254.116.59 -Port 502
   ```

   That is Windows PowerShell (`TcpTestSucceeded : True` is a pass). On any OS
   with Python installed:

   ```
   python -c "import socket; socket.create_connection(('169.254.116.59', 502), timeout=3); print('port 502 open')"
   ```

   If this fails while the web page loads, the Modbus TCP Gateway is switched
   off on the D1. Step 5 fixes that.

### 5. Check the D1's settings in the web UI

The lab's D1 is already configured. You normally only **verify**, but one
switch has come back OFF after power cycles, so always look at it.

In the browser at `http://<D1 IP>/`:

| Page | Setting | Must be |
|---|---|---|
| **Communication** | Modbus TCP Gateway | **ON**. This has turned itself OFF after a power cycle or a STOP. If it is off, flip it on. |
| Communication | Port / Unit Identifier | 502 / 255 |
| **Drive Profile** | dropdown | **Modbus TCP Gateway**. The PC owns motion in this mode; the UI arrow buttons do nothing. |
| **Inputs/Outputs** | DI 7 "Enable" | high. With nothing wired to X2.7, the trick is to set the DI 7 level to `L` (inverted). Alternatively put 24 V from X2.11 on X2.7 and leave the level at `H`. |
| **Axis** | Available Stroke | 2500 mm |
| Axis | Max. Velocity / Max. Acceleration | 300 mm/s / 1000 mm/s². The drive refuses anything faster, whatever the script asks. |
| Axis | Reference > Method | **SCP** (set current position). Required: the script refuses to run with any other method because no reference switches exist. |
| Axis | Limit Switch > Position | None |
| **Motor** | Gear | ON, 5:1 |

**If the D1 has lost its settings,** load `config/Test_binary_2026-10-08.txt`
(Start page > Configuration > Load, with DI 7 off while loading); it restores
everything in the table in one click.

**Changing the Drive Profile** (or any Axis field) is only needed if something
above is wrong. The firmware locks those fields while a bus is active or DI 7
is high, so the order matters:

1. Communication > Modbus TCP Gateway **OFF**
2. Inputs/Outputs > DI 7 **low** (level back to `H` if you use the trick)
3. Make the change (Drive Profile dropdown, Axis field, ...)
4. Inputs/Outputs > DI 7 **high** again
5. Communication > Modbus TCP Gateway **ON**

Doing it in another order looks like it worked, but the drive keeps ignoring
the PC and the script reports "the D1 ignores the controlword".

### 6. Install Python

Python 3.9 or newer. On Windows, install it from python.org or the Microsoft
Store and tick **"Add python.exe to PATH"** in the installer. Check in a new
terminal:

```
python --version
```

### 7. Set up `run_path.py`

Open `run_path.py` in any text editor. The SETTINGS block at the top is the
only part to touch:

```python
HOST = "169.254.116.59"   # the IP that scrolls on the D1's X9 display

STOPS = [200, 500, 2450]  # mm from the zero end, visited in this order
SPEED = 100               # mm/s    (100 is comfortable; the rail gets loud above that)
ACCEL = 300               # mm/s^2  how quickly to speed up and slow down
PAUSE = 1.0               # seconds to wait at each stop
RETURN_TO_START = True    # leave True so the next run starts from zero

MAX_SPEED = 300           # the script refuses anything faster
MAX_ACCEL = 500           # the script refuses anything harder
MAX_POSITION = 2500       # furthest stop allowed
```

- Put the address from the display into `HOST`.
- Stops are millimetres from the zero end, between 0 and `MAX_POSITION`.
- Stops can also be given on the command line, in which case the `STOPS` line
  is ignored: `python run_path.py 200 500 100`.

### 8. Put the carriage at the zero end

The zero end is the end of the rail **without the motor** on it. Every run
starts and ends with the carriage there, so it is normally already in place.
If someone moved it, push it back by hand. The motor is unpowered between
runs.

### 9. Run it

Open a terminal **in this folder** (Windows 11: right-click inside the folder
and choose "Open in Terminal", or `cd` to it) and run:

```
python run_path.py
```

The script connects, checks that DI 7 is on and that the drive obeys the PC,
energises the motor, and then asks:

```
stops: 200 -> 500 -> 2450 mm   speed 100 mm/s   accel 300 mm/s^2
Zero convention: the carriage must be at the ZERO END (the end WITHOUT the motor) before every run.
(Runs finish there. If it was moved while the drive was off, push it back first.)
Is the carriage at the zero end (no-motor end) right now? [y/N]
```

Type `y` and press Enter. The current carriage position becomes 0 mm and the
carriage visits each stop in turn:

```
start = 0.00 mm
  at  200.00 mm   (2.5 s)
  at  500.00 mm   (3.3 s)
  at 2450.00 mm   (19.8 s)
  back at 0.00 mm   (24.8 s)
done
```

At the end the motor current is switched off. Ctrl-C at any time stops the
carriage.

## First run on a new computer: a short, safe test

Before a full path, place the carriage about 150 mm from the end **without
the motor** and run

```
python run_path.py 50
```

The carriage should move 50 mm **toward the motor** and come back. If it
moves toward the near end instead, press Ctrl-C: the motor direction has
been changed (Motor page, polarity setting) and must be put back before
anything else. Either way nothing reaches a hard stop.

## Which end is zero

**Zero is the end of the rail without the motor.** Positive stops move the
carriage away from it, toward the motor, so a 2450 mm stop ends up near the
motor end. The script has no idea where the carriage is; it trusts you. The
convention in use since 15 Oct 2026: every run starts with the carriage at
the zero end and parks it there again, so the next run finds it in place.
The positive direction is fixed by the motor wiring and the Motor page's
polarity setting; leave that setting alone.

Because the drive has no encoder, the 0 mm reference is lost whenever the
motor is disabled or the D1 is powered down. That is why every run re-zeros at
the start and parks back at 0 mm at the end. If a run is interrupted, the
script prints where it left the carriage; push it back to the zero end before
the next run.

## Running back and forth for hours

`display_sweep.py` shuttles the carriage between two positions for a set
number of hours, for a running display or a soak test. Same preparation as
`run_path.py` (steps 1 to 6, carriage at the zero end). Edit its SETTINGS
block (`LOW`, `HIGH`, `SPEED`, `HOURS`) or give the numbers on the command
line:

```
python display_sweep.py              # ends and hours from the SETTINGS block in the file
python display_sweep.py 200 2000     # ends in mm
python display_sweep.py 200 2000 4   # ... for 4 hours (0 = until Ctrl-C)
```

It goes to LOW, then LOW to HIGH to LOW and so on, printing one line per leg.
When the time is up it finishes the leg it is on and parks at 0 mm. Ctrl-C
stops it earlier. Before a long run:

- **Plug the laptop in and leave the lid open.** The script asks the OS not
  to sleep, but closing the lid still sleeps most laptops. If the link drops
  anyway, the script reconnects and continues, as long as the drive was never
  disabled in the meantime.
- **A drive fault stops the run** and is not retried. Fix the cause, push the
  carriage to the zero end, start again.
- **There is no encoder**, so skipped steps go unnoticed and the positions
  drift. Keep LOW and HIGH well inside the rail and look at the carriage now
  and then.

## Running the arm dance and the rail sweep together

`run_both.py` is the show: the carriage sweeps back and forth for hours while
the xArm6 riding on it repeats its two-minute dance. The two scripts stay
independent; `run_both.py` only starts them, repeats the dance while the sweep
runs, relays what both print (prefixed `[arm]` / `[rail]`), and stops both if
one of them fails.

Before a run:

- The arm must be at the START pose in `Dance_Cycle.py` (within 1 degree) and
  the carriage at the zero end, as always.
- The arm's control box and the D1 are on different networks (the arm on
  `192.168.1.x`, the D1 on `169.254.x.x`), so the computer needs a link to
  both. See "Setting up another computer".
- Edit `display_sweep.py` for the sweep (`LOW`, `HIGH`, `SPEED`, `HOURS`) and
  `Dance_Cycle.py` for the dance. With the arm on the carriage keep the rail
  gentle: 50 mm/s and 25 mm/s^2 have been fine. `run_both.py` refuses sweep
  settings above its own `MAX_RAIL_SPEED` / `MAX_RAIL_ACCEL` (100 / 100).
- **No collision checking of any kind is done.** Make sure the dancing arm
  cannot reach anything along the whole sweep.

```
python run_both.py              # ends and hours from display_sweep.py
python run_both.py 200 2000 4   # 200 <-> 2000 mm for 4 hours, like display_sweep.py
```

It asks once whether the arm and carriage are in place, starts the sweep, and
starts the dance as soon as the carriage is homed and moving. When the sweep's
hours are up the carriage parks at 0, the dance under way finishes, and the
script exits. **Ctrl-C stops both** (each does its own controlled stop; the
arm script then prints a Python traceback, which is normal). If one script
fails, the other is sent Ctrl-C as well.

## Setting up another computer

Everything the show needs is in this repository plus one pip package. On a new
Windows PC (the lab's Dell, for example):

1. **Python 3.12** from python.org, ticking "Add python.exe to PATH". Then in a
   new terminal: `pip install xarm-python-sdk`.
2. **This repository:** `git clone https://github.com/elijahhyoung/Critical-Matter-Group-Robotic-Rail.git`
   (or download the ZIP). Keep the folder together; the scripts import
   `dryve_d1/` from next to themselves.
3. **Two Ethernet links.** The arm needs an adapter with a fixed address on its
   network: Settings > Network & Internet > Ethernet > that adapter > IP
   assignment > Edit > Manual, IPv4 on, IP `192.168.1.10`, subnet mask
   `255.255.255.0`, gateway blank. The D1 needs a second adapter (a USB
   adapter is fine) left on Automatic: with no router on that cable Windows
   gives it a `169.254.x.x` address by itself within a minute. The laptop used
   two USB-to-Ethernet adapters; moving those to the new PC is the quickest
   route. (Later, one switch with the D1 on a Manual IP in the arm's network
   would do with a single port; then change `HOST` in the rail scripts.)
4. **Check both links** from a terminal, replacing the D1 address with what its
   display scrolls: `ping 192.168.1.228`, `ping 169.254.116.59`, then
   `Test-NetConnection 192.168.1.228 -Port 502` and
   `Test-NetConnection 169.254.116.59 -Port 502` (both `TcpTestSucceeded : True`).
   If the D1 answers ping but not port 502, switch Modbus TCP Gateway ON in its
   web page (step 5 above).
5. **Prove each half alone first:** `python run_path.py 50` with the carriage
   150 mm from the no-motor end (direction check, see "First run on a new
   computer"), then `python Dance_Cycle.py` with the arm at START, then
   `python run_both.py`.
6. For long runs the PC must not sleep: plug it in and set the power plan so
   it never sleeps while the show runs (`display_sweep.py` asks the OS to stay
   awake, but a desktop power plan can still override that).

## Troubleshooting

| What you see | What it means | What to do |
|---|---|---|
| `Cannot talk to the D1 at ...: timed out` | The computer cannot reach the D1 at all. | Steps 2 to 4: D1 powered? Right address (read the display)? Does `ping` answer? Does your adapter have a `169.254.x.x` address? Try turning Wi-Fi off. A VPN or a third-party firewall can also block it. |
| `Cannot talk to the D1 ...: actively refused` (WinError 10061) while the web page loads | Port 502 is closed: Modbus TCP Gateway is OFF. Happens after power cycles. | Communication page > Modbus TCP Gateway ON. |
| `timeout waiting for D1 ... is Modbus TCP switched ON` | The socket opened but the D1 does not answer telegrams. | Modbus TCP Gateway ON; Unit Identifier must be 255. |
| `DI 7 'Enable' is not set` | The enable input is low; the drive will not move. | Inputs/Outputs page: DI 7 level trick (`L` with nothing wired) or 24 V on X2.7. |
| `the D1 ignores the controlword ... Drive Profile is not acting as 'Modbus TCP Gateway'` | The PC is not in control; the drive is still in Binary or another mode. | Step 5, "Changing the Drive Profile", in exactly that order. |
| `drive homing method is 17, not SCP(37)` | Axis > Reference method is a limit-switch method. | Set it to SCP (Axis page; unlock first as in step 5). |
| `drive in FAULT (0x.... E..)` | The D1 has tripped. The E-number is also on the display and in the web UI. | Fix the cause (E08 load supply low, E02 motor over-current, E10 temperature, ...), then run again: the script clears the fault at start. |
| `Not moving. Fix these settings first:` | A stop, speed or acceleration is outside the limits in the SETTINGS block. | Edit the values. |
| `timeout after ... s: at X mm, target Y mm` | The carriage did not get there in time. | Is something blocking it? Did it hit an end stop (wrong zero end)? |
| The position counts up (`at 1261 mm`), no fault, but the carriage and belt do not move | The motor is getting no current. With no encoder the D1 cannot tell; it counts the steps it sent. Seen 21 Oct 2026 with the M12 plug on the motor cable unplugged. | Check the M12 plug at the motor and the X5 terminals on the D1, then `python run_path.py 50`. |
| Carriage moves toward the end stop | The carriage was not at the no-motor end, or the motor polarity was changed in the web UI. | Ctrl-C. Push the carriage to the end without the motor; check the Motor page polarity setting. |
| `NOTE: carriage is at X mm, NOT at the zero end` after Ctrl-C or an error | The run stopped early. | Push the carriage back to the zero end before the next run. |
| Web UI arrow buttons do nothing | Normal while Drive Profile = Modbus TCP Gateway. | See "Jogging by hand" below. |

## Jogging by hand from the web UI

The arrow buttons on the D1 pages only work in the Binary profile. To use
them: Modbus TCP Gateway OFF > DI 7 low > Drive Profile **Binary** > DI 7
high. To hand control back to the PC: Modbus OFF > DI 7 low > Drive Profile
**Modbus TCP Gateway** > DI 7 high > Modbus ON.

## How it works (only if you want to change something)

igus ships no SDK for the D1. Its "Modbus TCP Gateway" tunnels CANopen CiA 402
object reads and writes inside Modbus function code 43, one object per
telegram. The `dryve_d1` package implements that framing (`gateway.py`), the
CiA 402 state machine and the Profile Position set-point handshake
(`rail.py`), "set current position" homing, and the scaling between
millimetres and the drive's integer units (`units.py`, read from the drive at
connect: 0.01 mm per unit, 143.99 mm per pulley revolution, 5:1 gear). It also
reads the Axis page's Max. Velocity and Max. Acceleration and clamps any
request above them, so the UI limits stay in force from Python.

To write your own program:

```python
from dryve_d1 import Rail

with Rail("169.254.116.59") as rail:          # the package caps speed at 100 mm/s unless you pass max_speed_mm_s=
    rail.enable()                              # motor energised
    rail.home_here()                           # here = 0 mm (drive must be on SCP)
    rail.move_abs(500, speed=100, accel=300)   # mm, mm/s, mm/s^2; blocks until reached
    rail.move_rel(-100, speed=50, accel=300)
    print(rail.position_mm(), rail.status())
    rail.disable()
```

References: dryve D1 operating manual, chapter "Modbus TCP Gateway"
(https://www.igus.eu/D1 > Downloads). igus dryve support: de-dryve@igus.net.
