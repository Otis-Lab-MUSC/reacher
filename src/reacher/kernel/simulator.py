"""Simulated serial port for testing without physical Arduino hardware.

Provides SimulatedSerial (drop-in for serial.Serial) and FirmwareSimulator
(generates paradigm-aware firmware output).
"""

import json
import queue
import random
import threading
import time
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# PR step sequence: 1, 2, 4, 6, 9, 12, 15, 20, 25, 32, 40, 50, 62, 77, 95, ...
_PR_STEPS = [1, 2, 4, 6, 9, 12, 15, 20, 25, 32, 40, 50, 62, 77, 95, 118, 145, 178, 219, 268]

PARADIGM_TO_SCHEDULE = {
    "fr": "FIXED_RATIO",
    "pr": "PROGRESSIVE_RATIO",
    "vi": "VARIABLE_INTERVAL",
    "omission": "OMISSION",
    "pavlovian": "PAVLOVIAN",
}

SCHEDULE_TO_SKETCH = {
    "FIXED_RATIO": "fr.ino",
    "PROGRESSIVE_RATIO": "pr.ino",
    "VARIABLE_INTERVAL": "vi.ino",
    "OMISSION": "omission.ino",
    "PAVLOVIAN": "pavlovian.ino",
}


class FirmwareSimulator:
    """Generates firmware-protocol-compliant JSON output for a simulated session."""

    def __init__(self, tx_queue: queue.Queue):
        self._tx = tx_queue
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

        # Configuration state (updated by incoming commands)
        self.schedule = "FIXED_RATIO"
        self.ratio = 5
        self.pr_step = 2
        self.vi_interval = 30000  # ms
        self.omission_interval = 10000  # ms
        self.cue_frequency = 8000
        self.cue_duration = 1000
        self.pump_duration = 3000
        self.lever_rh_armed = True
        self.lever_rh_active = True
        self.lever_lh_armed = True
        self.lever_lh_active = False
        self.cue_armed = True
        self.pump_armed = True
        self.microscope_armed = False
        self.lick_armed = True
        self.laser_armed = False
        self.laser_frequency = 20
        self.laser_duration = 10000  # ms
        self.laser_mode = "CONTINGENT"
        self.cue2_armed = False
        self.cue2_frequency = 2900
        self.cue2_duration = 1000
        self.pump2_armed = False
        self.pump2_duration = 3000
        self.lever_rh_timeout = 20000  # ms
        self.lever_lh_timeout = 20000  # ms

        # Pavlovian parameters
        self.pav_cs_plus_count = 10
        self.pav_cs_minus_count = 5
        self.pav_cs_plus_freq = 8000
        self.pav_cs_minus_freq = 2000
        self.pav_cue_duration = 5000
        self.pav_trace_interval = 1000
        self.pav_iti_mean = 30000
        self.pav_iti_min = 20000
        self.pav_iti_max = 40000

        # Runtime clock (ms since session start)
        self._clock = 0

    def _send(self, msg: dict):
        self._tx.put(json.dumps(msg).encode() + b"\n")

    def handle_command(self, cmd_data: dict):
        cmd = cmd_data.get("cmd")
        if cmd is None:
            return

        if cmd == 102:  # IDENTIFY
            self._send_identification()
        elif cmd == 101:  # SESSION_START
            self.start()
        elif cmd == 100:  # SESSION_END
            self.stop()
        elif cmd == 105:  # SESSION_PAUSE
            if cmd_data.get("paused"):
                self._running = False
            else:
                self._running = True
        elif cmd == 103:  # TEST_CHAIN
            self._send_test_chain()
        elif cmd == 104:  # TEST_MODE
            pass
        # Device tests
        elif cmd == 303:  # CUE_TEST
            self._send_device_test("CUE", 8, "TONE", self.cue_duration)
        elif cmd == 403:  # PUMP_TEST
            self._send_device_test("PUMP", 9, "INFUSION", self.pump_duration)
        elif cmd == 903:  # MICROSCOPE_TEST
            self._send_device_test("MICROSCOPE", 10, "TIMESTAMP", 100)
        elif cmd == 603:  # LASER_TEST
            self._send_device_test("LASER", 11, "PULSE", 500)
        # Arm/disarm
        elif cmd == 301:
            self.cue_armed = True
        elif cmd == 300:
            self.cue_armed = False
        elif cmd == 401:
            self.pump_armed = True
        elif cmd == 400:
            self.pump_armed = False
        elif cmd == 501:
            self.lick_armed = True
        elif cmd == 500:
            self.lick_armed = False
        elif cmd == 601:
            self.laser_armed = True
        elif cmd == 600:
            self.laser_armed = False
        elif cmd == 901:
            self.microscope_armed = True
        elif cmd == 900:
            self.microscope_armed = False
        elif cmd == 1001:
            self.lever_rh_armed = True
        elif cmd == 1000:
            self.lever_rh_armed = False
        elif cmd == 1301:
            self.lever_lh_armed = True
        elif cmd == 1300:
            self.lever_lh_armed = False
        elif cmd == 1081:
            self.lever_rh_active = True
        elif cmd == 1080:
            self.lever_rh_active = False
        elif cmd == 1381:
            self.lever_lh_active = True
        elif cmd == 1380:
            self.lever_lh_active = False
        # Parameter setters
        elif cmd == 201:
            self.ratio = cmd_data.get("ratio", self.ratio)
        elif cmd == 202:
            paradigm_val = cmd_data.get("paradigm")
            if isinstance(paradigm_val, str):
                self.schedule = PARADIGM_TO_SCHEDULE.get(paradigm_val, self.schedule)
        elif cmd == 203:
            self.omission_interval = cmd_data.get("interval", self.omission_interval)
        elif cmd == 204:
            self.vi_interval = cmd_data.get("interval", self.vi_interval)
        elif cmd == 205:
            self.pr_step = cmd_data.get("step", self.pr_step)
        elif cmd == 371:
            self.cue_frequency = cmd_data.get("frequency", self.cue_frequency)
        elif cmd == 372:
            self.cue_duration = cmd_data.get("duration", self.cue_duration)
        elif cmd == 472:
            self.pump_duration = cmd_data.get("duration", self.pump_duration)
        elif cmd == 311:
            self.cue2_armed = True
        elif cmd == 310:
            self.cue2_armed = False
        elif cmd == 313:  # CUE2_TEST
            self._send_device_test("CUE", 12, "TONE", self.cue2_duration)
        elif cmd == 381:
            self.cue2_frequency = cmd_data.get("frequency", self.cue2_frequency)
        elif cmd == 382:
            self.cue2_duration = cmd_data.get("duration", self.cue2_duration)
        elif cmd == 411:
            self.pump2_armed = True
        elif cmd == 410:
            self.pump2_armed = False
        elif cmd == 413:  # PUMP2_TEST
            self._send_device_test("PUMP", 13, "INFUSION", self.pump2_duration)
        elif cmd == 482:
            self.pump2_duration = cmd_data.get("duration", self.pump2_duration)
        elif cmd == 671:
            self.laser_frequency = cmd_data.get("frequency", self.laser_frequency)
        elif cmd == 672:
            self.laser_duration = cmd_data.get("duration", self.laser_duration)
        elif cmd == 681:
            self.laser_mode = "CONTINGENT"
        elif cmd == 682:
            self.laser_mode = "INDEPENDENT"
        elif cmd == 1074:
            self.lever_rh_timeout = cmd_data.get("timeout", self.lever_rh_timeout)
        elif cmd == 1075:
            self.ratio = cmd_data.get("ratio", self.ratio)
        elif cmd == 1374:
            self.lever_lh_timeout = cmd_data.get("timeout", self.lever_lh_timeout)
        elif cmd == 1375:
            self.ratio = cmd_data.get("ratio", self.ratio)
        # Pavlovian parameters
        elif cmd == 208:
            self.pav_cs_plus_count = cmd_data.get("count", self.pav_cs_plus_count)
        elif cmd == 209:
            self.pav_cs_minus_count = cmd_data.get("count", self.pav_cs_minus_count)
        elif cmd == 210:
            self.pav_cs_plus_freq = cmd_data.get("frequency", self.pav_cs_plus_freq)
        elif cmd == 211:
            self.pav_cs_minus_freq = cmd_data.get("frequency", self.pav_cs_minus_freq)
        elif cmd == 213:
            self.pav_cue_duration = cmd_data.get("duration", self.pav_cue_duration)
        elif cmd == 214:
            self.pav_trace_interval = cmd_data.get("interval", self.pav_trace_interval)
        elif cmd == 216:
            self.pav_iti_mean = cmd_data.get("iti_mean", self.pav_iti_mean)
        elif cmd == 217:
            self.pav_iti_min = cmd_data.get("iti_min", self.pav_iti_min)
        elif cmd == 218:
            self.pav_iti_max = cmd_data.get("iti_max", self.pav_iti_max)

    def _send_identification(self):
        sketch = SCHEDULE_TO_SKETCH.get(self.schedule, "fr.ino")
        self._send({
            "level": "000", "device": "CONTROLLER", "sketch": sketch,
            "version": "v2.0.0-sim", "baud_rate": 115200,
            "schedule": self.schedule,
        })
        # Device config messages
        self._send({
            "level": "000", "device": "CUE",
            "armed": self.cue_armed, "frequency": self.cue_frequency,
            "duration": self.cue_duration,
        })
        active_orientation = "RH" if self.lever_rh_active else "LH"
        self._send({
            "level": "000", "device": f"SWITCH_LEVER_{active_orientation}",
            "armed": self.lever_rh_armed if active_orientation == "RH" else self.lever_lh_armed,
            "reinforced": True,
        })
        self._send({
            "level": "000", "device": "PUMP",
            "armed": self.pump_armed, "duration": self.pump_duration,
        })
        if self.lick_armed:
            self._send({"level": "000", "device": "LICK_CIRCUIT", "armed": True})
        if self.microscope_armed:
            self._send({"level": "000", "device": "MICROSCOPE", "armed": True})
        if self.laser_armed:
            self._send({"level": "000", "device": "LASER", "armed": True})

    def _send_device_test(self, device: str, pin: int, event: str, duration: int):
        ts = self._clock
        self._send({
            "level": "007", "device": device, "pin": pin,
            "event": event, "start_timestamp": ts,
            "end_timestamp": ts + duration,
        })

    def _send_test_chain(self):
        self._send_device_test("CUE", 8, "TONE", self.cue_duration)
        self._send_device_test("PUMP", 9, "INFUSION", self.pump_duration)

    def start(self):
        if self._running:
            return
        self._running = True
        self._stop_event.clear()
        self._clock = 0
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)
        self._thread = None

    def _run_loop(self):
        schedule = self.schedule
        if schedule == "FIXED_RATIO":
            self._run_fr()
        elif schedule == "PROGRESSIVE_RATIO":
            self._run_pr()
        elif schedule == "VARIABLE_INTERVAL":
            self._run_vi()
        elif schedule == "OMISSION":
            self._run_omission()
        elif schedule == "PAVLOVIAN":
            self._run_pavlovian()

    # --- Paradigm runners ---

    def _run_fr(self):
        press_count = 0
        while self._running and not self._stop_event.is_set():
            delay = random.uniform(2, 8)
            if self._stop_event.wait(delay):
                break
            if not self._running:
                break
            self._clock += int(delay * 1000)

            # Occasional inactive press (~15%)
            if random.random() < 0.15:
                self._emit_lever_press("INACTIVE")
                continue

            self._emit_lever_press("ACTIVE")
            press_count += 1

            if press_count >= self.ratio:
                press_count = 0
                self._emit_reinforcement_chain()

            if random.random() < 0.2:
                self._emit_lick()
            self._emit_microscope_frame()

    def _run_pr(self):
        step_index = 0
        while self._running and not self._stop_event.is_set():
            current_ratio = _PR_STEPS[min(step_index, len(_PR_STEPS) - 1)]
            press_count = 0
            while press_count < current_ratio and self._running and not self._stop_event.is_set():
                # Intervals increase with ratio
                base_delay = min(2 + step_index * 0.5, 15)
                delay = random.uniform(base_delay * 0.5, base_delay * 1.5)
                if self._stop_event.wait(delay):
                    break
                if not self._running:
                    break
                self._clock += int(delay * 1000)
                self._emit_lever_press("ACTIVE")
                press_count += 1

                if random.random() < 0.1:
                    self._emit_lever_press("INACTIVE")
                self._emit_microscope_frame()

            if self._running and not self._stop_event.is_set():
                self._emit_reinforcement_chain()
                if random.random() < 0.2:
                    self._emit_lick()
                step_index += 1

    def _run_vi(self):
        # Variable interval: reinforcement available after random interval elapses
        while self._running and not self._stop_event.is_set():
            # Wait for the variable interval to elapse
            interval_s = random.uniform(
                self.vi_interval * 0.5 / 1000,
                self.vi_interval * 1.5 / 1000,
            )
            elapsed = 0
            # Generate presses during interval (non-reinforced)
            while elapsed < interval_s and self._running and not self._stop_event.is_set():
                delay = random.uniform(2, 6)
                if self._stop_event.wait(delay):
                    break
                if not self._running:
                    break
                self._clock += int(delay * 1000)
                elapsed += delay
                self._emit_lever_press("ACTIVE")
                self._emit_microscope_frame()

            if not self._running or self._stop_event.is_set():
                break

            # Next press after interval is reinforced
            delay = random.uniform(1, 4)
            if self._stop_event.wait(delay):
                break
            self._clock += int(delay * 1000)
            self._emit_lever_press("ACTIVE")
            self._emit_reinforcement_chain()
            if random.random() < 0.2:
                self._emit_lick()

    def _run_omission(self):
        while self._running and not self._stop_event.is_set():
            interval_s = self.omission_interval / 1000
            pressed = False

            # During omission interval, animal may or may not press
            elapsed = 0
            while elapsed < interval_s and self._running and not self._stop_event.is_set():
                delay = random.uniform(2, 5)
                if self._stop_event.wait(delay):
                    break
                if not self._running:
                    break
                self._clock += int(delay * 1000)
                elapsed += delay

                # ~40% chance of pressing during interval
                if random.random() < 0.4:
                    self._emit_lever_press("ACTIVE")
                    pressed = True

            if not self._running or self._stop_event.is_set():
                break

            # Reinforcement delivered only if NO press during interval
            if not pressed:
                self._emit_reinforcement_chain()
                if random.random() < 0.3:
                    self._emit_lick()
            else:
                self._send({
                    "level": "007", "device": "CONTROLLER", "pin": 0,
                    "event": "OMISSION_WITHHELD", "timestamp": self._clock,
                })
            self._emit_microscope_frame()

    def _run_pavlovian(self):
        # Build trial list: CS+ and CS- in random order
        trials = (
            ["CS_PLUS"] * self.pav_cs_plus_count
            + ["CS_MINUS"] * self.pav_cs_minus_count
        )
        random.shuffle(trials)

        for trial_type in trials:
            if not self._running or self._stop_event.is_set():
                break

            freq = self.pav_cs_plus_freq if trial_type == "CS_PLUS" else self.pav_cs_minus_freq

            # Cue onset
            if self.cue_armed:
                self._send({
                    "level": "007", "device": "CUE", "pin": 8,
                    "event": "TONE", "start_timestamp": self._clock,
                    "end_timestamp": self._clock + self.pav_cue_duration,
                })
            cue_s = self.pav_cue_duration / 1000
            if self._stop_event.wait(cue_s):
                break
            self._clock += self.pav_cue_duration

            # Trace interval
            trace_s = self.pav_trace_interval / 1000
            if self._stop_event.wait(trace_s):
                break
            self._clock += self.pav_trace_interval

            # Reward (CS+ only)
            if trial_type == "CS_PLUS" and self.pump_armed:
                self._send({
                    "level": "007", "device": "PUMP", "pin": 9,
                    "event": "INFUSION", "start_timestamp": self._clock,
                    "end_timestamp": self._clock + self.pump_duration,
                })
                pump_s = self.pump_duration / 1000
                if self._stop_event.wait(pump_s):
                    break
                self._clock += self.pump_duration

            if random.random() < 0.5:
                self._emit_lick()
            self._emit_microscope_frame()

            # ITI
            iti_ms = random.randint(self.pav_iti_min, self.pav_iti_max)
            iti_s = iti_ms / 1000
            if self._stop_event.wait(iti_s):
                break
            self._clock += iti_ms

        # Signal completion
        if self._running and not self._stop_event.is_set():
            self._send({
                "level": "007", "device": "PAVLOV", "pin": 0,
                "event": "ALL_TRIALS_COMPLETE", "timestamp": self._clock,
            })

    # --- Event emitters ---

    def _emit_lever_press(self, press_class: str):
        if press_class == "ACTIVE":
            orientation = "RH" if self.lever_rh_active else "LH"
        else:
            # Inactive press comes from the non-reinforced lever
            orientation = "LH" if self.lever_rh_active else "RH"

        armed = self.lever_rh_armed if orientation == "RH" else self.lever_lh_armed
        if not armed:
            return

        pin = 2 if orientation == "RH" else 3
        duration = random.randint(80, 200)
        self._send({
            "level": "007", "device": "SWITCH_LEVER", "pin": pin,
            "event": "PRESS", "class": press_class,
            "start_timestamp": self._clock,
            "end_timestamp": self._clock + duration,
            "orientation": orientation,
        })
        self._clock += duration

    def _emit_reinforcement_chain(self):
        # Cue
        if self.cue_armed:
            self._send({
                "level": "007", "device": "CUE", "pin": 8,
                "event": "TONE", "start_timestamp": self._clock,
                "end_timestamp": self._clock + self.cue_duration,
            })
            self._clock += self.cue_duration

        # Pump
        if self.pump_armed:
            self._send({
                "level": "007", "device": "PUMP", "pin": 9,
                "event": "INFUSION", "start_timestamp": self._clock,
                "end_timestamp": self._clock + self.pump_duration,
            })
            self._clock += self.pump_duration

        # Laser (contingent mode fires with reinforcement chain)
        if self.laser_armed and self.laser_mode == "CONTINGENT":
            self._send({
                "level": "007", "device": "LASER", "pin": 11,
                "event": "PULSE", "start_timestamp": self._clock,
                "end_timestamp": self._clock + self.laser_duration,
            })
            self._clock += self.laser_duration

    def _emit_lick(self):
        if not self.lick_armed:
            return
        duration = random.randint(30, 100)
        self._send({
            "level": "007", "device": "LICK_CIRCUIT", "pin": 4,
            "event": "LICK", "start_timestamp": self._clock,
            "end_timestamp": self._clock + duration,
        })
        self._clock += duration

    def _emit_microscope_frame(self):
        if not self.microscope_armed:
            return
        self._send({
            "level": "008", "device": "MICROSCOPE", "pin": 10,
            "event": "TIMESTAMP", "timestamp": self._clock,
        })


