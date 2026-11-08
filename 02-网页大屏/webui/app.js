const sensorGrid = document.getElementById("sensorGrid");
const coilWrap = document.getElementById("coilWrap");
const onlineBadge = document.getElementById("onlineBadge");
const updatedAtEl = document.getElementById("updatedAt");
const wallClockEl = document.getElementById("wallClock");
const versionTagEl = document.getElementById("versionTag");
const logList = document.getElementById("logList");
const alertList = document.getElementById("alertList");

const brightnessRange = document.getElementById("brightnessRange");
const fanRange = document.getElementById("fanRange");
const brightnessValue = document.getElementById("brightnessValue");
const fanValue = document.getElementById("fanValue");

const kpiCO2 = document.getElementById("kpiCO2");
const kpiTemp = document.getElementById("kpiTemp");
const kpiHumidity = document.getElementById("kpiHumidity");
const kpiNoise = document.getElementById("kpiNoise");

const qualityAnomalyCount = document.getElementById("qualityAnomalyCount");
const qualityCompleteness = document.getElementById("qualityCompleteness");
const qualityCompletenessSub = document.getElementById("qualityCompletenessSub");
const qualityMeterFill = document.getElementById("qualityMeterFill");

const gaugeChartEl = document.getElementById("gaugeChart");
const actuatorChartEl = document.getElementById("actuatorChart");
const trendChartEl = document.getElementById("trendChart");

const gaugeSideCO2 = document.getElementById("gaugeSideCO2");
const gaugeSideTemp = document.getElementById("gaugeSideTemp");
const gaugeSideHum = document.getElementById("gaugeSideHum");
const gaugeSideNoise = document.getElementById("gaugeSideNoise");

const sideBrightness = document.getElementById("sideBrightness");
const sideFan = document.getElementById("sideFan");
const sideActiveCount = document.getElementById("sideActiveCount");
const sideActivePercent = document.getElementById("sideActivePercent");

const sceneButtons = Array.from(document.querySelectorAll(".scene-btn"));

const sensorRanges = {
  "二氧化碳": [450, 2000],
  "TVOC总挥发物": [0, 2.0],
  "甲醛": [0, 1.2],
  "PM2.5": [0, 200],
  "湿度": [20, 90],
  "温度": [10, 40],
  "PM10": [0, 300],
  "PM1.0": [0, 150],
  "光照度": [0, 2000],
  "噪声": [30, 100],
};

const alarmRules = {
  "二氧化碳": { warn: 1000, err: 1500, unit: "ppm" },
  "TVOC总挥发物": { warn: 0.8, err: 1.2, unit: "mg/m³" },
  "甲醛": { warn: 0.08, err: 0.15, unit: "mg/m³" },
  "PM2.5": { warn: 75, err: 115, unit: "μg/m³" },
  "湿度": { warnLow: 30, warnHigh: 75, errLow: 25, errHigh: 85, unit: "%RH" },
  "温度": { warnLow: 18, warnHigh: 30, errLow: 15, errHigh: 35, unit: "℃" },
  "PM10": { warn: 150, err: 250, unit: "μg/m³" },
  "PM1.0": { warn: 80, err: 120, unit: "μg/m³" },
  "光照度": { warn: 1500, err: 1800, unit: "Lux" },
  "噪声": { warn: 70, err: 85, unit: "dB" },
};

let brightnessTimer = null;
let fanTimer = null;
let chartWarned = false;
let brightnessSyncHoldUntil = 0;
let fanSyncHoldUntil = 0;

let gaugeChart = null;
let actuatorChart = null;
let trendChart = null;

const MAX_HISTORY = 64;
const REFRESH_INTERVAL_MS = 1000;
const RANGE_SYNC_HOLD_MS = 1500;
const DASHBOARD_VERSION = "v2.0.0-real";

const trendHistory = {
  t: [],
  co2: [],
  temp: [],
  hum: [],
  noise: [],
};

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function fmt1(v) {
  return Number(v || 0).toFixed(1);
}

function fmt2(v) {
  return Number(v || 0).toFixed(2);
}

function setText(node, text) {
  if (node) node.textContent = text;
}

function formatIsoLocal(iso) {
  if (!iso) return "--";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso);
  return d.toLocaleString();
}

