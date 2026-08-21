# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Pidog is a Python control library and application suite for the **SunFounder PiDog (V2)** quadruped robot running on Raspberry Pi. It provides low-level hardware abstraction, kinematics/gait calculation, preset actions, sensor integration (IMU, ultrasonic, dual-touch, sound direction, camera), audio/TTS/STT, and LLM-powered voice assistant pipelines.

Beyond the upstream library, this repo adds:
- **`pidog/companion/`**: an embodied multimodal pet-companion subsystem (event bus, orchestrator, Xiaomi ASR/TTS + MiniMax VLM adapters, autonomous behavior engine) — see design spec in `docs/superpowers/specs/2026-08-16-embodied-pet-companion-design.md`.
- **`pidog-control/`**: an OpenClaw skill with a Unix-socket daemon (JSON-RPC) plus a FastAPI local web console (`pidog-control/web/`) for browser control over LAN.
- **`start.sh`**: all-in-one services manager (daemon + camera MJPEG stream + web console).

Design docs and plans live in `docs/` (Chinese); `PIDOG.md` is a detailed Chinese-language project map that complements this file.

## Development & Installation Commands

### Installation on Raspberry Pi

```bash
# System dependencies
sudo apt install git python3-pip python3-setuptools python3-smbus

# Required upstream libraries
cd ~/ && git clone -b 2.5.x --depth=1 https://github.com/sunfounder/robot-hat.git && cd robot-hat && sudo python3 install.py
cd ~/ && git clone --depth=1 https://github.com/sunfounder/vilib.git && cd vilib && sudo python3 install.py

# Install pidog in editable/development mode
cd ~/pidog && sudo pip3 install -e . --break-system-packages

# Audio setup (I2S amp / ALSA sound card configuration)
sudo bash i2samp.sh
```

### Quick Reinstall / Rebuild during Debugging

```bash
sudo pip3 uninstall pidog -y --break-system-packages && sudo pip3 install . --break-system-packages --no-deps --no-build-isolation
```

Note: `pyproject.toml` declares only `packages = ["pidog"]` — the `pidog.companion` subpackage is not listed. Editable installs work from the source tree, but be aware when building wheels or diagnosing import errors in non-editable installs.

### Running Tests

Hardware-dependent tests (must be run on Raspberry Pi with Robot HAT attached):

```bash
# Hardware verification tests
python3 test/stand_test.py
python3 test/servo_test.py
python3 test/imu_test.py
python3 test/ultrasonic_test.py
python3 test/rgb_strip_test.py
python3 test/dual_touch_test.py
python3 test/sound_direction_test.py
```

Companion subsystem unit tests (pure software, mock-based, no hardware needed):

```bash
# All companion tests
python3 -m unittest discover -s test/companion -v

# Single test module
python3 -m unittest test.companion.test_orchestrator -v

# Cloud service connectivity check (real API calls; requires .env with keys)
python3 test_companion_cloud_services.py
```

### Servo Calibration

```bash
sudo python3 examples/0_calibration.py   # curses TUI
sudo python3 examples/servo_zeroing.py
```

### Running the High-Level Applications

```bash
python3 examples/3_patrol.py
python3 examples/20_voice_active_dog_gpt.py
python3 examples/20_voice_active_dog_doubao_cn.py
python3 examples/21_embodied_pet_companion.py   # companion subsystem entry point
```

### Services Manager (daemon + camera + web console)

```bash
./start.sh start    # starts pidog-control daemon, vilib MJPEG stream (:9000), web console (:8000)
./start.sh status
./start.sh stop
./start.sh restart
```

Web console can also be run standalone (see `pidog-control/web/README.md`):

```bash
pip3 install 'fastapi>=0.110' 'uvicorn[standard]>=0.27' pydantic
python3 pidog-control/scripts/pidog_ctl.py start   # ensure daemon is up
cd pidog-control/web && python3 web_server.py      # serves http://pidog.local:8000/ (or fallback IP)
```

## Architecture & Hardware Abstraction

### 1. Servo & Pin Layout

Pidog uses 12 PWM channels via SunFounder Robot HAT:
- **8 Leg Servos** (pins `[2, 3, 7, 8, 0, 1, 10, 11]`):
  - Left Front (`[2, 3]`), Right Front (`[7, 8]`)
  - Left Hind (`[0, 1]`), Right Hind (`[10, 11]`)
- **3 Head Servos** (pins `[4, 6, 5]`): `[yaw, roll, pitch]`
- **1 Tail Servo** (pin `[9]`): horizontal swing

Configuration and calibration offsets are stored at `~/.config/pidog/pidog.conf`.

### 2. Core Python Architecture (`pidog/`)

- **`pidog/pidog.py` (`Pidog` class)**: Central controller coordinating inverse kinematics, background worker threads, and hardware interfaces.
  - Multi-threaded execution: runs separate threads for legs, head, tail, RGB strip, and IMU tracking, plus an ultrasonic process writing a shared `Value`.
  - Coordinate system / Kinematics: `pose()` calculates leg inverse kinematics; `body_acc_calc()` performs IMU orientation matrix transformations using `numpy`.
  - Important lifecycle rule: scripts must call `dog.close()` at the end, or servos/RGB/IMU stay powered (tail/legs may jitter). `stop_and_lie()` is the emergency stop.
