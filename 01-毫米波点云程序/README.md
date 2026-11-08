# 毫米波雷达上位机 K1 板卡优化（v16）

原程序 `video_real12.py` **一个字节都没改**（MD5 `710842fb001d4b9a80b4564a6cc7bae0`，随时可以跑原版对照）。
所有优化都在新文件里，放在 `v16/`。

---

## 一、结论先说：快了多少

同一块 K1 板、同一套假雷达数据（1 个人 / 58 点 / 3 帧窗口 = 174 点）、同一个探针测出来的：

| 版本 | 点云刷新 | 单帧耗时 | 说明 |
|---|---|---|---|
| `video_real12.py`（原版） | **0.5 fps** | 878 ms | 卡成 PPT，3D 视图还是一片空白 |
| `video_real_k1.py` | **3.3 fps** | 95 ms | 换掉逐点配色、去掉阻塞读、拆线程 |
| `video_real_k1_lite.py`（极速版） | **9.5 fps** | 44 ms | 每帧都画到雷达的 10 Hz 上限为止 |

摄像头（原版被同一条链路拖累）：

| 状态 | 摄像头帧率 |
|---|---|
| 原版（跟着雷达同步节奏走） | 约 12 fps |
| 极速版 · 雷达满负荷时 | **20~23 fps** |
| 极速版 · 雷达暂时没点云时 | **30 fps**（设备满帧） |

CPU 占用：极速版采集进程 53%、界面进程 7%，还留有余量。

> 点云再往上提没意义：雷达本身只有约 10 帧/秒，页面 9.5 fps 已经吃满。
> 现在**卡的不是界面，是数据源**（见第五节）。

---

## 二、为什么原来那么卡（逐条实测）

1. **`self.timer.start()` 没给间隔** —— 0 ms 定时器，界面死循环重绘。
2. **UI 线程里阻塞取数** —— `data_process()` 里 `queue.get(timeout=1)`，一没数据就卡 1 秒。
3. **逐点配色** —— 每个点调一次 `setData` 带颜色列表，106 ms/帧；一个簇一个颜色只要 5 ms。
4. **pyqtgraph 控件本身贵** —— 每张图每帧重绘 55~90 ms。3 张 2D 图 + 1 个 3D 视图 + 1 个图像，
   加起来就 300 ms 以上。贴一张现成的位图只要几毫秒。
5. **Python GIL 抢锁** —— 同一套自绘代码，没有后台线程 26 fps，接上串口解析/摄像头/同步三个线程
   掉到 4.8 fps。2 Mbaud 串口逐字符解析是纯 Python 密集循环，和界面抢同一个 GIL。
   → 采集必须放到**独立进程**。
6. **摄像头被同步层掐死**（这次新查出来的）—— 原版 `CameraCapture.run()` 往同步队列投递用的是
   `put(timeout=0.2)`。同步层只按雷达 10 Hz 消费，队列一满，**每帧白等 0.2 秒**，
   于是 30 fps 的摄像头实际只跑到 12 fps。这就是"摄像头也卡"的直接原因。
   → 改成不阻塞投递 + 满了丢最旧，摄像头源头立刻回到 24~30 fps。
7. **摄像头被 10 Hz 配对** —— 原版把两路数据按时间戳配对，配对速率 = 雷达帧率 = 10 Hz，
   于是 30 fps 的摄像头只能显示 10 fps。→ 摄像头单独走一条通道。
8. **整块 1400×800 画布每帧重画** —— 光背景填充 + 整图转换就十几毫秒，其中摄像头那块还是白填的。

另外，原版的 3D 视图在 K1 上原生跑是**一片空白**，日志里一直刷
`OpenGL.error.GLError ... glMatrixMode ... invalid operation`（这块板子的 Mesa 是 softpipe）。
极速版不用 OpenGL，3D 视图是用 2D 投影画出来的，反而能正常显示。

---

## 三、三个版本是什么关系

| 文件 | 改了什么 | 用来做什么 |
|---|---|---|
| `v16/video_real_k1.py` | 只改渲染：逐点配色→按簇配色、去掉阻塞读、定时器给间隔、线程优先级 | 想保留 pyqtgraph 界面风格的话用这个（3.3 fps） |
| `v16/video_real_k1_lite.py` | **推荐**。采集独立进程 + 界面分块自绘，不依赖 pyqtgraph/OpenGL | 板上原生跑，9.5 fps + 摄像头 20~30 fps |