function logEvent(text, type = "ok") {
  if (!logList) return;
  const li = document.createElement("li");
  li.className = `log-item ${type}`;
  const time = new Date().toLocaleTimeString();
  li.textContent = `[${time}] ${text}`;
  logList.prepend(li);
  while (logList.children.length > 2) {
    logList.removeChild(logList.lastChild);
  }
}

function updateWallClock() {
  const now = new Date();
  const y = now.getFullYear();
  const m = String(now.getMonth() + 1).padStart(2, "0");
  const d = String(now.getDate()).padStart(2, "0");
  const hh = String(now.getHours()).padStart(2, "0");
  const mm = String(now.getMinutes()).padStart(2, "0");
  const ss = String(now.getSeconds()).padStart(2, "0");
  if (wallClockEl) {
    wallClockEl.textContent = `${y}年${m}月${d}日 ${hh}:${mm}:${ss}`;
  }
}

async function postJSON(url, payload) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await res.json();
  if (!res.ok || !data.success) {
    throw new Error(data.error || `HTTP ${res.status}`);
  }
  return data;
}

function renderSensorCards(pretty) {
  if (!sensorGrid) return;

  if (sensorGrid.children.length === 0) {
    pretty.forEach((item, idx) => {
      const card = document.createElement("article");
      card.className = "sensor-card";
      card.style.animationDelay = `${idx * 30}ms`;
      card.innerHTML = `
        <p class="sensor-tag">${item.tag}</p>
        <p class="sensor-val" data-tag="${item.tag}">--</p>
        <div class="sensor-meter">
          <span class="sensor-meter-fill" data-meter="${item.tag}"></span>
        </div>
      `;
      sensorGrid.appendChild(card);
    });
  }

  pretty.forEach((item) => {
    const valNode = sensorGrid.querySelector(`.sensor-val[data-tag="${item.tag}"]`);
    const meterNode = sensorGrid.querySelector(`.sensor-meter-fill[data-meter="${item.tag}"]`);
    if (valNode) {
      valNode.textContent = `${fmt2(item.value)} ${item.unit}`;
    }
    if (meterNode) {
      const [min, max] = sensorRanges[item.tag] || [0, 100];
      const percent = clamp(((Number(item.value) - min) / (max - min)) * 100, 0, 100);
      meterNode.style.width = `${percent.toFixed(1)}%`;
    }
  });
}

function renderCoils(coils, labels) {
  if (!coilWrap) return;

  if (coilWrap.children.length === 0) {
    Object.entries(coils).forEach(([key, enabled]) => {
      const btn = document.createElement("button");
      btn.className = "coil-btn";
      btn.dataset.key = key;
      btn.textContent = labels[key] || key;
      btn.addEventListener("click", async () => {
        const next = !btn.classList.contains("active");
        try {
          await postJSON("/api/control/coil", { name: key, enabled: next });
          btn.classList.toggle("active", next);
          logEvent(`${btn.textContent} ${next ? "开启" : "关闭"} 成功`);
        } catch (err) {
          logEvent(`${btn.textContent} 控制失败: ${err.message}`, "err");
        }
      });
      btn.classList.toggle("active", Boolean(enabled));
      coilWrap.appendChild(btn);
    });
  } else {
    Object.entries(coils).forEach(([key, enabled]) => {
      const btn = coilWrap.querySelector(`.coil-btn[data-key="${key}"]`);
      if (btn) btn.classList.toggle("active", Boolean(enabled));
    });
  }
}

function renderRanges(actuators) {
  const b = Number(actuators.brightness_percent || 0);
  const f = Number(actuators.fan_percent || 0);
  const nowTs = Date.now();

  if (brightnessRange && nowTs >= brightnessSyncHoldUntil) {
    brightnessRange.value = String(Math.round(b));
  }
  if (fanRange && nowTs >= fanSyncHoldUntil) {
    fanRange.value = String(Math.round(f));
  }
  if (brightnessValue && nowTs >= brightnessSyncHoldUntil) {
    brightnessValue.textContent = `${Math.round(b)}%`;
  }
  if (fanValue && nowTs >= fanSyncHoldUntil) {
    fanValue.textContent = `${Math.round(f)}%`;
  }

  setText(sideBrightness, `${Math.round(b)}%`);
  setText(sideFan, `${Math.round(f)}%`);
}

