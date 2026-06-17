# REACHER API Setup Guide

This guide walks you through installing and running the REACHER API server and monitor dashboard on Linux, macOS, and Windows. It covers everything from a fresh machine to a fully running system.

---

## Quick Install

If you already have Python 3.10+ installed, the fastest path is:

```bash
# Install (isolated — no system packages affected)
pipx install reacher2p

# Run
reacher
```

### Raspberry Pi / Headless Linux

A single command handles Python, serial permissions, systemd services, and firewall:

```bash
curl -fsSL https://raw.githubusercontent.com/otis-lab-musc/reacher/main/scripts/install.sh | bash
```

After running, the REACHER API starts on boot via systemd. The rest of this guide covers manual setup for when you need more control.

---

## What You're Setting Up

The **REACHER API** is a Python application with two parts:

- **`reacher`** -- The API server. This is the brain of the system. It talks to Arduino hardware over USB, manages experiment sessions, and serves data to connected clients (the Labrynth GUI, CLI, or any HTTP client).

- **`reacher-monitor`** -- A live terminal dashboard. It shows the server's status, the current pairing code, and any active experiment sessions. This is what you'll see on a dedicated host machine's screen.

Together, they turn a computer (a Raspberry Pi, laptop, or desktop) into a REACHER host that can be controlled remotely from the Labrynth interface.

---

## Prerequisites

### Python

REACHER requires **Python 3.10 or newer**. Check your version:

```
python3 --version
```

If Python is not installed or the version is too old, follow the instructions for your platform below.

**Linux (Debian/Ubuntu/Raspberry Pi OS/DietPi):**
```
sudo apt update
sudo apt install python3 python3-pip python3-venv
```

**macOS:**

