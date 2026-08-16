# PIDOG 项目说明

> 这是 PIDOG V2（SunFounder 树莓派机器狗）项目的项目级 AI 协作说明，对应 Claude Code 的 `/init` 输出风格。
> 项目仓库根目录: `/Users/lixiaochao/Documents/workspace/pidog`

---

## 1. 项目一句话总结

这是一个面向 **SunFounder PiDog V2** 机器狗硬件的 **Python 控制库 + 示例 + CLI 工具集**，运行在 Raspberry Pi 上。
仓库同时包含一个本地 **OpenClaw skill (`pidog-control/`)**，用于以子进程守护 + JSON RPC 的方式从外部安全地操控狗的姿态、灯板与语音。

- **Python 包名**: `pidog` （`pyproject.toml` -> version `1.3.13`，根据 `pidog/version.py`）
- **作者**: SunFounder (`service@sunfounder.com`)
- **许可证**: GPL-3.0
- **主分支**: `git` 仓库，`main` 默认分支
- **目标平台**: Linux on Raspberry Pi（`Operating System :: POSIX :: Linux`）

---

## 2. 技术栈

| 类别 | 技术 |
| --- | --- |
| 语言 | Python 3.7+ |
| 核心数学 | `numpy`（IMU 姿态、坐标变换）|
| 硬件抽象 | SunFounder `robot-hat`（`Robot`、`Pin`、`Ultrasonic`、`Music`、`I2C`、`utils.reset_mcu`）|
| 摄像头 / 视觉 | SunFounder `vilib`（人脸检测、球检测、Web 预览）|
| 音频驱动 | ALSA / `i2samp.sh` 配置 HifiBerry-DAC 或 googlevoicehat-soundcard |
| 语音转文字 (STT) | 通过 `robot_hat.stt`（在 `pidog/stt.py` 内 re-export）|
| 语音合成 (TTS) | Piper / EdgeTTS / Espeak / Pico2Wave（ `pidog/tts.py` re-export）|
| LLM | OpenAI / Ollama / 字节豆包 三种适配器（ `pidog/llm.py` re-export）|
| 进程间通信 | `multiprocessing`（共享 Value / Lock）+ `threading`（动作腿/头/尾/灯/IMU 各一线程）|
| 串口 / I2C | `smbus`、`robot_hat.I2C` |
| 应用打包 | `pyproject.toml`（setuptools / wheel），入口 `pidog = pidog:__main__` |
| 安装脚本 | Bash（`bin/pidog_app_install.sh`、`bin/pidog_app`、`i2samp.sh`）|
| 外部 skill | OpenClaw skill `pidog-control`（带 `SKILL.md` / `HANDOFF.md` / 参考文档与 CLI）|

---

## 3. 项目结构