极速版的界面布局和原版一致（摄像头在左上、状态在左下、右边 2×2 四张图），
但拆成了三块独立刷新的区域，各刷各的：

```
 +------------------+   +------------------------+
 |   摄像头 30 fps   |   |  XY 图     |  YZ 图      |
 +------------------+   |------------+------------|
 |   状态/人数 10 Hz |   |  XZ 图     |  3D 视图    |
 +------------------+   +------------------------+
```

算法部分**完全沿用原程序**：串口协议解析、时间戳同步、
DBSCAN(eps=0.5, min_samples=30)、跌倒判定、按簇配色、各视图坐标范围，全都没动。

---

## 四、怎么跑

### 板上原生运行（推荐）

```bash
ssh testk1@192.168.3.16          # 密码 123456
cd /home/testk1/mmwave
bash run_native.sh               # 默认极速版
bash run_native.sh lite          # 极速版 + 每 2 秒打印帧率
bash run_native.sh fast          # pyqtgraph 优化版（3.3 fps）
bash run_native.sh orig          # 原版，用来对照
```

`run_native.sh` 会自动做这些事：设 `QT_API=pyqt5`（Ubuntu 的 python3-pyqtgraph 会拉 PyQt6，
混用会 segfault）、`MPLBACKEND=Agg`、`DISPLAY=:0`、`xhost +local:`、
自动挑串口和摄像头、检查依赖（缺了会直接把 apt 命令打出来）。

**串口权限**（原生跑第一次一定会踩）：

```bash
sudo usermod -aG dialout $USER          # 加入 dialout 组，重新登录生效
sudo chmod 666 /dev/ttyACM0             # 或者先临时放开
# 我已经在板上装好持久规则了：
# /etc/udev/rules.d/99-mmwave.rules
# KERNEL=="ttyACM[0-9]*", SUBSYSTEM=="tty", MODE="0666"
```

### Docker 里运行（可选）

```bash
bash run.sh lite
```

同一个文件、同一个探针，实测对比（假硬件、负荷固定）：

| 环境 | 点云 | 摄像头 | 单帧 |
|---|---|---|---|
| 容器内 | 8.7 fps | 17~19 fps | 12.2 ms |
| 板上原生 | 9.1~9.6 fps | 20~23 fps | 10.7 ms |

**结论：容器不是瓶颈**，差的那 5~10% 是 XWayland 多一层转发。
之前 Docker 里只有 9 fps，是代码的问题，不是容器的锅。
现在两者都能用 —— 原生少一层转发、串口/摄像头直通、调试方便，推荐原生；
要环境一致性/交付给别人，Docker 也没问题。两边的代码是同一份（宿主机目录 bind mount）。

---

## 五、现场实测（真雷达 + 摄像头）

```
[lite] 点云 7.3 fps | 摄像头显示 17.1 fps / 源头 28.9 fps
       | 聚类 21 | 栅格 7 | 合成 21 | 雷达 9 包/秒（有点 6）
```

- **点云 7.3 fps**：雷达这 2 秒里只发了 9 个包、其中 6 个包带点，显示已经跟满数据源
- **摄像头 17~30 fps**：有目标时 17~22 fps，没目标时 30 fps（设备满帧）
- 界面每帧合成 21 ms，还留有余量

### 雷达"没点云"的两种情况（很容易误判）

用 `_local_debug/radar_raw_probe.py` 抓原始字节看清楚了：

```
dc ff ee ff | 00×16 | d3 ff ee ff | cb fe dd ff | c4 fe dd ff
   包头        头部体      头结束      点云开始      点云结束（空的）
```

雷达是**一直按 10 帧/秒在发**的。没目标时一帧只有 36 字节、**0 个点**，
而且这时头部体是 16 字节（32 个 hex 字符），而原程序的 `extract_head` 要求正好 40 个 hex 字符，
所以**这类帧会被整帧丢掉**——不是程序坏了，是本来就没探测到东西。

极速版因此加了个雷达包计数器，界面上能分清：

