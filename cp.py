# controller-pedestrian.py  (v3.2)
#approved
#line 184, 289, 194
#line 343,349,354 

import os
import socket
import json
import time
import random
import csv
import datetime
import select
import signal
from typing import Optional, Tuple

# - env helpers -

def getenv_float(name: str, default: float) -> float:
    v = os.getenv(name)
    if v is None:
        return float(default)
    try:
        return float(v)
    except ValueError:
        return float(default)

def getenv_int(name: str, default: int) -> int:
    v = os.getenv(name)
    if v is None:
        return int(default)
    try:
        return int(v)
    except ValueError:
        return int(default)

def getenv_first(names, default):
    """Return the first defined environment variable value among names, else default."""
    for n in names:
        v = os.getenv(n)
        if v is not None:
            return v
    return default

def getenv_float_first(names, default: float) -> float:
    for n in names:
        v = os.getenv(n)
        if v is not None:
            try:
                return float(v)
            except ValueError:
                pass
    return float(default)

# - CONFIG -

# Networking (accept legacy aliases)
LISTEN_IP   = getenv_first(["VM_IP", "CTRL_LISTEN_IP"], "0.0.0.0")           #moiz  VM
LISTEN_PORT = int(getenv_first(["VM_PORT", "CTRL_LISTEN_PORT"], "9000"))    #moiz  VM
CARLA_IP    = os.getenv("CARLA_IP", "192.168.1.25")                            #naveed cvarla 
CARLA_PORT  = getenv_int("CARLA_PORT", 9001)                                #naveed carla 

# Thresholds (defaults = naveed's tested values)
SLOWDOWN_START_M = getenv_float("SLOWDOWN_START_M", 15.0)  # slowdown at/below this distance
# Prefer BRAKE_RANGE_M; fall back to legacy BRAKE_M
BRAKE_RANGE_M    = getenv_float_first(["BRAKE_RANGE_M", "BRAKE_M"], 6.0)

# Faults (post-decision). When SAFE_MODE=1, faults are disabled (flip/drop/delay all zero)
FLIP_PROB  = getenv_float("FLIP_PROB", 0.20)   # flips brake<->resume only
DROP_PROB  = getenv_float("DROP_PROB", 0.10)   # drops outgoing payload
DELAY_MIN  = getenv_float("DELAY_MIN", 0.0)
DELAY_MAX  = getenv_float("DELAY_MAX", 0.0)

COOLDOWN_S           = getenv_float("COOLDOWN_S", 0.20)
STALE_TIMEOUT_S      = getenv_float("STALE_TIMEOUT_S", 0.50)
NO_PED_FRAMES_NEEDED = getenv_int("NO_PED_FRAMES", 5)
MIN_BRAKE_HOLD_S     = getenv_float("MIN_BRAKE_HOLD_S", 0.50)

VERBOSITY = os.getenv("VERBOSITY", "all").lower()  # "all" | "sends" | "quiet"
def _v_all():   return VERBOSITY == "all"
def _v_send():  return VERBOSITY in ("all", "sends")
def _v_quiet(): return VERBOSITY == "quiet"

SAFE_MODE = os.getenv("SAFE_MODE", "1") == "1"
if SAFE_MODE:
    FLIP_PROB = 0.0
    DROP_PROB = 0.0
    DELAY_MIN = 0.0
    DELAY_MAX = 0.0

DELAY_RANGE_S = (min(DELAY_MIN, DELAY_MAX), max(DELAY_MIN, DELAY_MAX))
SLOWDOWN_ENABLED = BRAKE_RANGE_M < SLOWDOWN_START_M  # if false, only brake/resume will be used

# - LOGGING -

ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
session_id = f"session_{ts}"
csv_dir = os.path.expanduser("~/csv")
os.makedirs(csv_dir, exist_ok=True)
CSV_LOG_FILE  = os.path.join(csv_dir, f"{session_id}_log.csv")
JSON_LOG_FILE = os.path.join(csv_dir, f"{session_id}_log.json")

# - SOCKETS -

recv_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
recv_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
try:
    recv_sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4_194_304)  # 4 MB if OS allows
except OSError:
    pass
recv_sock.bind((LISTEN_IP, LISTEN_PORT))
recv_sock.setblocking(False)

send_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
try:
    send_sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1_048_576)
except OSError:
    pass

# - STATE -

