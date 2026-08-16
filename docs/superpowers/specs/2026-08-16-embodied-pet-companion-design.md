# Pidog 具身多模态智能伴侣与自主宠物系统设计规范 (Spec)

## 1. 概述与目标

本项目基于 **SunFounder PiDog V2** 硬件平台（主控为 Raspberry Pi Zero 2W，具备 512MB RAM），对现有 Python 控制库进行系统级扩展，构建一套**多模态具身语音伴侣 + 自主宠物生态系统**。

### 核心设计目标
1. **多模态具身对话**：集成外部云端 **MiniMax M3** 多模态大模型，并在识别到视觉意图时按需抓取摄像头图像，结合 **小米 ASR / TTS** 实现低延迟拟真小狗语音对话与动作协同。
2. **模块化与可插拔设计**：抽象 `BaseASR`、`BaseTTS`、`BaseVLM` 标准适配器，便于后续无缝替换其他服务商（如 OpenAI、火山、阿里云等）。
3. **物理与多模态交互唤醒**：通过触摸（摸头/背）、声源定位（拍手/大声）和超声波靠近进行唤醒与交互，降低 Pi Zero 2W 常驻运行语音唤醒引擎的负载。
4. **自主宠物生理与行为系统**：构建轻量级状态机（精力、无聊度、亲密度、情绪），支持无交互时的自发行为与即时传感器反射（摇尾、伸懒腰、悬空保护）。

---

## 2. 总体架构与目录结构

```
pidog/
├── companion/                      # 扩展的核心模块包
│   ├── __init__.py
│   ├── config.py                   # 全局配置 (API 密钥、行为阈值、传感器参数)
│   ├── core/
│   │   ├── event_bus.py            # 轻量发布-订阅事件总线
│   │   ├── orchestrator.py         # 核心会话调度器与优先级管理
│   │   └── context.py              # 对话历史与上下文管理
│   ├── adapters/                   # AI 服务适配器
│   │   ├── __init__.py
│   │   ├── base.py                 # BaseASR, BaseTTS, BaseVLM 抽象基类
│   │   ├── asr_xiaomi.py           # 小米 ASR 适配器实现
│   │   ├── tts_xiaomi.py           # 小米 TTS 适配器实现
│   │   ├── vlm_minimax.py          # MiniMax M3 多模态适配器实现
│   │   └── factory.py              # Adapter 工厂类
│   ├── behavior/                   # 自主宠物行为系统
│   │   ├── __init__.py
│   │   ├── state.py                # 虚拟生理状态模型 (PetState)
│   │   ├── behavior_engine.py      # 自发行为树与动作调度
│   │   └── reflexes.py             # 硬件传感器即时反射逻辑
│   └── hardware/                   # 硬件服务包装
│       ├── __init__.py
│       ├── sensor_worker.py        # 传感器低频轮询与事件派发 (Touch/IMU/Ultrasonic/Mic)
│       ├── camera_helper.py        # 按需抓帧与 JPEG 压缩
│       ├── emotion_expressor.py    # 动作 + 11颗RGB灯板 + 音效协同表现器
│       └── audio_player.py         # 非阻塞 ALSA 音频流播放管理
└── examples/
    └── 21_embodied_pet_companion.py # 场景演示与入口
```

---

## 3. 详细设计

### 3.1 AI Provider 抽象层 (`companion/adapters/`)

统一所有外部 AI 服务接口，业务逻辑不直接依赖任何具体 SDK。

- **`BaseASR`**:
  - `recognize(audio_data: bytes, sample_rate: int = 16000) -> str`
- **`BaseTTS`**:
  - `synthesize(text: str, voice_params: Optional[dict] = None) -> bytes`
  - `synthesize_stream(text: str) -> Generator[bytes, None, None]`
- **`BaseVLM`**:
  - `chat(messages: list, image_bytes: Optional[bytes] = None, system_prompt: Optional[str] = None) -> str`
- **`MiniMaxVLM`**:
  - 支持将文本与 base64 编码的 JPEG 图像组装为 MiniMax M3 请求。
- **`XiaomiASR` / `XiaomiTTS`**:
  - 封装小米开放平台语音识别与合成 API。

---

### 3.2 具身对话与结构化动作协议

#### System Prompt 指令格式
大模型在输出口语回复的同时，携带动作与情绪标签：
```text
你是一只聪明、可爱、忠诚的机器狗 Pidog。
回复必须带有小狗的亲昵语气。
你可以使用特殊标签控制身体动作与表情：
动作标签: [action:wag_tail], [action:bark], [action:nod], [action:shake_head], [action:stretch], [action:sit], [action:lie], [action:scratch]
情绪标签: [emotion:happy], [emotion:curious], [emotion:confused], [emotion:angry], [emotion:sleepy]
例如：[emotion:happy][action:wag_tail] 汪！主人我看到你啦，今天过得怎么样？
```

#### 主动视觉意图识别
- 在用户提问中通过正则/关键词检测视觉意图（如“看”、“这是什么”、“我手里”、“颜色”等）。
- 命中时调用 `camera_helper` 抓取单帧 640x480 图像送入 MiniMax M3。

---

### 3.3 自主宠物行为系统 (`companion/behavior/`)

#### 1. 状态模型 (`PetState`)
- `energy` (0~100): 随时间自然消耗，低能量触发困倦打盹。
- `boredom` (0~100): 长期无交互升高，高时触发自发动作（伸懒腰、哼唧）。
- `intimacy` (0~100): 抚摸增加，高亲密度触发更亲昵反应（蹭人、打滚）。
- `mood`: 枚举值（`HAPPY`, `CURIOUS`, `NEUTRAL`, `SCARED`, `SLEEPY`）。

#### 2. 行为优先级与抢占规则
- **P0（最高，悬空/翻倒保护）**：IMU 触发，立即切断动作与播音，进入保护姿势。
- **P1（语音交互模式）**：物理唤醒后打断自主行为，执行 ASR -> VLM -> TTS + 动作。
- **P2（即时反射）**：触摸摸头/划背，立即播放呼噜声 + 摇尾 + 亮灯。
- **P3（最低，自主生态）**：后台自发心跳（10~20s 周期）：伸懒腰、打哈欠、趴下。

---

## 4. 资源控制与异常保护 (Pi Zero 2W 优化)

1. **按需抓帧**：不保持长推流，仅意图触发时抓帧并快速释放。
2. **轻量线程模型**：传感器轮询频率 20~30Hz，降低 CPU 消耗。
3. **网络与 API 超时降级**：API 异常时触发小狗困惑动作（歪头 + 黄灯 + `confused_1.mp3`），系统不崩溃。

---

## 5. 验收标准与测试验证

1. **硬件反射测试**：
   - 触摸头部/背部能够立即触发摇尾与灯效响应（延迟 < 200ms）。
   - 提起机器狗能够触发悬空检测与保护动作。
2. **适配器单测**：
   - 使用 Mock 音频和文本分别验证 `XiaomiASR`、`XiaomiTTS` 与 `MiniMaxVLM`。
3. **多模态与意图测试**：
   - 提问“你看这是什么”，系统自动拍摄当前照片并交由 MiniMax 回答。
4. **全链路交互测试**：
   - 摸头唤醒 -> 语音输入 -> 小米 ASR -> MiniMax 生成 -> 小米 TTS 播放 + 动作同步展现。
