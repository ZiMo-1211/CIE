import json, time, urllib.request
t0 = time.time()
last_alive = 0
with open('/home/testk1/mmwave/logs/points.log', 'a', buffering=1) as f:
    f.write(f"\n=== 开始 {time.strftime('%H:%M:%S')} ===\n")
    while time.time() - t0 < 7200:
        el = time.time() - t0
        try:
            d = json.load(urllib.request.urlopen('http://127.0.0.1:8091/status', timeout=3))
            rb = d.get('raw_bbox') or [0]*6
            b = d.get('bbox') or [0]*6
            if d.get('raw_points', 0) > 0:
                f.write(f"[{time.strftime('%H:%M:%S')}] 原始{d['raw_points']:4d}点 "
                        f"x {rb[0]:+.2f}~{rb[1]:+.2f} y {rb[2]:+.2f}~{rb[3]:+.2f} z {rb[4]:+.2f}~{rb[5]:+.2f}"
                        f" | 过滤后{d['points']:4d}点 y {b[2]:+.2f}~{b[3]:+.2f} z {b[4]:+.2f}~{b[5]:+.2f}"
                        f" | 人={'有' if d['person_detected'] else '无'} 状态={d['state']}\n")
            elif el - last_alive >= 120:
                last_alive = el
                f.write(f"[{time.strftime('%H:%M:%S')}] …静默（雷达在线，原始点也为 0）\n")
        except Exception as e:
            f.write(f"[{time.strftime('%H:%M:%S')}] 读取失败 {e}\n")
        time.sleep(0.5)