| 现象 | 含义 | 处理 |
|---|---|---|
| 雷达 10 包/秒，有点 0 | 雷达正常，探测范围内没人 | 让目标站到前方 1~3 m 内 |
| 雷达 0 包/秒 | 串口没数据 | 查连线/供电/`/dev/ttyACM0` 权限 |
| 雷达 9 包/秒，有点 6，点云 7 fps | 正常 | — |

工具：

```bash
python3 _local_debug/radar_watch.py /dev/ttyACM0        # 20 秒，数帧数点、看坐标范围
python3 _local_debug/radar_raw_probe.py /dev/ttyACM0 20 # 抓原始字节，逐帧分析丢帧原因
python3 _local_debug/cam_bench.py /dev/video20          # 摄像头裸帧率（板上 29.6 fps）
```

---

## 六、中文显示成方块 / 文字叠在一起（都已修）

**方块**：板上 `fc-match sans-serif` 原来是 **Noto Sans**——只有拉丁字母，没有汉字字形。
修了两处：

1. 写 `~/.config/fontconfig/fonts.conf`，把 `sans-serif` / `sans` / `Arial` / `Helvetica`
   的优先级指到 `Noto Sans CJK SC`（**原程序也跟着受益，不用改它一行代码**）
2. 极速版启动时自己挑字体（`pick_cjk_font()`，候选见源码，找不到就手动加载
   `/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc`），启动日志会打 `界面字体 Noto Sans CJK SC`

**文字叠在一起**：Qt 的 `WA_OpaquePaintEvent` / `WA_NoSystemBackground` 一设，标签就不再擦背景，
状态文字一变就盖在旧文字上，越叠越糊。现在状态区改成**自己画**：
每次 `fillRect` 整块黑底再画三行字，结构上不可能叠。

---

## 七、现在真正卡的地方：雷达数据源

跑真硬件时观察到：雷达（`/dev/ttyACM0`）出点很不稳定，
有时 5~6 帧/秒、有时干脆一帧不出（界面上显示"无目标（雷达无点云）"，但摄像头照常 30 fps 刷新）。

这已经不是程序能解决的了，要查硬件侧：

- 前方 1~3 m 内要有真实目标（人），空旷环境下雷达出点本来就少；
- 雷达上的 UART 波特率要和程序里的 `2000000` 对上；
- USB 供电/线材（之前出现过一次 `ttyACM0` 卡死，重插才好）；
- 用 `python3 _local_debug/fps_probe.py --entry video_real12.py --seconds 15 --real-serial`
  看原版能拿到多少帧，作为基线。

---

## 八、把"程序是否在跑 / 是否有人 / 正常还是跌倒"接到 Docker 里的网页大屏（已做）

板子上的 `xiaozhi` 容器用 **host 网络**跑着 `web_real_server.py`（端口 8090，DataV 大屏），
极速版在 **8091** 开了一个只读 JSON 接口，网页轮询它显示三个状态。

```
毫米波程序（宿主机 :8091）── HTTP/JSON ──▶ Docker 网页（:8090）里的 radar.js
```

### 三个状态

| 状态块 | 取值 | 判据 |
|---|---|---|
| **程序** | `运行中` / `无响应` / `不在线` | 接口能取到且 3 秒内有更新 = 运行中；取到但 `stale` = 卡住；取不到 = 没在跑 |
| **人员** | `有人` / `无人` / `无数据` | 只看 `person_detected`（点数 > 10）。**不显示人数** |
| **状态** | `正常` / `跌倒` / `--` | `fall_detected`，判定规则完全沿用原程序；没人时显示 `--` |

配色：青绿 = 运行中/有人/正常，红色呼吸 = 无响应/跌倒，灰色 = 不在线/无人/--。
下面还有一行实时指标：`点 / 簇 / fps / 雷达包率 / 数据延迟`，用来判断"程序在跑但雷达没数据"这类情况。

> 三个块和毫米波程序界面顶部那行是同一套判据：
> `状态：检测到目标 - 疑似跌倒` ⇔ 人员 `有人` + 状态 `跌倒`，所以两边不会互相打脸。

接口字段（`curl http://127.0.0.1:8091/status`）：

