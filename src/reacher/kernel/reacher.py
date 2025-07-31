import serial
import queue
import threading
import time
import csv
import json
import os
import logging
from typing import List, Dict, Union, Optional
from serial.tools import list_ports

class REACHER:
    """A class to manage serial communication and data collection for REACHER experiments."""

    def __init__(self) -> None:
        """Initialize a REACHER instance with default settings.

        **Description:**
        - Sets up a new REACHER instance with default serial communication settings (baudrate: 115200).
        - Initializes threads for serial reading and queue handling.
        - Prepares data structures for behavioral and frame data logging.
        - Configures program control flags and variables for experiment management.
        """
                
        # Serial variables
        self.ser: serial.Serial = serial.Serial(baudrate=115200)
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

        # Data process variables
        self.behavior_data: List[Dict[str, Union[str, int]]] = []
        self.frame_data: List[str] = []

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
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s [%(levelname)s]: %(message)s',
            handlers=[
                    logging.FileHandler(self.interface_log),
                    logging.StreamHandler()
                ]
        )
        self.logger = logging.getLogger(__name__)
        self.data_destination: Optional[str] = None
        self.behavior_filename: Optional[str] = None
        self.code_dict: Dict = {
            "000": self.update_firmware_information,
            "001": self.logger.info,
            "006": self.logger.error,
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

        self.ser = serial.Serial(baudrate=115200)
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
        
        self.logger.info("COM ports successfully accessed")
        
        return ["No available ports"] if len(available_ports) == 0 else available_ports
    
    def set_COM_port(self, port: str) -> None:
        """Set the COM port for serial communication.

        **Description:**
        - Configures the serial port to the specified name (e.g., "COM3").
        - Only applies if the port is valid and exists in the system.

        **Args:**
        - `port (str)`: The name of the COM port to use.
        """
        
        self.logger.info("Setting COM port")
        
        if port in [p.device for p in list_ports.comports() if p.vid and p.pid]:
            self.ser.port = port
            
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
            
            with self.thread_lock:
                data = json.loads(line)
                
                with open(self.controller_log, 'a', newline='') as file:
                            file.write(str(data))
                            file.write('\n')
                            file.flush()
                
                level = data['level']
                self.code_dict.get(level)(data)
                    
            return
        except json.JSONDecodeError as e:
            self.logger.error(f"Failed to parse JSON: {e}. Raw data: {data}")
        except Exception as e:
            self.logger.error(f"Error processing JSON data: {e}")

    def update_firmware_information(self, event: dict) -> None:
        if event["device"] == "CONTROLLER":
            self.firmware_information = event
            self.logger.info("--> Updated arduino configuration")
        else:
            self.hardware_settings.append(event)
            self.logger.info("--> Updated hardware defaults list")
            
        
    def update_behavioral_events(self, event: dict) -> None:
        entry_dict: Dict[str, Union[str, int]] = {}
        
        match event.get('device'):
            case "SWITCH_LEVER":
                entry_dict['device'] = event.get('orientation') + "_LEVER"
                entry_dict['event'] = f"{event.get('class')}_{event.get('event')}"
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
                
        self.behavior_data.append(entry_dict)
        self.logger.info("--> Updated behavioral data")
                
    def update_frame_events(self, event: dict) -> None:
        self.frame_data.append(event.get('timestamp'))
        self.logger.info("--> Updated frame data")

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
            self.logger.debug(f"Sending command '{send}' to Arduino.")
            self.ser.write(send)
            self.ser.flush()

    def set_limit_type(self, limit_type: str) -> None:
        """Set the type of limit for program execution.

        **Description:**
        - Defines the stopping condition for the experiment.
        - Valid options: "Time", "Infusion", or "Both".

        **Args:**
        - `limit_type (str)`: The type of limit to enforce.
        """
        if limit_type in ['Time', 'Infusion', 'Both']: 
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
        if self.program_flag.is_set():
            self.program_flag.clear()
        self.program_running = True
        self.send_serial_command({"cmd": 101})
        self.program_start_time = time.time()
        self.logger.info(f"Program started at {self.get_time()}")

    def stop_program(self) -> None:
        """Stop the experimental program.

        **Description:**
        - Terminates the experiment by sending "END-PROGRAM".
        - Cleans up resources and records the end time.
        """
        self.logger.info("Ending program...")
        self.send_serial_command({"cmd": 100})
        time.sleep(2)
        self.program_flag.set()
        self.program_running = False
        self.clear_queue()
        self.close_serial()
        self.program_end_time = time.time()
        self.logger.info(f"Program ended at {self.get_time()}")

    def pause_program(self) -> None:
        """Pause the experimental program.

        **Description:**
        - Temporarily halts the experiment and records the pause start time.
        """
        self.program_flag.set()
        self.paused_start_time = time.time()

    def resume_program(self) -> None:
        """Resume the experimental program.

        **Description:**
        - Resumes the experiment and calculates total paused time.
        """
        if self.program_flag.is_set():
            self.program_flag.clear()
        self.paused_time = time.time() - self.paused_start_time

    def get_program_running(self) -> bool:
        """Check if the program is currently running.

        **Description:**
        - Returns the current state of the experiment.

        **Returns:**
        - `bool`: True if running, False if paused or stopped.
        """
        return self.program_running

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
        infusion_count = sum(1 for entry in self.behavior_data if entry['device'] == 'PUMP' and entry['event'] == 'INFUSION')
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
            data_folder_path = os.path.join(self.data_destination, f"{self.behavior_filename.split('.')[0]}-{time.time():.4f}")
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
        """Get the collected behavioral data.

        **Description:**
        - Returns all recorded behavioral events (e.g., lever presses).

        **Returns:**
        - `List[Dict[str, Union[str, int]]]`: List of event dictionaries.
        """
        return self.behavior_data
    
    def get_frame_data(self) -> List[str]:
        """Get the collected frame data.

        **Description:**
        - Returns all recorded frame timestamps.

        **Returns:**
        - `List[str]`: List of frame timestamps.
        """
        return self.frame_data
    
    def get_frame_timestamps_count(self) -> int:
        return len(self.frame_data) if self.frame_data else 0
    
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