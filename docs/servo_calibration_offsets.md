# PiDog 舵机校准偏移量记录 (Servo Calibration Offsets)

记录时间: 2026-08-16
校准工具: `examples/0_calibration.py`
配置文件路径: `~/.config/pidog/pidog.conf`

---

## 1. 舵机布局与控制键位映射

```text
                               PiDog  Calibration                            
Press key to select servo:  
1 ~ 8 : Leg servos                               [9]      
9 : Head yaw                                [-] ┌─┐  [0]  
0 : Head roll                                   │ │     
- : Head pitch                           [2][1]┌└─┘┐[3][4]
= : Tail                                       │   │    
                                               │   │    
Press key to adjust servo:               [6][5]└─┬─┘[7][8]
W or A: increase angle                          [=]     
S or D: decreases angle                          /      

Ctrl+C: Quit    Space: Save
```

---

## 2. 舵机校准偏移量数据

### 腿部舵机偏移 (Leg Offsets)

顺序对应 `[1, 2, 3, 4, 5, 6, 7, 8]`（8路腿部舵机）：

| 舵机编号      | 对应关节                               | 偏移角度 (°) |
| :------------ | :------------------------------------- | :------------ |
| **[1]** | 左前腿 - 大腿 (Left Front - Shoulder)  | `0.00`      |
| **[2]** | 左前腿 - 小腿 (Left Front - Knee)      | `-4.40`     |
| **[3]** | 右前腿 - 大腿 (Right Front - Shoulder) | `3.52`      |
| **[4]** | 右前腿 - 小腿 (Right Front - Knee)     | `-3.96`     |
| **[5]** | 左后腿 - 大腿 (Left Hind - Hip)        | `0.00`      |
| **[6]** | 左后腿 - 小腿 (Left Hind - Knee)       | `0.00`      |
| **[7]** | 右后腿 - 大腿 (Right Hind - Hip)       | `2.64`      |
| **[8]** | 右后腿 - 小腿 (Right Hind - Knee)      | `7.03`      |

```text
leg_offset: 0.00, -4.40, 3.52, -3.96, 0.00, 0.00, 2.64, 7.03
```

---

### 头部舵机偏移 (Head Offsets)

顺序对应 `[Yaw, Roll, Pitch]`：

| 舵机编号 / 轴         | 对应运动方向             | 偏移角度 (°) |
| :-------------------- | :----------------------- | :------------ |
| **[9] (Yaw)**   | 头部偏航（水平左右转头） | `-8.79`     |
| **[0] (Roll)**  | 头部倾斜（左右歪头）     | `-1.32`     |
| **[-] (Pitch)** | 头部俯仰（上下点头）     | -11.87        |

```text
head_offset: -8.79, -1.32, -11.87
```

---

### 尾巴舵机偏移 (Tail Offset)

| 舵机编号             | 对应运动方向 | 偏移角度 (°) |
| :------------------- | :----------- | :------------ |
| **[=] (Tail)** | 尾巴左右摆动 | `0.00`      |

---

## 3. 配置文件内容参考 (`~/.config/pidog/pidog.conf`)

如果需要在新设备上手动恢复此校准结果，可以直接写入 `~/.config/pidog/pidog.conf`：

```ini
[servo_offset]
leg_offset = [0.00, -4.40, 3.52, -3.96, 0.00, 0.00, 2.64, 7.03]
head_offset = [-8.79, -1.32, 5.71]
tail_offset = [0.00]
```
