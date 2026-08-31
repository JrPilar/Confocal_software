# Confocal Microscope Control System Documentation

## Overview

This software provides a graphical user interface (GUI) for controlling a Piezoconcept C3 piezoelectric stage and acquiring data from a SPAD23 from PiImaging. The system enables confocal scanning by coordinating precise stage movements with photon detection.

**Original command documentation:**
- Piezo stage commands: `List of commands for the USB interface-Version July 2023.pdf`
- SPAD system manual: `SPAD23systemmanual.pdf`

---

## System Architecture

The application consists of three main Python modules:

| Module | Description |
|--------|-------------|
| `main.py` | Main application with GUI, scan orchestration, and visualization |
| `stage.py` | Low-level communication with Piezoconcept C3 stage via serial port |
| `spad_array.py` | TCP socket communication with SPAD array and data management |

---

## Connection Details

### Piezo Stage Connection

- **Port:** COM4 (configurable in `stage.py`)
- **Baudrate:** 115200
- **Protocol:** Serial (RS-232)
- **Data format:** 8 bits, no parity, 1 stop bit

### SPAD Array Connection

- **Host:** 127.0.0.1 (localhost)
- **Port:** 9998 (configurable in GUI)
- **Protocol:** TCP socket

---

## Module Documentation

### stage.py - PiezoController Class

The `PiezoController` class handles all communication with the Piezoconcept C3 piezoelectric stage.

#### Connection Management

| Method | Description |
|--------|-------------|
| `connect()` | Opens serial connection to the stage. Initializes buffers. Returns `True` on success, `False` on failure. |
| `disconnect()` | Closes the serial connection. |
| `is_connected()` | Returns `True` if the serial port is open and active. |

#### Position Control

| Method | Description |
|--------|-------------|
| `get_position(axis)` | Reads current position for specified axis ('X', 'Y', or 'Z'). Returns position in micrometers (µm). |
| `get_all_positions()` | Reads and returns all axis positions as a dictionary `{'X': x, 'Y': y, 'Z': z}`. |
| `move_absolute(axis, position_um)` | Moves the specified axis to an absolute position in micrometers. |
| `move_relative(axis, delta_um)` | Moves the specified axis by a relative delta in micrometers. |
| `center()` | Moves all axes to the center of their range (middle of min/max limits). |
| `wait_on_target(axes=None, timeout=30)` | Blocks until the specified axes stop moving. Checks each axis **individually** (not all at once) to avoid COM port congestion. Defaults to `['X', 'Y', 'Z']`. |

#### Scan Configuration

| Method | Description |
|--------|-------------|
| `set_stime(dt_ms)` | Sets STIME = 2*dt (time between waveform steps in milliseconds). Used in **normal mode** (dt < 100µs). Returns the formatted time string. |
| `set_shtim(dt_ms)` | Sets SHTIM = dt (illumination time in milliseconds). Used in **normal mode** (dt < 100µs). Returns the formatted time string. |
| `configure_ttl_ports()` | Configures TTL output ports for **normal mode**: `CHAIO 1o1t` (X), `CHAIO 2o2t` (Y), `CHAIO 3o3t` (Z). |
| `set_stime_direct(dt_ms)` | Sets STIME = dt directly (time between waveform steps = dwell time). Used in **external clock mode** (dt >= 100µs). Returns the formatted time string. |
| `configure_ttl_ports_external_clock()` | Configures TTL output ports for **external clock mode**: `CHAIO 1o1s` (X), `CHAIO 2o2s` (Y), `CHAIO 3o3s` (Z). The 's' mode sends a TTL signal at the start of each motion step (before stabilization), triggering the SPAD external dwell clock. |
| `is_external_clock_mode(dt_ms)` | Static method. Returns `True` if dt >= `EXTERNAL_CLOCK_THRESHOLD_MS` (0.1ms = 100µs), `False` otherwise. |
| `set_waveform_x(nx, start_um, stop_um)` | Programs the X-axis waveform path. `nx` = number of steps, `start_um`/`stop_um` = start/end positions. |
| `set_waveform_y(ny, start_um, stop_um)` | Programs the Y-axis waveform path. |
| `set_waveform_z(nz, start_um, stop_um)` | Programs the Z-axis waveform path. |
| `run_scan(nz)` | Starts the scan. Returns `"RUXYZ"` if nz>0, otherwise `"RUXY_"`. |

#### Helper Methods