```
pidog/
├── README.md                      # 官方安装指引（git clone robot-hat / vilib / pidog + i2samp.sh）
├── DESCRIPTION.rst                # PyPI 短描述
├── LICENSE                        # GPLv3
├── MANIFEST.in                    # 打包清单
├── pyproject.toml                 # Python 包元数据
├── i2samp.sh                      # ALSA / audio card 配置脚本（自动安装 dep, 配置 asound）
├── pidog/                         # ★ 核心 Python 包
│   ├── __init__.py                # 暴露 Pidog、__version__、__main__
│   ├── version.py                 # __version__ = "1.3.13"
│   ├── pidog.py                   # ★ Pidog 主类（~970 行）
│   ├── preset_actions.py          # 高层预设动作（scratch / hand_shake / bark / pant / push_up / howling / sit_2_stand ...）
│   ├── actions_dictionary.py      # ActionDict：所有原子的 pose / 步态角度定义
│   ├── action_flow.py             # ActionFlow 动作编排（forward/backward/turn/bark/... 的语义层）
│   ├── walk.py                    # Walk 步态（8 SECTION, 4 STEP, LEG_ORDER=[1,0,4,0,2,0,3,0]）
│   ├── trot.py                    # Trot 对角小跑步态（2 SECTION, 3 STEP, 一次抬两条腿）
│   ├── rgb_strip.py               # RGBStrip（I2C 0x74, 11 颗灯, 模式: monochromatic/breath/boom/bark/speak/listen）
│   ├── sh3001.py                  # SH3001 IMU 驱动（加速度 / 陀螺仪原始数据 + 校准）
│   ├── sound_direction.py         # 4 麦克风阵列声源定位（360°/精度 20°）
│   ├── dual_touch.py              # 前后双触摸传感器 + 滑动方向识别（TouchStyle）
│   ├── llm.py / tts.py / stt.py / voice_assistant.py  # 自 `robot_hat` 透传
├── basic_examples/                # 13 个入门示例（零件级）
│   ├── 1_pidog_init.py
│   ├── 2_legs_control.py
│   ├── 3_head_control.py
│   ├── 4_tail_control.py
│   ├── 5_stop_actions.py
│   ├── 6_do_preset_actions.py
│   ├── 7_sound_effect.py
│   ├── 8_ultrasonic_read.py
│   ├── 9_rgb_control.py
│   ├── 10_imu_read.py
│   ├── 11_sound_direction_read.py
│   ├── 12_dual_touch_read.py
│   └── 13_camera_easy_use.py
├── examples/                      # 22 个进阶示例（场景级）
│   ├── 0_calibration.py           # 舵机校准（curses TUI）
│   ├── 1_wake_up.py
│   ├── 2_function_demonstration.py
│   ├── 3_patrol.py                # 巡逻 + 避障（ultrasonic 阈值）
│   ├── 4_response.py
│   ├── 5_rest.py
│   ├── 6_be_picked_up.py
│   ├── 7_face_track.py            # vilib 人脸 + 转头追踪 + 声源定位
│   ├── 8_pushup.py
│   ├── 9_howling.py
│   ├── 10_balance.py              # 键盘控制（含 Walk 步态坐标）
│   ├── 11_keyboard_control.py
│   ├── 12_app_control.py          # SunFounder 手机遥控
│   ├── 13_ball_track.py           # vilib 球追踪
│   ├── 18.online_llm_test.py
│   ├── 19_voice_active_dog_ollama.py
│   ├── 20_voice_active_dog_gpt.py
│   ├── 20_voice_active_dog_doubao_cn.py
│   ├── voice_active_dog.py        # ★ VoiceActiveDog 类（唤醒词 + 对话 + 动作触发）
│   ├── custom_actions.py
│   ├── servo_zeroing.py
│   └── curses_utils.py
├── test/                          # 单硬件测试脚本（运行在真机上做冒烟）
│   ├── servo_test.py
│   ├── imu_test.py
│   ├── rgb_strip_test.py
│   ├── ultrasonic_test.py / ultrasonic_iic_test.py
│   ├── sound_direction_test.py
│   ├── dual_touch_test.py
│   ├── tail.py / stand_test.py / power_test.py / angry_bark.py
│   └── cover_photo.py / test_close.py
├── sounds/                        # 12 个内置音频
│   ├── single_bark_1.mp3 / single_bark_2.mp3
│   ├── growl_1.mp3 / growl_2.mp3
│   ├── panting.mp3、snoring.mp3、howling.mp3
│   ├── confused_1/2/3.mp3
│   ├── woohoo.mp3、angry.wav
├── bin/
│   ├── pidog_app                  # systemd 风格的 start/stop/restart 服务脚本
│   └── pidog_app_install.sh       # 把 pidog_app 拷到 /usr/local/bin 并注册自启
└── pidog-control/                 # ★ 本地 OpenClaw skill
    ├── SKILL.md                   # skill 元信息 / 触发条件
    ├── HANDOFF.md                 # 给协作者的快速交接说明
    ├── scripts/
    │   ├── pidog_ctl.py           # CLI：status / safe-test / action / light / say / demo / start / stop / serve
    │   └── pidog_rgb_ctl.py       # 独立灯板 CLI
    └── references/
        ├── actions.md             # 稳定可暴露的动作子集
        ├── api-notes.md           # 来自上游仓库的 API 笔记
        ├── install-layout.md      # 标准 ~/pidog、~/robot-hat、~/vilib 安装布局
        ├── light-board.md         # rgb_strip.set_mode 的可用模式与已知坑
        └── troubleshooting.md     # Import / 运动 / 音频 / 灯光 故障排查清单
```