braking_active = False
last_tx_cmd, last_tx_time = None, 0.0
last_brake_sent_time = 0.0
last_recv_time: Optional[float] = None
no_ped_count = 0
stale_asserted = False

stats = {
    "packets_received": 0,
    "packets_parsed": 0,
    "decode_errors": 0,
    "backlog_batches": 0,
    "backlog_dropped": 0,  # packets intentionally overwritten by latest-wins
    "commands_attempted": 0,
    "commands_sent": 0,
    "brakes_sent": 0,
    "resumes_sent": 0,
    "slowdowns_sent": 0,
    "flips": 0,
    "drops": 0,
    "rate_limited": 0,
    "stale_enforced": 0,
    "resume_debounced": 0,
    "decisions_none": 0,
    "total_delay_s": 0.0,
}
start_time = time.time()

# - FUNCTIONS -

def compute_latency(recv_time: float, sent_time) -> float:
    """If sender includes send_time, compute one-way latency estimate."""
    try:
        st = float(sent_time)
        if st <= recv_time:
            return recv_time - st
    except (TypeError, ValueError):
        pass
    return 0.0

def decide(speed_kmh: float, pedestrian: bool, distance) -> Tuple[Optional[str], str, Optional[float]]:
    """
    Returns (cmd, reason, distance_used)
      cmd in {"brake","slowdown","resume",None}
      distance_used is float or None (for slowdown payload & logging)
    Safety rules:
      - If pedestrian and distance is None -> brake
      - Brake when d <= BRAKE_RANGE_M
      - Slowdown when BRAKE_RANGE_M < d <= SLOWDOWN_START_M (only if slowdown enabled)
      - If no pedestrian and we were braking -> propose resume (debounced later)
    """
    global braking_active

    if pedestrian:
        if distance is not None:
            try:
                d = float(distance)
            except (TypeError, ValueError):
                d = None

            if d is not None and d >= 0.0:
                if d <= BRAKE_RANGE_M:
                    return "brake", f"ped d={d:.1f}m ≤ BRAKE_RANGE_M={BRAKE_RANGE_M:.1f}", d
                if SLOWDOWN_ENABLED and d <= SLOWDOWN_START_M:
                    return "slowdown", f"ped detected slowdown  ({BRAKE_RANGE_M:.1f}<d={d:.1f}≤{SLOWDOWN_START_M:.1f})", d   #line 184 
                return None, f"ped far (d={d:.1f}m > {SLOWDOWN_START_M:.1f}m)", d
            else:
                return "brake", "ped detected, malformed distance (assume close)", None
        else:
            return "brake", "ped detected, distance=None (assume close)", None
    else:
        if braking_active:
            return "resume", "no pedestrian; resume allowed", None                                                                             #line 194
        return None, "no pedestrian", None

def apply_faults(cmd: Optional[str], reason: str):
    """
    Post-decision faults:
      - Flip only affects brake/resume.
      - Drop/Delay may affect any non-None command.
    """
    out_cmd = cmd
    flipped = False
    dropped = False
    delay_used = 0.0
    out_reason = reason

    if out_cmd in ("brake", "resume") and random.random() < FLIP_PROB:
        out_cmd = "resume" if out_cmd == "brake" else "brake"
        flipped = True
        out_reason = f"flipped command ({reason})"

    if out_cmd is not None and random.random() < DROP_PROB:
        dropped = True
        out_reason = f"dropped command ({out_reason})"

    if out_cmd is not None:
        low, high = DELAY_RANGE_S
        if high > 0 and high >= low >= 0:
            delay_used = random.uniform(low, high)
            if delay_used > 0:
                time.sleep(delay_used)

    return out_cmd, dropped, delay_used, flipped, out_reason

def map_to_tx_payload(cmd: Optional[str], dist: Optional[float]):
    """
    Map internal command to the exact JSON expected by the CARLA receiver.
    - brake   -> {"cmd":"brake"}
    - resume  -> {"cmd":"resume"}
    - slowdown-> {"cmd":"slowdown","distance":<float>} only if dist is numeric
    """
    if cmd == "brake":
        return {"cmd": "brake"}
    if cmd == "resume":
        return {"cmd": "resume"}
    if cmd == "slowdown":
        if isinstance(dist, (int, float)):
            return {"cmd": "slowdown", "distance": round(float(dist), 2)}
        return None
    return None

