# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Pidog is a Python control library and application suite for the **SunFounder PiDog (V2)** quadruped robot running on Raspberry Pi. It provides low-level hardware abstraction, kinematics/gait calculation, preset actions, sensor integration (IMU, ultrasonic, dual-touch, sound direction, camera), audio/TTS/STT, and LLM-powered voice assistant pipelines.

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

### Running Tests and Examples

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

# Servo calibration (Curses TUI)
sudo python3 examples/0_calibration.py
sudo python3 examples/servo_zeroing.py

# Basic functional tests
python3 basic_examples/1_pidog_init.py
python3 basic_examples/6_do_preset_actions.py

# High-level applications
python3 examples/3_patrol.py
python3 examples/20_voice_active_dog_gpt.py
python3 examples/20_voice_active_dog_doubao_cn.py
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
  - Multi-threaded execution: runs separate threads for legs, head, tail, RGB strip, and IMU tracking.
  - Coordinate system / Kinematics: `pose()` calculates leg inverse kinematics; `body_acc_calc()` performs IMU orientation matrix transformations using `numpy`.
- **`pidog/actions_dictionary.py` (`ActionDict`)**: Low-level servo angle keyframes for atomic poses (`stand`, `sit`, `lie`, `trot`, etc.).
- **`pidog/action_flow.py` (`ActionFlow`)**: High-level action sequencer matching user commands to movement primitives.
- **`pidog/walk.py` & `pidog/trot.py`**: Gait generators (8-phase crawl walk and 2-phase diagonal trot gait).
- **`pidog/preset_actions.py`**: Complex compound routines (`scratch`, `hand_shake`, `push_up`, `bark`, `pant`, `howling`, etc.).

### 3. Sensors & Peripherals

- **`sh3001.py`**: 6-DOF IMU driver (accelerometer + gyroscope) via I2C for attitude estimation and self-balancing.
- **`rgb_strip.py`**: 11-LED RGB light strip via WS2812/I2C (0x74) with animations (`breath`, `boom`, `bark`, `speak`, `listen`).
- **`sound_direction.py`**: 4-microphone circular array for 360° sound localization (~20° resolution).
- **`dual_touch.py`**: Dual capacitive touch sensors on head/back with stroke direction detection.
- **`stt.py`, `tts.py`, `llm.py`, `voice_assistant.py`**: Re-exports from `robot_hat` integrating STT, TTS engines (Piper, EdgeTTS), and LLMs (OpenAI, Ollama, Doubao).

### 4. OpenClaw Skill (`pidog-control/`)

Contains the CLI tools and daemon interface for controlling Pidog via JSON-RPC / IPC, enabling safe headless operation from external automation agents.
