(function () {
  "use strict";

  var COLORS = ["#4f6ef7", "#22b8cf", "#12b886", "#f59f00", "#e64980", "#845ef7", "#74b816", "#f76707"];
  var $ = function (id) { return document.getElementById(id); };

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  function fmt(v) {
    if (typeof v === "number") {
      return v.toLocaleString("zh-CN", { maximumFractionDigits: 2 });
    }
    return String(v == null ? "" : v);
  }

  function hideError() { $("error").classList.add("hidden"); $("error").textContent = ""; }
  function showError(msg) {
    var e = $("error");
    e.textContent = msg;
    e.classList.remove("hidden");
  }

  // ---------------------------------------------------------------- 表格
  function renderTable(columns, rows) {
    var box = $("table");
    box.innerHTML = "";
    if (!columns || !columns.length) { box.textContent = "（无结果）"; return; }
    var html = "<table><thead><tr>";
    for (var i = 0; i < columns.length; i++) html += "<th>" + esc(columns[i]) + "</th>";
    html += "</tr></thead><tbody>";
    for (var r = 0; r < rows.length; r++) {
      html += "<tr>";
      for (var c = 0; c < rows[r].length; c++) {
        var v = rows[r][c];
        var cls = typeof v === "number" ? ' class="num"' : "";
        html += "<td" + cls + ">" + esc(fmt(v)) + "</td>";
      }
      html += "</tr>";
    }
    html += "</tbody></table>";
    box.innerHTML = html;
  }

  // ---------------------------------------------------------------- 图表
  function numberCard(viz, columns, rows) {
    var v = rows.length ? rows[0][0] : 0;
    var label = viz.y || (columns.length ? columns[0] : "");
    return "<div><div class='kpi'>" + esc(fmt(v)) + "</div><div class='kpi-label'>" + esc(label) + "</div></div>";
  }

  function bars(viz, columns, rows) {
    var W = 560, H = 300, padL = 40, padB = 60, padT = 20, padR = 20;
    var innerW = W - padL - padR, innerH = H - padT - padB;
    var vals = rows.map(function (r) { return Number(r[1]); });
    var max = Math.max.apply(null, vals.concat([1]));
    var n = rows.length;
    var band = innerW / Math.max(n, 1);
    var barW = Math.min(band * 0.6, 48);
    var s = "<svg viewBox='0 0 " + W + " " + H + "' xmlns='http://www.w3.org/2000/svg'>";
    for (var i = 0; i < n; i++) {
      var h = (vals[i] / max) * innerH;
      var x = padL + i * band + (band - barW) / 2;
      var y = padT + innerH - h;
      s += "<rect x='" + x.toFixed(1) + "' y='" + y.toFixed(1) + "' width='" + barW.toFixed(1) + "' height='" + h.toFixed(1) + "' rx='3' fill='" + COLORS[i % COLORS.length] + "'></rect>";
      s += "<text x='" + (x + barW / 2).toFixed(1) + "' y='" + (padT + innerH + 16) + "' text-anchor='middle' font-size='11' fill='#6b7280'>" + esc(String(rows[i][0])) + "</text>";
    }
    s += "</svg>";
    return s;
  }

  function pie(viz, columns, rows) {
    var W = 560, H = 300, cx = 150, cy = 150, r = 110;
    var vals = rows.map(function (r) { return Number(r[1]); });
    var total = vals.reduce(function (a, b) { return a + b; }, 0) || 1;
    var angle = -Math.PI / 2;
    var s = "<svg viewBox='0 0 " + W + " " + H + "' xmlns='http://www.w3.org/2000/svg'>";
    for (var i = 0; i < vals.length; i++) {
      var frac = vals[i] / total;
      var end = angle + frac * 2 * Math.PI;
      var x1 = cx + r * Math.cos(angle), y1 = cy + r * Math.sin(angle);
      var x2 = cx + r * Math.cos(end), y2 = cy + r * Math.sin(end);
      var large = frac > 0.5 ? 1 : 0;
      s += "<path d='M " + cx + " " + cy + " L " + x1.toFixed(2) + " " + y1.toFixed(2) + " A " + r + " " + r + " 0 " + large + " 1 " + x2.toFixed(2) + " " + y2.toFixed(2) + " Z' fill='" + COLORS[i % COLORS.length] + "'></path>";
      angle = end;
    }
    // 图例
    var lx = 300, ly = 40;
    for (var j = 0; j < rows.length && j < 10; j++) {
      var pct = (vals[j] / total * 100).toFixed(1);
      s += "<rect x='" + lx + "' y='" + (ly + j * 24) + "' width='12' height='12' rx='2' fill='" + COLORS[j % COLORS.length] + "'></rect>";
      s += "<text x='" + (lx + 18) + "' y='" + (ly + j * 24 + 11) + "' font-size='12' fill='#1a1d2e'>" + esc(String(rows[j][0]) + " (" + pct + "%)") + "</text>";
    }
    s += "</svg>";
    return s;
  }

  function line(viz, columns, rows) {
    var W = 560, H = 300, padL = 50, padB = 60, padT = 20, padR = 20;
    var innerW = W - padL - padR, innerH = H - padT - padB;
    var vals = rows.map(function (r) { return Number(r[1]); });
    var max = Math.max.apply(null, vals.concat([1]));
    var n = rows.length;
    var pts = "";
    for (var i = 0; i < n; i++) {
      var x = padL + (n === 1 ? innerW / 2 : (i / (n - 1)) * innerW);
      var y = padT + innerH - (vals[i] / max) * innerH;
      pts += (i ? " " : "") + x.toFixed(1) + "," + y.toFixed(1);
    }
    var s = "<svg viewBox='0 0 " + W + " " + H + "' xmlns='http://www.w3.org/2000/svg'>";
    s += "<polyline points='" + pts + "' fill='none' stroke='#4f6ef7' stroke-width='2.5'></polyline>";
    var step = Math.max(1, Math.floor((n - 1) / 8));
    for (var k = 0; k < n; k++) {
      var lx = padL + (n === 1 ? innerW / 2 : (k / (n - 1)) * innerW);
      var ly = padT + innerH - (vals[k] / max) * innerH;
      s += "<circle cx='" + lx.toFixed(1) + "' cy='" + ly.toFixed(1) + "' r='3' fill='#4f6ef7'></circle>";
      if (k % step === 0 || k === n - 1) {
        var label = String(rows[k][0]).slice(0, 10);
        s += "<text x='" + lx.toFixed(1) + "' y='" + (padT + innerH + 16) + "' text-anchor='middle' font-size='11' fill='#6b7280'>" + esc(label) + "</text>";
      }
    }
    s += "</svg>";
    return s;
  }

  function renderChart(viz, columns, rows) {
    var box = $("chart");
    box.innerHTML = "";
    if (!viz || !rows || !rows.length) { box.textContent = "（无数据）"; return; }
    var chart = viz.chart;
    if (chart === "number") { box.innerHTML = numberCard(viz, columns, rows); }
    else if (chart === "bar") { box.innerHTML = bars(viz, columns, rows); }
    else if (chart === "pie") { box.innerHTML = pie(viz, columns, rows); }
    else if (chart === "line") { box.innerHTML = line(viz, columns, rows); }
    else { box.textContent = "该结果以表格形式展示"; }
  }

  // ---------------------------------------------------------------- 主流程
  function render(data) {
    if (data.error) { showError(data.error); return; }
    hideError();
    $("dsl").textContent = JSON.stringify(data.dsl, null, 2);
    $("sql").textContent = data.sql;
    $("explain").textContent = data.explanation || "";
    renderChart(data.viz, data.columns, data.rows);
    renderTable(data.columns, data.rows);
  }

  function run() {
    var q = $("query").value.trim();
    if (!q) { showError("请输入问题"); return; }
    hideError();
    var btn = $("run");
    btn.disabled = true;
    btn.textContent = "查询中…";
    var principal = $("principal").value || null;
    fetch("/api/query", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query: q, principal: principal })
    })
      .then(function (r) { return r.json(); })
      .then(function (data) { render(data); })
      .catch(function (err) { showError("请求失败：" + err); })
      .finally(function () {
        btn.disabled = false;
        btn.textContent = "查询";
      });
  }

  $("run").addEventListener("click", run);
  $("query").addEventListener("keydown", function (e) { if (e.key === "Enter") run(); });
  run();
})();
