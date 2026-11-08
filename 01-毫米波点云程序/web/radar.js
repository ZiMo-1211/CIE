/* 毫米波状态（板载程序 → 网页大屏）
 *
 * 数据来自板子上跑的毫米波程序（video_real_k1_lite.py）的只读接口：
 *     http://<本机IP>:8091/status
 *
 * 显示三块：
 *   程序：运行中 / 无响应 / 不在线   —— 判断毫米波程序有没有在跑
 *   人员：有人 / 无人                —— 只判断有没有人，不显示人数
 *   状态：正常 / 跌倒                 —— 有人时才判断
 *
 * 判定口径和毫米波程序界面顶部那行完全一致：
 *   有人  = person_detected（点数 > 10）
 *   跌倒  = fall_detected（原程序状态行的判据）
 * 所以网页和人机界面不会互相打脸。
 *
 * 不想要就删掉 index.html 里的 <script src="./radar.js">、那段 <style>
 * 和 footer 里的 <section class="footer-segment radar-seg">，原页面没动过。
 */
(function () {
  "use strict";

  var PORT = 8091;
  var API = "http://" + (location.hostname || "127.0.0.1") + ":" + PORT + "/status";
  var POLL_MS = 800;

  var progChip = document.getElementById("radarProgChip");
  var personChip = document.getElementById("radarPersonChip");
  var stateChip = document.getElementById("radarStateChip");
  var metricsEl = document.getElementById("radarMetrics");
  if (!progChip || !personChip || !stateChip) return;

  function setChip(chip, text, tone) {
    var v = chip.querySelector(".chip-value");
    if (v && v.textContent !== text) v.textContent = text;
    if (chip.dataset.tone !== tone) chip.dataset.tone = tone;
  }

  function setAll(prog, person, st, metrics) {
    setChip(progChip, prog[0], prog[1]);
    setChip(personChip, person[0], person[1]);
    setChip(stateChip, st[0], st[1]);
    if (metricsEl && metricsEl.textContent !== metrics) metricsEl.textContent = metrics;
  }

  // 接口取不到 = 程序没在跑
  function renderDead() {
    setAll(["不在线", "off"], ["--", "off"], ["--", "off"],
           "毫米波程序未运行（" + API + "）");
  }

  function render(d) {
    if (!d) return renderDead();

    // 能取到但 3 秒没更新 = 进程还在、数据卡住了
    if (d.stale) {
      setAll(["无响应", "alarm"], ["--", "off"], ["--", "off"],
             "程序进程还在，但超过 3 秒没出新数据");
      return;
    }

    var prog = ["运行中", "on"];

    // 有没有人：只看 person_detected（点数 > 10），不显示人数
    var hasPerson = d.online && !!d.person_detected;
    var person = ["无数据", "off"];
    if (d.online) person = hasPerson ? ["有人", "on"] : ["无人", "off"];

    // 正常 / 跌倒：有人时才判断
    var st = ["--", "off"];
    if (hasPerson) {
      st = d.fall_detected ? ["跌倒", "alarm"] : ["正常", "on"];
    }

    var metrics = "点 " + (d.points != null ? d.points : "-") +
                  " · 簇 " + (d.clusters != null ? d.clusters : "-") +
                  " · " + (d.fps != null ? d.fps : "-") + " fps" +
                  " · 雷达 " + (d.radar_pkt_rate != null ? d.radar_pkt_rate : "-") + " 包/秒" +
                  " · 延迟 " + (d.delay_ms != null ? Math.round(d.delay_ms) : "-") + " ms";
    setAll(prog, person, st, metrics);
  }

  function tick() {
    fetch(API, { cache: "no-store" })
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .then(render)
      .catch(renderDead);
  }

  tick();
  setInterval(tick, POLL_MS);
})();