- **`pidog/actions_dictionary.py` (`ActionDict`)**: Low-level servo angle keyframes for atomic poses (`stand`, `sit`, `lie`, `trot`, etc.). `set_height(20-95)` / `set_barycenter(-60..60)` adjust stance.
- **`pidog/action_flow.py` (`ActionFlow`)**: High-level action sequencer matching user commands to movement primitives; maintains a Posture (STAND/SIT/LIE) and ActionStatus state machine. Used by `VoiceActiveDog` (`examples/voice_active_dog.py`).
- **`pidog/walk.py` & `pidog/trot.py`**: Gait generators (8-phase crawl walk and 2-phase diagonal trot gait).
- **`pidog/preset_actions.py`**: Complex compound routines (`scratch`, `hand_shake`, `push_up`, `bark`, `pant`, `howling`, etc.).
- **`pidog/stt.py`, `tts.py`, `llm.py`, `voice_assistant.py`**: Thin re-exports from `robot_hat` — the real implementations live in the robot-hat repo.

### 3. Sensors & Peripherals

- **`sh3001.py`**: 6-DOF IMU driver (accelerometer + gyroscope) via I2C for attitude estimation and self-balancing.
- **`rgb_strip.py`**: 11-LED RGB light strip via I2C (0x74) with animations (`breath`, `boom`, `bark`, `speak`, `listen`). Known quirk: `solid` is unreliable; `speak('bark')` may fail with "No sound found" — use `speak('single_bark_1')`.
- **`sound_direction.py`**: 4-microphone circular array for 360° sound localization (~20° resolution).
- **`dual_touch.py`**: Dual capacitive touch sensors on head/back with stroke direction detection.

### 4. Embodied Companion Subsystem (`pidog/companion/`)

Multimodal pet-companion stack designed for Pi Zero 2W constraints (512MB RAM). Entry point: `examples/21_embodied_pet_companion.py`.

- **`core/`**:
  - `event_bus.py`: lightweight pub-sub bus; all components communicate via events (e.g. `sensor.touch`, audio triggers).
  - `orchestrator.py` (`CompanionOrchestrator`): central coordinator — subscribes to audio/sensor events, detects visual intent via keyword regex, calls VLM with conversation history, parses semantic tags from model output (`[action:wag_tail]`, `[emotion:happy]`, `[sound:howling]`, `[owner_emotion:sad]`), dispatches cleaned text to TTS and expressions to the actuator.
  - `context.py`: conversation history; `sensor_context.py`: shared sensor snapshot.
- **`adapters/`**: pluggable AI providers behind `BaseASR` / `BaseTTS` / `BaseVLM` abstract classes, created via `AdapterFactory` from config. Implementations: `asr_xiaomi.py`, `tts_xiaomi.py` (MiMo platform), `vlm_minimax.py`. Add new providers by subclassing the base and registering in the factory.
- **`behavior/`**: autonomous pet behavior — `state.py` (`PetState`: energy/boredom/intimacy/mood), `behavior_engine.py` (spontaneous behaviors with priority P0 midair-protection > P1 voice interaction > P2 touch reflexes > P3 idle behaviors), `approach.py` (walk toward sound source/owner), `sound_library.py` (named sound effects mapping).
- **`hardware/`**: worker wrappers — `sensor_worker.py` (low-frequency polling, event dispatch), `audio_worker.py` (mic capture with VAD; muted during audio playback), `clap_detector.py`, `camera_helper.py` (on-demand single-frame JPEG capture, no continuous streaming), `emotion_expressor.py` (coordinated actions + RGB + sounds).

Configuration comes from `.env` at repo root (see `.env.example` for all keys: `MIMO_API_KEY`, `MINIMAX_API_KEY`, `MINIMAX_MODEL`, `XIAOMI_TTS_VOICE`, `AUDIO_INPUT_DEVICE`, battery thresholds, etc.). `config.py` includes a dependency-free `load_dotenv()` that searches cwd, repo root, and home dir, and never overrides existing environment variables.

### 5. pidog-control Skill & Web Console (`pidog-control/`)

CLI tools and daemon for controlling Pidog via JSON-RPC over a Unix socket (`~/.openclaw/pidog-control/controller.sock`), enabling safe headless operation from external automation agents. Key invariant: **only the daemon process imports `pidog.Pidog`** — the web console and other clients talk to it via the socket, avoiding multi-process hardware contention.

- `scripts/pidog_ctl.py`: CLI — `status` / `safe-test` / `action <name> [--hold]` / `light <mode> [--color]` / `say <sound>` / `start` / `stop` / `serve`.
- `scripts/pidog_rgb_ctl.py`: standalone RGB CLI that skips full `Pidog()` init.
- `web/`: FastAPI web console — REST API (`/api/*`), WebSocket status push (`/ws/status`), static vanilla-JS SPA, mDNS registration (`pidog.local:8000`), config in `web_server.toml`. `daemon_client.py` wraps the socket protocol; `status_poller.py` fans status out to WebSocket clients.
- `references/`: confirmed API notes, action whitelist, light-board quirks, troubleshooting. Read these before inventing APIs.

Detailed design doc: `docs/plans/local-web-console.md`.

## Operational Notes

- All hardware drivers require the Pi + Robot HAT; a desktop environment will fail at `import` with `ImportError`.
- Every example starts by resetting servos to `lie` posture on `Pidog()` init — place the robot on a stable surface with room to move before running anything.
- Avoid running hardware-controlling processes simultaneously (e.g. web console daemon and `examples/21_embodied_pet_companion.py`); stop one before starting another.
- Hardware examples place dog on floor; `Ctrl-C` handlers still run `dog.close()`; in emergencies use `stop_and_lie()`.
- This repo is GPL-3.0; derivative distributions must keep the license.