def maybe_send_payload(payload: Optional[dict], reason_after: str, timestamp: str):
    """Send with change-only + cooldown; update state/stats; print based on verbosity."""
    global last_tx_cmd, last_tx_time, braking_active, last_brake_sent_time
    if not payload:
        return False, "no_cmd"

    cmd = payload.get("cmd")
    now = time.time()

    # change-only + cooldown
    if cmd == last_tx_cmd and (now - last_tx_time) < COOLDOWN_S:
        stats["rate_limited"] += 1
        if _v_all():
            print(f"[{timestamp}] ==> No TX (rate-limited, last='{last_tx_cmd}', dt={now-last_tx_time:.2f}s) ({reason_after})")
        return False, "rate_limited"

    try:
        send_sock.sendto(json.dumps(payload).encode(), (CARLA_IP, CARLA_PORT))
        last_tx_cmd, last_tx_time = cmd, now
        stats["commands_sent"] += 1
        if cmd == "brake":
            braking_active = True
            last_brake_sent_time = now
            stats["brakes_sent"] += 1
        elif cmd == "resume":
            braking_active = False
            stats["resumes_sent"] += 1
        elif cmd == "slowdown":
            stats["slowdowns_sent"] += 1

        if _v_send():
            print(f"[{timestamp}] ==> SENT {cmd.upper()} ({reason_after})")
        return True, "sent"
    except OSError as se:
        print(f"[ERROR] send failed: {se}")
        return False, "send_error"

def _handle_sigint(sig, frame):
    raise KeyboardInterrupt

signal.signal(signal.SIGINT, _handle_sigint)

# - BANNER -

if not _v_quiet():
    print(f"\n\n")                                                                                                                #line 289 
    print(f"[INFO] Listening on {LISTEN_IP}:{LISTEN_PORT}")
    print(f"[INFO] Sending commands to {CARLA_IP}:{CARLA_PORT}")
    print(f"[INFO] Logging to:\n  CSV:  {CSV_LOG_FILE}\n  JSON: {JSON_LOG_FILE}")
    print(f"[INFO] SAFE_MODE={'ON' if SAFE_MODE else 'OFF'} | VERBOSITY={VERBOSITY}")
    print(f"[INFO] Thresholds: SLOWDOWN_START_M={SLOWDOWN_START_M} | BRAKE_RANGE_M={BRAKE_RANGE_M} | SLOWDOWN_ENABLED={SLOWDOWN_ENABLED}")
    print(f"[INFO] COOLDOWN_S={COOLDOWN_S} | STALE_TIMEOUT_S={STALE_TIMEOUT_S} | "
          f"NO_PED_FRAMES={NO_PED_FRAMES_NEEDED} | MIN_BRAKE_HOLD_S={MIN_BRAKE_HOLD_S}")
    print(f"[INFO] Faults: FLIP_PROB={FLIP_PROB} | DROP_PROB={DROP_PROB} | DELAY_RANGE_S={DELAY_RANGE_S}")

# - MAIN -

BUF_SIZE = 2048

