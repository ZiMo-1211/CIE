#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""给 docker 里的网页加"毫米波人员感知"面板（幂等）。

改动只有三处，全部有唯一锚点，锚点不对就报错退出、绝不乱改：
  1. <head> 里加一段 <style>
  2. footer 的 .footer-grid 加第三段 <section class="footer-segment radar-seg">
     （同时把 footer 从 2 栏改成 3 栏）
  3. </body> 前加 <script src="./radar.js">

为什么放 footer 不放 header：
  .screen-16x9 是 grid-template-rows: 13% 71% 16% + overflow:hidden，
  header 高度被锁死，多塞一行会被裁掉（这就是"只显示一半"的原因）。

用法：
  patch_web.py <index.html>             打补丁（已打过则跳过）
  patch_web.py <index.html> --from-bak  先还原成 .bak 再打（推荐，保证干净）
"""
import os
import shutil
import sys

STYLE_BLOCK = """
  <style>
    /* ===== 毫米波人员感知（radar.js 驱动；不要就整段删掉）===== */
    .dv-footer .footer-grid { grid-template-columns: repeat(3, minmax(0, 1fr)); }
    .radar-chips {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 4px;
    }
    .radar-chip {
      display: inline-flex;
      align-items: baseline;
      gap: 7px;
      padding: 7px 13px;
      border-radius: 10px;
      border: 1px solid rgba(140, 160, 184, 0.45);
      background: rgba(140, 160, 184, 0.12);
      color: #c8d6e5;
      font-size: clamp(14px, 0.95vw, 18px);
      font-weight: 800;
      white-space: nowrap;
    }
    .radar-chip .chip-label {
      font-size: 0.74em;
      font-weight: 600;
      opacity: 0.72;
      letter-spacing: 0.05em;
    }
    .radar-chip[data-tone="on"] {
      border-color: rgba(25, 245, 193, 0.7);
      background: rgba(25, 245, 193, 0.14);
      color: #beffe9;
      box-shadow: 0 0 16px rgba(25, 245, 193, 0.22);
    }
    .radar-chip[data-tone="alarm"] {
      border-color: rgba(255, 64, 90, 0.9);
      background: rgba(255, 64, 90, 0.22);
      color: #ffd7dd;
      box-shadow: 0 0 20px rgba(255, 64, 90, 0.55);
      animation: radarPulse 1s ease-in-out infinite;
    }
    .radar-metrics {
      margin-top: 9px;
      font-size: clamp(12px, 0.78vw, 14px);
      color: #8fa8c0;
      font-variant-numeric: tabular-nums;
    }
    @keyframes radarPulse { 50% { box-shadow: 0 0 6px rgba(255, 64, 90, 0.2); } }
  </style>"""

SEGMENT_BLOCK = """
          <section class="footer-segment radar-seg">
            <div class="panel-head panel-head-inline">
              <h2>毫米波人员感知</h2>
              <p>板载 60GHz 雷达实时判定</p>
            </div>
            <div class="radar-chips">
              <div id="radarProgChip" class="radar-chip" data-tone="off">
                <span class="chip-label">程序</span><span class="chip-value">--</span>
              </div>
              <div id="radarPersonChip" class="radar-chip" data-tone="off">
                <span class="chip-label">人员</span><span class="chip-value">--</span>
              </div>
              <div id="radarStateChip" class="radar-chip" data-tone="off">
                <span class="chip-label">状态</span><span class="chip-value">--</span>
              </div>
            </div>
            <div id="radarMetrics" class="radar-metrics">等待毫米波数据…</div>
          </section>
"""

SCRIPT_TAG = '  <script src="./radar.js"></script>\n'

ANCHOR_STYLE = '  <link rel="stylesheet" href="./styles.css" />'
ANCHOR_FOOTER = '            <ul id="logList" class="log-list"></ul>\n          </section>\n'
ANCHOR_SCRIPT = '  <script src="./app.js"></script>\n'
MARKER = 'id="radarProgChip"'


def patch(text):
    if MARKER in text:
        return text, "已打过补丁，跳过"

    for name, anchor in (("styles.css 引用", ANCHOR_STYLE),
                         ("footer 日志段结尾", ANCHOR_FOOTER),
                         ("app.js 引用", ANCHOR_SCRIPT)):
        n = text.count(anchor)
        if n != 1:
            raise SystemExit(f"锚点「{name}」出现 {n} 次（应为 1），中止")

    text = text.replace(ANCHOR_STYLE, ANCHOR_STYLE + STYLE_BLOCK)
    text = text.replace(ANCHOR_FOOTER, ANCHOR_FOOTER + SEGMENT_BLOCK.lstrip("\n"))
    text = text.replace(ANCHOR_SCRIPT, ANCHOR_SCRIPT + SCRIPT_TAG)
    return text, "已插入样式 + footer 面板 + 脚本引用"


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        raise SystemExit(__doc__)
    path = args[0]
    from_bak = "--from-bak" in sys.argv

    if from_bak:
        bak = path + ".bak"
        if not os.path.exists(bak):
            raise SystemExit(f"找不到备份 {bak}")
        shutil.copyfile(bak, path)
        print(f"  已从 {os.path.basename(bak)} 还原")

    with open(path, "r", encoding="utf-8") as f:
        text = f.read()

    new_text, msg = patch(text)
    print(f"  {msg}")

    if new_text != text:
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(new_text)
        print(f"  已写入 {path}")


if __name__ == "__main__":
    main()