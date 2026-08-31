#!/usr/bin/env python
"""
spad_array.py - Communication with SPAD array via TCP socket and data saving.

SPAD Commands:
  - C,dt_us,nz,nx,ny  - scanning (dt in microseconds)
  - I,dt              - photon measurement (dt in milliseconds)

SPAD Response:
  - sequence of lines with comma-separated data, terminated by "DONE" or "ERROR"
"""

import os
import socket
import time


# =============================================================================
# SPAD configuration constants
# =============================================================================
SPAD_HOST = '127.0.0.1'
SPAD_PORT = 9998


# =============================================================================
# SPAD communication class
# =============================================================================
class SpadController:
    """Communication with SPAD array via TCP socket."""

    def __init__(self, host=SPAD_HOST, port=SPAD_PORT):
        self._host = host
        self._port = port
        self._sock = None

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------
    def connect(self):
        """Connects to SPAD via socket."""
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._sock.connect((self._host, self._port))
            # Read welcome message
            data = self._sock.recv(8192)
            print(f"Connected to SPAD: {data.decode('utf8').strip()}")
            return True
        except Exception as e:
            print(f"Connection error with SPAD: {e}")
            self._sock = None
            return False

    def disconnect(self):
        """Closes connection with SPAD."""
        if self._sock is not None:
            try:
                self._sock.close()
            except:
                pass
        self._sock = None

    def is_connected(self):
        return self._sock is not None

    # ------------------------------------------------------------------
    # Command sending
    # ------------------------------------------------------------------
    def send_command(self, cmd):
        """Sends command to SPAD."""
        if self._sock is None:
            raise ConnectionError("SPAD socket is not open")
        self._sock.sendall(cmd.encode('utf-8') + b'\n')

    def send_scan_command(self, dt_us, nz, nx, ny, ext_frame_clk=0):
        """Sends scan command: C,dt_us,nz,nx,ny,ext_frame_clk."""
        cmd = f"C,{dt_us},{nz},{nx},{ny},{ext_frame_clk}"
        self.send_command(cmd)
        return cmd

    def send_scan_command_external_clock(self, nz, nx, ny, ext_frame_clk=0):
        """
        Sends scan command for external clock mode: C,0,nz,nx,ny,ext_frame_clk.
        The first 0 means 'use external dwell clock' - the SPAD uses external
        TTL signals from the piezo stage to time the pixel dwell instead
        of internal timing.
        The ext_frame_clk=0 means no external frame clock.
        """
        cmd = f"C,0,{nz},{nx},{ny},{ext_frame_clk}"
        self.send_command(cmd)
        return cmd

    def send_measure_command(self, dt_ms):
        """Sends measure command: I,dt (dt in milliseconds)."""
        cmd = f"I,{int(dt_ms)}"
        self.send_command(cmd)
        return cmd

    # ------------------------------------------------------------------
    # Data reception
    # ------------------------------------------------------------------
    def receive_data(self, timeout=60):
        """Receives data from SPAD. Returns string with data (without 'DONE'/'ERROR')."""
        if self._sock is None:
            raise ConnectionError("SPAD socket is not open")
        self._sock.settimeout(timeout)
        datastr = ""
        status = "OK"
        while True:
            try:
                data = self._sock.recv(32768)
                if not data:
                    break
                datastr += data.decode('utf-8')
                # Check for DONE or ERROR marker at the end of the data string
                # (not just the last chunk, since markers can be split across recv boundaries)
                stripped = datastr.rstrip()
                if stripped.endswith("DONE"):
                    status = "OK"
                    break
                elif stripped.endswith("ERROR"):
                    status = "ERROR"
                    print(f"SPAD error: {data[-160:]}")
                    break
            except socket.timeout:
                print("Timeout waiting for SPAD data")
                status = "TIMEOUT"
                break
        self._sock.settimeout(None)
        return datastr, status

    def receive_measure_data(self, timeout=5):
        """
        Receives data from SPAD for a single I,dt measurement command.
        
        This mimics the reference program (python_tcp_count.py) behavior:
        - Does a single recv() call (no loop waiting for DONE marker)
        - The I,dt command response may not include a DONE marker
        - Returns the raw data with empty lines removed
        
        :param timeout: socket timeout in seconds
        :return: (data_string, status_string)
        """
        if self._sock is None:
            raise ConnectionError("SPAD socket is not open")
        self._sock.settimeout(timeout)
        try:
            # Single recv() like the reference program
            data = self._sock.recv(8192)
            if not data:
                self._sock.settimeout(None)
                return "", "EMPTY"
            datastr = data.decode('utf-8')
            # Remove trailing DONE/ERROR if present (for compatibility)
            datastr = clean_spad_response(datastr).strip()
            self._sock.settimeout(None)
            return datastr, "OK"
        except socket.timeout:
            print("Timeout waiting for SPAD measure data")
            self._sock.settimeout(None)
            return "", "TIMEOUT"
        except Exception as e:
            print(f"Error receiving SPAD measure data: {e}")
            self._sock.settimeout(None)
            return "", "ERROR"