class SimulatedSerial:
    """Drop-in replacement for serial.Serial used by the REACHER kernel.

    Implements only the interface subset that read_serial() and
    send_serial_command() rely on. Data flows through an internal queue
    bridging the FirmwareSimulator output thread to REACHER's serial reader.
    """

    def __init__(self, baudrate: int = 115200, timeout: float = 1):
        self.port: Optional[str] = "SIMULATOR"
        self.baudrate = baudrate
        self.timeout = timeout
        self.is_open = False
        self._rx_queue: queue.Queue = queue.Queue()
        self._simulator = FirmwareSimulator(self._rx_queue)

    def open(self):
        self.is_open = True
        logger.info("SimulatedSerial opened")

    def close(self):
        self._simulator.stop()
        self.is_open = False
        logger.info("SimulatedSerial closed")

    def readline(self) -> bytes:
        try:
            return self._rx_queue.get(timeout=self.timeout)
        except queue.Empty:
            return b""

    def write(self, data: bytes):
        try:
            text = data.decode("utf-8").strip()
            cmd_data = json.loads(text)
            self._simulator.handle_command(cmd_data)
        except (json.JSONDecodeError, UnicodeDecodeError):
            logger.warning("SimulatedSerial: could not parse write data: %r", data)

    def flush(self):
        pass

    def reset_input_buffer(self):
        while not self._rx_queue.empty():
            try:
                self._rx_queue.get_nowait()
            except queue.Empty:
                break

    @property
    def in_waiting(self) -> int:
        return 1 if not self._rx_queue.empty() else 0