| Method | Description |
|--------|-------------|
| `format_position(pos_um)` | Converts position in µm to command format: `'100u'` for whole micrometers, `'1257n'` for nanometer precision. |
| `ms_to_time_str(ms)` | Converts time in milliseconds to command format: `'50m'` for ms, `'200u'` for microseconds. |
| `send_raw(cmd, expect_response, delay)` | Sends a raw command string directly to the stage. |

#### Axis Limits

```python
AXIS_LIMITS = {
    'X': {'min': 0.0, 'max': 120.0},  # micrometers
    'Y': {'min': 0.0, 'max': 80.0},   # micrometers
    'Z': {'min': 0.0, 'max': 120.0},  # micrometers
}
```

#### Mode Selection Threshold

```python
EXTERNAL_CLOCK_THRESHOLD_MS = 0.1  # 100 µs
```

- **dt < 100 µs** → Normal mode (internal SPAD timing)
- **dt ≥ 100 µs** → External clock mode (piezo TTL signals drive SPAD)

---

### spad_array.py - SpadController Class

The `SpadController` class handles TCP socket communication with the SPAD array.

#### Connection Management

| Method | Description |
|--------|-------------|
| `connect()` | Establishes TCP connection to SPAD server. Reads and displays welcome message. Returns `True` on success. |
| `disconnect()` | Closes the TCP socket connection. |
| `is_connected()` | Returns `True` if the socket is active. |

#### Data Acquisition

| Method | Description |
|--------|-------------|
| `send_command(cmd)` | Sends a raw command string to SPAD (adds newline terminator). |
| `send_scan_command(dt_us, nz, nx, ny)` | Sends scan command in format: `C,dt_us,nz,nx,ny`. Note: `dt_us` is in microseconds. |
| `send_scan_command_external_clock(nz, nx, ny)` | Sends scan command for external clock mode: `C,0,nz,nx,ny`. The first `0` tells SPAD to use external TTL signals from the piezo stage instead of internal timing. |
| `send_measure_command(dt_ms)` | Sends single measurement command: `I,<dt_ms>`. Used in Manual mode for per-pixel acquisition. |
| `receive_data(timeout=60)` | Receives data from SPAD for scan commands (`C,`). Loops reading chunks until "DONE" or "ERROR" marker is found. Returns tuple `(data_string, status)`. Status is "OK", "ERROR", or "TIMEOUT". |
| `receive_measure_data(timeout=5)` | Receives data from SPAD for single measurement commands (`I,dt`). Does a **single `recv()` call** (matching the reference program `python_tcp_count.py` behavior) — no loop waiting for "DONE" marker, since `I,dt` responses may not include one. Returns tuple `(data_string, status)`. |

#### Data Management Functions

| Function | Description |
|----------|-------------|
| `find_next_run_number(save_dir)` | Finds the highest existing Run_XXX.txt number in directory and returns the next sequential number. |
| `save_spad_data(data, save_dir, run_number)` | Saves SPAD data to `Run_XXX.txt` file. Creates directory if needed. |
| `clean_spad_response(raw_data)` | Removes "DONE" or "ERROR" end marker from SPAD data. |
| `parse_scan_parameters(params)` | Calculates start/stop positions from center position and step count. Returns extended parameter dictionary. |

---

### main.py - Main Application

The main application provides a PyQt5-based GUI with three tabs: Stage Control, Confocal Scan, and Log.

#### GUI Components

**Tab 1: Stage Control**

| Component | Function |
|-----------|----------|
| Position Display (X, Y, Z) | Shows current stage position in micrometers, updated every 200ms |
| X+/X-, Y+/Y-, Z+/Z- buttons | Step movement in nanometer increments (value from Step [nm] field) |
| Step [nm] field | Step size in nanometers for relative movement |
| Absolute position fields (X, Y, Z) | Target positions for absolute movement |
| Move button | Executes absolute movement to specified coordinates |
| Set current button | Copies current position to absolute position fields |
| Center stage button | Moves all axes to center of their range |

**Tab 2: Confocal Scan**

| Component | Function |
|-----------|----------|
| Scan method | Selector: "Wavefront - Piezo is master" or "Manual - python is master" |
| X steps (nx), Y steps (ny), Z steps (nz) | Number of scan steps per axis (nz=0 for 2D scan) |
| X/Y step, Z step | Step size in nanometers |
| Dwell time (dt) | Integration time per pixel in **microseconds** (µs). Values < 100µs use internal SPAD timing; values ≥ 100µs use external clock mode. |
| Number of scans | How many times to repeat the same scan |
| X/Y/Z center [µm] | Center position of the scan area |
| Use current position as center | Sets center to current stage position |
| Save directory | Folder for storing scan data files |
| Start scan button | Begins the scan sequence |
| Progress bar | Shows scan progress |

