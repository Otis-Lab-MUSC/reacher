import bisect
import csv
import serial
import queue
import threading
import time
import json
import io
import os
import logging
from typing import Callable, Dict, List, Optional, Union
from serial.tools import list_ports

from .commands import build_command_payload, SCHEDULE_TO_PARADIGM

_USE_VALUE = object()  # sentinel: use the `value` arg from send_command()

# Maps command codes to (device_name, field_name, value_or_sentinel)
# _USE_VALUE means the value is taken from the `value` arg passed to send_command().
_COMMAND_STATE_MAP: dict[int, tuple[str, str, object]] = {
    # --- Arm/Disarm ---
    300: ("CUE", "armed", False),
    301: ("CUE", "armed", True),
    310: ("CUE2", "armed", False),
    311: ("CUE2", "armed", True),
    400: ("PUMP", "armed", False),
    401: ("PUMP", "armed", True),
    410: ("PUMP2", "armed", False),
    411: ("PUMP2", "armed", True),
    500: ("LICK_CIRCUIT", "armed", False),
    501: ("LICK_CIRCUIT", "armed", True),
    600: ("LASER", "armed", False),
    601: ("LASER", "armed", True),
    900: ("MICROSCOPE", "armed", False),
    901: ("MICROSCOPE", "armed", True),
    1000: ("LEVER_RH", "armed", False),
    1001: ("LEVER_RH", "armed", True),
    1300: ("LEVER_LH", "armed", False),
    1301: ("LEVER_LH", "armed", True),
    # --- Cue parameters ---
    371: ("CUE", "frequency", _USE_VALUE),
    372: ("CUE", "duration", _USE_VALUE),
    381: ("CUE2", "frequency", _USE_VALUE),
    382: ("CUE2", "duration", _USE_VALUE),
    # --- Cue pulse parameters ---
    374: ("CUE", "pulse_on", _USE_VALUE),
    375: ("CUE", "pulse_off", _USE_VALUE),
    384: ("CUE2", "pulse_on", _USE_VALUE),
    385: ("CUE2", "pulse_off", _USE_VALUE),
    # --- Pump parameters ---
    472: ("PUMP", "duration", _USE_VALUE),
    482: ("PUMP2", "duration", _USE_VALUE),
    221: ("PUMP2", "active", _USE_VALUE),
    # --- Laser parameters ---
    671: ("LASER", "frequency", _USE_VALUE),
    672: ("LASER", "duration", _USE_VALUE),
    673: ("LASER", "onset_delay", _USE_VALUE),
    681: ("LASER", "mode", "contingent"),
    682: ("LASER", "mode", "independent"),
    684: ("LASER", "mode", "rh_lever"),
    # --- Pavlovian laser ---
    691: ("LASER", "trial_filter", "cs_plus"),
    692: ("LASER", "trial_filter", "cs_minus"),
    693: ("LASER", "trial_filter", "cs_both"),
    694: ("LASER", "phase", "reward"),
    695: ("LASER", "phase", "cue"),
    # --- Lever parameters ---
    1074: ("LEVER_RH", "timeout", _USE_VALUE),
    1075: ("LEVER_RH", "ratio", _USE_VALUE),
    1374: ("LEVER_LH", "timeout", _USE_VALUE),
    1375: ("LEVER_LH", "ratio", _USE_VALUE),
    # --- Pin reassignment (suffix x76) ---
    376: ("CUE", "pin", _USE_VALUE),
    386: ("CUE2", "pin", _USE_VALUE),
    476: ("PUMP", "pin", _USE_VALUE),
    486: ("PUMP2", "pin", _USE_VALUE),
    576: ("LICK_CIRCUIT", "pin", _USE_VALUE),
    676: ("LASER", "pin", _USE_VALUE),
    976: ("MICROSCOPE", "trigger_pin", _USE_VALUE),
    1076: ("LEVER_RH", "pin", _USE_VALUE),
    1376: ("LEVER_LH", "pin", _USE_VALUE),
    # --- SLM ---
    1100: ("SLM", "armed", False),
    1101: ("SLM", "armed", True),
    1176: ("SLM", "pin", _USE_VALUE),
}

