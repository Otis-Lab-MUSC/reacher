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
        self.queue: queue.Queue = queue.Queue()

        # Thread variables
        self.serial_thread: threading.Thread = threading.Thread(target=self.read_serial, daemon=True)
        self.queue_thread: threading.Thread = threading.Thread(target=self.handle_queue, daemon=True)
        self.time_check_thread: threading.Thread = threading.Thread(target=self.monitor_time_limit, daemon=True)
        self.thread_lock: threading.Lock = threading.Lock()
        self.serial_flag: threading.Event = threading.Event()
        self.program_flag: threading.Event = threading.Event()
        self.program_running: bool = False
        self.time_check_flag: threading.Event = threading.Event()
        self.serial_flag.set()
        self.program_flag.set()
        self.time_check_flag.set()
        self.serial_thread.start()
        self.queue_thread.start()
        self.time_check_thread.start()

        # Data process variables (guarded by thread_lock for cross-thread access)
        self.behavior_data: List[Dict[str, Union[str, int]]] = []
        self.frame_data: List[int] = []
        self._infusion_count: int = 0  # Atomic counter — avoids O(n) rescan in check_limit_met

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
        self.data_destination: Optional[str] = None
        self.behavior_filename: Optional[str] = None
        self.code_dict: Dict = {
            "000": self.update_firmware_information,
            "001": self.logger.info,
            "006": self.handle_firmware_error,
            "007": self.update_behavioral_events,
            "008": self.update_frame_events
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
        self._infusion_count = 0

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
        self.queue = queue.Queue()

        self.serial_flag.clear()
        self.program_flag.set()
        self.time_check_flag.set()
        self.serial_thread = threading.Thread(target=self.read_serial, daemon=True)
        self.queue_thread = threading.Thread(target=self.handle_queue, daemon=True)
        self.time_check_thread = threading.Thread(target=self.monitor_time_limit, daemon=True)
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
        - Only applies if the port is valid and exists in the system.

        **Args:**
        - `port (str)`: The name of the COM port to use.
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
            self.serial_thread = threading.Thread(target=self.read_serial, daemon=True)
            self.serial_thread.start()
        if not self.queue_thread.is_alive():
            self.logger.info("--> Starting queue thread")
            self.queue_thread = threading.Thread(target=self.handle_queue, daemon=True)
            self.queue_thread.start()
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
            self.logger.info("--> Cleanup complete")

    def read_serial(self) -> None:
        """Read data from the serial port and queue it for processing.

        **Description:**
        - Runs in a thread to continuously monitor the serial port.
        - Reads incoming data when available and adds it to the queue.
        - Uses a lock to ensure thread-safe operations.
        """
        while not self.serial_flag.is_set():
            if self.ser.is_open and self.ser.in_waiting > 0:
                with self.thread_lock:
                    data = self.ser.readline().decode(encoding='utf-8', errors='replace').strip()
                    self.logger.info(f"Serial data received: {data}")
                    self.queue.put(data)
            else:
                time.sleep(0.1)

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

            with open(self.controller_log, 'a', newline='') as file:
                file.write(json.dumps(data))
                file.write('\n')
                file.flush()
                os.fsync(file.fileno())

            level = data['level']
            handler = self.code_dict.get(level)
            if handler is not None:
                handler(data)
            else:
                self.logger.warning(f"Unknown event level: {level}. Data: {data}")

        except json.JSONDecodeError as e:
            self.logger.error(f"Failed to parse JSON: {e}. Raw data: {line}")
        except Exception as e:
            self.logger.error(f"Error processing data: {e}. Raw line: {line}")

    def handle_firmware_error(self, event: dict) -> None:
        """Handle firmware error events (level 006).

        Args:
            event: Parsed JSON event dict from firmware containing 'desc' key.
        """
        desc = event.get("desc", "Unknown")
        self.logger.error(f"Firmware error: {desc}")
        self._emit("error", event)

    def update_firmware_information(self, event: dict) -> None:
        if event["device"] == "CONTROLLER":
            self.firmware_information = event
            self.logger.info("--> Updated arduino configuration")
            self._emit("config", event)
        else:
            self.hardware_settings.append(event)
            self.logger.info("--> Updated hardware defaults list")
            self._emit("config", event)
            
        
    def update_behavioral_events(self, event: dict) -> None:
        entry_dict: Dict[str, Union[str, int]] = {}
        
        match event.get('device'):
            case "SWITCH_LEVER":
                entry_dict['device'] = event.get('orientation') + "_LEVER"
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
            case _:
                entry_dict['device'] = event.get('device')
                entry_dict['event'] = event.get('event')
                entry_dict['start_timestamp'] = event.get('start_timestamp')
                entry_dict['end_timestamp'] = event.get('end_timestamp')  
                
        # Append-only event log (written before program_running guard)
        self._write_event_log({"type": "behavior", **entry_dict})

        # Always emit for real-time UI feedback (e.g. hardware testing)
        self._emit("event", entry_dict)

        # Only persist to dataset when actively recording
        if self.program_running and not self.program_flag.is_set():
            with self.thread_lock:
                self.behavior_data.append(entry_dict)
                if entry_dict.get('device') == 'PUMP' and entry_dict.get('event') == 'INFUSION':
                    self._infusion_count += 1
            self.logger.info("--> Updated behavioral data")

        # Auto-stop when firmware signals all Pavlovian trials are complete
        if (entry_dict.get('device') == 'PAVLOV'
                and entry_dict.get('event') == 'ALL_TRIALS_COMPLETE'
                and self.program_running):
            self.logger.info("All Pavlovian trials complete, stopping program")
            threading.Thread(target=self.stop_program, daemon=True).start()
                
    def update_frame_events(self, event: dict) -> None:
        ts = int(event.get('timestamp'))
        self._write_event_log({"type": "frame", "timestamp": ts})
        with self.thread_lock:
            self.frame_data.append(ts)
        self.logger.info("--> Updated frame data")
        self._emit("frame", {"timestamp": ts})

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

    def _emit(self, event_type: str, data: dict) -> None:
        """Broadcast an event via the registered callback (if any)."""
        if self.event_callback and self.session_id:
            try:
                self.event_callback(self.session_id, event_type, data)
            except Exception:
                self.logger.warning("Event callback failed", exc_info=True)

    def _write_event_log(self, entry: dict) -> None:
        """Append a JSON line to the append-only event log, fsynced to disk."""
        try:
            path = os.path.join(self.reacher_log_path, "event_log.jsonl")
            with open(path, "a") as f:
                f.write(json.dumps(entry) + "\n")
                f.flush()
                os.fsync(f.fileno())
        except Exception:
            self.logger.warning("Failed to write event log entry", exc_info=True)

    def _auto_export(self) -> None:
        """Write behavior_events.csv and frame_timestamps.csv to the session log directory.

        Called automatically on stop_program(). Failure never blocks shutdown.
        """
        try:
            behavior = self.get_behavior_data()
            frame_data = self.get_frame_data()
            frame_timestamps = sorted(int(ts) for ts in frame_data if ts)

            # behavior_events.csv
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
            path = os.path.join(self.reacher_log_path, "behavior_events.csv")
            with open(path, "w", newline="") as f:
                f.write(csv_buf.getvalue())
                f.flush()
                os.fsync(f.fileno())

            # frame_timestamps.csv
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

            self.logger.info("Auto-export complete: behavior_events.csv + frame_timestamps.csv")
        except Exception:
            self.logger.warning("Auto-export failed", exc_info=True)

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
        self._infusion_count = 0
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
        self.send_serial_command({"cmd": 100})
        time.sleep(2)
        self.program_flag.set()
        # Drain remaining events with timeout to avoid indefinite hang
        self._join_queue_with_timeout(5.0)
        self.close_serial()
        self.program_end_time = time.time()
        self._write_event_log({"type": "SESSION_END", "timestamp": self.program_end_time})
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
        if self.program_flag.is_set():
            self.program_flag.clear()
        self.paused_time += time.time() - self.paused_start_time
        self.send_serial_command({"cmd": 105, "paused": False})

    def get_program_running(self) -> bool:
        """Check if the program is currently running (not paused or stopped).

        **Returns:**
        - `bool`: True if running and not paused, False otherwise.
        """
        return self.program_running and not self.program_flag.is_set()

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
        if not self.behavior_filename and not self.data_destination:
            self.data_destination = os.path.expanduser(r'~/REACHER/DATA')
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
    
    def get_firmware_information(self) -> Dict:
        """Get the current Arduino configuration.

        **Description:**
        - Retrieves the configuration data received from the microcontroller.
        - Thread-safe access to the configuration dictionary.

        **Returns:**
        - `Dict`: The configuration dictionary.
        """
        return self.firmware_information
    
    def get_hardware_settings(self) -> List:
        return self.hardware_settings
    
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

    """
    **Contact:**
    - For inquiries or support, please email: [thejoshbq@proton.me](mailto:thejoshbq@proton.me).
    """