**Tab 3: Log**

| Component | Function |
|-----------|----------|
| Log text area | Timestamped log messages from operations |
| Clear log button | Clears the log display |

**Preview Panel (right side)**

| Component | Function |
|-----------|----------|
| File browser | Selects Run_XXX.txt file for preview |
| Preview button | Displays scan data as heatmap image |
| Normalize checkbox | Normalizes image intensity to 0-1 range |

#### Status Bar

| Component | Function |
|-----------|----------|
| Piezo indicator (green/red LED) | Shows piezo connection status |
| SPAD indicator (green/red LED) | Shows SPAD connection status |
| Connect/Disconnect buttons | Toggle connections for piezo and SPAD |
| Port field | SPAD TCP port number (default: 9998) |

---

## Command Reference

### Piezo Stage Commands

| Command | Format | Description |
|---------|--------|-------------|
| `GET_X`, `GET_Y`, `GET_Z` | - | Read current position of specified axis |
| `MOVEX`, `MOVEY`, `MOVEZ` | `MOVE<axis> <pos>` | Move to absolute position (e.g., `MOVEX 100u`, `MOVEY 1257n`) |
| `MOVRX`, `MOVRY`, `MOVRZ` | `MOVR<axis> <delta>` | Relative movement (e.g., `MOVRX +100n`) |
| `STIME` | `STIME <time>` | Set time between waveform steps (e.g., `STIME 100m` = 100ms) |
| `SHTIM` | `SHTIM <time>` | Set illumination/shutter time (e.g., `SHTIM 50m` = 50ms) |
| `CHAIO` | `CHAIO <port>o<ttl>t` | Configure TTL I/O port (e.g., `CHAIO 1o1t` = output 1, trigger 1) |
| `SWF_X`, `SWF_Y`, `SWF_Z` | `SWF_<axis> <n> <start> <stop>` | Program waveform path (e.g., `SWF_X 8 50u 57u`) |
| `RUXYZ` | - | Start 3D scan (X, Y, Z axes) |
| `RUXY_` | - | Start 2D scan (X, Y axes only) |

**Time format suffixes:**
- `u` = microseconds (e.g., `200u` = 200 µs)
- `m` = milliseconds (e.g., `50m` = 50 ms)

**Position format suffixes:**
- `u` = micrometers (e.g., `100u` = 100 µm)
- `n` = nanometers (e.g., `1257n` = 1.257 µm)

### SPAD Commands

| Command | Format | Description |
|---------|--------|-------------|
| `C` | `C,<dt_us>,<nz>,<nx>,<ny>` | Start confocal scan with internal timing. `dt_us` in microseconds, `nz` = Z frames, `nx` = X elements, `ny` = Y elements. Used in **normal Wavefront mode** (dt < 100µs). |
| `C` | `C,0,<nz>,<nx>,<ny>` | Start confocal scan with **external dwell clock**. `0` tells SPAD to use external TTL signals from piezo stage instead of internal timing. Used in **external clock Wavefront mode** (dt ≥ 100µs). |
| `I` | `I,<dt_ms>` | Single photon measurement command. Sends one pixel measurement with dwell time `dt_ms` in milliseconds. Used in **Manual mode** (Python is master). |

**SPAD Response Format:**
- Lines of comma-separated values with coordinate prefix `(z,y,x),`
- Terminated by `DONE` (success) or `ERROR` (failure)
- Each line contains photon counts from multiple channels

---

## Scan Process Flow

The confocal scan automatically selects one of two modes based on dwell time (dt):

- **Normal mode** when dt < 100µs (internal SPAD timing)
- **External clock mode** when dt ≥ 100µs (uses external dwell clock from piezo stage)

### Normal Mode (dt < 100µs)

1. **Move to start position**
   - If nz > 0: Move Z to start_z, wait on target
   - Move Y to start_y, wait on target
   - Move X to start_x, wait on target

2. **Configure timing**
   - Send `STIME 2*dt` (time between steps = 2 × dwell time)
   - Send `SHTIM dt` (illumination/shutter time = dwell time)