function renderMeta(meta, online) {
  if (onlineBadge) {
    onlineBadge.textContent = online ? "在线" : "离线";
    onlineBadge.classList.toggle("online", online);
  }
  if (updatedAtEl) {
    updatedAtEl.textContent = `更新时间: ${formatIsoLocal(meta.updated_at)}`;
  }
}

function renderKPI(pretty) {
  const byTag = Object.fromEntries(pretty.map((x) => [x.tag, Number(x.value)]));
  if (kpiCO2) kpiCO2.textContent = Number(byTag["二氧化碳"] || 0).toFixed(0);
  if (kpiTemp) kpiTemp.textContent = fmt1(byTag["温度"]);
  if (kpiHumidity) kpiHumidity.textContent = fmt1(byTag["湿度"]);
  if (kpiNoise) kpiNoise.textContent = fmt1(byTag["噪声"]);
}

function getTagValue(pretty, tag) {
  const node = pretty.find((x) => x.tag === tag);
  return Number(node ? node.value : 0);
}

function computeComfortScore(pretty) {
  const co2 = getTagValue(pretty, "二氧化碳");
  const temp = getTagValue(pretty, "温度");
  const hum = getTagValue(pretty, "湿度");
  const noise = getTagValue(pretty, "噪声");

  const co2Score = clamp(100 - (co2 - 450) / 8.5, 0, 100);
  const tempScore = clamp(100 - Math.abs(temp - 24) * 7.5, 0, 100);
  const humScore = clamp(100 - Math.abs(hum - 50) * 2.4, 0, 100);
  const noiseScore = clamp(100 - (noise - 35) * 2.2, 0, 100);

  return Math.round(co2Score * 0.42 + tempScore * 0.22 + humScore * 0.2 + noiseScore * 0.16);
}

function evaluateAlarm(item) {
  const rule = alarmRules[item.tag];
  if (!rule) return { level: "ok", message: `${item.tag} 正常` };

  const v = Number(item.value);

  if (typeof rule.err === "number" && v >= rule.err) {
    return { level: "err", message: `${item.tag} 过高 ${fmt2(v)} ${rule.unit}` };
  }
  if (typeof rule.warn === "number" && v >= rule.warn) {
    return { level: "warn", message: `${item.tag} 偏高 ${fmt2(v)} ${rule.unit}` };
  }

  if (typeof rule.errLow === "number" && (v <= rule.errLow || v >= rule.errHigh)) {
    return { level: "err", message: `${item.tag} 越界 ${fmt2(v)} ${rule.unit}` };
  }
  if (typeof rule.warnLow === "number" && (v <= rule.warnLow || v >= rule.warnHigh)) {
    return { level: "warn", message: `${item.tag} 偏离 ${fmt2(v)} ${rule.unit}` };
  }

  return { level: "ok", message: `${item.tag} 正常` };
}

function renderQuality(pretty) {
  const count = pretty.length;
  const completeness = Math.round((count / 10) * 100);
  const anomalyCount = pretty
    .map((item) => evaluateAlarm(item))
    .filter((a) => a.level === "warn" || a.level === "err").length;

  if (qualityAnomalyCount) qualityAnomalyCount.textContent = String(anomalyCount);
  if (qualityCompleteness) qualityCompleteness.textContent = `${completeness}%`;
  if (qualityCompletenessSub) qualityCompletenessSub.textContent = `${count}/10`;
  if (qualityMeterFill) {
    qualityMeterFill.style.width = `${completeness}%`;
    qualityMeterFill.style.background = anomalyCount > 0
      ? "linear-gradient(90deg, #ffd166, #ff5b6e)"
      : "linear-gradient(90deg, #00e5ff, #19f5c1)";
  }
}