Install [Homebrew](https://brew.sh) if you don't have it, then:
```
brew install python@3.12
```

**Windows:**

Download and run the installer from [python.org](https://www.python.org/downloads/). During installation, **check the box that says "Add Python to PATH"**.

### avrdude (for firmware uploads)

If you plan to flash firmware onto Arduino boards from this machine, you also need `avrdude`:

**Linux:**
```
sudo apt install avrdude
```

**macOS:**
```
brew install avrdude
```

**Windows:**

`avrdude` is bundled with the [Arduino IDE](https://www.arduino.cc/en/software). If you have the IDE installed, it's already on your system. Otherwise, download it separately from the [avrdude releases page](https://github.com/avrdudes/avrdude/releases).

---

## Step 1: Get the Source Code

Download or clone the REACHER repository:

```
git clone https://github.com/thejoshbq/phoxel-workbench.git
cd phoxel-workbench/reacher
```

If you don't have `git`, you can download the repository as a ZIP file from GitHub and extract it.

---

## Step 2: Install REACHER

From inside the `reacher/` directory, install the package:

**Linux / macOS:**
```
pip3 install -e .
```

**Windows (Command Prompt or PowerShell):**
```
pip install -e .
```

> **Note for Raspberry Pi OS, DietPi, or Ubuntu 23.04+:** These systems use "externally managed" Python (PEP 668). You'll need to add the `--break-system-packages` flag:
> ```
> pip3 install --break-system-packages -e .
> ```
> Alternatively, use a virtual environment (see the [Virtual Environment](#using-a-virtual-environment-optional) section below).

This installs two commands:
- `reacher` -- starts the API server
- `reacher-monitor` -- starts the monitor dashboard

Verify the installation:
```
reacher --help
reacher-monitor --help
```

If the commands are not found, your Python scripts directory may not be on your system PATH. See [Troubleshooting](#commands-not-found-after-install).

---

## Step 3: Run the Server

Start the REACHER API server:

```
reacher
```

On the first run, the server will:
1. Generate a unique device ID (saved to `~/.reacher/device_id`)
2. Generate an API key (saved to `~/.reacher/api_key`) and print it to the terminal
3. Start listening on port **6229**
4. Begin advertising itself on the local network for discovery
5. Print a **pairing code** (6 digits, rotates every 5 minutes) if no frontend is bundled

You should see output like:
```
  +--------------------------------------+
  |  PAIRING CODE :  482-091             |
  |  Rotates every 5 minutes             |
  +--------------------------------------+

  API key      : a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4
```

Leave this terminal running. The server needs to stay active to accept connections.

---

## Step 4: Run the Monitor

Open a **second terminal** on the same machine and run:

```
reacher-monitor
```

The monitor connects to the local server automatically and displays:
- **Server status** (online/offline)
- **Pairing code** (the code remote users enter to pair with this machine)
- **Active sessions** (any running experiments)

Press `Ctrl+C` to stop the monitor. The server continues running independently.

### Connecting to a remote server

If the API server is running on a different machine (e.g., a Pi at `192.168.1.50`), you can point the monitor at it:

```
reacher-monitor --url http://192.168.1.50:6229
```

You'll also need the API key from that machine. Either copy the `~/.reacher/api_key` file or set the environment variable:

```
export REACHER_API_KEY=<the key from the remote machine>
reacher-monitor --url http://192.168.1.50:6229
```

---

## Step 5: Pair with Labrynth (Optional)

If you're using the Labrynth GUI on a separate machine to control this one:

1. Open Labrynth on the remote machine
2. Go to the **Machines** panel
3. Either:
   - **Automatic discovery:** Wait for the host to appear in the discovered devices list (may take up to 30 seconds)
   - **Manual pairing:** Click "Add by IP", enter the host's IP address and port (e.g., `http://192.168.1.50:6229`), then enter the 6-digit pairing code shown on the host's terminal or monitor

Once paired, the host is saved and will reconnect automatically in future sessions.

---

## Platform-Specific: Auto-Start on Boot

The sections below explain how to make the REACHER server and monitor start automatically when the machine boots up. This is optional -- you can always start them manually.

---

### Linux (Raspberry Pi, Ubuntu, Debian)

Linux uses **systemd** to manage services that start at boot.

#### 1. Set up permissions

Your user account needs access to serial ports (for Arduino communication) and the terminal (for the monitor):

```
sudo usermod -a -G dialout $USER
sudo usermod -a -G tty $USER
```

**Log out and back in** for the group changes to take effect.

#### 2. Install the service files

REACHER includes pre-written systemd service files. Copy them to the system directory:

```
sudo cp systemd/reacher@.service /etc/systemd/system/
sudo cp systemd/reacher-monitor@.service /etc/systemd/system/
sudo systemctl daemon-reload
```

The `@` in the filename means these are *template* services -- you specify which user account to run them under when you enable them.

#### 3. Enable the services

Replace `$USER` with your actual username (e.g., `pi`, `root`, `josh`):

```
sudo systemctl enable reacher@$USER
sudo systemctl enable reacher-monitor@$USER
```

This tells systemd to start both services automatically at boot. The monitor service depends on the API server and will start it automatically.

#### 4. Start now (without rebooting)

```
sudo systemctl start reacher@$USER
sudo systemctl start reacher-monitor@$USER
```

#### 5. Check status

```
sudo systemctl status reacher@$USER
sudo systemctl status reacher-monitor@$USER
```

Both should show **active (running)**. If something went wrong, check the logs:

```
journalctl -u reacher@$USER -f
```

#### 6. (Optional) Display the monitor on the physical screen

If this is a headless device (like a Raspberry Pi) connected to a display, you can make the monitor take over the physical screen (tty1) instead of showing a login prompt:

```
sudo systemctl mask getty@tty1
```

This prevents the login prompt from appearing on tty1. The monitor service is already configured to display there. After a reboot, the monitor dashboard will appear on the connected display automatically.

To undo this later:
```
sudo systemctl unmask getty@tty1
```

#### Quick reference

| Action | Command |
|--------|---------|
| Start server | `sudo systemctl start reacher@$USER` |
| Stop server | `sudo systemctl stop reacher@$USER` |
| Restart server | `sudo systemctl restart reacher@$USER` |
| View logs | `journalctl -u reacher@$USER -f` |
| Disable auto-start | `sudo systemctl disable reacher@$USER` |

---

### macOS

macOS uses **Launch Agents** to start programs at login.

#### 1. Create the API server launch agent

Create the file `~/Library/LaunchAgents/com.reacher.api.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.reacher.api</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/local/bin/reacher</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/tmp/reacher-api.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/reacher-api.log</string>
</dict>
</plist>
```

> **Important:** The path `/usr/local/bin/reacher` assumes a Homebrew Python installation. If you installed Python differently, find the correct path with `which reacher` and update the plist accordingly.

#### 2. Create the monitor launch agent

Create the file `~/Library/LaunchAgents/com.reacher.monitor.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.reacher.monitor</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/local/bin/reacher-monitor</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/tmp/reacher-monitor.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/reacher-monitor.log</string>
</dict>
</plist>
```

#### 3. Load the agents

```
launchctl load ~/Library/LaunchAgents/com.reacher.api.plist
launchctl load ~/Library/LaunchAgents/com.reacher.monitor.plist
```

Both will start immediately and restart automatically if they crash.

#### Quick reference

| Action | Command |
|--------|---------|
| Start | `launchctl load ~/Library/LaunchAgents/com.reacher.api.plist` |
| Stop | `launchctl unload ~/Library/LaunchAgents/com.reacher.api.plist` |
| View logs | `tail -f /tmp/reacher-api.log` |
| Remove auto-start | Delete the `.plist` files from `~/Library/LaunchAgents/` |

---

### Windows

Windows uses **Task Scheduler** to start programs at login.

#### Option A: Task Scheduler (recommended)

1. Open **Task Scheduler** (search for it in the Start menu)
2. Click **Create Basic Task**
3. Name it `REACHER API Server`
4. Trigger: **When I log on**
5. Action: **Start a program**
6. Program/script: `reacher`
   - If that doesn't work, use the full path. Find it with: `where reacher` in a command prompt
7. Click **Finish**

Repeat for the monitor:
1. Create another basic task named `REACHER Monitor`
2. Same trigger: **When I log on**
3. Program/script: `reacher-monitor`

#### Option B: Startup folder (simpler, less control)

1. Press `Win+R`, type `shell:startup`, press Enter
2. In the folder that opens, create a file called `reacher.bat` with this content:

```bat
@echo off
start "" reacher
timeout /t 5 /nobreak >nul
start "" reacher-monitor
```

3. Save the file. Both programs will start in separate windows every time you log in.

#### Quick reference

| Action | How |
|--------|-----|
| Start manually | Open a terminal, run `reacher` and `reacher-monitor` |
| Stop | Close the terminal windows, or press `Ctrl+C` |
| View API key | `type %USERPROFILE%\.reacher\api_key` |
| Disable auto-start | Delete the task in Task Scheduler, or delete the `.bat` from the startup folder |

---

## Configuration

### Environment Variables

These are optional. The defaults work for most setups.

| Variable | Default | What it does |
|----------|---------|-------------|
| `REACHER_PORT` | `6229` | The port the server listens on |
| `REACHER_HOST` | `0.0.0.0` | The network interface to bind to. `0.0.0.0` means all interfaces (recommended for remote access) |
| `REACHER_API_KEY` | Auto-generated | Override the API key instead of using the auto-generated one |
| `REACHER_BROKER_URL` | Not set | URL of a REACHER broker for networks where automatic discovery doesn't work (e.g., university networks) |
| `REACHER_CORS_ORIGINS` | Not set | Extra allowed origins for cross-origin requests (comma-separated) |

To set an environment variable:

**Linux / macOS:**
```
export REACHER_PORT=8080
reacher
```

**Windows (Command Prompt):**
```
set REACHER_PORT=8080
reacher
```

**Windows (PowerShell):**
```
$env:REACHER_PORT = "8080"
reacher
```

For systemd services on Linux, add environment variables to the service file's `Environment` line, or create an environment file and reference it with `EnvironmentFile=`.

### Files Created by REACHER

REACHER creates a few files in your home directory:

| File | Purpose |
|------|---------|
| `~/.reacher/device_id` | Unique identifier for this machine |
| `~/.reacher/api_key` | API authentication key (keep this private) |
| `~/.reacher/paired` | Flag file indicating this device has been paired |
| `~/.reacher/machines.json` | Registry of paired remote machines (on controller machines) |
| `~/REACHER/LOG/` | Experiment logs (one folder per session) |
| `~/REACHER/DATA/` | Exported experiment data |

On Windows, `~` refers to `C:\Users\YourUsername`.

---

## Using a Virtual Environment (Optional)

A virtual environment keeps REACHER's dependencies separate from the rest of your system. This is recommended on systems with "externally managed" Python (Ubuntu 23.04+, Fedora 38+, Raspberry Pi OS Bookworm+).

```
cd phoxel-workbench/reacher

# Create the virtual environment
python3 -m venv .venv

# Activate it
source .venv/bin/activate        # Linux / macOS
# .venv\Scripts\activate          # Windows

# Install REACHER inside the venv
pip install -e .

# Run (while activated)
reacher
```

When using a virtual environment with systemd, update the service file's `ExecStart` to use the full path to the venv's binary:

```
ExecStart=/path/to/phoxel-workbench/reacher/.venv/bin/reacher
```

---

## Troubleshooting

### Commands not found after install

If `reacher` or `reacher-monitor` is not recognized after installation, the Python scripts directory is not on your PATH.

**Find where pip installed the scripts:**
```
python3 -m site --user-base
```

The scripts are in the `bin/` subdirectory of that path (or `Scripts/` on Windows). Add it to your PATH:

**Linux / macOS** (add to `~/.bashrc` or `~/.zshrc`):
```
export PATH="$HOME/.local/bin:$PATH"
```

**Windows:** Add `%APPDATA%\Python\Python3x\Scripts` to your system PATH via System Settings > Environment Variables.

### "Externally managed environment" error

If `pip install` fails with an "externally managed environment" error:

- **Quick fix:** Add `--break-system-packages` to the pip command
- **Better fix:** Use a [virtual environment](#using-a-virtual-environment-optional)

### Server won't start (port already in use)

If the server says the port is already in use, either another REACHER instance is running or another program is using port 6229.

**Find what's using the port:**

Linux/macOS: `lsof -i :6229`

Windows: `netstat -ano | findstr 6229`

Kill the process or change the port with `REACHER_PORT=<another port>`.

### Monitor shows "OFFLINE"

The monitor can't reach the API server. Check that:
1. The server is running (`reacher` in another terminal)
2. The URL is correct (default: `http://localhost:6229`)
3. The port isn't blocked by a firewall

### Systemd service fails with "Exec format error"

The console script file is empty or corrupted. Reinstall:

```
pip3 install --break-system-packages --force-reinstall -e .
```

Then verify the scripts exist and are non-empty:
```
ls -la $(which reacher) $(which reacher-monitor)
```

### Serial port permission denied (Linux)

If the server can't access Arduino serial ports:

```
sudo usermod -a -G dialout $USER
```

Log out and back in for the change to take effect.

### Discovery doesn't find devices on the network

Automatic discovery relies on either mDNS multicast or subnet scanning. If neither finds your device:

1. **Check connectivity:** Can you reach the device directly? Try `curl http://<device-ip>:6229/health`
2. **University / managed networks:** Multicast is often blocked. Set `REACHER_BROKER_URL` on the peripheral device to point it at your controller machine
3. **Different subnets:** If the machines are on different network segments, use manual pairing (IP address + pairing code)
4. **Firewall:** Ensure port 6229 is open on both machines

---

## Uninstalling

```
pip3 uninstall reacher
```

To also remove configuration files:

**Linux / macOS:**
```
rm -rf ~/.reacher
```

**Windows:**
```
rmdir /s %USERPROFILE%\.reacher
```

To remove systemd services (Linux):
```
sudo systemctl disable reacher@$USER reacher-monitor@$USER
sudo rm /etc/systemd/system/reacher@.service /etc/systemd/system/reacher-monitor@.service
sudo systemctl daemon-reload
```
