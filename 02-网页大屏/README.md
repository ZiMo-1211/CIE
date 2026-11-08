# 02 网页大屏（web_real_server.py）

DataV 风格大屏，跑在 Docker 容器 `xiaozhi` 里，监听 **:8090**，用 host 网络。
除了原有的 Modbus 设备面板，还在页脚接入了毫米波的三块状态（`radar.js`）。

## 文件

```
web_real_server.py     HTTP 服务（http.server，静态文件 + /api/status）
webui/
  index.html           大屏页面（已打上毫米波状态补丁）
  index.html.bak       打补丁前的原始页面（回滚用）
  styles.css           样式
  app.js               大屏逻辑（轮询 /api/status）
  radar.js             毫米波三块状态（程序 / 人员 / 状态）
  vendor/echarts.min.js
```

## 启动

**必须用容器里的 `/opt/venv-mcp-official/bin/python`**（只有它装了 `pymodbus`）。
用容器默认 `python` 会直接报 `ModuleNotFoundError: No module named 'pymodbus'`。

```bash
docker exec -d xiaozhi bash -lc 'cd /opt/demo-real && nohup /opt/venv-mcp-official/bin/python web_real_server.py > /tmp/web.log 2>&1'
```

打开 `http://<板子IP>:8090/`。

## 毫米波状态从哪来

页面加载 `radar.js`，它每 800 ms 轮询板子宿主机的毫米波接口：

```
http://<location.hostname>:8091/status
```

因为容器是 **host 网络**，网页里的 `127.0.0.1:8091` 就是宿主机，两边直接通。

三块状态和毫米波程序界面那行是同一套判据，不会互相打脸：

| 网页 | 毫米波界面 |
|---|---|
| 程序 运行中 | （接口在响应） |
| 人员 有人 | 状态：检测到目标 |
| 状态 跌倒 | 状态：…疑似跌倒 |

接口取不到 → 程序「不在线」；取到但 `stale=true` → 「无响应」。

## 毫米波部分怎么改/怎么撤

页面补丁是幂等的，`radar.js` 和 `patch_web.py` 在本仓库的
`01-毫米波点云程序/web/` 里（同一份文件），改完重新打补丁：

```bash
cd /home/testk1/mmwave/web
docker cp xiaozhi:/opt/demo-real/webui/index.html.bak ./index.html
python3 patch_web.py ./index.html --from-bak
docker cp ./index.html xiaozhi:/opt/demo-real/webui/index.html
docker cp ./radar.js   xiaozhi:/opt/demo-real/webui/radar.js
```

**完全撤掉**毫米波显示：

```bash
docker exec xiaozhi cp /opt/demo-real/webui/index.html.bak /opt/demo-real/webui/index.html
```

`radar.js` 找不到那三个 DOM 节点会自己安静退出，留着也不影响。

## 布局注意事项

`styles.css` 里 `.screen-16x9` 是固定比例网格：

```css
grid-template-rows: 13% 71% 16%;
overflow: hidden;
```

所以页头只有 13%（1080p 下约 137 px），塞不下第三行；
毫米波的三块状态因此放在**页脚第三列**（16%，约 161 px），不会出现"只显示一半"的裁切问题。