# =============================================================================
# Output file management functions
# =============================================================================
def find_next_run_number(save_dir):
    """
    Finds the highest existing Run number in the directory and returns the next one.
    Searches for files named Run_XXX.txt.
    """
    existing_runs = []
    if not os.path.isdir(save_dir):
        return 1
    for f in os.listdir(save_dir):
        if f.startswith('Run_') and f.endswith('.txt'):
            try:
                num = int(f.replace('Run_', '').replace('.txt', ''))
                existing_runs.append(num)
            except ValueError:
                pass
    return max(existing_runs) + 1 if existing_runs else 1


def save_spad_data(data, save_dir, run_number):
    """
    Saves SPAD data to Run_XXX.txt file.
    
    :param data: raw data from SPAD (string)
    :param save_dir: target directory
    :param run_number: sequence number
    :return: path to saved file
    """
    os.makedirs(save_dir, exist_ok=True)
    run_filename = f"Run_{run_number:03d}.txt"
    run_path = os.path.join(save_dir, run_filename)
    with open(run_path, 'w') as f:
        f.write(data)
    return run_path


def clean_spad_response(raw_data):
    """
    Removes 'DONE' or 'ERROR' from the end of SPAD data.
    
    :param raw_data: raw data from SPAD (may end with 'DONE' or 'ERROR')
    :return: data without end marker
    """
    if raw_data.endswith("DONE"):
        return raw_data[:-5]
    elif raw_data.endswith("ERROR"):
        return raw_data[:-6]
    return raw_data


def parse_scan_parameters(params):
    """
    Parses and verifies scan parameters.
    Calculates start and end positions (center in the middle of the frame).
    
    :param params: dict with scan parameters
    :return: dict with extended parameters (start_x, stop_x, etc.)
    """
    nx = int(params['nx'])
    ny = int(params['ny'])
    nz = int(params['nz'])
    dt = float(params['dt'])          # illumination time in ms
    dx = float(params['dx'])          # step in X/Y in nm
    dz = float(params.get('dz', dx))  # step in Z in nm
    xo = float(params['xo'])          # X center in um
    yo = float(params['yo'])          # Y center in um
    zo = float(params['zo'])          # Z center in um

    # Convert steps from nm to um
    dx_um = dx / 1000.0
    dz_um = dz / 1000.0

    # Calculate start and end positions (center in the middle of the frame)
    start_x = xo - (nx - 1) * dx_um / 2.0
    stop_x = xo + (nx - 1) * dx_um / 2.0
    start_y = yo - (ny - 1) * dx_um / 2.0
    stop_y = yo + (ny - 1) * dx_um / 2.0
    start_z = zo - (nz - 1) * dz_um / 2.0 if nz > 0 else zo
    stop_z = zo + (nz - 1) * dz_um / 2.0 if nz > 0 else zo

    return {
        'nx': nx, 'ny': ny, 'nz': nz,
        'dt': dt,
        'dx': dx, 'dz': dz,
        'dx_um': dx_um, 'dz_um': dz_um,
        'xo': xo, 'yo': yo, 'zo': zo,
        'start_x': start_x, 'stop_x': stop_x,
        'start_y': start_y, 'stop_y': stop_y,
        'start_z': start_z, 'stop_z': stop_z,
    }