3. **Configure TTL ports**
   - Send `CHAIO 1o1t` (X axis trigger on target)
   - Send `CHAIO 2o2t` (Y axis trigger on target)
   - Send `CHAIO 3o3t` (Z axis trigger on target, if nz > 0)

4. **Program waveforms**
   - Send `SWF_X nx start_x stop_x`
   - Send `SWF_Y ny start_y stop_y`
   - Send `SWF_Z nz start_z stop_z` (if nz > 0)

5. **Start SPAD acquisition**
   - Send `C,dt_us,nz,nx,ny` to SPAD (dt_us = dt × 1000)

6. **Start piezo scan**
   - Send `RUXYZ` (if nz > 0) or `RUXY_` (if nz = 0)

7. **Receive data**
   - Wait for SPAD to send data (loop until "DONE" marker)
   - Parse and clean response

8. **Save data**
   - Save photon counts to `Run_XXX.txt`
   - Save scan parameters to `Run_XXX_parameters.csv`

9. **Return to center**
   - Move X, Y, Z back to center position
   - Log scan completion time

### External Clock Mode (dt ≥ 100µs)

This mode is designed for longer dwell times (≥100µs). Instead of using the SPAD's internal timing, the piezo stage provides TTL signals that act as an external dwell clock. The signals are sent at the **start of each motion step** (before the stage fully stabilizes), meaning the pixel is being exposed while the stage is still settling from the previous step. Line signal starts acquisition of the first pixel and first dwell line signal stops it and starts the next one.

1. **Move to start position**
   - If nz > 0: Move Z to start_z, wait on target
   - Move Y to start_y, wait on target
   - Move X to start_x, wait on target

2. **Configure timing** (no SHTIM)
   - Send `STIME dt` (time between steps = dwell time directly, not 2×dt)

3. **Configure TTL ports** (start-of-motion mode)
   - Send `CHAIO 1o1s` (X axis signal at start of motion)
   - Send `CHAIO 2o2s` (Y axis signal at start of motion)
   - Send `CHAIO 3o3s` (Z axis signal at start of motion, if nz > 0)
   - The 's' mode fires the TTL line at the beginning of each step, which:
     - **Line pulse** triggers the start of exposure for the first point
     - **Dwell pulse** ends exposure of current point and starts exposure of next point

4. **Program waveforms**
   - Send `SWF_X nx start_x stop_x`
   - Send `SWF_Y ny start_y stop_y`
   - Send `SWF_Z nz start_z stop_z` (if nz > 0)

5. **Start SPAD acquisition** (external clock)
   - Send `C,0,nz,nx,ny` to SPAD (0 = use external dwell clock)

6. **Start piezo scan**
   - Send `RUXYZ` (if nz > 0) or `RUXY_` (if nz = 0)

7. **Receive data**
   - Wait for SPAD to send data (loop until "DONE" marker)
   - Parse and clean response

8. **Save data**
   - Save photon counts to `Run_XXX.txt`
   - Save scan parameters to `Run_XXX_parameters.csv`

9. **Return to center**
   - Move X, Y, Z back to center position
   - Log scan completion time

### Manual Mode (Python is master)

In this mode, Python explicitly controls each step of the scan. For each pixel, the software:
1. Moves the piezo stage to the target X/Y/Z position
2. Waits for the stage to stabilize (`wait_on_target`)
3. Sends a single `I,<dt_ms>` command to SPAD to acquire one pixel
4. Receives the data from SPAD using a **single `recv()` call** (matching the reference program `python_tcp_count.py`)
5. Repeats for the next pixel

The scan follows a serpentine (raster) pattern: even Y lines are scanned left-to-right, odd Y lines are scanned right-to-left, minimizing stage travel distance.

**Sequence:**

1. **Calculate positions**
   - Start position derived from center and step count: `start = center - (steps - 1) * step / 2`
   - For 3D scans (nz > 0), iterate through Z planes

2. **Move to start position**
   - If nz > 0: Move Z to start_z, wait on target
   - Move Y to start_y, wait on target
   - Move X to start_x, wait on target

3. **Log image dimensions** (e.g., "Image size: 8 (X) x 8 (Y) = 64 pixels")