---

## 4. 核心架构

### 4.1 `Pidog` 主类（`pidog/pidog.py`）

单一入口类，几乎所有示例都从 `from pidog import Pidog` 开始。最重要的属性/方法：

**结构常量（毫米/舵机编号）**

- `LEG = 42`、`FOOT = 76`、`BODY_LENGTH = 117`、`BODY_WIDTH = 98`
- 8 条腿舵机脚位: `[2, 3, 7, 8, 0, 1, 10, 11]`（左前(LF) ×2 / 右前(RF) ×2 / 左后(LH) ×2 / 右后(RH) ×2）
- 头部 3 轴舵机: `[4, 6, 5]` = `[yaw, roll, pitch]`
- 尾巴: `[9]`
- 默认角速度: `HEAD_DPS=300`、`LEGS_DPS=428`、`TAIL_DPS=500`
- PID: `KP=0.033, KI=0, KD=0`

**核心方法**

| 方法 | 用途 |
| --- | --- |
| `__init__` | `utils.reset_mcu()`、绑定 legs / head / tail 三组 `Robot` 实例、启动 IMU、RGB、触感、超声波等后台线程 |
| `do_action(name, step_count, speed, pitch_comp)` | 入口动作调用（ `stand/sit/lie/wag_tail/forward/backward/turn_left/turn_right/push_up/stretch/...` 由 `ActionDict` 提供）|
| `legs_move / head_move / head_move_raw / tail_move` | 直接送目标角（支持非阻塞 `immediately=False` 入队） |
| `head_rpy_to_angle` | 从 RPY 角度转舵机实际角度（含 `HEAD_PITCH_OFFSET` 补偿）|
| `set_pose(x,y,z)`、`set_rpy(roll,pitch,yaw,pid=False)` | 通过身体坐标 + RPY 来设置姿态，内部 PID 用于 IMU 自平衡 |
| `legs_angle_calculation(coords)` | `@classmethod`，二维坐标 -> 8 个腿舵机角的反解 |
| `speak(name, volume)` / `speak_block(name, volume)` | 播放 `sounds/` 内 wav/mp3，含 pulseaudio 兜底 |
| `read_distance()` | 超声波距离读数（从共享 Value 拉取，独立进程线程写入）|
| `get_battery_voltage()` | 通过 robot_hat 读取电池电压 |
| `stop_and_lie(speed=85)` | 紧急停止并趴下 |
| `sensory_process_start()` | 启动 _ultrasonic_thread 守护（写共享 Value）|
| `close() / close_all_thread()` | 退出，需在脚本末尾调用以释放 MCU、RGB、IMU、舵机 |

> 五个常驻线程：`_legs_action_thread`, `_head_action_thread`, `_tail_action_thread`, `_rgb_strip_thread`, `_imu_thread`。外加一个 _ultrasonic_thread 单独进程。

### 4.2 动作 / 步态子系统

- `ActionDict`（`actions_dictionary.py`）—— 把 `'stand'`、`'sit'`、`'lie'`、`'forward'` 等名字翻译成 `(angles, 'legs'|'head'|'tail')` 元组。可通过 `set_height(20-95)` / `set_barycenter(-60..60)` 调站高与重心。
- `Walk`（`walk.py`）—— 标准 walk gait，每条腿依次抬；SECTION_COUNT=8，STEP_COUNT=6。
- `Trot`（`trot.py`）—— 对角小跑步态；对角两条腿一起抬，速度更快但稳定性稍弱。
- `preset_actions`（`preset_actions.py`）—— 26 个高层动作函数（`scratch`、`hand_shake`、`high_five`、`pant`、`bark`、`push_up`、`howling`、`attack_posture`、`lick_hand`、`sit_2_stand`、`shake_head`、`nod`、`think`、`alert`、`surprise`...），都接收 `my_dog` 实例并组合原子动作。
- `ActionFlow`（`action_flow.py`）—— 语义层：把自然语言短语（`"forward"`、`"turn left"`、`"bark harder"`、`"wag tail"`、`"lie"` 等）映射到 `dog_obj.do_action(...)`，并维护 Posture（`STAND/SIT/LIE`）与 `ActionStatus`（`STANDBY/THINK/ACTIONS/ACTIONS_DONE`）状态机，被 `VoiceActiveDog` 调用。