```json
{"online": true, "person_detected": true, "fall_detected": false,
 "state": "normal", "state_text": "正常活动", "person_count": 1, "fall_count": 0,
 "points": 116, "clusters": 1, "fps": 9.5, "delay_ms": 7,
 "radar_pkt_rate": 10, "radar_hit_rate": 10, "ts": 1793764853099,
 "stale": false, "ok": true}
```

| 字段 | 含义 |
|---|---|
| `online` | 雷达有包在进来（`false` = 串口没数据） |
| `person_detected` / `person_count` | 是否检测到人 / 人数 |
| `fall_detected` / `state` | 是否跌倒；`none` / `normal` / `fall` |
| `stale` | 超过 3 秒没更新 → 程序卡了 |

### 为什么放在 footer 而不是 header

`styles.css` 里：

```css
.screen-16x9 {
  height: min(calc(100vh - 24px), calc((100vw - 44px) * 9 / 16));
  grid-template-rows: 13% 71% 16%;   /* header 只有 13% ≈ 137px */
  overflow: hidden;                  /* 超出的直接裁掉 */
}
```

最先往 `header` 的 `.header-meta`（竖排 flex）里加了第三行，总高超过 137px，
**被 `overflow: hidden` 在面板下边界裁断，三个状态块都只露出上半截**。
改成 footer 第三栏（`.footer-grid` 从 2 栏扩成 3 栏，footer 有 16% ≈ 161px），
实测内容底边 132px < 161px，完整不裁。

### 改动范围

原来 `app.js` 一行没动，只加三个锚点：

- `index.html` 加 `<script src="./radar.js">` 和一段 `<style>`
- footer 的 `.footer-grid` 里加第三段 `<section class="footer-segment radar-seg">`（含"程序""人员""状态"三块）
- 新增 `webui/radar.js`（800 ms 轮询，自己处理离线）

### 重打 / 回滚

容器没有挂载目录，改的文件在容器可写层里，**容器重建会丢**。原件在
`/opt/demo-real/webui/index.html.bak`。重打（先从备份还原，保证干净）：

```bash
docker cp xiaozhi:/opt/demo-real/webui/index.html.bak /tmp/index.html
python3 patch_web.py /tmp/index.html --from-bak        # 见 v16/web/
docker cp /tmp/index.html xiaozhi:/opt/demo-real/webui/index.html
docker cp radar.js        xiaozhi:/opt/demo-real/webui/radar.js
docker exec xiaozhi chown ubuntu:ubuntu /opt/demo-real/webui/index.html /opt/demo-real/webui/radar.js
```

回滚：

```bash
docker exec xiaozhi cp /opt/demo-real/webui/index.html.bak /opt/demo-real/webui/index.html
docker exec xiaozhi rm -f /opt/demo-real/webui/radar.js
```

不用开浏览器就能验证（板子上有 chromium）：

```bash
curl -s http://127.0.0.1:8091/status                       # 接口本身
chromium-browser --headless=new --no-sandbox --dump-dom \
  http://127.0.0.1:8090/ | grep -o 'id="radar[^"]*"[^>]*'  # 三个状态块的实时值
```

不要这个接口：`MMWAVE_HTTP_PORT=0 bash run_native.sh lite`。

---

## 九、可调参数（环境变量）

```bash
MMWAVE_UI_MS=33          点云刷新间隔（毫秒）
MMWAVE_CAM_MS=25         摄像头刷新间隔（毫秒，25 → 最高 40 fps）
MMWAVE_WIN_FRAME=2       滑动窗口帧数（默认 2；原程序是 3，见第五节实测）
MMWAVE_MAX_POINTS=1200   每帧最多画多少点
MMWAVE_DBSCAN_EVERY=2    聚类每 N 帧做一次（板上 DBSCAN 约 15 ms）
MMWAVE_MIN_SAMPLES=30    DBSCAN 最少点数，默认 30（同原程序）
MMWAVE_DBSCAN_EPS=0.5    DBSCAN 邻域半径（米）
MMWAVE_FALL_BY_CLUSTER=0 置 1 让状态行的跌倒判定也按簇（详见下方说明）
MMWAVE_HTTP_PORT=8091    状态接口端口，0 = 关闭
MMWAVE_PROC=0            采集放回界面进程（对照用，会明显变慢）
MMWAVE_PROFILE=1         每 2 秒打印 "点云 / 摄像头 / 聚类 / 栅格 / 合成 / 数据延迟 / 窗口跨度"
MMWAVE_SYNTH_HW=1        本地调试用假雷达 + 假摄像头
```