4. **Iterate through all pixels** (Z → Y → X loops):
   - **Z loop** (if nz > 0): move to each Z plane, wait on target
   - **Y loop**: move to each Y line position (serpentine: forward/backward), wait on target
   - **X loop**: move to each X position within the line, wait on target, then:
     - Send `I,<dt>` command to SPAD (dt in milliseconds)
     - Receive SPAD data for that single pixel via `receive_measure_data()`
     - Format data with coordinate prefix `(z,y,x),` matching wavefront format
     - Append to collected data
   - After each X line, return to the start of the next line

5. **Save data** - concatenate all pixel data and save to `Run_XXX.txt`

6. **Save parameters** - save scan parameters to `Run_XXX_parameters.csv`

7. **Return to center** - move X, Y, Z back to center position

8. **Log scan completion time**

**Pros:** Full control over each pixel, suitable for debugging or custom scan patterns  
**Cons:** Significantly slower than Wavefront mode due to per-pixel stabilization waits  
**Use case:** Small scans, test measurements, or when precise per-pixel timing is needed

---

## Data File Format

### Run_XXX.txt

Each line contains data for one pixel in the format:
```
(z,y,x),ch0,ch1,ch2,ch3,ch4,ch5,ch6,ch7,ch8,ch9,ch10,ch11,ch12,ch13,ch14,ch15,ch16,ch17,ch18,ch19,ch20,ch21,ch22
```

Where:
- `(z,y,x)` = pixel coordinates (Z frame, Y line, X column)
- `ch0` through `ch22` = photon counts for each of the 23 SPAD channels

### Run_XXX_parameters.csv

CSV file with scan parameters:
```
Parameter,Value,Unit
Method,Wavefront,
Illumination time (dt),100.0,ms
X/Y step (dx),100.0,nm
Z step (dz),100.0,nm
X steps (nx),8,steps
Y steps (ny),8,steps
Z steps (nz),1,steps
X center (xo),60.0,µm
Y center (yo),40.0,µm
Z center (zo),60.0,µm
X start,56.5,µm
X stop,63.5,µm
Y start,36.5,µm
Y stop,43.5,µm
Z start,60.0,µm
Z stop,60.0,µm
```

---

## Usage

### Running the Application

```bash
python main.py
```

### Requirements

- Python 3.x
- PyQt5
- pyserial
- numpy
- matplotlib

### Typical Workflow

1. Click "Connect" for Piezo to establish serial connection
2. Click "Connect" for SPAD to establish socket connection
3. Use Stage Control tab to position the sample:
   - Use X+/X- buttons for fine positioning
   - Use absolute position fields for precise coordinates
   - Click "Center stage" to return to center
4. Switch to Confocal Scan tab:
   - Select scan method (Wavefront or Manual)
   - Set scan parameters (steps, step size, dwell time in µs)
   - Click "Use current position as center" or enter center manually
   - Select save directory
   - Click "Start scan"
5. View results in Preview tab after scan completes

---

## File Structure

```
new_control/
├── main.py           # Main application with GUI
├── mainApp.py        # Qt Designer generated UI code
├── mainApp.ui        # Qt Designer UI definition
├── stage.py          # Piezo stage controller
├── spad_array.py     # SPAD array controller
├── python_tcp_count.py  # Reference program from manufacturer
├── DOCUMENTATION.md  # This documentation
├── requirements.txt  # Python dependencies
├── run.vbs           # Windows launcher script
├── scan_data/        # Default data output directory (created)
│   ├── Run_001.txt
│   ├── Run_001_parameters.csv
│   └── ...
└── instructions/     # Manufacturer documentation
    ├── List of commands for the USB interface-Version July 2023.pdf
    └── SPAD23systemmanual.pdf
```

---

## Error Handling

- **Connection errors:** Displayed in log and status bar; connection indicators turn red
- **Position out of range:** Warning dialog when attempting to move beyond axis limits
- **Invalid input:** Warning dialog for non-numeric values in input fields
- **Scan errors:** Error message in log and popup dialog; scan continues to next iteration if multiple scans configured
- **SPAD timeout:** Configurable timeout for data reception; status marked as TIMEOUT
- **Manual mode data loss (fixed):** Previously, `I,dt` responses were read with a loop waiting for "DONE" marker (which never comes). Now uses `receive_measure_data()` with a single `recv()` call, matching the manufacturer's reference program.

---

## Thread Safety

Scan operations run in a separate `QThread` (`ScanWorker`) to keep the GUI responsive. The thread emits signals for:
- `progress(int, str)` - Scan progress updates
- `finished(bool, str)` - Scan completion status
- `log(str)` - Log messages

All stage and SPAD operations are executed sequentially within the scan thread.