### 4.3 传感器驱动

| 模块 | 类 | 用途 |
| --- | --- | --- |
| `rgb_strip.py` | `RGBStrip(addr=0x74, nums=11)` | 11 灯 LED 灯板；模式 `monochromatic / breath / boom / bark / speak / listen`；预设颜色：white/red/yellow/green/blue/cyan/magenta/pink/black |
| `sh3001.py` | `Sh3001(I2C)` | 6 轴 IMU（acc + gyro），用于 IMU 自平衡（RPY 反馈）|
| `sound_direction.py` | `SoundDirection()` | 4 麦声源定位（0-355°，20° 精度）|
| `dual_touch.py` | `DualTouch()` | 前后触摸电容；`TouchStyle`: REAR / FRONT / REAR_TO_FRONT / FRONT_TO_REAR |

### 4.4 AI/语音层（薄封装）

- `pidog/llm.py`、`pidog/tts.py`、`pidog/stt.py`、`pidog/voice_assistant.py` 全部只是 `from robot_hat.X import *`。所有真实现都在 `robot-hat` 仓库里。
- 顶层 demo `examples/voice_active_dog.py` 定义了 `VoiceActiveDog` 类，组合：唤醒词（OpenWakeWord / oww） + STT + LLM + TTS + `ActionFlow` + ultrasonic/dual_touch 触发器 + 视觉（vilib）条件。
- 主入口脚本：
  - `examples/20_voice_active_dog_gpt.py`（OpenAI）
  - `examples/19_voice_active_dog_ollama.py`（本地 Ollama）
  - `examples/20_voice_active_dog_doubao_cn.py`（豆包，中文）

### 4.5 外部 CLI skill：`pidog-control/`

这是一个独立的 OpenClaw skill（不是 PyPI 包），目标是让"非 PIDOG 上下文的代理"也能安全地操控真狗：

- `pidog_ctl.py status` → 检查 `~/pidog`、`~/robot-hat`、`~/vilib` 是否就位，`import pidog` 是否成功。
- `pidog_ctl.py safe-test` → 在新机器上第一次跑动作前，先做一个低风险冒烟。
- `pidog_ctl.py action <name> [--hold]` → 暴露的稳定动作集：`stand / sit / lie / wag-tail / bark / forward / backward / turn-left / turn-right`，带 `--hold` 后驻守。
- `pidog_ctl.py light <mode> [--color]` → `off / breath / listen / boom / solid`（`solid` 文档化为实验性，映射到 `boom`）。
- `pidog_ctl.py say <sound>` → 播 sounds/ 内音频。
- `pidog_ctl.py start|stop|serve|send-action|send-light` → 后台守护进程模式（`scripts/` 内有用 multiprocessing + JSON RPC + PID 文件实现的 `PiDogController`）。
- `pidog_rgb_ctl.py` 是灯板专用入口，独立于主 controller，方便调试。

参考文档 `pidog-control/references/` 中明确记录了：
- `breath` 在真机上是短促的，不保证长动画；
- `solid` 当前不可靠，关灯时可能抛 `_rgb_strip_thread Exception: list index out of range`；
- `speak('bark')` 在某些环境找不到对应音频，所以 bark 默认用 `single_bark_1`。

---

## 5. 安装与运行

> 详细命令见 `README.md`，这里给最小可用步骤：

```bash
# 1) 系统依赖
sudo apt install git python3-pip python3-setuptools python3-smbus

# 2) 关联依赖（必须配套安装）
cd ~/
git clone -b 2.5.x --depth=1 https://github.com/sunfounder/robot-hat.git
cd robot-hat && sudo python3 install.py
cd ~/
git clone --depth=1 https://github.com/sunfounder/vilib.git
cd vilib && sudo python3 install.py

# 3) 本仓库
cd ~/
git clone --depth=1 https://github.com/sunfounder/pidog.git
sudo pip3 install ~/pidog --break-system-packages

# 4) 音频（声卡 / 麦克风）
sudo bash ~/pidog/i2samp.sh     # 配置 ALSA asound.conf
```

调试时常用：

