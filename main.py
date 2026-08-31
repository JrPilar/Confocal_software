#!/usr/bin/env python
"""
main.py - GUI for controlling piezo stage (Piezoconcept C3) and SPAD array.

Connects piezo stage control via COM5 with SPAD data acquisition via socket.
Uses modules:
  - stage.py      (PiezoController, AXIS_LIMITS)
  - spad_array.py (SpadController, save/parse functions)
  - mainApp.py    (Ui_MainWindow - GUI layout from Qt Designer)

Run: python main.py

Requirements: PyQt5, pyserial, numpy
"""

import sys
import os
import time
import csv
import numpy as np
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QLabel,
    QLineEdit, QPushButton, QMessageBox, QFileDialog
)
from PyQt5.QtCore import Qt, QTimer, QThread, pyqtSignal
from PyQt5.QtGui import QFont, QDoubleValidator, QColor, QPainter, QIntValidator
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_qt5agg import NavigationToolbar2QT as NavigationToolbar
from matplotlib.figure import Figure
import matplotlib.pyplot as plt

from mainApp import Ui_MainWindow
from stage import PiezoController, AXIS_LIMITS, EXTERNAL_CLOCK_THRESHOLD_MS
from spad_array import (
    SpadController, SPAD_PORT, find_next_run_number, save_spad_data,
    clean_spad_response, parse_scan_parameters
)


# =============================================================================
# Connection indicator widget - colored LED (green/red)
# =============================================================================
class ConnectionIndicator(QWidget):
    """Round connection indicator - changes color from red to green."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(16, 16)
        self._color = QColor(220, 50, 50)  # red (default)

    def set_green(self):
        """Set green color (connected)."""
        self._color = QColor(50, 200, 50)
        self.update()

    def set_red(self):
        """Set red color (disconnected)."""
        self._color = QColor(220, 50, 50)
        self.update()

    def paintEvent(self, event):
        """Draws round LED."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setBrush(self._color)
        painter.setPen(QColor(80, 80, 80))
        painter.drawEllipse(1, 1, self.width() - 2, self.height() - 2)


# =============================================================================
# Matplotlib canvas for image display
# =============================================================================
class MplCanvas(FigureCanvas):
    """Matplotlib canvas for displaying scan images."""
    def __init__(self, parent=None, width=5, height=4, dpi=100):
        self.fig = Figure(figsize=(width, height), dpi=dpi)
        self.axes = self.fig.add_subplot(111)
        super().__init__(self.fig)
        self.setParent(parent)
        self.fig.tight_layout()