with open(CSV_LOG_FILE, "w", newline="") as csvfile, open(JSON_LOG_FILE, "w") as jsonfile:
    logger = csv.writer(csvfile)
    logger.writerow([
        "Timestamp", "|",
        "Speed (km/h)", "|",
        "Pedestrian", "|",
        "Distance (m)", "|",
        "Decision", "|",
        "Fault", "|",
        "Latency (s)", "|",
        "Delay (s)", "|",
        "Reason Type", "|",
        "Reason Detail", "|"
    ])

    latest_msg = None
    try:
        while True:
            # - receive (non-blocking) + drain backlog (latest-wins) -
            got = 0
            ready, _, _ = select.select([recv_sock], [], [], 0.05)
            if ready:
                while True:
                    try:
                        msg, _ = recv_sock.recvfrom(BUF_SIZE)
                        stats["packets_received"] += 1
                        got += 1
                        latest_msg = msg
                    except (BlockingIOError, InterruptedError):
                        break
                stats["backlog_batches"] += 1
                if got > 1:
                    stats["backlog_dropped"] += (got - 1)

            now = time.time()

            # - stale telemetry safety brake -
            if last_recv_time is not None and (now - last_recv_time) > STALE_TIMEOUT_S and not stale_asserted:
                ts_print = datetime.datetime.fromtimestamp(now).strftime("%Y-%m-%d %H:%M:%S")
                payload = {"cmd": "brake"}  # safety first
                stats["commands_attempted"] += 1
                sent, _ = maybe_send_payload(payload, f"No Data Reached (> {STALE_TIMEOUT_S:.2f}s), safety brake", ts_print)  #line 343,349,354 telemetry stale chnaged No Data Reache
                if sent:
                    stats["stale_enforced"] += 1
                    stale_asserted = True

                # minimal log row for stale event
                logger.writerow([ts_print, "|", "-1.0", "(km/h)|", False, "|", "None", "|",
                                 "brake", "|", "None", "|", "0.000", "(s)|", "0.000", "(s)|",
                                 "No Data Reached", "|", f"> {STALE_TIMEOUT_S:.2f}s", "|"])
                csvfile.flush()
                jsonfile.write(json.dumps({
                    "timestamp": ts_print,
                    "decision": "brake",
                    "reason_type": "No Data Reached",
                    "reason_detail": f"> {STALE_TIMEOUT_S:.2f}s"
                }) + "\n")
                jsonfile.flush()

            if latest_msg is None:
                continue

            recv_time = time.time()
            timestamp = datetime.datetime.fromtimestamp(recv_time).strftime("%Y-%m-%d %H:%M:%S")

            # - parse latest telemetry -
            try:
                data = json.loads(latest_msg.decode())
                stats["packets_parsed"] += 1
            except json.JSONDecodeError:
                stats["decode_errors"] += 1
                latest_msg = None
                continue

            try:
                speed = float(data.get("speed", -1.0))
            except (TypeError, ValueError):
                speed = -1.0
            pedestrian = bool(data.get("pedestrian_detected", False))
            distance = data.get("distance", None)
            latency = compute_latency(recv_time, data.get("send_time", None))

            last_recv_time = recv_time
            stale_asserted = False

            # track consecutive no-ped frames
            no_ped_count = 0 if pedestrian else (no_ped_count + 1)

            # - decision -
            decision, reason, dist_used = decide(speed, pedestrian, distance)
            if decision is None:
                stats["decisions_none"] += 1

            # resume debounce gates
            if decision == "resume":
                gate = False
                if no_ped_count < NO_PED_FRAMES_NEEDED:
                    gate = True
                    reason = f"debounce resume (no_ped_frames={no_ped_count}<{NO_PED_FRAMES_NEEDED})"
                if (time.time() - last_brake_sent_time) < MIN_BRAKE_HOLD_S:
                    gate = True
                    reason = f"debounce resume (min_hold {MIN_BRAKE_HOLD_S:.2f}s)"
                if gate:
                    decision = None
                    stats["resume_debounced"] += 1

            # - faults (flip/drop/delay) -
            post_cmd, dropped, delay_used, flipped, reason_after = apply_faults(decision, reason)
            stats["total_delay_s"] += float(delay_used)
            if flipped: stats["flips"] += 1
            if dropped: stats["drops"] += 1

            # - build payload + attempt send -
            payload = map_to_tx_payload(post_cmd, dist_used)
            if payload:
                stats["commands_attempted"] += 1

            if _v_all():
                dist_str = ("%.1f" % distance) if isinstance(distance, (int, float)) else "None"
                print(f"[{timestamp}] Speed={speed:.1f} | Pedestrian={pedestrian} | Dist={dist_str}")

            if payload and not dropped:
                maybe_send_payload(payload, reason_after, timestamp)
            else:
                if _v_all():
                    why = "dropped" if dropped else reason_after
                    print(f"[{timestamp}] ==> No TX ({why})")

            # - logs -
            fault_parts = []
            if flipped: fault_parts.append("Flip")
            if dropped: fault_parts.append("Drop")
            if delay_used > 0: fault_parts.append(f"Delay={delay_used:.2f}s")
            fault_summary = " + ".join(fault_parts) if fault_parts else "None"

            # split reason for CSV
            if "(" in reason_after and reason_after.endswith(")"):
                type_part, detail = reason_after.split("(", 1)
                reason_type = type_part.strip().capitalize()
                reason_detail = detail[:-1].strip()
            else:
                reason_type = reason_after.capitalize()
                reason_detail = ""

            logger.writerow([
                timestamp, "|",
                f"{speed:.1f}", "(km/h)|",
                pedestrian, "|",
                f"{distance:.1f}" if isinstance(distance, (int, float)) else "None", "|",
                decision or "None", "|",
                fault_summary, "|",
                f"{latency:.3f}", "(s)|",
                f"{delay_used:.3f}", "(s)|",
                reason_type, "|",
                reason_detail, "|"
            ])
            csvfile.flush()

            jsonfile.write(json.dumps({
                "timestamp": timestamp,
                "speed_kmh": round(speed, 2),
                "pedestrian": pedestrian,
                "distance_m": (round(float(distance), 2) if isinstance(distance, (int, float)) else None),
                "decision": decision,
                "fault": fault_summary,
                "latency": round(latency, 3),
                "delay": round(delay_used, 3),
                "reason_type": reason_type,
                "reason_detail": reason_detail
            }) + "\n")
            jsonfile.flush()

            latest_msg = None

    except KeyboardInterrupt:
        pass
    except Exception as e:
        print(f"[ERROR] {e}")
    finally:
        end_time = time.time()
        print("\n=== SESSION SUMMARY ===")
        for k, v in stats.items():
            print(f"{k}: {v}")
        print(f"Logs: {CSV_LOG_FILE}  |  {JSON_LOG_FILE}")
        try:
            with open(JSON_LOG_FILE, "a") as jf:
                jf.write(json.dumps({"summary": {
                    "session_id": session_id,
                    "start_time": datetime.datetime.fromtimestamp(start_time).isoformat(),
                    "end_time": datetime.datetime.fromtimestamp(end_time).isoformat(),
                    "duration_s": round(end_time - start_time, 3),
                    "config": {
                        "listen_ip": LISTEN_IP,
                        "listen_port": LISTEN_PORT,
                        "carla_ip": CARLA_IP,
                        "carla_port": CARLA_PORT,
                        "slowdown_start_m": SLOWDOWN_START_M,
                        "brake_range_m": BRAKE_RANGE_M,
                        "cooldown_s": COOLDOWN_S,
                        "stale_timeout_s": STALE_TIMEOUT_S,
                        "no_ped_frames_needed": NO_PED_FRAMES_NEEDED,
                        "min_brake_hold_s": MIN_BRAKE_HOLD_S,
                        "safe_mode": SAFE_MODE,
                        "flip_prob": FLIP_PROB,
                        "drop_prob": DROP_PROB,
                        "delay_range_s": list(DELAY_RANGE_S),
                        "verbosity": VERBOSITY,
                        "slowdown_enabled": SLOWDOWN_ENABLED,
                    },
                    "stats": stats
                }}) + "\n")
        except Exception as e:
            print(f"[WARN] Failed to append summary to JSON log: {e}")

        # append CSV summary (optional trailer, like your older version)
        try:
            with open(CSV_LOG_FILE, "a", newline="") as csvsum:
                w = csv.writer(csvsum)
                w.writerow([])
                w.writerow(["=== SESSION SUMMARY ==="])
                w.writerow(["session_id", session_id])
                w.writerow(["start_time", datetime.datetime.fromtimestamp(start_time).isoformat()])
                w.writerow(["end_time",   datetime.datetime.fromtimestamp(end_time).isoformat()])
                w.writerow(["duration_s", round(end_time - start_time, 3)])

                w.writerow(["- CONFIG -"])
                w.writerow(["listen_ip", LISTEN_IP])
                w.writerow(["listen_port", LISTEN_PORT])
                w.writerow(["carla_ip", CARLA_IP])
                w.writerow(["carla_port", CARLA_PORT])
                w.writerow(["slowdown_start_m", SLOWDOWN_START_M])
                w.writerow(["brake_range_m", BRAKE_RANGE_M])
                w.writerow(["cooldown_s", COOLDOWN_S])
                w.writerow(["stale_timeout_s", STALE_TIMEOUT_S])
                w.writerow(["no_ped_frames_needed", NO_PED_FRAMES_NEEDED])
                w.writerow(["min_brake_hold_s", MIN_BRAKE_HOLD_S])
                w.writerow(["safe_mode", SAFE_MODE])
                w.writerow(["flip_prob", FLIP_PROB])
                w.writerow(["drop_prob", DROP_PROB])
                w.writerow(["delay_range_s", f"{DELAY_RANGE_S[0]}..{DELAY_RANGE_S[1]}"])
                w.writerow(["verbosity", VERBOSITY])
                w.writerow(["slowdown_enabled", SLOWDOWN_ENABLED])

                w.writerow(["- STATS -"])
                for k, v in stats.items():
                    w.writerow([k, v])
        except Exception as e:
            print(f"[WARN] Failed to append summary to CSV: {e}")
