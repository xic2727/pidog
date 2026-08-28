#!/usr/bin/env bash
# PiDog All-in-One Services Manager
# 管理 PiDog 所有后台服务 (Daemon 控制进程 + 摄像头视频推流 + Web 控制台)

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PIDOG_CTL="$SCRIPT_DIR/pidog-control/scripts/pidog_ctl.py"
PIDOG_CAMERA="$SCRIPT_DIR/pidog-control/scripts/pidog_camera.py"
WEB_SERVER="$SCRIPT_DIR/pidog-control/web/web_server.py"

# Camera process pattern used by pgrep for status / graceful stop.
# Matches both the script launch and the inline vilib snippet (for upgrade
# scenarios where the old subprocess is still alive during restart).
CAMERA_PGREP_PATTERN="pidog_camera.py|vilib.camera_start"

LOG_DIR="$HOME/.openclaw/pidog-control"
mkdir -p "$LOG_DIR"

CAMERA_LOG="$LOG_DIR/camera.log"
WEB_LOG="$LOG_DIR/web_server.log"
WEB_PID_FILE="$LOG_DIR/web_server.pid"
CAMERA_PID_FILE="$LOG_DIR/camera.pid"

start_services() {
    echo "=========================================="
    echo "          正在启动 PiDog 服务"
    echo "=========================================="

    # 1. 启动硬件控制 Daemon 进程
    echo "[1/3] 启动硬件控制守护进程 (Daemon)..."
    python3 "$PIDOG_CTL" start --force
    sleep 2

    # 2. 后台启动摄像头推流服务
    #    自定义 picamera2 进程替代原 vilib.display(web=True),避免 vilib 内部
    #    Flask 在首帧未就绪时抛出 cv2.imencode(img=None) 反复刷屏 camera.log。
    #    URL 与旧实现保持一致:http://<host>:9000/mjpg,SPA / web_server.toml 无需改动。
    echo "[2/3] 后台启动摄像头推流服务 (port 9000)..."
    pkill -f "$CAMERA_PGREP_PATTERN" 2>/dev/null || true

    nohup python3 "$PIDOG_CAMERA" > "$CAMERA_LOG" 2>&1 &
    CAM_PID=$!
    echo "$CAM_PID" > "$CAMERA_PID_FILE"
    echo "      摄像头服务已启动 [PID $CAM_PID], 日志: $CAMERA_LOG"
    sleep 2

    # 3. 后台启动 Web 控制台
    echo "[3/3] 后台启动 Web 控制台 (port 8000)..."
    if [ -f "$WEB_PID_FILE" ] && kill -0 $(cat "$WEB_PID_FILE") 2>/dev/null; then
        echo "      Web 服务已在运行中 [PID $(cat "$WEB_PID_FILE")]"
    else
        nohup python3 "$WEB_SERVER" > "$WEB_LOG" 2>&1 &
        echo $! > "$WEB_PID_FILE"
        echo "      Web 服务已启动 [PID $!], 日志: $WEB_LOG"
    fi

    echo "=========================================="
    echo "所有服务已在后台成功启动！"
    echo "控制台页面: http://$(hostname -I | awk '{print $1}'):8000"
    echo "=========================================="
}

stop_services() {
    echo "=========================================="
    echo "          正在停止 PiDog 服务"
    echo "=========================================="

    # 1. 停止 Web 服务
    if [ -f "$WEB_PID_FILE" ]; then
        PID=$(cat "$WEB_PID_FILE")
        if kill -0 "$PID" 2>/dev/null; then
            echo "[1/3] 停止 Web 控制台 [PID $PID]..."
            kill "$PID" 2>/dev/null || true
        fi
        rm -f "$WEB_PID_FILE"
    fi
    pkill -f "web_server.py" 2>/dev/null || true

    # 2. 停止摄像头服务 (优先 SIGTERM/SIGINT 让 picamera2 触发 signal_handler 并调用 camera.close)
    echo "[2/3] 停止摄像头推流服务..."
    CAM_PID=""
    if [ -f "$CAMERA_PID_FILE" ]; then
        CAM_PID=$(cat "$CAMERA_PID_FILE")
        rm -f "$CAMERA_PID_FILE"
    fi

    # 发送优雅终止信号
    if [ -n "$CAM_PID" ] && kill -0 "$CAM_PID" 2>/dev/null; then
        kill -SIGTERM "$CAM_PID" 2>/dev/null || true
    fi
    pkill -SIGTERM -f "$CAMERA_PGREP_PATTERN" 2>/dev/null || true

    # 循环等待最多 3 秒让摄像头关闭并释放 /dev/video* 句柄与 9000 端口
    WAIT_SEC=0
    while pgrep -f "$CAMERA_PGREP_PATTERN" >/dev/null 2>&1 && [ $WAIT_SEC -lt 30 ]; do
        sleep 0.1
        WAIT_SEC=$((WAIT_SEC + 1))
    done

    # 若 3 秒后进程仍未退出，尝试 SIGINT 再次提醒
    if pgrep -f "$CAMERA_PGREP_PATTERN" >/dev/null 2>&1; then
        pkill -SIGINT -f "$CAMERA_PGREP_PATTERN" 2>/dev/null || true
        sleep 1
    fi

    # 最终兜底：若仍残留则强制清理，确保不会影响下次启动
    if pgrep -f "$CAMERA_PGREP_PATTERN" >/dev/null 2>&1; then
        echo "      摄像头进程超时未响应，强制清理..."
        pkill -9 -f "$CAMERA_PGREP_PATTERN" 2>/dev/null || true
    else
        echo "      摄像头服务已优雅关闭，设备句柄已释放"
    fi

    # 3. 停止 Daemon 控制进程
    echo "[3/3] 停止硬件控制守护进程..."
    python3 "$PIDOG_CTL" stop 2>/dev/null || true

    echo "=========================================="
    echo "所有服务已停止！"
    echo "=========================================="
}

status_services() {
    echo "=========================================="
    echo "          PiDog 服务运行状态"
    echo "=========================================="
    python3 "$PIDOG_CTL" status

    echo -n "Camera Stream (port 9000): "
    if pgrep -f "$CAMERA_PGREP_PATTERN" > /dev/null; then
        echo "running"
    else
        echo "stopped"
    fi

    echo -n "Web Server    (port 8000): "
    if [ -f "$WEB_PID_FILE" ] && kill -0 $(cat "$WEB_PID_FILE") 2>/dev/null; then
        echo "running [PID $(cat "$WEB_PID_FILE")]"
    else
        echo "stopped"
    fi
    echo "=========================================="
}

case "$1" in
    start)
        start_services
        ;;
    stop)
        stop_services
        ;;
    status)
        status_services
        ;;
    restart)
        stop_services
        sleep 2
        start_services
        ;;
    *)
        echo "用法: $0 {start|stop|status|restart}"
        exit 1
        ;;
esac