# =============================================================================
# Scan worker thread (to keep GUI responsive)
# =============================================================================
class ScanWorker(QThread):
    """Executes scan in a separate thread."""
    progress = pyqtSignal(int, str)
    finished = pyqtSignal(bool, str)
    log = pyqtSignal(str)

    def __init__(self, piezo, spad, params):
        super().__init__()
        self.piezo = piezo
        self.spad = spad
        self.params = params  # dict with scan parameters

    def run(self):
        try:
            self._execute_scan()
            self.finished.emit(True, "Scan completed successfully")
        except Exception as e:
            self.finished.emit(False, f"Scan error: {e}")

    def _execute_scan(self):
        p = self.params
        num_scans = int(p['num_scans'])
        save_dir = p['save_dir']
        method = p.get('method', 'Wavefront')

        # Expand parameters (calculate start/stop positions)
        sp = parse_scan_parameters(p)
        nx, ny, nz = sp['nx'], sp['ny'], sp['nz']
        dt = sp['dt']
        start_x, stop_x = sp['start_x'], sp['stop_x']
        start_y, stop_y = sp['start_y'], sp['stop_y']
        start_z, stop_z = sp['start_z'], sp['stop_z']
        dx_um = sp['dx_um']

        self.log.emit(f"Scan parameters: {nx}x{ny}x{nz}, dt={dt}ms, dx={p['dx']}nm")
        self.log.emit(f"X: {start_x:.3f} -> {stop_x:.3f} um")
        self.log.emit(f"Y: {start_y:.3f} -> {stop_y:.3f} um")
        if nz > 0:
            self.log.emit(f"Z: {start_z:.3f} -> {stop_z:.3f} um")
        self.log.emit(f"Number of scans: {num_scans}")
        self.log.emit(f"Save directory: {save_dir}")
        self.log.emit(f"Method: {method}")

        # Ensure directory exists and find next Run number
        os.makedirs(save_dir, exist_ok=True)
        next_run = find_next_run_number(save_dir)

        for scan_idx in range(num_scans):
            self.log.emit(f"\n--- Scan {scan_idx + 1}/{num_scans} ---")
            self.progress.emit(scan_idx, f"Scan {scan_idx + 1}/{num_scans}")

            scan_start = time.time()
            if method == "Wavefront":
                self._execute_wavefront_scan(sp, p, next_run, save_dir)
            else:
                self._execute_manual_scan(sp, p, next_run, save_dir)
            scan_elapsed = time.time() - scan_start
            
            self.log.emit(f"Scan {scan_idx + 1} completed in {scan_elapsed:.1f}s")
            
            next_run += 1

            # Short pause between scans
            time.sleep(1)

        self.log.emit("\nAll scans completed!")

    def _execute_wavefront_scan(self, sp, p, run_number, save_dir):
        """Execute Wavefront scan - Piezo is master.
        
        Two modes based on dwell time (dt):
          - dt < 100ms: Normal mode (SHTIM, CHAIO 1o1t/2o2t/3o3t, C,dt_us,...)
          - dt >= 100ms: External clock mode (STIME=dt, CHAIO 1o1s/2o2s/3o3s, C,0,...)
        """
        nx, ny, nz = sp['nx'], sp['ny'], sp['nz']
        dt = sp['dt']
        start_x, stop_x = sp['start_x'], sp['stop_x']
        start_y, stop_y = sp['start_y'], sp['stop_y']
        start_z, stop_z = sp['start_z'], sp['stop_z']

        # Determine which mode to use
        use_external_clock = self.piezo.is_external_clock_mode(dt)
        mode_name = "External clock" if use_external_clock else "Normal"
        self.log.emit(f"Wavefront mode: {mode_name} (dt={dt}ms)")

        # Step 1: Move to start position
        self.log.emit("Moving to start position...")
        if nz > 0:
            self.piezo.move_absolute('Z', start_z)
            self.piezo.wait_on_target()
        self.piezo.move_absolute('Y', start_y)
        self.piezo.wait_on_target()
        self.piezo.move_absolute('X', start_x)
        self.piezo.wait_on_target()
        time.sleep(0.2)

        # Step 2: Set timing
        if use_external_clock:
            # External clock mode: STIME = dt (no SHTIM needed)
            stime_str = self.piezo.set_stime_direct(dt)
            self.log.emit(f"STIME = {stime_str} (dt={int(dt)}ms, external clock mode)")
            self.log.emit("SHTIM not used in external clock mode")
        else:
            # Normal mode: STIME = 2*dt, SHTIM = dt
            stime_str = self.piezo.set_stime(dt)
            shtim_str = self.piezo.set_shtim(dt)
            self.log.emit(f"STIME = {stime_str} (2*dt={int(2*dt)}ms)")
            self.log.emit(f"SHTIM = {shtim_str} (dt={int(dt)}ms)")

        # Step 3: Configure TTL ports
        self.log.emit("Configuring TTL ports...")
        if use_external_clock:
            # External clock mode: CHAIO 1o1s/2o2s/3o3s
            # 's' = signal at start of motion (before stabilization)
            # This triggers external dwell clock: line starts exposure,
            # dwell ends current and starts next exposure
            self.piezo.configure_ttl_ports_external_clock()
        else:
            # Normal mode: CHAIO 1o1t/2o2t/3o3t
            self.piezo.configure_ttl_ports()

        # Step 4: Program SWF paths
        cmd_swf_x = self.piezo.set_waveform_x(nx, start_x, stop_x)
        self.log.emit(f"X waveform: {cmd_swf_x}")
        cmd_swf_y = self.piezo.set_waveform_y(ny, start_y, stop_y)
        self.log.emit(f"Y waveform: {cmd_swf_y}")
        if nz > 0:
            cmd_swf_z = self.piezo.set_waveform_z(nz, start_z, stop_z)
            self.log.emit(f"Z waveform: {cmd_swf_z}")

        # Step 5: Send command to SPAD
        if use_external_clock:
            # External clock: C,0,nz,nx,ny (0 = external dwell clock)
            spad_cmd = self.spad.send_scan_command_external_clock(nz, nx, ny)
        else:
            # Normal: C,dt_us,nz,nx,ny
            dt_us = int(dt * 1000)  # ms -> us
            spad_cmd = self.spad.send_scan_command(dt_us, nz, nx, ny)
        self.log.emit(f"SPAD command: {spad_cmd}")

        # Step 6: Start scan on piezo
        run_cmd = self.piezo.run_scan(nz)
        self.log.emit(f"Starting: {run_cmd}")

        # Step 7: Receive data from SPAD
        self.log.emit("Waiting for SPAD data...")
        spad_data, status = self.spad.receive_data(timeout=300)

        if status != "OK" or not spad_data:
            self.log.emit(f"WARNING: Problem with SPAD data (status={status})")
            return

        # Remove end marker
        clean_data = clean_spad_response(spad_data)

        # Step 8: Save data to file
        run_path = save_spad_data(clean_data, save_dir, run_number)
        self.log.emit(f"Saved: {run_path}")
        
        # Step 9: Save parameters to CSV file
        csv_path = os.path.join(save_dir, f"Run_{run_number:03d}_parameters.csv")
        self._save_parameters_csv(csv_path, sp, p)
        self.log.emit(f"Parameters saved: {csv_path}")

        # Return to scan center
        self.log.emit("Returning to scan center...")
        self.piezo.move_absolute('X', sp['xo'])
        self.piezo.wait_on_target()
        self.piezo.move_absolute('Y', sp['yo'])
        self.piezo.wait_on_target()
        if nz > 0:
            self.piezo.move_absolute('Z', sp['zo'])
            self.piezo.wait_on_target()
        self.log.emit(f"Returned to center: X={sp['xo']:.3f}, Y={sp['yo']:.3f}, Z={sp['zo']:.3f} µm")

    def _execute_manual_scan(self, sp, p, run_number, save_dir):
        """Execute Manual scan - Python is master, step by step movement."""
        nx, ny, nz = sp['nx'], sp['ny'], sp['nz']
        dt = sp['dt']
        xo, yo, zo = sp['xo'], sp['yo'], sp['zo']
        dx_um = sp['dx_um']
        dz_um = sp.get('dz_um', dx_um)
        start_x = sp['start_x']
        start_y = sp['start_y']
        stop_x = sp['stop_x']
        stop_y = sp['stop_y']

        # Calculate start positions
        start_z = zo - (nz - 1) * dz_um / 2.0 if nz > 0 else zo

        # Step 1: Move to start position
        self.log.emit("Moving to start position...")
        if nz > 0:
            self.piezo.move_absolute('Z', start_z)
            self.piezo.wait_on_target()
        self.piezo.move_absolute('Y', start_y)
        self.piezo.wait_on_target()
        self.piezo.move_absolute('X', start_x)
        self.piezo.wait_on_target()
        time.sleep(0.1)

        # Collect all data
        all_data = []
        total_points = nx * ny * (nz + 1) if nz > 0 else nx * ny
        point_count = 0

        # Log image dimensions
        if nz > 0:
            self.log.emit(f"Image size: {nx} (X) x {ny} (Y) x {nz + 1} (Z) = {total_points} pixels")
        else:
            self.log.emit(f"Image size: {nx} (X) x {ny} (Y) = {total_points} pixels")

        # Z loop (if nz > 0)
        for z_idx in range(nz + 1 if nz > 0 else 1):
            if nz > 0:
                current_z = start_z + z_idx * dz_um
                self.piezo.move_absolute('Z', current_z)
                self.piezo.wait_on_target()
                time.sleep(0.1)

            # Y loop - forward and backward
            for y_idx in range(ny):
                # Determine Y position and direction
                if y_idx % 2 == 0:
                    # Forward direction (left to right)
                    y_pos = start_y + y_idx * dx_um
                    x_direction = 1
                else:
                    # Backward direction (right to left)
                    y_pos = start_y + y_idx * dx_um
                    x_direction = -1

                self.piezo.move_absolute('Y', y_pos)
                self.piezo.wait_on_target()
                time.sleep(0.1)

                # X loop
                for x_idx in range(nx):
                    if y_idx % 2 == 0:
                        # Forward: x goes from 0 to nx-1
                        x_pos = start_x + x_idx * dx_um
                    else:
                        # Backward: x goes from nx-1 to 0
                        x_pos = start_x + (nx - 1 - x_idx) * dx_um

                    self.piezo.move_absolute('X', x_pos)
                    self.piezo.wait_on_target()
                    time.sleep(0.05)

                    # Send I,dt command to SPAD
                    spad_cmd = self.spad.send_measure_command(dt)
                    self.log.emit(f"SPAD command: {spad_cmd} at X={x_pos:.3f}, Y={y_pos:.3f}")

                    # Receive data from SPAD using measure mode (single recv, like reference program)
                    spad_data, status = self.spad.receive_measure_data(timeout=dt/1000.0 + 5)

                    if status == "OK" and spad_data:
                        # Format data with coordinate prefix like wavefront: (z,y,x),counts
                        coord_prefix = f"({z_idx},{y_idx},{x_idx}),"
                        all_data.append(coord_prefix + spad_data)
                        point_count += 1
                        self.progress.emit(point_count, f"Point {point_count}/{total_points}")
                    else:
                        self.log.emit(f"WARNING: Problem with SPAD data at X={x_pos:.3f}, Y={y_pos:.3f} (status={status})")
                # After completing X line, move back to start of line
                if y_idx % 2 == 0:
                    # Was moving forward, go back to start
                    self.piezo.move_absolute('X', start_x)
                    self.piezo.wait_on_target()
                else:
                    # Was moving backward, go back to end
                    self.piezo.move_absolute('X', stop_x)
                    self.piezo.wait_on_target()
                time.sleep(0.1)

        # Step 8: Save data to file
        # Format data as single string with newlines
        data_str = '\n'.join(all_data)
        run_path = save_spad_data(data_str, save_dir, run_number)
        self.log.emit(f"Saved: {run_path}")
        
        # Step 9: Save parameters to CSV file
        csv_path = os.path.join(save_dir, f"Run_{run_number:03d}_parameters.csv")
        self._save_parameters_csv(csv_path, sp, p)
        self.log.emit(f"Parameters saved: {csv_path}")

        # Return to scan center
        self.log.emit("Returning to scan center...")
        self.piezo.move_absolute('X', xo)
        self.piezo.wait_on_target()
        self.piezo.move_absolute('Y', yo)
        self.piezo.wait_on_target()
        if nz > 0:
            self.piezo.move_absolute('Z', zo)
            self.piezo.wait_on_target()
        self.log.emit(f"Returned to center: X={xo:.3f}, Y={yo:.3f}, Z={zo:.3f} µm")

    def _save_parameters_csv(self, csv_path, sp, p):
        """Saves scan parameters to CSV file."""
        with open(csv_path, 'w', newline='') as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow(['Parameter', 'Value', 'Unit'])
            writer.writerow(['Method', p.get('method', 'Wavefront'), ''])
            writer.writerow(['Illumination time (dt)', sp['dt'], 'ms'])
            writer.writerow(['X/Y step (dx)', sp['dx'], 'nm'])
            writer.writerow(['Z step (dz)', sp['dz'], 'nm'])
            writer.writerow(['X steps (nx)', sp['nx'], 'steps'])
            writer.writerow(['Y steps (ny)', sp['ny'], 'steps'])
            writer.writerow(['Z steps (nz)', sp['nz'], 'steps'])
            writer.writerow(['X center (xo)', sp['xo'], 'µm'])
            writer.writerow(['Y center (yo)', sp['yo'], 'µm'])
            writer.writerow(['Z center (zo)', sp['zo'], 'µm'])
            writer.writerow(['X start', sp['start_x'], 'µm'])
            writer.writerow(['X stop', sp['stop_x'], 'µm'])
            writer.writerow(['Y start', sp['start_y'], 'µm'])
            writer.writerow(['Y stop', sp['stop_y'], 'µm'])
            writer.writerow(['Z start', sp['start_z'], 'µm'])
            writer.writerow(['Z stop', sp['stop_z'], 'µm'])


