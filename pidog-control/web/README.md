# PiDog Local Web Console (v1)

一个 FastAPI 应用, 把 `pidog-control` 的 Unix-socket daemon 包装成同网段下手机/平板/电脑都能用的 Web 控制台。

- 设计文档: `docs/plans/local-web-console.md` (v0.3)
- 适用: SunFounder PiDog V2 + 已按官方流程安装 (`~/pidog`、`~/robot-hat`、`~/vilib`)
- 适用: **同 WiFi 局域网**使用, 不做公网/HTTPS/鉴权 (留 v2)

## 快速开始 (开发)

```bash
# 0) 一次性系统依赖 (让手机/平板能用 pidog.local 访问)
sudo apt install avahi-daemon
sudo systemctl enable --now avahi-daemon

# 1) 一次性 Python 依赖
pip3 install 'fastapi>=0.110' 'uvicorn[standard]>=0.27' pydantic

# 2) 启动上游 daemon (如果还没在跑)
python3 ~/pidog/pidog-control/scripts/pidog_ctl.py start

# 3) 启动本服务 (前台, 日志直接看)
cd ~/pidog/pidog-control/web
python3 web_server.py

# 4) 终端会打印:
#    [pidog-web] LAN access   : http://pidog.local:8000/
#    [pidog-web] fallback IP  : http://192.168.x.x:8000/
# 5) 把上面任一 URL 在手机/平板/电脑浏览器打开即可 (需同一 WiFi)
```

如果 `pidog.local` 打不开, 改用 IP 那行; 如果 IP 也不通, 多半是 AP isolation (酒店/校园网常见), 给 PiDog 接手机热点即可。

## systemd (开机自启)

```bash
# 1) 复制 unit
mkdir -p ~/.config/systemd/user
cp systemd/pidog-web.service ~/.config/systemd/user/
# 或者放到 /etc/systemd/system/ (需要 sudo)

# 2) 改 ExecStart 里的路径, 匹配你机器上的实际位置
#    User= 改成你的用户名 (避免 root 跑)
#    WorkingDirectory= 改成 web_server.py 所在目录

# 3) 启动
systemctl --user daemon-reload
systemctl --user enable --now pidog-web

# 4) 看日志
journalctl --user -u pidog-web -f
```

> 注意 systemd unit 里默认的 `User=pidog` 只是个占位, 改成本机实际用户; 同时确认 daemon 已经在跑 (`pidog_ctl.py status`)。

## 验证清单

| 步骤 | 期望 |
|---|---|
| 打开 `http://<pi_ip>:8000/` | 看到页面、视频区有画面 (前提: `start.sh` 启动了摄像头) |
| 顶栏点 "停所有" | 灯条熄灭 |
| 点 Sit → 看狗 | 狗坐下, 按钮变高亮 (hold) |
| 切到 Stand | 旧 hold 自动释放 |
| 改亮度滑块 | 灯条亮度跟随 |
| 选不同颜色 + Boom | 灯条变彩色 Boom |
| 拔网线/重连 | WS 几秒内自动重连 |

## 故障排查

| 现象 | 原因 / 处理 |
|---|---|
| 浏览器连不上 (ERR_CONNECTION_REFUSED) | 8000 端口没起, 看启动日志; 防火墙是否放行 |
| 视频区 "视频不可用" | 摄像头服务没跑。`./start.sh start` 会自动起 `pidog_camera.py`; 也可手动 `python3 pidog-control/scripts/pidog_camera.py` |
| 视频区一直转圈且 `camera.log` 刷 `img is not a numpy array` | 老 vilib 行为,已替换为 picamera2 自建服务;升级到当前代码后重启 `./start.sh restart` |
| 摄像头服务退出 (exit code 3) | 看 `camera.log`:通常是 picamera2/libcamera 报错 (排线 / `/dev/video*` 被占用 / 已有 vilib 进程在跑) |
| 按钮点了无反应 | daemon 没跑, 跑 `pidog_ctl.py start`; 或 daemon 死锁, 跑 `pidog_ctl.py restart` |
| 动作返回 503 DAEMON_DOWN | 同上 |
| 手机 4G 打开 `pidog.local` 失败 | 手机必须和 PiDog 在同一 WiFi; 4G 不行 |
| iOS Safari 滑块很难拖 | 已知: iOS 对 `<input type=range>` 不友好, v1.1 改触屏手势 |
| `pidog.local` 解析失败 (Linux 客户机) | 客户机需要装 `avahi-daemon`/`nss-mdns`, 桌面机通常自带 |

## 摄像头服务 (替代 vilib web 流)

`./start.sh` 现在后台启动 `pidog-control/scripts/pidog_camera.py`,使用
`picamera2` 直接抓帧并以 `multipart/x-mixed-replace` 暴露在 `:9000/mjpg`,
URL 与旧 `Vilib.display(web=True)` 完全一致,SPA 与 `web_server.toml`
无需改动。这样规避了 vilib 内部在首帧未就绪时抛 `cv2.imencode(img=None)`
导致每次请求都刷 `camera.log` 的问题。

可调环境变量 (在 `start.sh` 里 export 或 systemd unit 里 `Environment=`):

| 变量 | 默认 | 说明 |
|---|---|---|
| `PIDOG_CAMERA_PORT` | `9000` | MJPEG 监听端口 |
| `PIDOG_CAMERA_BIND` | `0.0.0.0` | 绑定地址 |
| `PIDOG_CAMERA_SIZE` | `640x480` | 分辨率 |
| `PIDOG_CAMERA_FPS` | `15` | 帧率 |
| `PIDOG_CAMERA_QUALITY` | `75` | JPEG 质量 1-100 |
| `PIDOG_CAMERA_VFLIP` | `false` | 垂直翻转 |
| `PIDOG_CAMERA_HFLIP` | `false` | 水平翻转 |

## 目录结构

```
pidog-control/web/
├── __init__.py
├── web_server.py            ← FastAPI 入口
├── daemon_client.py         ← 封装 controller_request, 加白名单+超时
├── config.py                ← 读 web_server.toml
├── status_poller.py         ← 周期 ping daemon, WS 推送
├── mdns_register.py         ← 启动时探测 avahi, 拼 URL
├── routes/
│   ├── __init__.py
│   ├── actions.py
│   ├── lights.py
│   ├── status.py
│   └── camera.py
├── static/
│   ├── index.html
│   ├── style.css
│   ├── app.js
│   └── manifest.webmanifest
├── web_server.toml
├── systemd/
│   └── pidog-web.service
└── README.md
```

## 不在 v1 范围

- 鉴权、HTTPS、公网 — v2
- TTS、声音选择、传感器面板、编排拖拽 — v1.1+
- 头部 RPY 触屏手势 (手机上拖) — v1.1
- 与 `pidog/companion/` 编排器联动 — v2.1
- WebRTC 双向音视频 — v2.2

详见 `docs/plans/local-web-console.md`。