function renderAlerts(pretty, actuators) {
  if (!alertList) return;
  const alertItems = [];

  pretty.forEach((item) => {
    const alarm = evaluateAlarm(item);
    if (alarm.level !== "ok") {
      alertItems.push(alarm);
    }
  });

  if (actuators && actuators.coils && actuators.coils.red) {
    alertItems.push({ level: "warn", message: "红灯当前处于激活状态" });
  }

  const sorted = alertItems.sort((a, b) => {
    const p = { err: 2, warn: 1, ok: 0 };
    return p[b.level] - p[a.level];
  });

  const finalItems = sorted.slice(0, 2);
  if (finalItems.length === 0) {
    finalItems.push({ level: "ok", message: "当前无阈值告警" });
  }

  alertList.innerHTML = "";
  finalItems.forEach((a) => {
    const li = document.createElement("li");
    li.className = `alert-item ${a.level}`;
    li.textContent = a.message;
    alertList.appendChild(li);
  });
}

function ensureCharts() {
  if (!window.echarts || !gaugeChartEl || !actuatorChartEl || !trendChartEl) {
    if (!chartWarned) {
      chartWarned = true;
      logEvent("ECharts 未加载，仪表盘不可用", "err");
    }
    return false;
  }

  if (!gaugeChart) {
    gaugeChart = window.echarts.init(gaugeChartEl);
    actuatorChart = window.echarts.init(actuatorChartEl);
    trendChart = window.echarts.init(trendChartEl);

    window.addEventListener("resize", () => {
      gaugeChart.resize();
      actuatorChart.resize();
      trendChart.resize();
    });

    gaugeChart.setOption({
      backgroundColor: "transparent",
      series: [
        {
          type: "gauge",
          min: 0,
          max: 100,
          startAngle: 210,
          endAngle: -30,
          splitNumber: 10,
          radius: "92%",
          progress: {
            show: true,
            width: 22,
            itemStyle: {
              color: new window.echarts.graphic.LinearGradient(0, 0, 1, 0, [
                { offset: 0, color: "#00e5ff" },
                { offset: 1, color: "#19f5c1" },
              ]),
            },
          },
          axisLine: { lineStyle: { width: 22, color: [[1, "rgba(92, 197, 255, 0.22)"]] } },
          axisTick: { show: false },
          splitLine: { show: false },
          axisLabel: { show: false },
          pointer: { show: true, length: "70%", width: 5, itemStyle: { color: "#dcf8ff" } },
          anchor: {
            show: true,
            size: 12,
            itemStyle: { color: "#0a1f35", borderColor: "#00e5ff", borderWidth: 2 },
          },
          title: { offsetCenter: [0, "70%"], color: "#89bdd4", fontSize: 14 },
          detail: { valueAnimation: true, offsetCenter: [0, "14%"], color: "#e5fbff", fontSize: 48, formatter: "{value}" },
          data: [{ value: 0, name: "舒适指数" }],
        },
      ],
    });

    actuatorChart.setOption({
      backgroundColor: "transparent",
      tooltip: { trigger: "item" },
      legend: { left: "center", bottom: 0, itemGap: 12, textStyle: { color: "#9fcbde", fontSize: 12 } },
      series: [
        {
          name: "执行器占用",
          type: "pie",
          radius: ["44%", "68%"],
          center: ["50%", "34%"],
          avoidLabelOverlap: false,
          label: { show: false },
          labelLine: { show: false },
          itemStyle: { borderColor: "#0a2038", borderWidth: 3 },
          data: [
            { value: 0, name: "已激活", itemStyle: { color: "#00e5ff" } },
            { value: 100, name: "未激活", itemStyle: { color: "rgba(90, 170, 210, 0.35)" } },
          ],
        },
      ],
    });

    trendChart.setOption({
      backgroundColor: "transparent",
      tooltip: {
        trigger: "axis",
        backgroundColor: "rgba(8, 24, 44, 0.9)",
        borderColor: "rgba(124,215,255,0.4)",
        textStyle: { color: "#d8f4ff", fontSize: 13 },
      },
      legend: { top: 4, textStyle: { color: "#9fcbde", fontSize: 13 } },
      grid: { left: 54, right: 34, top: 40, bottom: 30 },
      xAxis: {
        type: "category",
        boundaryGap: false,
        axisLine: { lineStyle: { color: "rgba(130, 205, 245, 0.4)" } },
        axisLabel: { color: "#89b4cc", fontSize: 12, hideOverlap: true },
        splitLine: { show: false },
        data: [],
      },
      yAxis: [
        {
          type: "value",
          name: "CO2",
          position: "left",
          nameTextStyle: { color: "#72dfff", fontSize: 12 },
          axisLabel: { color: "#92bed3", fontSize: 12 },
          splitLine: { lineStyle: { color: "rgba(114, 176, 210, 0.12)" } },
          axisLine: { show: true, lineStyle: { color: "#3b92bb" } },
        },
        {
          type: "value",
          name: "环境",
          position: "right",
          nameTextStyle: { color: "#ffd166", fontSize: 12 },
          axisLabel: { color: "#d7b57a", fontSize: 12 },
          splitLine: { show: false },
          axisLine: { show: true, lineStyle: { color: "#a8823a" } },
        },
      ],
      series: [
        {
          name: "CO2",
          type: "line",
          smooth: true,
          showSymbol: false,
          yAxisIndex: 0,
          lineStyle: { width: 2, color: "#00e5ff" },
          areaStyle: {
            color: new window.echarts.graphic.LinearGradient(0, 0, 0, 1, [
              { offset: 0, color: "rgba(0, 229, 255, 0.35)" },
              { offset: 1, color: "rgba(0, 229, 255, 0.02)" },
            ]),
          },
          data: [],
        },
        { name: "温度", type: "line", smooth: true, showSymbol: false, yAxisIndex: 1, lineStyle: { width: 2, color: "#ffd166" }, data: [] },
        { name: "湿度", type: "line", smooth: true, showSymbol: false, yAxisIndex: 1, lineStyle: { width: 2, color: "#19f5c1" }, data: [] },
        { name: "噪声", type: "line", smooth: true, showSymbol: false, yAxisIndex: 1, lineStyle: { width: 2, color: "#ff5b6e" }, data: [] },
      ],
    });
  }

  return true;
}