例：想再省一点 CPU 就 `MMWAVE_CAM_MS=40 MMWAVE_DBSCAN_EVERY=3 bash run_native.sh lite`。

### 关于原程序两套跌倒判据（已做成开关，默认不改行为）

| 判据 | 条件 | 用处 |
|---|---|---|
| 按簇 | 簇内最高点 < **-0.4** 且高差 < 0.5 | "实时跌倒人数" |
| 全局 | 最高点 < **0.6** 且高差 < 0.4 | 状态行"疑似跌倒/正常活动" |

目标高度落在 -0.4 ~ 0.6 之间时两者会打架，出现"人数 1、跌倒人数 0、状态却疑似跌倒"。
`MMWAVE_FALL_BY_CLUSTER=1` 可以让状态行也按簇判断，两边就一致了（默认 0 = 原行为）。

近距离目标点少（一帧 10~20 个）时 `min_samples=30` 聚不出簇，"范围内人数"会恒为 0；
想让小目标也成簇可以 `MMWAVE_MIN_SAMPLES=12`（实测簇数 0 → 1、人数 0 → 1）。

### 摄像头没插 / 掉线时也能跑

摄像头掉线（`/dev/video*` 全都打不开，USB 重枚举后节点名也会从 `video20` 变成 `video50` 之类）时：

- `run_native.sh` 探测不到就**留空**，不再兜底成 `0`（`VideoCapture(0)` 在 K1 上会报
  GStreamer `no element 0`，反而把整条采集链路弄死）；
- `launcher.py` 只接受 `/dev/` 开头的真实设备节点，探测不到就自己按
  `/dev/video0,1,2…` 顺序**试读一帧**，谁能读用谁；
- 摄像头一直没有帧时，采集进程 3 秒后自动切到"**只送雷达**"模式，
  界面照常出点云（实测 7~10 fps），只是摄像头面板是黑的。

所以摄像头坏了不会连累点云。插回摄像头后重启程序即可自动认出。

---

## 十、没硬件也能调试（PC 上就能跑）

```powershell
D:\Python313\python.exe _local_debug\local_launcher.py --entry video_real_k1_lite.py --profile --seconds 20 --snapshot shot.png
D:\Python313\python.exe _local_debug\fps_probe.py --entry video_real_k1_lite.py --seconds 15
```

`_local_debug/fake_hw.py` 会造出符合协议的真帧（含簇中心、SNR），
`mkframe.py` 还带 200 组自检；`cam_bench.py` 用来测摄像头裸帧率（板上实测 29.6 fps）。

---

## 十一、文件清单

```
v16/
  video_real_k1_lite.py     极速版（推荐）
  video_real_k1.py          pyqtgraph 优化版
  README.md                 本文
  K1_原生运行截图.png        板上原生运行的样子
  K1_极速版截图.png          Docker 里的截图

_k1_deploy/                 （宿主机目录，容器里挂成 /opt/mmwave）
  run_native.sh             板上原生启动脚本
  run.sh                    Docker 启动脚本
  launcher.py               把 COM7 / VideoCapture(0) 换成实际设备
  pick_serial.py            自动挑串口
  pick_camera.py            自动挑摄像头
  mkframe.py                协议编码 + 自检
  replay_serial.py          pty 假串口（不接雷达也能全链路跑）
  Dockerfile / deploy.sh

_local_debug/               本地调试工具（PC 和板上都能用）
  fake_hw.py                假雷达 + 假摄像头
  local_launcher.py         带截图/限时的启动器
  fps_probe.py              统一帧率探针
  check_shot.py             截图分区检查
  cam_bench.py              摄像头裸帧率
  web/radar.js              网页两个状态灯（容器内 /opt/demo-real/webui/）
  web/patch_web.py          往网页 index.html 打补丁（幂等）
  radar_watch.py            盯雷达串口数帧数点
  radar_raw_probe.py        抓原始字节分析丢帧
  check_env.py              依赖自检
```

原版 `video_real12.py` 保持原样，随时可以 `python3 video_real12.py` 对照。