```bash
# 重新安装（替换版）
sudo pip3 uninstall pidog -y && cd ~/pidog && sudo pip3 install . --break-system-packages --no-deps --no-build-isolation

# 主程序（GPT 版语音狗）
sudo python3 ~/pidog/examples/20_voice_active_dog_gpt.py

# 安装 systemd 风格的常驻 app
sudo bash ~/pidog/bin/pidog_app_install.sh
sudo pidog_app start   # / stop / restart
```

`secret.py`（OpenAI key 等）放在工作目录，`**secret*` 已被 `.gitignore` 屏蔽。

---

## 6. 常见开发任务速查

| 我想做的事 | 从哪里开始 |
| --- | --- |
| 改单个动作的角度 | 编辑 `pidog/actions_dictionary.py` 的 `@property` 块，或在 `pidog/preset_actions.py` 新写一个组合 |
| 调整身体高度/重心 | 用 `dog.actions_dict.set_height(60)` / `set_barycenter(-10)`（实例化后）|
| 改变步态手感 | 改 `pidog/walk.py`、`pidog/trot.py` 里的 `LEG_ORDER`、`LEG_STEP_HEIGHT`、`STEP_COUNT` |
| 加一种新灯效 | 在 `pidog/rgb_strip.py` 的 `STYLES` 列表加名字，写对应实现 |
| 加一种新传感器 | 仿 `sh3001.py` / `sound_direction.py` 写驱动类，在 `Pidog.__init__` 实例化，并在最终 `close()` 里释放 |
| 加一种新自然语言命令 | 在 `pidog/action_flow.py` 的 `ActionFlow.OPERATIONS` 字典加一项，规定 function / before / after / poseture |
| 让一条示例跑起来 | `basic_examples/` 里的脚本结构最简单，先从 `1_pidog_init.py` 开始 |
| 接 LLM 的语音狗 | 改 `examples/voice_active_dog.py` 的 `INSTRUCTIONS / NAME / WELCOME / WAKE_WORD`，再选一种 `examples/20_*`.py 作为入口 |

---

## 7. 给后续 Agent 的注意事项

- **必须在真机上跑**：所有驱动都依赖 `~/robot-hat` + I2C/SPI 硬件，纯桌面环境跑会 `ImportError`。
- **运行前确认姿势**：所有示例脚本开始都会 `Pidog()` 复位舵机到 lie 姿态；启动前请把狗放在桌面或地板上，并保持足够活动空间。
- **不要忘了 `dog.close()`**：MCU、RGB、IMU、舵机若不显式 close，可能让尾巴或腿带电发抖。
- **舵机脚位不要硬编码**：通过 `Pidog(... leg_pins=..., head_pins=..., tail_pin=...)` 注入；默认顺序见上。
- **声效文件可能在不同 release 间变动**：参考 `pidog-control/references/api-notes.md`，`speak('bark')` 不保证有效，遇到 `No sound found` 时换 `single_bark_1` 或用目录枚举。
- **i2samp.sh 会改 /boot/firmware/config.txt 与 /etc/asound.conf**：debug record 提示卸载后用 `--no-deps --no-build-isolation` 重装能解决大部分诡异 bug。
- **pidog-control/ skill 不在 PyPI**：它依赖 `import pidog`，要先完成主库安装。建议把它当成本仓库的附属 skill 而非独立产品。
- **licensing**：本仓库是 GPL-3.0；对外发布派生代码须沿用 GPL-3.0。
- **常见 kill 命令**：`<C-c>` -> 仍然看 `dog.close()` 块；紧急时调 `examples/3_patrol.py` 类似脚本里的 `my_dog.stop_and_lie()`。

---

## 8. 一句话 TL;DR 给下次会话

> 这是一个"在树莓派上用 Python 调 12 个舵机 + 多种传感器 + 可选 LLM 的四足机器狗"项目。改动作看 `actions_dictionary.py` 与 `preset_actions.py`；改步态看 `walk.py / trot.py`；改语音狗看 `examples/voice_active_dog.py` + `examples/20_*`.py；外部代理走 `pidog-control/scripts/pidog_ctl.py`。所有硬件默认走 `~/pidog`、`~/robot-hat`、`~/vilib` 三件套。