function updateCharts(data) {
  if (!ensureCharts()) return;

  const pretty = data.sensor.pretty || [];
  const co2 = getTagValue(pretty, "二氧化碳");
  const temp = getTagValue(pretty, "温度");
  const hum = getTagValue(pretty, "湿度");
  const noise = getTagValue(pretty, "噪声");
  const score = computeComfortScore(pretty);

  setText(gaugeSideCO2, `${co2.toFixed(0)}`);
  setText(gaugeSideTemp, `${temp.toFixed(1)}`);
  setText(gaugeSideHum, `${hum.toFixed(1)}`);
  setText(gaugeSideNoise, `${noise.toFixed(1)}`);

  const now = new Date();
  const label = `${now.getMinutes().toString().padStart(2, "0")}:${now
    .getSeconds()
    .toString()
    .padStart(2, "0")}`;

  trendHistory.t.push(label);
  trendHistory.co2.push(Number(co2.toFixed(1)));
  trendHistory.temp.push(Number(temp.toFixed(1)));
  trendHistory.hum.push(Number(hum.toFixed(1)));
  trendHistory.noise.push(Number(noise.toFixed(1)));

  if (trendHistory.t.length > MAX_HISTORY) {
    trendHistory.t.shift();
    trendHistory.co2.shift();
    trendHistory.temp.shift();
    trendHistory.hum.shift();
    trendHistory.noise.shift();
  }

  const coils = data.actuators.coils || {};
  const activeCount = Object.values(coils).filter(Boolean).length;
  const activePercent = Math.round((activeCount / 4) * 100);
  const inactivePercent = 100 - activePercent;

  setText(sideActiveCount, `${activeCount}/4`);
  setText(sideActivePercent, `${activePercent}%`);

  gaugeChart.setOption({
    series: [{ data: [{ value: score, name: "舒适指数" }] }],
  });

  actuatorChart.setOption({
    series: [
      {
        data: [
          { value: activePercent, name: "已激活", itemStyle: { color: "#00e5ff" } },
          { value: inactivePercent, name: "未激活", itemStyle: { color: "rgba(90, 170, 210, 0.35)" } },
        ],
      },
    ],
  });

  trendChart.setOption({
    xAxis: { data: trendHistory.t },
    series: [
      { data: trendHistory.co2 },
      { data: trendHistory.temp },
      { data: trendHistory.hum },
      { data: trendHistory.noise },
    ],
  });
}

