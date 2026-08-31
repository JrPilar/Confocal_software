#!/usr/bin/env python
"""
stage.py - Communication with Piezoconcept C3 piezoelectric stage via COM5 port.

Commands:
  - GET_X, GET_Y, GET_Z        - read position
  - MOVEX, MOVEY, MOVEZ        - absolute movement
  - MOVRX, MOVRY, MOVRZ        - relative movement
  - STIME, SHTIM               - time settings
  - CHAIO                       - TTL port configuration
  - SWF_X, SWF_Y, SWF_Z        - waveform path definition
  - RUXYZ, RUXY_               - start scan
"""

import time
import serial


# =============================================================================
# Stage configuration constants
# =============================================================================
PIEZO_PORT = 'COM4'
PIEZO_BAUDRATE = 115200
PIEZO_TIMEOUT = 2.0

# Piezo stage axis limits (in micrometers)
AXIS_LIMITS = {
    'X': {'min': 0.0, 'max': 120.0},
    'Y': {'min': 0.0, 'max': 80.0},
    'Z': {'min': 0.0, 'max': 120.0},
}

# Threshold for switching between normal and external clock mode (in ms)
EXTERNAL_CLOCK_THRESHOLD_MS = 0.1  # 100 µs (0.1 ms)


# =============================================================================
# Piezo stage controller class
# =============================================================================
class PiezoController:
    """Low-level communication with piezoelectric stage via COM."""

    def __init__(self, port=PIEZO_PORT, baudrate=PIEZO_BAUDRATE, timeout=PIEZO_TIMEOUT):
        self._port = port
        self._baud = baudrate
        self._timeout = timeout
        self._ser = None

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------
    def connect(self):
        """Opens serial connection with the stage."""
        if self._ser is not None and self._ser.is_open:
            return True
        try:
            self._ser = serial.Serial(
                port=self._port,
                baudrate=self._baud,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=self._timeout,
                write_timeout=None,
                xonxoff=False,
                rtscts=False
            )
            time.sleep(1.0)
            self._ser.reset_input_buffer()
            self._ser.reset_output_buffer()
            print(f"Connected to piezo stage on {self._port}")
            return True
        except Exception as e:
            print(f"Connection error with {self._port}: {e}")
            self._ser = None
            return False

    def disconnect(self):
        """Closes connection."""
        if self._ser is not None and self._ser.is_open:
            try:
                self._ser.close()
            except:
                pass
        self._ser = None

    def is_connected(self):
        return self._ser is not None and self._ser.is_open

    def _reopen_port(self):
        """Reopens serial port after a timeout."""
        try:
            self._ser.close()
        except:
            pass
        time.sleep(0.5)
        self._ser = serial.Serial(
            port=self._port,
            baudrate=self._baud,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=self._timeout,
            write_timeout=None,
            xonxoff=False,
            rtscts=False
        )
        time.sleep(0.5)
        self._ser.reset_input_buffer()
        self._ser.reset_output_buffer()

    # ------------------------------------------------------------------
    # Command sending
    # ------------------------------------------------------------------
    def _send_cmd(self, cmd, expect_response=True, delay=0.15):
        """Sends command to the stage and optionally receives response."""
        if self._ser is None or not self._ser.is_open:
            raise ConnectionError("Serial port is not open")
        
        for attempt in range(2):  # one retry on failure
            try:
                cmd_bytes = (cmd + '\n').encode('utf-8')
                self._ser.write(cmd_bytes)
                if not expect_response:
                    return None
                time.sleep(delay)
                response = b''
                while True:
                    chunk = self._ser.read(1)
                    if not chunk:
                        break
                    if chunk == b'\n':
                        break
                    response += chunk
                return response.decode('utf-8').strip() if response else ''
            except (serial.SerialTimeoutException, OSError) as e:
                if attempt == 0:
                    print(f"Timeout on '{cmd}', reopening port...")
                    self._reopen_port()
                else:
                    raise ConnectionError(f"Write timeout for command: '{cmd}' after retry")
            except Exception as e:
                raise ConnectionError(f"Error sending command '{cmd}': {e}")

    def send_raw(self, cmd, expect_response=True, delay=0.05):
        """Sends raw command to the stage."""
        return self._send_cmd(cmd, expect_response=expect_response, delay=delay)

    # ------------------------------------------------------------------
    # Position formatting
    # ------------------------------------------------------------------
    @staticmethod
    def format_position(pos_um):
        """Formats position in µm to command format (e.g., '100u' or '1257n')."""
        if pos_um == int(pos_um):
            return f"{int(pos_um)}u"
        else:
            return f"{int(round(pos_um * 1000))}n"

    @staticmethod
    def ms_to_time_str(ms):
        """Converts time in ms to string for STIME/SHTIM commands (e.g., '50m', '2s', '200u')."""
        if ms >= 1000:
            return f"{int(ms/1000)}s"
        elif ms >= 1:
            return f"{int(ms)}m"
        else:
            return f"{int(ms*1000)}u"

    # ------------------------------------------------------------------
    # Position reading
    # ------------------------------------------------------------------
    def get_position(self, axis='X'):
        """Reads current position for the specified axis (X, Y, or Z)."""
        cmd = f"GET_{axis}"
        resp = self._send_cmd(cmd, expect_response=True, delay=0.1)
        if resp:
            try:
                val_str = resp.strip().split(' ')[0]
                return float(val_str)
            except ValueError:
                return 0.0
        return 0.0

    def get_all_positions(self):
        """Reads all axis positions. Returns dict {X, Y, Z}."""
        return {
            'X': self.get_position('X'),
            'Y': self.get_position('Y'),
            'Z': self.get_position('Z'),
        }

    # ------------------------------------------------------------------
    # Movement
    # ------------------------------------------------------------------
    def move_absolute(self, axis, position_um):
        """Moves the specified axis to an absolute position (in µm)."""
        cmd_map = {'X': 'MOVEX', 'Y': 'MOVEY', 'Z': 'MOVEZ'}
        cmd = f"{cmd_map[axis]} {self.format_position(position_um)}"
        self._send_cmd(cmd, expect_response=True)
        time.sleep(0.1)

    def move_relative(self, axis, delta_um):
        """Moves the specified axis by a specified delta (in µm)."""
        cmd_map = {'X': 'MOVRX', 'Y': 'MOVRY', 'Z': 'MOVRZ'}
        sign = '+' if delta_um >= 0 else ''
        cmd = f"{cmd_map[axis]} {sign}{self.format_position(delta_um)}"
        self._send_cmd(cmd, expect_response=True)
        time.sleep(0.1)

    def center(self):
        """Sets all axes to the middle of their range."""
        for axis, limits in AXIS_LIMITS.items():
            center = (limits['min'] + limits['max']) / 2.0
            self.move_absolute(axis, center)

    # ------------------------------------------------------------------
    # Wait for stop
    # ------------------------------------------------------------------
    def wait_on_target(self, axes=None, timeout=30):
        """
        Waits until the specified axes stop moving.
        Checks each axis individually to avoid COM port congestion.
        
        :param axes: list of axes to check, e.g. ['X'], ['X','Y'], ['X','Y','Z'].
                     If None, checks all axes (['X', 'Y', 'Z']).
        :param timeout: max wait time per axis in seconds
        """
        if axes is None:
            axes = ['X', 'Y', 'Z']
        for axis in axes:
            t0 = time.time()
            while True:
                pos1 = self.get_position(axis)
                time.sleep(0.05)
                pos2 = self.get_position(axis)
                if abs(pos2 - pos1) <= 0.005:
                    break
                if time.time() - t0 > timeout:
                    print(f"WaitOnTarget timeout for {axis} after {timeout}s")
                    break

    # ------------------------------------------------------------------
    # Waveform scan commands
    # ------------------------------------------------------------------
    def set_stime_direct(self, dt_ms):
        """
        Sets STIME = dt (time between waveform steps = dwell time).
        For external clock mode: STIME = dt directly (not 2*dt).
        """
        stime_val = int(dt_ms)
        stime_str = self.ms_to_time_str(stime_val)
        time.sleep(0.2)
        self._send_cmd(f"STIME {stime_str}", expect_response=True)
        return stime_str

    def configure_ttl_ports_external_clock(self):
        """
        Configures TTL ports for external clock mode:
          CHAIO 1o1t - X axis
          CHAIO 2o2t - Y axis
          CHAIO 3o3t - Z axis
        """
        self._send_cmd("CHAIO 1o1t", expect_response=True)
        time.sleep(0.2)
        self._send_cmd("CHAIO 2o2t", expect_response=True)
        time.sleep(0.2)
        self._send_cmd("CHAIO 3o3t", expect_response=True)
        time.sleep(0.2)

    def set_waveform_x(self, nx, start_um, stop_um):
        """Programs waveform path for X axis."""
        cmd = f"SWF_X {nx} {self.format_position(start_um)} {self.format_position(stop_um)}"
        time.sleep(0.2)
        self._send_cmd(cmd, expect_response=True)
        return cmd

    def set_waveform_y(self, ny, start_um, stop_um):
        """Programs waveform path for Y axis."""
        cmd = f"SWF_Y {ny} {self.format_position(start_um)} {self.format_position(stop_um)}"
        time.sleep(0.2)
        self._send_cmd(cmd, expect_response=True)
        return cmd

    def set_waveform_z(self, nz, start_um, stop_um):
        """Programs waveform path for Z axis."""
        cmd = f"SWF_Z {nz} {self.format_position(start_um)} {self.format_position(stop_um)}"
        time.sleep(0.2)
        self._send_cmd(cmd, expect_response=True)
        return cmd

    def run_scan(self, nz):
        """Starts scan: RUXYZ if nz>0, RUXY_ if nz==0."""
        if nz > 0:
            cmd = "RUXYZ"
        else:
            cmd = "RUXY_"
        time.sleep(0.2)
        self._send_cmd(cmd, expect_response=False)
        return cmd

    # ------------------------------------------------------------------
    # Helper: determine mode based on dwell time
    # ------------------------------------------------------------------
    @staticmethod
    def is_external_clock_mode(dt_ms):
        """
        Determines which wavefront scan mode to use based on dwell time.
        
        - dt < EXTERNAL_CLOCK_THRESHOLD_MS: Normal mode (internal SPAD timing)
          Uses SHTIM, CHAIO 1o1t/2o2t/3o3t, C,dt_us,...
          
        - dt >= EXTERNAL_CLOCK_THRESHOLD_MS: External clock mode
          Uses STIME=dt, CHAIO 1o1s/2o2s/3o3s, C,0,...
        """
        return dt_ms >= EXTERNAL_CLOCK_THRESHOLD_MS
