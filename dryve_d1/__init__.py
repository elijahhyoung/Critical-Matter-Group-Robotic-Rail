"""
dryve_d1 - minimal, dependency-free Python SDK for the igus dryve D1 motor
controller over its Modbus TCP Gateway.

    from dryve_d1 import Rail
    with Rail("169.254.116.59") as rail:
        rail.enable(); rail.home_here()
        rail.move_abs(300, speed=100, accel=500)

Layers:
    gateway.py  - telegram framing + TCP transport (object-dictionary read/write)
    cia402.py   - object indices, control/status word bits, state decoding
    units.py    - mm <-> drive-unit scaling (60A8h / 60A9h / 6092h)
    rail.py     - Rail: enable / home / move / stop
"""
from .cia402 import CW, OD, SW, HomingMethod, Mode, State, Status
from .gateway import DEFAULT_UNIT_ID, D1Gateway, GatewayError, GatewayException
from .rail import MAX_SPEED_MM_S, Rail, RailError
from .units import Units

__all__ = [
    "Rail", "RailError", "Units", "D1Gateway", "GatewayError", "GatewayException",
    "DEFAULT_UNIT_ID", "MAX_SPEED_MM_S",
    "OD", "CW", "SW", "Mode", "State", "Status", "HomingMethod",
]
__version__ = "0.2.1"