async function applyScene(sceneName) {
  try {
    if (sceneName === "presentation") {
      await postJSON("/api/control/coil", { name: "buzzer", enabled: false });
      await postJSON("/api/control/coil", { name: "green", enabled: true });
      await postJSON("/api/control/coil", { name: "yellow", enabled: false });
      await postJSON("/api/control/coil", { name: "red", enabled: false });
      await postJSON("/api/control/brightness", { percent: 62 });
      await postJSON("/api/control/fan", { percent: 36 });
      logEvent("展示模式已应用");
      return;
    }

    if (sceneName === "alert") {
      await postJSON("/api/control/coil", { name: "buzzer", enabled: true });
      await postJSON("/api/control/coil", { name: "green", enabled: false });
      await postJSON("/api/control/coil", { name: "yellow", enabled: false });
      await postJSON("/api/control/coil", { name: "red", enabled: true });
      await postJSON("/api/control/brightness", { percent: 88 });
      await postJSON("/api/control/fan", { percent: 72 });
      logEvent("告警模式已应用", "err");
      return;
    }

    if (sceneName === "quiet") {
      await postJSON("/api/control/coil", { name: "buzzer", enabled: false });
      await postJSON("/api/control/coil", { name: "green", enabled: false });
      await postJSON("/api/control/coil", { name: "yellow", enabled: false });
      await postJSON("/api/control/coil", { name: "red", enabled: false });
      await postJSON("/api/control/brightness", { percent: 20 });
      await postJSON("/api/control/fan", { percent: 18 });
      logEvent("静音模式已应用");
    }
  } catch (err) {
    logEvent(`场景切换失败: ${err.message}`, "err");
  }
}

function bindSceneEvents() {
  sceneButtons.forEach((btn) => {
    btn.addEventListener("click", () => {
      applyScene(btn.dataset.scene);
    });
  });
}

function bindRangeEvents() {
  if (brightnessRange) {
    brightnessRange.addEventListener("input", () => {
      const val = Number(brightnessRange.value);
      brightnessSyncHoldUntil = Date.now() + RANGE_SYNC_HOLD_MS;
      if (brightnessValue) brightnessValue.textContent = `${val}%`;
      if (brightnessTimer) clearTimeout(brightnessTimer);
      brightnessTimer = setTimeout(async () => {
        try {
          await postJSON("/api/control/brightness", { percent: val });
          logEvent(`小灯亮度设置为 ${val}%`);
        } catch (err) {
          brightnessSyncHoldUntil = 0;
          logEvent(`小灯亮度控制失败: ${err.message}`, "err");
        }
      }, 180);
    });
  }

  if (fanRange) {
    fanRange.addEventListener("input", () => {
      const val = Number(fanRange.value);
      fanSyncHoldUntil = Date.now() + RANGE_SYNC_HOLD_MS;
      if (fanValue) fanValue.textContent = `${val}%`;
      if (fanTimer) clearTimeout(fanTimer);
      fanTimer = setTimeout(async () => {
        try {
          await postJSON("/api/control/fan", { percent: val });
          logEvent(`风扇设置为 ${val}%`);
        } catch (err) {
          fanSyncHoldUntil = 0;
          logEvent(`风扇控制失败: ${err.message}`, "err");
        }
      }, 180);
    });
  }
}

async function refreshStatus() {
  try {
    const res = await fetch("/api/status");
    const data = await res.json();
    if (!data.success) throw new Error(data.error || "unknown");

    const pretty = data.sensor.pretty || [];

    renderMeta(data.meta, Boolean(data.sensor.online));
    renderSensorCards(pretty);
    renderKPI(pretty);
    renderQuality(pretty);
    renderAlerts(pretty, data.actuators || {});

    renderCoils(data.actuators.coils || {}, data.actuators.coil_labels || {});
    renderRanges(data.actuators || {});
    updateCharts(data);
  } catch (err) {
    logEvent(`状态刷新失败: ${err.message}`, "err");
  }
}

async function bootstrap() {
  updateWallClock();
  setInterval(updateWallClock, 1000);

  if (versionTagEl) versionTagEl.textContent = DASHBOARD_VERSION;

  bindSceneEvents();
  bindRangeEvents();

  await refreshStatus();
  logEvent("DataV 大屏已更新");
  setInterval(refreshStatus, REFRESH_INTERVAL_MS);
}

bootstrap();














