from xarm.wrapper import XArmAPI
import math
import time

# ==========================================
# XARM6 - ANIMALISTIC DANCE
# ==========================================

ROBOT_IP = "192.168.1.228"

START = [180, -95, -12, 22, -28, -82]

TRANSITION_TIME = 5.0
DANCE_TIME = 120.0
RETURN_TIME = 5.0

RUN_HOURS = 2
RUN_TIME = RUN_HOURS * 3600.0

UPDATE_RATE = 50.0
DT = 1.0 / UPDATE_RATE

# Joint ranges in degrees, cycles per 120 sec,
# and phase offsets in radians.
JOINTS = [
    (140, 220, 3, 0.0),     # J1
    (-100, -80, 6, 0.4),    # J2
    (-40, -12, 6, 1.7),     # J3
    (-5, 40, 9, 2.2),       # J4
    (-20, -5, 6, 3.1),      # J5
    (-120, -80, 12, 0.8),  # J6
]


def dance_angles(t):
    angles = []

    for low, high, cycles, phase in JOINTS:
        center = (low + high) / 2.0
        amplitude = (high - low) / 2.0

        angle = center + amplitude * math.sin(
            2.0 * math.pi * cycles * t / DANCE_TIME
            + phase
        )

        angles.append(angle)

    return angles


def smoothstep(x):
    x = max(0.0, min(1.0, x))
    return x * x * (3.0 - 2.0 * x)


def interpolate(a, b, fraction):
    s = smoothstep(fraction)

    return [
        a[i] + s * (b[i] - a[i])
        for i in range(6)
    ]


def check(code, operation):
    if code != 0:
        raise RuntimeError(
            "{} failed: {}".format(operation, code)
        )


def validate_ranges():
    for j, (low, high, cycles, phase) in enumerate(JOINTS):
        if low >= high:
            raise ValueError("Invalid range for J{}".format(j + 1))

    print("Mathematical trajectory ranges valid.")
    print("Collision safety not verified.")


def stream_motion(arm, duration, trajectory, label):
    print("Starting:", label)

    start_time = time.monotonic()
    next_update = start_time

    while True:
        elapsed = time.monotonic() - start_time

        if elapsed >= duration:
            break

        angles = trajectory(elapsed)

        code = arm.set_servo_angle_j(
            angles,
            is_radian=False
        )

        check(code, label)

        next_update += DT
        sleep_time = next_update - time.monotonic()

        if sleep_time > 0:
            time.sleep(sleep_time)

    print("Completed:", label)


def main():
    validate_ranges()

    arm = XArmAPI(
        ROBOT_IP,
        is_radian=False
    )

    try:
        if not arm.connected:
            raise RuntimeError("Robot not connected")

        code, current = arm.get_servo_angle(
            is_radian=False
        )
        check(code, "Read joint angles")

        print("Current angles:", current[:6])

        for i in range(6):
            if abs(current[i] - START[i]) > 1.0:
                raise RuntimeError(
                    "J{} starting position incorrect. "
                    "Expected {}, got {:.2f}".format(
                        i + 1, START[i], current[i]
                    )
                )

        # Servo streaming mode
        check(arm.motion_enable(True), "Enable motion")
        check(arm.set_mode(1), "Set servo mode")
        check(arm.set_state(0), "Set ready state")

        first_pose = dance_angles(0.0)
        last_pose = dance_angles(RUN_TIME % DANCE_TIME)

        # Phase 1: transition into dance
        stream_motion(
            arm,
            TRANSITION_TIME,
            lambda t: interpolate(
                START,
                first_pose,
                t / TRANSITION_TIME
            ),
            "Transition into dance"
        )

        # Phase 2: timed continuous dance
        stream_motion(
            arm,
            RUN_TIME,
            lambda t: dance_angles(t % DANCE_TIME),
            "Animalistic dance"
        )

        # Phase 3: return home
        stream_motion(
            arm,
            RETURN_TIME,
            lambda t: interpolate(
                last_pose,
                START,
                t / RETURN_TIME
            ),
            "Return to starting pose"
        )

        print("Dance complete.")

    finally:
        try:
            arm.set_state(4)
        finally:
            arm.disconnect()

        print("Disconnected.")


if __name__ == "__main__":
    main()