class REACHER:
    """A class to manage serial communication and data collection for REACHER experiments."""

    def __init__(
        self,
        session_id: Optional[str] = None,
        event_callback: Optional[Callable[[str, str, dict], None]] = None,
        on_stop: Optional[Callable[[], None]] = None,
    ) -> None:
        """Initialize a REACHER instance.

        Args:
            session_id: Unique identifier for this session (assigned by SessionManager).
            event_callback: Optional callback ``(session_id, event_type, data)`` for
                broadcasting events over WebSocket.
            on_stop: Optional callback invoked after ``stop_program()`` completes,
                used by SessionManager to broadcast the "stopped" state.
        """

        self.session_id: Optional[str] = session_id
        self.event_callback = event_callback
        self._on_stop = on_stop

        # Serial variables
        self._is_simulated: bool = False
        self.ser: serial.Serial = serial.Serial(baudrate=115200, timeout=1)
        self.queue: queue.Queue = queue.Queue(maxsize=5000)
        # Fix: F-003 — Configurable serial reconnection parameters
        self._SERIAL_RECONNECT_RETRIES: int = 3
        self._SERIAL_RECONNECT_DELAY: int = 3  # seconds between retry attempts
        # Fix 2.6: count queue-overflow drops and throttle WS-warning emission.
        # Without visibility, an overflowed queue silently drops serial lines
        # while the frontend still shows "connected". The counter is cumulative;
        # the throttle keeps an overflow storm from flooding the WS queue in
        # turn (which would itself need to drop). -inf ensures the first
        # overflow always emits.
        self._queue_overflow_count: int = 0
        self._last_queue_overflow_emit: float = float("-inf")

        # Fix: #33 — Surface log-write failures to the operator via WS warning.
        # Throttle per log_type (1 s) and accumulate a consecutive-failure count
        # so a ENOSPC storm emits one event/sec with the run total, not one per write.
        self._last_log_failure_emit: Dict[str, float] = {}
        self._log_failure_counts: Dict[str, int] = {"event_log": 0, "controller_log": 0}

        # Fix 7.4: track cumulative event_callback failures so the API layer
        # can surface degraded WS/observer health without risking recursion
        # (a broken callback is the one channel we can't use to self-report).
        self._emit_failure_count: int = 0

        # Thread variables
        # Fix: PY-007 — Threads created here but started lazily in open_serial()
        # Fix 7.1: wrap each target in a resilient shim so an unhandled
        # exception in read_serial/handle_queue/monitor_time_limit does not
        # silently kill the daemon. See _resilient().
        self.serial_thread: threading.Thread = self._make_thread(self.read_serial, "read_serial")
        self.queue_thread: threading.Thread = self._make_thread(self.handle_queue, "handle_queue")
        self.time_check_thread: threading.Thread = self._make_thread(self.monitor_time_limit, "monitor_time_limit")
        self.thread_lock: threading.Lock = threading.Lock()
        self.serial_flag: threading.Event = threading.Event()
        self.program_flag: threading.Event = threading.Event()
        self.program_running: bool = False
        self.time_check_flag: threading.Event = threading.Event()
        self.serial_flag.set()
        self.program_flag.set()
        self.time_check_flag.set()
        # Fix: LAZ-001 — Event gate for firmware readiness (IDENTIFY response)
        # Set when firmware_information is populated (IDENTIFY ack received),
        # used to delay "connected" state transition until bootloader exits and
        # firmware is truly ready to process commands.
        self._firmware_ready: threading.Event = threading.Event()
        # Gate for the firmware's CONTROLLER END event (level 007, device CONTROLLER, event END).
        # stop_program() waits on this before exporting so the END row lands in behavior_data.
        self._controller_end_received: threading.Event = threading.Event()

        # Data process variables (guarded by thread_lock for cross-thread access)
        self.behavior_data: List[Dict[str, Union[str, int]]] = []
        self.frame_data: List[int] = []
        self.slm_data: List[int] = []
        self._infusion_count: int = 0  # Atomic counter — avoids O(n) rescan in check_limit_met
        # Fix: F-002 — Memory warning thresholds for unbounded data lists
        self._DATA_WARNING_THRESHOLD = 100_000  # Warn when lists exceed this size
        self._data_warning_emitted: bool = False

        # Segmentation state
        self._segment_number: int = 0
        self._cumulative_infusion_count: int = 0
        self._segment_exports: List[str] = []
        self._segment_event_counts: List[int] = []

        # Program variables
        self.program_start_time: Optional[float] = None
        self.program_end_time: Optional[float] = None
        self.paused_time: float = 0
        self.paused_start_time: Optional[float] = None
        self.limit_type: Optional[str] = None
        self.infusion_limit: Optional[int] = None
        self.time_limit: Optional[int] = None
        self.stop_delay: Optional[int] = None
        self.last_infusion_time: Optional[float] = None

        # Configuration variables
        self.box_name: Optional[str] = None
        self.firmware_information: Dict = {
            "sketch": None,
            "version": None,
            "baud_rate": None,
            "desc": None
        }
        self.hardware_settings: List = []
        self.reacher_log_path = os.path.expanduser(fr'~/REACHER/LOG/{self.get_time()}')
        os.makedirs(self.reacher_log_path, exist_ok=True)
        self.controller_log: str = os.path.join(self.reacher_log_path, "controller_log.json")
        self.interface_log: str = os.path.join(self.reacher_log_path, "interface_log.log")
        self.logger = logging.getLogger(f"reacher.{session_id or 'default'}")
        self.logger.setLevel(logging.INFO)
        formatter = logging.Formatter('%(asctime)s [%(levelname)s]: %(message)s')
        fh = logging.FileHandler(self.interface_log)
        fh.setFormatter(formatter)
        self.logger.addHandler(fh)
        if not self.logger.handlers or not any(isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler) for h in self.logger.handlers):
            sh = logging.StreamHandler()
            sh.setFormatter(formatter)
            self.logger.addHandler(sh)
        # Fix: F-010 — Persistent file handle for event log (avoids open/close per event)
        self._event_log_path = os.path.join(self.reacher_log_path, "event_log.jsonl")
        self._event_log_file: Optional[io.TextIOWrapper] = None
        self._event_log_write_count: int = 0
        self._EVENT_LOG_FSYNC_INTERVAL: int = 50  # fsync every N writes

        # Fix 4.9: mirror the event-log pattern — keep controller_log.json open
        # across writes and batch fsyncs instead of open/close/fsync per line.
        self._controller_log_file: Optional[io.TextIOWrapper] = None
        self._controller_log_write_count: int = 0
        self._CONTROLLER_LOG_FSYNC_INTERVAL: int = 10

        self.data_destination: Optional[str] = None
        self.behavior_filename: Optional[str] = None
        self.code_dict: Dict = {
            "000": self.update_firmware_information,
            "001": self.logger.info,
            "006": self.handle_firmware_error,
            "007": self.update_behavioral_events,
            "008": self.update_frame_events,
            "009": self.update_slm_events
        }

        self.logger.info("REACHER instance created")

    def reset(self) -> None:
        """Reset the REACHER instance to its initial state.

        **Description:**
        - Stops any active program and clears the data queue.
        - Closes the serial connection and reinitializes all variables.
        - Restarts serial and queue handling threads.
        - Ensures a clean slate for a new experiment session.
        """
        
        self.logger.info("Resetting REACHER instance")

        if not self.program_flag.is_set():
            self.stop_program()

        self.clear_queue()
        self.close_serial()
        self.time_check_flag.clear()  # Stop the time check thread

        self.behavior_data = []
        self.frame_data = []
        self.slm_data = []
        self._infusion_count = 0
        self._data_warning_emitted = False  # Fix: F-002 — Reset warning flag
        self._event_log_write_count = 0  # Fix: F-010 — Reset write counter
        self._controller_log_write_count = 0  # Fix 4.9 — Reset write counter

        self.program_start_time = None
        self.program_end_time = None
        self.paused_time = 0
        self.paused_start_time = None
        self.limit_type = None
        self.infusion_limit = None
        self.time_limit = None
        self.stop_delay = None
        self.last_infusion_time = None

        self.firmware_information = {}
        self.controller_log: str = os.path.join(self.reacher_log_path, "controller_log.json")
        self.interface_log: str = os.path.join(self.reacher_log_path, "interface_log.log")
        self.data_destination = None
        self.behavior_filename = None

        if self._is_simulated:
            from .simulator import SimulatedSerial
            self.ser = SimulatedSerial(baudrate=115200, timeout=1)
            self.ser.port = "SIMULATOR"
        else:
            self.ser = serial.Serial(baudrate=115200, timeout=1)
        self.queue = queue.Queue(maxsize=5000)  # Fix: F-006 — Match __init__ maxsize

        self.serial_flag.clear()
        self.program_flag.set()
        self.time_check_flag.set()
        self.serial_thread = self._make_thread(self.read_serial, "read_serial")
        self.queue_thread = self._make_thread(self.handle_queue, "handle_queue")
        self.time_check_thread = self._make_thread(self.monitor_time_limit, "monitor_time_limit")
        self.serial_thread.start()
        self.queue_thread.start()
        self.time_check_thread.start()

        self.logger.info("REACHER instance reset")

    def get_COM_ports(self) -> List[str]:
        """Retrieve a list of available COM ports.

        **Description:**
        - Scans the system for connected serial devices.
        - Returns a list of COM port names (e.g., "COM1", "/dev/ttyUSB0").
        - If no ports are found, returns ["No available ports"].

        **Returns:**
        - `List[str]`: Available COM port names or a placeholder if none are detected.
        """
        
        self.logger.info("Accessing available COM ports")
        
        available_ports = [p.device for p in list_ports.comports() if p.vid and p.pid]
        available_ports.append("SIMULATOR")

        self.logger.info("COM ports successfully accessed")

        return available_ports
    
    def set_COM_port(self, port: str) -> None:
        """Set the COM port for serial communication.

        **Description:**
        - Configures the serial port to the specified name (e.g., "COM3").
        - Raises ValueError if port is not valid or not found in the system.

        **Args:**
        - `port (str)`: The name of the COM port to use.

        **Raises:**
        - `ValueError`: If the port is not available.
        """

        self.logger.info("Setting COM port")

        if port == "SIMULATOR":
            from .simulator import SimulatedSerial
            self.ser = SimulatedSerial(baudrate=115200, timeout=1)
            self.ser.port = "SIMULATOR"
            self._is_simulated = True
        elif port in [p.device for p in list_ports.comports() if p.vid and p.pid]:
            self.ser.port = port
            self._is_simulated = False
        else:
            # Fix: SER-003 — Raise on invalid port instead of silently ignoring
            raise ValueError(f"Port {port!r} is not available")

        self.logger.info(f"Set COM port to {port}")

    def open_serial(self) -> None:
        """Open the serial connection and start communication threads.

        **Description:**
        - Establishes a serial connection using the configured port.
        - Starts threads for reading serial data and processing the queue.
        - Sends a "LINK" command to initiate communication with the microcontroller.
        """
        
        self.logger.info("Opening serial connection")
        
        if self.ser.is_open:
            self.ser.close()
            time.sleep(1)
        self.ser.open()
        if self.serial_flag.is_set():
            self.serial_flag.clear()
        if not self.serial_thread.is_alive():
            self.logger.info("--> Starting serial thread")
            self.serial_thread = self._make_thread(self.read_serial, "read_serial")
            self.serial_thread.start()
        if not self.queue_thread.is_alive():
            self.logger.info("--> Starting queue thread")
            self.queue_thread = self._make_thread(self.handle_queue, "handle_queue")
            self.queue_thread.start()
        # Fix: PY-007 — Start time-check thread on connection (deferred from __init__)
        if not self.time_check_thread.is_alive():
            self.logger.info("--> Starting time check thread")
            self.time_check_thread = self._make_thread(
                self.monitor_time_limit, "monitor_time_limit"
            )
            self.time_check_thread.start()
        time.sleep(2)
        self.ser.reset_input_buffer()
        
        self.logger.info("--> Serial connection opened")

    def clear_queue(self) -> None:
        """Clear the data queue and wait for processing to complete.

        **Description:**
        - Empties the queue by adding a sentinel value (None).
        - Waits for all queued items to be processed before proceeding.
        - Ensures no residual data remains in the queue.
        """
        
        self.logger.info("Clearing queue")
        
        self.logger.info("---> Sending sentinel...")
        self.queue.put_nowait(None)
        self.logger.info("---> Waiting for queue to be processed")
        while not self.queue.empty():
            self.queue.get_nowait()
            self.queue.task_done()
        self.logger.info("---> Waiting for queue thread to terminate...")
        self.queue.join()
        
        self.logger.info("Queue terminated")

    def close_serial(self) -> None:
        """Close the serial connection and terminate related threads.

        **Description:**
        - Sends an "UNLINK" command to the microcontroller.
        - Closes the serial port and stops the serial thread.
        - Handles any errors during closure with detailed logging.
        """
        
        self.logger.info("Closing serial connection")
        
        try:
            self.serial_flag.set()
            self.logger.info("---> Serial flag set to terminate threads")
            if self.ser.is_open:
                time.sleep(0.5)
                self.ser.flush()
                self.ser.close()
                self.logger.info("---> Serial port closed")
            self.logger.info("---> Waiting for serial thread to terminate...")
            self.serial_thread.join(timeout=5)
            self.logger.info("---> Serial thread terminated")
        except Exception as e:
            self.logger.error(f"Error during closure: {e}")
        finally:
            self._close_event_log()
            self._close_controller_log()  # Fix 4.9
            self.logger.info("--> Cleanup complete")

    def _resilient(self, target, name: str):
        """Fix 7.1: wrap a thread body so an unhandled exception does not
        silently kill the daemon.

        Catches exceptions from ``target``, logs a traceback, emits a
        ``warning`` WS event with ``reason="thread_crash"``, and retries
        after a one-second back-off. Gives up after ten consecutive
        failures so a fundamentally broken target does not spin forever.
        A clean return from ``target`` (the normal stop path — serial_flag
        or time_check_flag is set) exits the shim immediately.
        """
        _MAX_RESTARTS = 10

        def wrapped():
            restarts = 0
            while True:
                try:
                    target()
                    return  # target exited cleanly via its own stop flag
                except Exception:
                    restarts += 1
                    self.logger.exception(
                        "Thread %s crashed (restart %d/%d)",
                        name, restarts, _MAX_RESTARTS,
                    )
                    try:
                        self._emit("warning", {
                            "reason": "thread_crash",
                            "thread": name,
                        })
                    except Exception:
                        # Protect against a failing event_callback — bug 7.4
                        # handles the counting; do not recurse here.
                        pass
                    if restarts >= _MAX_RESTARTS:
                        self.logger.error(
                            "Thread %s exceeded restart budget; giving up", name,
                        )
                        return
                    time.sleep(1.0)

        wrapped.__name__ = f"resilient_{name}"
        return wrapped

    def _make_thread(self, target, name: str) -> threading.Thread:
        """Fix 7.1: build a resilient daemon thread using ``_resilient``."""
        return threading.Thread(
            target=self._resilient(target, name),
            daemon=True,
            name=name,
        )

    def read_serial(self) -> None:
        """Read data from the serial port and queue it for processing.

        **Description:**
        - Runs in a thread to continuously monitor the serial port.
        - Reads incoming data when available and adds it to the queue.
        - Uses a lock to ensure thread-safe operations.
        """
        while not self.serial_flag.is_set():
            try:
                if self.ser.is_open and self.ser.in_waiting > 0:
                    # Fix: SER-004 — Read outside lock, only queue inside lock
                    data = self.ser.readline()
                    # Fix: SER-001 — Strict UTF-8 decode; discard corrupt lines
                    try:
                        decoded = data.decode(encoding='utf-8', errors='strict').strip()
                    except UnicodeDecodeError:
                        self.logger.warning("Corrupt serial data (non-UTF-8), discarding: %s", data.hex())
                        continue
                    self.logger.info(f"Serial data received: {decoded}")
                    # Fix: F-003 — Discard if queue is full; prevents OOM on I/O lag
                    try:
                        self.queue.put_nowait(decoded)
                    except queue.Full:
                        # Fix 2.6: surface overflow to the frontend so a silent
                        # data-loss window is visible. Throttle to one emission
                        # per second so an overflow storm doesn't itself flood
                        # the WS queue.
                        self._queue_overflow_count += 1
                        self.logger.warning("Serial queue full — dropping line")
                        _now = time.monotonic()
                        if _now - self._last_queue_overflow_emit > 1.0:
                            self._emit("warning", {
                                "reason": "queue_overflow",
                                "count": self._queue_overflow_count,
                            })
                            self._last_queue_overflow_emit = _now
                else:
                    time.sleep(0.1)
            except (serial.SerialException, OSError) as e:
                # Fix: F-003 — Attempt serial reconnection before giving up
                self.logger.error("Serial disconnect detected: %s", e)
                self._emit("disconnect", {"reason": str(e), "reconnecting": True})

                reconnected = False
                for attempt in range(1, self._SERIAL_RECONNECT_RETRIES + 1):
                    self.logger.info(
                        "Reconnection attempt %d/%d in %ds...",
                        attempt, self._SERIAL_RECONNECT_RETRIES, self._SERIAL_RECONNECT_DELAY,
                    )
                    time.sleep(self._SERIAL_RECONNECT_DELAY)
                    try:
                        if self.ser.is_open:
                            self.ser.close()
                        self.ser.open()
                        self.ser.reset_input_buffer()
                        self.logger.info("Serial reconnected on attempt %d", attempt)
                        self._emit("reconnected", {"attempt": attempt})
                        reconnected = True
                        break
                    except (serial.SerialException, OSError) as retry_err:
                        self.logger.warning("Reconnection attempt %d failed: %s", attempt, retry_err)

                if not reconnected:
                    self.logger.error("All reconnection attempts exhausted — serial permanently lost")
                    self._emit("disconnect", {"reason": str(e), "reconnecting": False})
                    self.serial_flag.set()
                    if self.program_running:
                        try:
                            self.stop_program()
                        except Exception:
                            self.logger.warning("Failed to stop program after disconnect", exc_info=True)
                    break

    def handle_queue(self) -> None:
        """Process data from the queue.

        **Description:**
        - Runs in a thread to handle queued serial data.
        - Processes each item as configuration or event data.
        - Terminates when a sentinel value (None) is received or serial flag is set.
        """
        while True:
            try:
                line = self.queue.get(timeout=1)
                self.queue.task_done()
                if line is None:
                    self.logger.info("Sentinel received. Exiting queue thread.")
                    break
                self.logger.info(f"--> Data in queue: {line}")

                self.handle_data(line)
            except queue.Empty:
                if self.serial_flag.is_set():
                    break
                continue

    def handle_data(self, line: str) -> None:
        """Process a line of data from the queue.

        **Description:**
        - Interprets a line of serial data.
        - Attempts to parse as JSON for configuration or as comma-separated event data.
        - Delegates to specific handlers based on data format.

        **Args:**
        - `line (str)`: The raw data line to process.
        """

        try:
            self.logger.info(f"--> Processing data: {line}")

            data = json.loads(line)

            self._write_controller_log(data)

            # Fix: F-004 — Use .get() so a missing 'level' key doesn't KeyError
            level = data.get('level')
            if level is None:
                self.logger.warning("Firmware event missing 'level': %s", data)
                return
            handler = self.code_dict.get(level)
            if handler is not None:
                handler(data)
            else:
                self.logger.warning(f"Unknown event level: {level}. Data: {data}")

        except json.JSONDecodeError as e:
            self.logger.error(f"Failed to parse JSON: {e}. Raw data: {line}")
        except Exception as e:
            # Fix: F-006 — Notify frontend of processing failures so events aren't silently dropped
            self.logger.error(f"Error processing data: {e}. Raw line: {line}")
            self._emit("kernel_error", {"reason": str(e), "raw": line})

    def handle_firmware_error(self, event: dict) -> None:
        """Handle firmware error events (level 006).

        Args:
            event: Parsed JSON event dict from firmware containing 'desc' key.
        """
        # Fix: XL-002 — Log error_code when present
        error_code = event.get("error_code", "UNKNOWN")
        desc = event.get("desc", "Unknown")
        self.logger.error(f"Firmware error [{error_code}]: {desc}")
        self._emit("error", event)

    # Fix: XL-001 — Minimum firmware version check
    MIN_FIRMWARE_VERSION = "v2.0.0"

    @staticmethod
    def _parse_version(version_str: str) -> tuple:
        """Parse a version string like 'v2.0.0' into a comparable tuple."""
        clean = version_str.lstrip("vV")
        parts = []
        for p in clean.split("."):
            try:
                parts.append(int(p))
            except ValueError:
                parts.append(0)
        return tuple(parts)

    def update_firmware_information(self, event: dict) -> None:
        if event["device"] == "CONTROLLER":
            with self.thread_lock:  # Fix: F-009 — guard cross-thread dict access
                self.firmware_information.update(event)
            self.logger.info("--> Updated arduino configuration")
            # Fix: LAZ-001 — Signal firmware readiness (IDENTIFY ack received)
            # Unblock any waiting connect/post-upload flow so state transitions to "connected"
            self._firmware_ready.set()
            # Fix: XL-001 — Warn on firmware version mismatch
            fw_version = event.get("version", "")
            if fw_version:
                try:
                    if self._parse_version(fw_version) < self._parse_version(self.MIN_FIRMWARE_VERSION):
                        self.logger.warning(
                            "Firmware version %s is below minimum %s",
                            fw_version, self.MIN_FIRMWARE_VERSION,
                        )
                        self._emit("error", {
                            "level": "006",
                            "error_code": "E_FW_VERSION",
                            "desc": f"Firmware {fw_version} is below minimum {self.MIN_FIRMWARE_VERSION}",
                            "device": "CONTROLLER",
                        })
                except Exception:
                    self.logger.debug("Could not parse firmware version %r", fw_version)
            # Fix: SER-002 — Verify baud rate matches after firmware identification
            fw_baud = event.get("baud_rate")
            if fw_baud is not None and self.ser.is_open:
                if int(fw_baud) != self.ser.baudrate:
                    self.logger.warning(
                        "Baud rate mismatch: firmware reports %s, serial configured %s",
                        fw_baud, self.ser.baudrate,
                    )
                    self._emit("error", {
                        "level": "006",
                        "error_code": "E_BAUD_MISMATCH",
                        "desc": f"Baud mismatch: firmware={fw_baud}, host={self.ser.baudrate}",
                        "device": "CONTROLLER",
                    })
            self._emit("config", event)
        else:
            device = event.get("device")
            with self.thread_lock:  # Fix: F-009 — guard cross-thread list access
                for i, entry in enumerate(self.hardware_settings):
                    if entry.get("device") == device:
                        self.hardware_settings[i] = event
                        break
                else:
                    self.hardware_settings.append(event)
            self.logger.info("--> Updated hardware defaults list")
            self._emit("config", event)

    def _update_hardware_setting(self, device: str, updates: dict) -> None:
        """Update a device entry in hardware_settings in-place and emit a config event."""
        with self.thread_lock:  # Fix: F-009 — guard cross-thread list access
            for entry in self.hardware_settings:
                if entry.get("device") == device:
                    entry.update(updates)
                    emit_data = dict(entry)
                    break
            else:
                new_entry = {"device": device, **updates}
                self.hardware_settings.append(new_entry)
                emit_data = dict(new_entry)
        self._emit("config", emit_data)

    def update_behavioral_events(self, event: dict) -> None:
        entry_dict: Dict[str, Union[str, int]] = {}
        
        match event.get('device'):
            case "LEVER_RH" | "LEVER_LH":
                entry_dict['device'] = event.get('device')
                entry_dict['event'] = f"{event.get('class')}_{event.get('event')}"
                entry_dict['start_timestamp'] = event.get('start_timestamp')
                entry_dict['end_timestamp'] = event.get('end_timestamp')
            case "SWITCH_LEVER":
                # Legacy firmware (pre-v2.4.x): orientation field holds "RH" or "LH"
                entry_dict['device'] = "LEVER_" + event.get('orientation', '')
                entry_dict['event'] = f"{event.get('class')}_{event.get('event')}"
                entry_dict['start_timestamp'] = event.get('start_timestamp')
                entry_dict['end_timestamp'] = event.get('end_timestamp')
            case "LICK_CIRCUIT":
                entry_dict['device'] = "LICK"
                entry_dict['event'] = event.get('event')
                entry_dict['start_timestamp'] = event.get('start_timestamp')
                entry_dict['end_timestamp'] = event.get('end_timestamp')
            case "CONTROLLER":
                entry_dict['device'] = event.get('device')
                entry_dict['event'] = event.get('event')
                entry_dict['start_timestamp'] = event.get('timestamp')
                entry_dict['end_timestamp'] = event.get('timestamp')
                if event.get('event') == 'END':
                    self._controller_end_received.set()
            case "PAVLOV":
                entry_dict['device'] = event.get('device')
                entry_dict['event'] = event.get('event')
                entry_dict['start_timestamp'] = event.get('start_timestamp') or event.get('timestamp')
                entry_dict['end_timestamp'] = event.get('end_timestamp') or event.get('timestamp')
                if (trial_type := event.get('trial_type')) is not None:
                    entry_dict['trial_type'] = trial_type
            case _:
                entry_dict['device'] = event.get('device')
                entry_dict['event'] = event.get('event')
                entry_dict['start_timestamp'] = event.get('start_timestamp')
                entry_dict['end_timestamp'] = event.get('end_timestamp')  
                
        # Append-only event log (written before program_running guard)
        self._write_event_log({"type": "behavior", **entry_dict})

        # Always emit for real-time UI feedback (e.g. hardware testing)
        self._emit("event", entry_dict)

        # Persist to dataset when actively recording, and always for CONTROLLER
        # events (START/END markers must survive the program_running=False guard
        # that stop_program() sets before the END event arrives from firmware).
        if (self.program_running and not self.program_flag.is_set()) \
                or entry_dict.get('device') == 'CONTROLLER':
            with self.thread_lock:
                self.behavior_data.append(entry_dict)
                if entry_dict.get('device') in ('PUMP', 'PUMP_1') and entry_dict.get('event') == 'INFUSION':
                    self._infusion_count += 1
                # Fix: F-002 — Warn when data lists grow dangerously large
                total = len(self.behavior_data) + len(self.frame_data) + len(self.slm_data)
            if total >= self._DATA_WARNING_THRESHOLD and not self._data_warning_emitted:
                self._data_warning_emitted = True
                self.logger.warning(
                    "Data list size (%d) exceeds threshold (%d) — risk of OOM. "
                    "event_log.jsonl is the durable backup.",
                    total, self._DATA_WARNING_THRESHOLD,
                )
                self._emit("warning", {
                    "message": "In-memory data lists are large. Data is safely logged to disk.",
                    "total_entries": total,
                    "threshold": self._DATA_WARNING_THRESHOLD,
                })
            self.logger.info("--> Updated behavioral data")

        # Auto-stop when firmware signals all Pavlovian trials are complete
        if (entry_dict.get('device') == 'PAVLOV'
                and entry_dict.get('event') == 'ALL_TRIALS_COMPLETE'
                and self.program_running):
            self.logger.info("All Pavlovian trials complete, stopping program")
            threading.Thread(target=self.stop_program, daemon=True).start()
                
    def update_frame_events(self, event: dict) -> None:
        if self.program_flag.is_set():
            return
        ts = int(event.get('timestamp'))
        self._write_event_log({"type": "frame", "timestamp": ts})
        with self.thread_lock:
            self.frame_data.append(ts)
        self.logger.info("--> Updated frame data")
        self._emit("frame", {"timestamp": ts})

    def update_slm_events(self, event: dict) -> None:
        if self.program_flag.is_set():
            return
        ts = int(event.get('timestamp'))
        self._write_event_log({"type": "slm", "timestamp": ts})
        with self.thread_lock:
            self.slm_data.append(ts)
        self.logger.info("--> Updated SLM timestamp data")
        self._emit("event", {
            "device": "SLM",
            "event": "TIMESTAMP",
            "start_timestamp": ts,
            "end_timestamp": ts,
        })

    def send_serial_command(self, command: dict) -> None:
        """Send a command to the Arduino via serial.

        **Description:**
        - Transmits a string command to the connected microcontroller.
        - Ensures the serial port is open before sending.

        **Args:**
        - `command (str)`: The command to send (e.g., "START-PROGRAM").

        **Raises:**
        - `Exception`: If the serial port is not open.
        """
        with self.thread_lock:
            if not self.ser.is_open:
                raise Exception("Serial port is not open.")
            send = json.dumps(command).encode() + b'\n'
            self.logger.info(f"Sending command '{send}' to Arduino.")
            self.ser.write(send)
            self.ser.flush()
            time.sleep(0.05)

    def send_command(self, code: int, value=None) -> None:
        """Send any command from the command registry over serial.

        Uses ``build_command_payload`` to construct the proper JSON payload
        (e.g. ``{"cmd": 371, "frequency": 8000}``) and transmits it.

        Args:
            code: Command code from CommandCode / COMMAND_REGISTRY.
            value: Optional payload value (int or bool depending on command spec).
        """
        payload = build_command_payload(code, value)
        self.send_serial_command(payload)
        if code in _COMMAND_STATE_MAP:
            device, field, mapped = _COMMAND_STATE_MAP[code]
            effective = value if mapped is _USE_VALUE else mapped
            self._update_hardware_setting(device, {field: effective})

    def _emit(self, event_type: str, data: dict) -> None:
        """Broadcast an event via the registered callback (if any).

        Fix 7.4: increment a failure counter rather than re-emitting a warning
        through the same callback (which would recurse if the callback is the
        broken channel). The counter is surfaced via /api/sessions/{sid}.
        """
        if self.event_callback and self.session_id:
            try:
                self.event_callback(self.session_id, event_type, data)
            except Exception:
                self._emit_failure_count += 1
                self.logger.warning(
                    "Event callback failed (total=%d)",
                    self._emit_failure_count, exc_info=True,
                )

    @property
    def emit_failure_count(self) -> int:
        """Cumulative count of event_callback invocations that raised."""
        return self._emit_failure_count

    def _write_event_log(self, entry: dict) -> None:
        """Append a JSON line to the append-only event log.

        Fix: F-010 — Keeps the file handle open for the session lifetime and
        batches fsync calls (every _EVENT_LOG_FSYNC_INTERVAL writes) to reduce
        I/O overhead at high event rates while maintaining durability.
        """
        try:
            if self._event_log_file is None or self._event_log_file.closed:
                self._event_log_file = open(self._event_log_path, "a")
            self._event_log_file.write(json.dumps(entry) + "\n")
            self._event_log_file.flush()
            self._event_log_write_count += 1
            if self._event_log_write_count >= self._EVENT_LOG_FSYNC_INTERVAL:
                os.fsync(self._event_log_file.fileno())
                self._event_log_write_count = 0
        except Exception:
            self.logger.warning("Failed to write event log entry", exc_info=True)
            self._log_failure_counts["event_log"] += 1
            _now = time.monotonic()
            if _now - self._last_log_failure_emit.get("event_log", float("-inf")) > 1.0:
                self._last_log_failure_emit["event_log"] = _now
                self._emit("warning", {
                    "reason": "log_write_failure",
                    "log_type": "event_log",
                    "failures_since_last_emit": self._log_failure_counts["event_log"],
                })
                self._log_failure_counts["event_log"] = 0

    def _close_event_log(self) -> None:
        """Flush and close the persistent event log file handle."""
        if self._event_log_file is not None and not self._event_log_file.closed:
            try:
                self._event_log_file.flush()
                os.fsync(self._event_log_file.fileno())
                self._event_log_file.close()
            except Exception:
                self.logger.warning("Failed to close event log", exc_info=True)
                self._emit("warning", {"reason": "log_close_failure", "log_type": "event_log"})

    def _write_controller_log(self, data: dict) -> None:
        """Append a JSON line to controller_log.json (Fix 4.9).

        Mirrors _write_event_log: persistent handle, flush every write, fsync
        every _CONTROLLER_LOG_FSYNC_INTERVAL writes. Avoids the syscall storm
        of open/close/fsync per firmware line at high event rates.
        """
        try:
            if self._controller_log_file is None or self._controller_log_file.closed:
                self._controller_log_file = open(self.controller_log, "a", newline="")
            self._controller_log_file.write(json.dumps(data) + "\n")
            self._controller_log_file.flush()
            self._controller_log_write_count += 1
            if self._controller_log_write_count >= self._CONTROLLER_LOG_FSYNC_INTERVAL:
                os.fsync(self._controller_log_file.fileno())
                self._controller_log_write_count = 0
        except Exception:
            self.logger.warning("Failed to write controller log", exc_info=True)
            self._log_failure_counts["controller_log"] += 1
            _now = time.monotonic()
            if _now - self._last_log_failure_emit.get("controller_log", float("-inf")) > 1.0:
                self._last_log_failure_emit["controller_log"] = _now
                self._emit("warning", {
                    "reason": "log_write_failure",
                    "log_type": "controller_log",
                    "failures_since_last_emit": self._log_failure_counts["controller_log"],
                })
                self._log_failure_counts["controller_log"] = 0

    def _close_controller_log(self) -> None:
        """Flush and close the persistent controller log file handle."""
        if self._controller_log_file is not None and not self._controller_log_file.closed:
            try:
                self._controller_log_file.flush()
                os.fsync(self._controller_log_file.fileno())
                self._controller_log_file.close()
            except Exception:
                self.logger.warning("Failed to close controller log", exc_info=True)
                self._emit("warning", {"reason": "log_close_failure", "log_type": "controller_log"})

    def _export_segment(self, behavior: list, suffix: str = "") -> str:
        """Write behavior_events{suffix}.csv to the session log directory.

        Uses the full frame_data list for frame-index binary search so that
        microscope frame indices remain continuous across splits.

        Args:
            behavior: Snapshot of behavior_data rows to export.
            suffix: Optional filename suffix (e.g. "_001").
        Returns:
            Path to the written CSV file.
        """
        frame_data = self.get_frame_data()
        frame_timestamps = sorted(int(ts) for ts in frame_data if ts)

        csv_buf = io.StringIO()
        fieldnames = ["device", "event", "start_timestamp", "end_timestamp", "start_frame_index", "end_frame_index"]
        writer = csv.DictWriter(csv_buf, fieldnames=fieldnames)
        writer.writeheader()
        for row in behavior:
            start_ts = row.get("start_timestamp")
            end_ts = row.get("end_timestamp")
            start_fi = self._find_frame_index(frame_timestamps, int(start_ts)) if start_ts not in (None, "") else None
            end_fi = self._find_frame_index(frame_timestamps, int(end_ts)) if end_ts not in (None, "") else None
            out = {k: row.get(k, "") for k in ("device", "event", "start_timestamp", "end_timestamp")}
            out["start_frame_index"] = start_fi if start_fi is not None else ""
            out["end_frame_index"] = end_fi if end_fi is not None else ""
            writer.writerow(out)
        path = os.path.join(self.reacher_log_path, f"behavior_events{suffix}.csv")
        with open(path, "w", newline="") as f:
            f.write(csv_buf.getvalue())
            f.flush()
            os.fsync(f.fileno())
        return path

    def _auto_export(self) -> None:
        """Write behavior_events.csv and frame_timestamps.csv to the session log directory.

        Called automatically on stop_program(). Failure never blocks shutdown.
        If segments were split during the session, the remaining data is exported
        with the next segment number suffix.
        """
        try:
            behavior = self.get_behavior_data()
            suffix = f"_{self._segment_number + 1:03d}" if self._segment_number > 0 else ""
            self._export_segment(behavior, suffix)

            # frame_timestamps.csv — only when microscope data was captured
            frame_data = self.get_frame_data()
            frame_timestamps = sorted(int(ts) for ts in frame_data if ts)
            if frame_timestamps:
                ft_buf = io.StringIO()
                ft_writer = csv.DictWriter(ft_buf, fieldnames=["frame_index", "timestamp_ms"])
                ft_writer.writeheader()
                for i, ts in enumerate(frame_timestamps):
                    ft_writer.writerow({"frame_index": i, "timestamp_ms": ts})
                path = os.path.join(self.reacher_log_path, "frame_timestamps.csv")
                with open(path, "w", newline="") as f:
                    f.write(ft_buf.getvalue())
                    f.flush()
                    os.fsync(f.fileno())

            # slm_timestamps.csv — only when SLM data was captured
            slm_data = self.get_slm_data()
            slm_timestamps = sorted(int(ts) for ts in slm_data if ts)
            if slm_timestamps:
                st_buf = io.StringIO()
                st_writer = csv.DictWriter(st_buf, fieldnames=["event_index", "timestamp_ms"])
                st_writer.writeheader()
                for i, ts in enumerate(slm_timestamps):
                    st_writer.writerow({"event_index": i, "timestamp_ms": ts})
                path = os.path.join(self.reacher_log_path, "slm_timestamps.csv")
                with open(path, "w", newline="") as f:
                    f.write(st_buf.getvalue())
                    f.flush()
                    os.fsync(f.fileno())

            export_parts = [f"behavior_events{suffix}.csv"]
            if frame_timestamps:
                export_parts.append("frame_timestamps.csv")
            if slm_timestamps:
                export_parts.append("slm_timestamps.csv")
            self.logger.info("Auto-export complete: %s", " + ".join(export_parts))
        except Exception as e:
            # Fix: F-005 — Surface export failures to the frontend in real time
            self.logger.warning("Auto-export failed", exc_info=True)
            self._emit("export_failed", {"reason": str(e)})

    @staticmethod
    def _find_frame_index(frame_timestamps: list, event_ts: int):
        """Return the index of the last frame at or before *event_ts*, or None."""
        if not frame_timestamps:
            return None
        idx = bisect.bisect_right(frame_timestamps, event_ts) - 1
        if idx < 0:
            return None
        return idx

    def _join_queue_with_timeout(self, timeout: float = 5.0) -> None:
        """Wait for all queued items to finish, with a timeout to avoid hanging."""
        with self.queue.all_tasks_done:
            while self.queue.unfinished_tasks:
                if not self.queue.all_tasks_done.wait(timeout=timeout):
                    self.logger.warning("Queue drain timed out after %.1fs", timeout)
                    break

    def get_detected_paradigm(self) -> Optional[str]:
        """Return the paradigm detected from firmware identification, or None."""
        schedule = self.firmware_information.get("schedule")
        if schedule and isinstance(schedule, str):
            return SCHEDULE_TO_PARADIGM.get(schedule)
        return None

    def set_limit_type(self, limit_type: str) -> None:
        """Set the type of limit for program execution.

        **Description:**
        - Defines the stopping condition for the experiment.
        - Valid options: "Time", "Infusion", or "Both".

        **Args:**
        - `limit_type (str)`: The type of limit to enforce.
        """
        if limit_type in ['Time', 'Infusion', 'Both', 'Trials']:
            self.limit_type = limit_type
            self.logger.info(f"Limit type set to: {limit_type}")
        else:
            self.logger.warning(f"Invalid limit type: {limit_type}")

    def set_infusion_limit(self, limit: int) -> None:
        """Set the maximum number of infusions allowed.

        **Description:**
        - Specifies the maximum number of infusions before stopping.

        **Args:**
        - `limit (int)`: The infusion limit.
        """
        self.infusion_limit = limit

    def set_time_limit(self, limit: int) -> None:
        """Set the maximum time allowed in seconds.

        **Description:**
        - Specifies the maximum duration of the experiment in seconds.

        **Args:**
        - `limit (int)`: The time limit in seconds.
        """
        self.time_limit = limit

    def set_stop_delay(self, delay: int) -> None:
        """Set the delay after last infusion before stopping.

        **Description:**
        - Sets a delay in seconds after the last infusion before program termination.

        **Args:**
        - `delay (int)`: The delay in seconds.
        """
        self.stop_delay = delay

    def start_program(self) -> None:
        """Start the experimental program.

        **Description:**
        - Initiates the experiment by sending "START-PROGRAM" to the microcontroller.
        - Records the start time for limit checking.
        """
        self.behavior_data = []
        self.frame_data = []
        self.slm_data = []
        self._infusion_count = 0
        self._segment_number = 0
        self._cumulative_infusion_count = 0
        self._segment_exports = []
        self._segment_event_counts = []
        self.paused_time = 0
        self.last_infusion_time = None
        if self.program_flag.is_set():
            self.program_flag.clear()
        self.program_running = True
        self.send_serial_command({"cmd": 101})
        self.program_start_time = time.time()
        self._write_event_log({"type": "SESSION_START", "timestamp": self.program_start_time})
        self.logger.info(f"Program started at {self.get_time()}")

    def stop_program(self) -> None:
        """Stop the experimental program.

        **Description:**
        - Terminates the experiment by sending "END-PROGRAM".
        - Drains remaining queued events before closing serial.
        - Cleans up resources and records the end time.
        - Invokes the ``on_stop`` callback so the session manager can
          broadcast the "stopped" state to the frontend.
        """
        with self.thread_lock:
            if not self.program_running:
                return
            self.program_running = False  # Guard against re-entrance
        self.logger.info("Ending program...")

        # Notify frontend IMMEDIATELY — before any blocking I/O
        if self._on_stop:
            try:
                self._on_stop()
            except Exception:
                self.logger.warning("on_stop callback failed", exc_info=True)

        # Then do cleanup (send firmware END, close serial, etc.)
        self._controller_end_received.clear()
        self.send_serial_command({"cmd": 100})
        # Wait for the firmware's CONTROLLER END event (~200ms after cmd 100).
        # Fallback to 2s sleep if it doesn't arrive within 8s.
        received = self._controller_end_received.wait(timeout=8.0)
        if not received:
            self.logger.warning("Timed out waiting for CONTROLLER END — export may be incomplete")
            time.sleep(2)
        self.program_flag.set()
        # Drain remaining events with timeout to avoid indefinite hang
        self._join_queue_with_timeout(5.0)
        self.close_serial()
        self.program_end_time = time.time()
        self._write_event_log({"type": "SESSION_END", "timestamp": self.program_end_time})
        self._close_event_log()  # Fix: F-010 — Ensure all events flushed before export
        self._auto_export()
        self.logger.info(f"Program ended at {self.get_time()}")

    def pause_program(self) -> None:
        """Pause the experimental program.

        **Description:**
        - Temporarily halts the experiment and records the pause start time.
        - Sends SESSION_PAUSE command to firmware to gate input processing.
        """
        self.program_flag.set()
        self.paused_start_time = time.time()
        self.send_serial_command({"cmd": 105, "paused": True})

    def resume_program(self) -> None:
        """Resume the experimental program.

        **Description:**
        - Resumes the experiment and calculates total paused time.
        - Sends SESSION_PAUSE(false) to firmware to resume processing.
        """
        # Fix: F-007 — Guard against calling resume when not paused (paused_start_time is None)
        if self.paused_start_time is None:
            self.logger.warning("resume_program() called but paused_start_time is None — ignoring")
            return
        if self.program_flag.is_set():
            self.program_flag.clear()
        self.paused_time += time.time() - self.paused_start_time
        self.paused_start_time = None
        self.send_serial_command({"cmd": 105, "paused": False})

    def get_program_running(self) -> bool:
        """Check if the program is currently running (not paused or stopped).

        **Returns:**
        - `bool`: True if running and not paused, False otherwise.
        """
        return self.program_running and not self.program_flag.is_set()

    def split_segment(self) -> dict:
        """Export the current segment and reset buffers for the next segment.

        Snapshots behavior_data, exports it as a numbered CSV, clears the
        in-memory buffer and per-segment counters, and increments the segment
        counter.  frame_data is NOT cleared — microscope frame indices remain
        continuous across splits.

        Returns:
            Dict with segment_number and export_path.
        """
        with self.thread_lock:
            snapshot = list(self.behavior_data)
            segment_infusions = self._infusion_count
            self._cumulative_infusion_count += segment_infusions
            self.behavior_data = []
            self._infusion_count = 0
            self._data_warning_emitted = False
            self._segment_number += 1

        if not snapshot:
            self.logger.warning("split_segment: no behavioral data in segment %d", self._segment_number)

        export_path = self._export_segment(snapshot, f"_{self._segment_number:03d}")
        self._segment_exports.append(export_path)
        self._segment_event_counts.append(len(snapshot))

        self._write_event_log({
            "type": "SEGMENT_SPLIT",
            "segment": self._segment_number,
            "events_exported": len(snapshot),
            "timestamp": time.time(),
        })
        self._emit("split", {
            "segment_number": self._segment_number,
            "export_path": export_path,
        })
        self.logger.info("Segment %d exported (%d events): %s", self._segment_number, len(snapshot), export_path)
        return {"segment_number": self._segment_number, "export_path": export_path}

    def restart_program(self) -> None:
        """Stop and re-start the Arduino without destroying the session.

        Sends SESSION_END then SESSION_START to the firmware, clears all
        in-memory data and counters, and resets timing.  The serial connection
        and hardware configuration are preserved.  program_running stays True
        throughout to avoid triggering stop_program's re-entrance guard.
        """
        self.logger.info("Restarting program...")
        self._controller_end_received.clear()
        self.send_serial_command({"cmd": 100})
        time.sleep(1)

        with self.thread_lock:
            self.behavior_data = []
            self.frame_data = []
            self.slm_data = []
            self._infusion_count = 0
            self._segment_number = 0
            self._cumulative_infusion_count = 0
            self._segment_exports = []
            self._segment_event_counts = []
            self._data_warning_emitted = False

        self.paused_time = 0
        self.paused_start_time = None
        self.last_infusion_time = None
        if self.program_flag.is_set():
            self.program_flag.clear()

        self.send_serial_command({"cmd": 101})
        self.program_start_time = time.time()

        self._write_event_log({"type": "SESSION_RESTART", "timestamp": self.program_start_time})
        self._emit("restart", {})
        self.logger.info("Program restarted at %s", self.get_time())

    def get_segment_number(self) -> int:
        """Return the current segment number (0 means no splits have occurred)."""
        return self._segment_number

    def get_segment_exports(self) -> List[str]:
        """Return a snapshot of split-segment CSV paths (does not include the final in-memory segment)."""
        with self.thread_lock:
            return list(self._segment_exports)

    def get_segment_event_counts(self) -> List[int]:
        """Return per-split behavior event counts, parallel to get_segment_exports()."""
        with self.thread_lock:
            return list(self._segment_event_counts)

    def get_event_log_path(self) -> str:
        """Return the absolute path to this session's event_log.jsonl."""
        return self._event_log_path

    def flush_event_log(self) -> None:
        """Force-flush the event log file handle. Safe to call when the handle is closed or None."""
        f = self._event_log_file
        if f is not None and not f.closed:
            try:
                f.flush()
                os.fsync(f.fileno())
            except Exception:
                self.logger.warning("flush_event_log failed", exc_info=True)

    def monitor_time_limit(self) -> None:
        """Continuously monitor the time limit in a separate thread.

        This method runs in a dedicated thread and checks if program limits are met,
        ensuring timely stopping even when no serial data is received.
        """
        while self.time_check_flag.is_set():
            if not self.program_flag.is_set():  # Program is running
                self.check_limit_met()
            time.sleep(0.1)  # Check every 100ms for responsiveness

    def check_limit_met(self) -> None:
        """Check if program limits have been met and stop if necessary.

        **Description:**
        - Evaluates time and/or infusion limits based on `limit_type`.
        - Automatically stops the program if limits are exceeded.
        """
        current_time = time.time()
        if self.program_start_time is None or self.limit_type is None:
            return  # No limits to check if program hasn't started or type unset

        elapsed_time = current_time - self.program_start_time - self.paused_time
        infusion_count = self._infusion_count  # Atomic counter — O(1)
        self.logger.debug(f"Checking limits: elapsed_time={elapsed_time:.2f}, time_limit={self.time_limit}, infusion_count={infusion_count}, infusion_limit={self.infusion_limit}")

        if self.limit_type == "Time":
            if elapsed_time >= self.time_limit:
                self.logger.info("Time limit met, stopping program")
                self.stop_program()
        elif self.limit_type == "Infusion":
            if infusion_count >= self.infusion_limit:
                if self.last_infusion_time is None:
                    self.last_infusion_time = current_time
                if self.last_infusion_time and (current_time - self.last_infusion_time >= self.stop_delay):
                    self.logger.info("Infusion limit met and stop delay elapsed, stopping program")
                    self.stop_program()
        elif self.limit_type == "Both":
            if infusion_count >= self.infusion_limit:
                if self.last_infusion_time is None:
                    self.last_infusion_time = current_time
            if (self.last_infusion_time and (current_time - self.last_infusion_time) >= self.stop_delay) or (elapsed_time >= self.time_limit):
                self.logger.info("Either infusion limit with stop delay or time limit met, stopping program")
                self.stop_program()
        elif self.limit_type == "Trials":
            pass  # Firmware signals ALL_TRIALS_COMPLETE; handled in update_behavioral_events

    def set_data_destination(self, folder: str) -> None:
        """Set the destination folder for data files.

        **Description:**
        - Specifies where data files (e.g., CSV logs) will be saved.

        **Args:**
        - `folder (str)`: The folder path.
        """
        self.data_destination = folder

    def set_filename(self, filename: str) -> None:
        """Set the filename for behavioral data.

        **Description:**
        - Defines the name of the behavioral data file.
        - Automatically appends ".csv" if not provided.

        **Args:**
        - `filename (str)`: The desired filename.
        """
        self.behavior_filename = filename

    def make_destination_folder(self) -> str:
        """Create a destination folder for data files.

        **Description:**
        - Creates a unique folder for storing data based on filename and timestamp.
        - Uses a default path (`~/REACHER/DATA`) if not specified.

        **Returns:**
        - `str`: The path to the created folder.
        """
        if not self.data_destination:
            self.data_destination = os.path.expanduser("~/Downloads")
        if not self.behavior_filename:
            self.behavior_filename = f"{self.get_time()}"
        containing_folder = os.path.join(self.data_destination, self.behavior_filename.split('.')[0])
        if os.path.exists(containing_folder):
            data_folder_path = os.path.join(self.data_destination, f"{self.behavior_filename.split('.')[0]}-{time.time_ns()}")
        else:
            data_folder_path = containing_folder   
        os.makedirs(data_folder_path, exist_ok=True)
        return data_folder_path         

    def set_box_name(self, box_name: str) -> None:
        """Set the name of the box for data organization.

        **Description:**
        - Specifies the name of the box for data storage and organization.

        **Args:**
        - `box_name (str)`: The name of the box.
        """
        self.box_name = box_name

    def get_data_destination(self) -> Optional[str]:
        """Get the current data destination folder.

        **Description:**
        - Retrieves the folder where data files are stored.

        **Returns:**
        - `Optional[str]`: The folder path or None if not set.
        """
        return self.data_destination
    
    def get_filename(self) -> Optional[str]:
        """Get the current behavioral data filename.

        **Description:**
        - Retrieves the filename used for behavioral data logging.

        **Returns:**
        - `Optional[str]`: The filename or None if not set.
        """
        return self.behavior_filename

    def get_behavior_data(self) -> List[Dict[str, Union[str, int]]]:
        """Get a snapshot of the collected behavioral data.

        **Returns:**
        - `List[Dict[str, Union[str, int]]]`: List of event dictionaries.
        """
        with self.thread_lock:
            return list(self.behavior_data)

    def get_frame_data(self) -> List[int]:
        """Get a snapshot of the collected frame data.

        **Returns:**
        - `List[int]`: List of frame timestamps in milliseconds.
        """
        with self.thread_lock:
            return list(self.frame_data)

    def get_frame_timestamps_count(self) -> int:
        with self.thread_lock:
            return len(self.frame_data)

    def get_slm_data(self) -> List[int]:
        """Get a snapshot of the collected SLM timestamp data.

        **Returns:**
        - `List[int]`: List of SLM event timestamps in milliseconds.
        """
        with self.thread_lock:
            return list(self.slm_data)
    
    def get_firmware_information(self) -> Dict:
        """Get the current Arduino configuration.

        **Description:**
        - Retrieves the configuration data received from the microcontroller.
        - Thread-safe access to the configuration dictionary.

        **Returns:**
        - `Dict`: The configuration dictionary.
        """
        with self.thread_lock:  # Fix: F-009 — snapshot under lock
            return dict(self.firmware_information)

    def get_hardware_settings(self) -> List:
        with self.thread_lock:  # Fix: F-009 — snapshot under lock
            return list(self.hardware_settings)
    
    def get_box_name(self) -> Optional[str]:
        """Get the name of the box.

        **Description:**
        - Retrieves the name of the box for data organization.

        **Returns:**
        - `Optional[str]`: The box name or None if not set.
        """
        return self.box_name
    
    def get_start_time(self) -> Optional[float]:
        """Get the program start time.

        **Description:**
        - Returns the timestamp when the experiment began.

        **Returns:**
        - `Optional[float]`: Start time in seconds since epoch, or None if not started.
        """
        return self.program_start_time
    
    def get_end_time(self) -> Optional[float]:
        """Get the program end time.

        **Description:**
        - Returns the timestamp when the experiment ended.

        **Returns:**
        - `Optional[float]`: End time in seconds since epoch, or None if not ended.
        """
        return self.program_end_time
    
    def get_time(self) -> str:
        """Get the current time as a formatted string.

        **Description:**
        - Provides the current local time in a readable format.

        **Returns:**
        - `str`: Time in "YYYY-MM-DD_HH-MM-SS" format.
        """
        local_time = time.localtime()
        formatted_time = time.strftime("%Y-%m-%d_%H-%M-%S", local_time)
        return formatted_time

    @staticmethod
    def prune_logs(days: int = 30) -> int:
        """Remove log directories older than *days* from ~/REACHER/LOG.

        Fix: PY-006 — CLI maintenance command support.

        Returns:
            Number of directories removed.
        """
        import shutil
        log_root = os.path.expanduser("~/REACHER/LOG")
        if not os.path.isdir(log_root):
            return 0
        cutoff = time.time() - (days * 86400)
        removed = 0
        for entry in os.listdir(log_root):
            path = os.path.join(log_root, entry)
            if os.path.isdir(path) and os.stat(path).st_mtime < cutoff:
                shutil.rmtree(path, ignore_errors=True)
                removed += 1
        return removed

    """
    **Contact:**
    - For inquiries or support, please email: [thejoshbq@proton.me](mailto:thejoshbq@proton.me).
    """