# =============================================================================
# Main GUI window - inherits from QMainWindow and Ui_MainWindow
# =============================================================================
class Window(QMainWindow, Ui_MainWindow):
    """Main application window for controlling piezo stage and SPAD."""

    def __init__(self):
        super().__init__()
        self.spad_port = SPAD_PORT
        self.piezo = PiezoController()
        self.spad = SpadController()
        self.scan_worker = None

        # Setup UI from Qt Designer
        self.setupUi(self)
        
        # Center labels of positions
        self.posX.setAlignment(Qt.AlignCenter)
        self.posY.setAlignment(Qt.AlignCenter)
        self.posZ.setAlignment(Qt.AlignCenter)
        
        # Add connection indicators to statusbar
        self._add_connection_indicators()

        # Connect button signals
        self._connect_signals()

        # Timer for position updates
        self.pos_timer = QTimer()
        self.pos_timer.timeout.connect(self._update_position_display)
        self.pos_timer.start(200)  # every 200 ms

        # Set validators for numeric fields
        self.stepSizeEdit.setValidator(QDoubleValidator())
        self.absX.setValidator(QDoubleValidator())
        self.absY.setValidator(QDoubleValidator())
        self.absZ.setValidator(QDoubleValidator())
        self.scanXo.setValidator(QDoubleValidator())
        self.scanYo.setValidator(QDoubleValidator())
        self.scanZo.setValidator(QDoubleValidator())

        # Set initial save directory
        self.scanDirEdit.setText(os.path.join(os.getcwd(), "scan_data"))

        # Initialize matplotlib canvas for preview
        self.preview_canvas = MplCanvas(self.previewWidget, width=5, height=4, dpi=100)
        preview_layout = QVBoxLayout(self.previewWidget)
        preview_layout.addWidget(self.preview_canvas)
        
        # Add navigation toolbar
        self.preview_toolbar = NavigationToolbar(self.preview_canvas, self.previewWidget)
        preview_layout.addWidget(self.preview_toolbar)

        self.statusbar.showMessage("Ready")

    def _add_connection_indicators(self):
        """Adds connection indicators and Connect/Disconnect buttons to statusbar."""
        status_widget = QWidget()
        status_layout = QHBoxLayout(status_widget)
        status_layout.setContentsMargins(0, 0, 0, 0)

        # Piezo indicator
        self.piezo_indicator = ConnectionIndicator()
        status_layout.addWidget(self.piezo_indicator)
        self.piezo_status_label = QLabel("Piezo: OFF")
        status_layout.addWidget(self.piezo_status_label)
        self.btn_connect_piezo = QPushButton("Connect")
        self.btn_connect_piezo.setFixedWidth(80)
        self.btn_connect_piezo.clicked.connect(self._toggle_piezo_connection)
        status_layout.addWidget(self.btn_connect_piezo)

        status_layout.addSpacing(20)

        # SPAD indicator
        self.spad_indicator = ConnectionIndicator()
        status_layout.addWidget(self.spad_indicator)
        self.spad_status_label = QLabel("SPAD: OFF")
        status_layout.addWidget(self.spad_status_label)
        self.btn_connect_spad = QPushButton("Connect")
        self.btn_connect_spad.setFixedWidth(65)
        self.btn_connect_spad.clicked.connect(self._toggle_spad_connection)
        status_layout.addWidget(self.btn_connect_spad)

        # SPAD port selection
        port_label = QLabel("Port:")
        status_layout.addWidget(port_label)
        self.spad_port_edit = QLineEdit()
        self.spad_port_edit.setFixedWidth(50)
        self.spad_port_edit.setText(str(self.spad_port))
        self.spad_port_edit.setValidator(QIntValidator(1, 65535))
        status_layout.addWidget(self.spad_port_edit)

        status_layout.addStretch()
        self.statusbar.addPermanentWidget(status_widget)

    def _connect_signals(self):
        """Connects UI button signals to methods."""
        # Step movement buttons
        self.btnXminus.clicked.connect(lambda: self._step_move('X', -1))
        self.btnXplus.clicked.connect(lambda: self._step_move('X', 1))
        self.btnYminus.clicked.connect(lambda: self._step_move('Y', -1))
        self.btnYplus.clicked.connect(lambda: self._step_move('Y', 1))
        self.btnZminus.clicked.connect(lambda: self._step_move('Z', -1))
        self.btnZplus.clicked.connect(lambda: self._step_move('Z', 1))

        # Absolute position
        self.btnGoAbs.clicked.connect(self._go_to_absolute)
        self.btnSetCurrent.clicked.connect(self._set_current_position)

        # Centering
        self.btnCenter.clicked.connect(self._center_stage)

        # Scan
        self.btnSetCenter.clicked.connect(self._set_scan_center_from_current)
        self.btnBrowseDir.clicked.connect(self._browse_save_dir)
        self.btnStartScan.clicked.connect(self._start_scan)

        # Log
        self.btnClearLog.clicked.connect(lambda: self.logText.clear())
        
        # Preview
        self.btnBrowsePreview.clicked.connect(self._browse_preview_file)
        self.btnPreview.clicked.connect(self._show_preview)

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------
    def _update_connection_indicators(self):
        """Updates connection indicator appearance."""
        if self.piezo.is_connected():
            self.piezo_indicator.set_green()
            self.piezo_status_label.setText("Piezo: ON")
            self.btn_connect_piezo.setText("Disconnect")
        else:
            self.piezo_indicator.set_red()
            self.piezo_status_label.setText("Piezo: OFF")
            self.btn_connect_piezo.setText("Connect")

        if self.spad.is_connected():
            self.spad_indicator.set_green()
            self.spad_status_label.setText("SPAD: ON")
            self.btn_connect_spad.setText("Disconnect")
        else:
            self.spad_indicator.set_red()
            self.spad_status_label.setText("SPAD: OFF")
            self.btn_connect_spad.setText("Connect")

    def _toggle_piezo_connection(self):
        """Toggles piezo connection."""
        if self.piezo.is_connected():
            self.piezo.disconnect()
            self.log("Disconnected from piezo stage")
        else:
            if self.piezo.connect():
                self.log("Connected to piezo stage")
            else:
                self.log("WARNING: Cannot connect to piezo stage")
        self._update_connection_indicators()

    def _toggle_spad_connection(self):
        """Toggles SPAD connection."""
        if self.spad.is_connected():
            self.spad.disconnect()
            self.log("Disconnected from SPAD")
        else:
            try:
                self.spad_port = int(self.spad_port_edit.text())
            except ValueError:
                self.log("WARNING: Invalid SPAD port number")
                return
            self.spad = SpadController(port=self.spad_port)
            if self.spad.connect():
                self.log(f"Connected to SPAD on port {self.spad_port}")
            else:
                self.log(f"WARNING: Cannot connect to SPAD on port {self.spad_port}")
        self._update_connection_indicators()

    # ------------------------------------------------------------------
    # Position update
    # ------------------------------------------------------------------
    def _update_position_display(self):
        """Updates position display (called by timer)."""
        if not self.piezo.is_connected():
            self.posX.setText("---")
            self.posY.setText("---")
            self.posZ.setText("---")
            return
        try:
            pos = self.piezo.get_all_positions()
            self.posX.setText(f"{pos['X']:.3f}")
            self.posY.setText(f"{pos['Y']:.3f}")
            self.posZ.setText(f"{pos['Z']:.3f}")
        except:
            pass

    # ------------------------------------------------------------------
    # Stage movement
    # ------------------------------------------------------------------
    def _step_move(self, axis, direction):
        """Performs one step in the specified direction."""
        if not self.piezo.is_connected():
            QMessageBox.warning(self, "Error", "No connection to piezo stage")
            return
        try:
            step_nm = float(self.stepSizeEdit.text())
            step_um = step_nm / 1000.0 * direction
            self.piezo.move_relative(axis, step_um)
            self.log(f"Move: {axis}{'+' if direction > 0 else '-'} {step_nm:.1f} nm")
        except ValueError:
            QMessageBox.warning(self, "Error", "Invalid step value")

    def _go_to_absolute(self):
        """Moves to absolute position."""
        if not self.piezo.is_connected():
            QMessageBox.warning(self, "Error", "No connection to piezo stage")
            return
        try:
            for axis, edit in [('X', self.absX), ('Y', self.absY), ('Z', self.absZ)]:
                pos = float(edit.text())
                limits = AXIS_LIMITS[axis]
                if pos < limits['min'] or pos > limits['max']:
                    QMessageBox.warning(self, "Error",
                        f"Position {axis}={pos} µm out of range ({limits['min']}-{limits['max']})")
                    return
                self.piezo.move_absolute(axis, pos)
            self.log(f"Moved to: X={self.absX.text()}, Y={self.absY.text()}, Z={self.absZ.text()} µm")
        except ValueError:
            QMessageBox.warning(self, "Error", "Invalid position value")

    def _set_current_position(self):
        """Copies current position to absolute position fields."""
        if not self.piezo.is_connected():
            return
        try:
            pos = self.piezo.get_all_positions()
            self.absX.setText(f"{pos['X']:.3f}")
            self.absY.setText(f"{pos['Y']:.3f}")
            self.absZ.setText(f"{pos['Z']:.3f}")
        except:
            pass

    def _center_stage(self):
        """Centers the stage."""
        if not self.piezo.is_connected():
            QMessageBox.warning(self, "Error", "No connection to piezo stage")
            return
        self.piezo.center()
        self.log("Stage centered (middle of range)")

    # ------------------------------------------------------------------
    # Confocal scan
    # ------------------------------------------------------------------
    def _set_scan_center_from_current(self):
        """Sets scan center to current stage position."""
        if not self.piezo.is_connected():
            return
        try:
            pos = self.piezo.get_all_positions()
            self.scanXo.setText(f"{pos['X']:.3f}")
            self.scanYo.setText(f"{pos['Y']:.3f}")
            self.scanZo.setText(f"{pos['Z']:.3f}")
        except:
            pass

    def _browse_save_dir(self):
        """Opens directory selection dialog."""
        dir_path = QFileDialog.getExistingDirectory(self, "Select save directory",
                                                     self.scanDirEdit.text())
        if dir_path:
            self.scanDirEdit.setText(dir_path)

    def _browse_preview_file(self):
        """Opens file selection dialog for preview."""
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Select scan file", "", "Text files (*.txt);;All files (*.*)"
        )
        if file_path:
            self.previewFileEdit.setText(file_path)
            self.previewFileName.setText(os.path.basename(file_path))

    def _start_scan(self):
        """Starts scan in a separate thread."""
        if not self.piezo.is_connected():
            QMessageBox.warning(self, "Error", "No connection to piezo stage")
            return
        if not self.spad.is_connected():
            QMessageBox.warning(self, "Error", "No connection to SPAD")
            return
        if self.scan_worker is not None and self.scan_worker.isRunning():
            QMessageBox.warning(self, "Warning", "Scan already in progress!")
            return

        # Get scan method
        method_index = self.scanMethod.currentIndex()
        method = "Wavefront" if method_index == 0 else "Manual"

        try:
            params = {
                'nx': self.scanNx.value(),
                'ny': self.scanNy.value(),
                'nz': self.scanNz.value(),
                'dx': self.scanDx.value(),
                'dz': self.scanDz.value(),
                'dt': self.scanDt.value() / 1000.0,  # convert µs to ms
                'num_scans': self.scanNum.value(),
                'xo': float(self.scanXo.text()),
                'yo': float(self.scanYo.text()),
                'zo': float(self.scanZo.text()),
                'save_dir': self.scanDirEdit.text(),
                'method': method,
            }
        except ValueError as e:
            QMessageBox.warning(self, "Error", f"Invalid parameters: {e}")
            return

        self.scanProgress.setVisible(True)
        self.scanProgress.setMaximum(params['num_scans'])
        self.scanProgress.setValue(0)

        self.scan_worker = ScanWorker(self.piezo, self.spad, params)
        self.scan_worker.progress.connect(self._on_scan_progress)
        self.scan_worker.finished.connect(self._on_scan_finished)
        self.scan_worker.log.connect(self.log)
        self.scan_worker.start()

        self.log("--- Starting scan ---")

    def _on_scan_progress(self, value, message):
        self.scanProgress.setValue(value + 1)
        self.statusbar.showMessage(message)

    def _on_scan_finished(self, success, message):
        self.scanProgress.setVisible(False)
        self.statusbar.showMessage(message)
        if success:
            self.log(message)
        else:
            self.log(f"ERROR: {message}")
            QMessageBox.critical(self, "Scan error", message)

    def _show_preview(self):
        """Displays preview of selected scan file."""
        file_path = self.previewFileEdit.text()
        if not file_path or not os.path.exists(file_path):
            QMessageBox.warning(self, "Warning", "Please select a valid file first")
            return
        
        try:
            # Load data
            with open(file_path, 'r') as f:
                data = f.read()
            
            # Parse data
            lines = data.strip().split('\n')
            values = []
            for line in lines:
                if line.strip():
                    parts = line.split(',')
                    if len(parts) >= 4:
                        # Sum channels 3-24 (indices 3-24)
                        counts = sum(int(parts[i]) for i in range(3, min(25, len(parts))))
                        values.append(counts)
            
            if not values:
                QMessageBox.warning(self, "Warning", "No valid data in file")
                return
            
            # Try to load parameters from CSV file
            csv_path = file_path.replace('.txt', '_parameters.csv')
            if os.path.exists(csv_path):
                params = self._load_parameters_csv(csv_path)
            else:
                params = None
            
            # Get dimensions from parameters or use defaults
            if params:
                nx = int(params.get('X steps (nx)', 8))
                ny = int(params.get('Y steps (ny)', 8))
                x_start = float(params.get('X start', 0))
                x_stop = float(params.get('X stop', nx))
                y_start = float(params.get('Y start', 0))
                y_stop = float(params.get('Y stop', ny))
            else:
                # Try to infer from filename or use defaults
                nx, ny = 8, 8
                x_start, x_stop = 0, 8
                y_start, y_stop = 0, 8
            
            # Reshape data into image
            if len(values) >= nx * ny:
                img_data = np.array(values[:nx * ny]).reshape(ny, nx)
            else:
                img_data = np.array(values).reshape(-1, nx)
            
            # Normalize if checked
            if self.checkNormalize.isChecked():
                img_data = img_data - img_data.min()
                if img_data.max() > 0:
                    img_data = img_data / img_data.max()
            
            # Clear previous plot and colorbar
            self.preview_canvas.axes.clear()
            self.preview_canvas.fig.clear()
            self.preview_canvas.axes = self.preview_canvas.fig.add_subplot(111)
            
            # Display image
            im = self.preview_canvas.axes.imshow(img_data, cmap='hot', interpolation='nearest', 
                                                  extent=[x_start, x_stop, y_stop, y_start])
            self.preview_canvas.axes.set_xlabel('X [µm]')
            self.preview_canvas.axes.set_ylabel('Y [µm]')
            self.preview_canvas.axes.set_title('Scan Preview')
            self.preview_canvas.axes.grid(False)
            self.preview_canvas.fig.colorbar(im, ax=self.preview_canvas.axes, label='Counts')
            self.preview_canvas.fig.tight_layout()
            self.preview_canvas.draw()
            
            self.log(f"Preview loaded: {os.path.basename(file_path)}")
            
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to load preview: {str(e)}")

    def _load_parameters_csv(self, csv_path):
        """Loads parameters from CSV file."""
        params = {}
        try:
            with open(csv_path, 'r') as f:
                reader = csv.reader(f)
                next(reader)  # Skip header
                for row in reader:
                    if len(row) >= 2:
                        params[row[0]] = row[1]
        except:
            pass
        return params

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------
    def log(self, message):
        """Adds message to log."""
        timestamp = time.strftime("%H:%M:%S")
        self.logText.append(f"[{timestamp}] {message}")
        # Scroll to bottom
        scrollbar = self.logText.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    # ------------------------------------------------------------------
    # Close
    # ------------------------------------------------------------------
    def closeEvent(self, event):
        """Closes connections on exit."""
        self.pos_timer.stop()
        if self.scan_worker is not None and self.scan_worker.isRunning():
            self.scan_worker.terminate()
            self.scan_worker.wait()
        self.piezo.disconnect()
        self.spad.disconnect()
        event.accept()


# =============================================================================
# Application startup
# =============================================================================
def main():
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    window = Window()
    window.show()
    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
