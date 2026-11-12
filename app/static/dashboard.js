"use strict";
const $ = s => document.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const store = { get: k => { try { return localStorage.getItem(k); } catch { return null; } }, set: (k, v) => { try { localStorage.setItem(k, v); } catch {} } };
const ICON = {
  ok: '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>',
  bad: '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round"><path d="M12 7v6M12 17v.5"/></svg>',
  idle: '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M4 16l5-7 4 4 7-9"/></svg>',
  edit: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 20h4L19 9l-4-4L4 16z"/><path d="M13.5 6.5l4 4"/></svg>',
  pause: '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="5" width="4" height="14" rx="1"/><rect x="14" y="5" width="4" height="14" rx="1"/></svg>',
  play: '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><path d="M8 5.5v13l11-6.5z"/></svg>',
  trash: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13"/></svg>',
};
const SLOTS = 40;
let apiKey = store.get("wt_key") || "", filter = "all", monitors = [], loadedAt = null, keyWaiters = null;

/* тема */
const applyTheme = t => { document.documentElement.dataset.theme = t; };
applyTheme(store.get("wt_theme") || (matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark"));
$("#theme").onclick = () => { const t = document.documentElement.dataset.theme === "light" ? "dark" : "light"; applyTheme(t); store.set("wt_theme", t); };

/* API */
function askKey() {
  if (!keyWaiters) {
    keyWaiters = [];
    $("#key-err").textContent = apiKey ? "Ключ не подошёл" : "";
    $("#f-key").value = "";
    $("#key-dlg").showModal();
  }
  return new Promise(resolve => keyWaiters.push(resolve));
}
$("#key-dlg").addEventListener("cancel", e => e.preventDefault());
$("#key-form").addEventListener("submit", () => {
  apiKey = $("#f-key").value.trim(); store.set("wt_key", apiKey);
  const waiters = keyWaiters; keyWaiters = null; waiters.forEach(r => r());
});
async function api(path, opts = {}) {
  const r = await fetch("/api" + path, { ...opts, headers: { "Content-Type": "application/json", "X-API-Key": apiKey } });
  if (r.status === 401) { await askKey(); return api(path, opts); }
  if (r.status === 429) {
    const min = Math.ceil((+r.headers.get("Retry-After") || 60) / 60);
    throw new Error(`Слишком много неверных ключей. Повторите через ${min} мин`);
  }
  if (!r.ok) {
    const d = (await r.json().catch(() => ({}))).detail;
    if (typeof d === "string") throw new Error(d);
    const field = Array.isArray(d) ? d[0]?.loc?.at(-1) : null;
    throw new Error({ url: "Некорректный адрес: он должен начинаться с http:// или https://", name: "Укажите название",
      keyword: "Проверьте ключевое слово", interval_seconds: "Недопустимый интервал", timeout_seconds: "Таймаут — от 1 до 60 секунд",
      failure_threshold: "Число неудач — от 1 до 20", expected_status: "Код ответа — от 100 до 599" }[field] ?? "Проверьте введённые данные");
  }
  return r.status === 204 ? null : r.json();
}

/* форматирование */
const pct = v => v == null ? "—" : (v >= 99.995 ? "100" : v.toFixed(2)) + "%";
const ms = v => v == null ? "—" : Math.round(v) + " мс";
function ago(iso) {
  if (!iso) return "ещё не проверялся";
  const s = Math.max(0, Math.round((Date.now() - new Date(iso)) / 1000));
  if (s < 60) return s + " сек назад";
  if (s < 3600) return Math.floor(s / 60) + " мин назад";
  if (s < 86400) return Math.floor(s / 3600) + " ч назад";
  return Math.floor(s / 86400) + " дн назад";
}
function span(a, b) {
  const m = Math.max(1, Math.round((new Date(b) - new Date(a)) / 60000));
  return m < 60 ? m + " мин" : m < 1440 ? Math.floor(m / 60) + " ч" + (m % 60 ? " " + (m % 60) + " мин" : "") : Math.floor(m / 1440) + " дн " + Math.floor(m % 1440 / 60) + " ч";
}
const dt = iso => new Date(iso).toLocaleString("ru-RU", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
const every = s => s < 60 ? s + " сек" : s < 3600 ? s / 60 + " мин" : s / 3600 + " ч";

const HUES = [[124,131,255],[176,124,255],[61,190,220],[255,140,90],[240,110,170],[80,200,140],[250,190,70]];
function avatar(name) {
  let h = 0; for (const ch of name) h = (h * 31 + ch.codePointAt(0)) >>> 0;
  const [a, b] = [HUES[h % HUES.length], HUES[(h + 3) % HUES.length]];
  return `<span class="ava" style="background:linear-gradient(135deg, rgb(${a}), rgb(${b}))">${esc([...name.trim()][0]?.toUpperCase() ?? "?")}</span>`;
}

/* плавный график отклика; точки сохраняем, чтобы показывать подсказку под курсором */
const charts = new Map();
function chart(m) {
  const pts = m.recent.filter(c => c.latency_ms != null);
  if (pts.length < 2) return `<div class="chart empty">${m.recent.length ? "Недостаточно данных для графика" : "График появится после первых проверок"}</div>`;
  const W = 400, H = 74, top = 14, bottom = 10, v = pts.map(c => c.latency_ms);
  const max = Math.max(...v), min = Math.min(...v), range = max - min || 1;
  const xy = v.map((y, i) => [i * W / (v.length - 1), H - bottom - (y - min) / range * (H - top - bottom)]);
  let d = `M${xy[0][0]} ${xy[0][1].toFixed(1)}`;
  for (let i = 1; i < xy.length; i++) {
    const [x0, y0] = xy[i - 1], [x1, y1] = xy[i], cx = (x0 + x1) / 2;
    d += ` C${cx.toFixed(1)} ${y0.toFixed(1)} ${cx.toFixed(1)} ${y1.toFixed(1)} ${x1.toFixed(1)} ${y1.toFixed(1)}`;
  }
  charts.set(String(m.id), { pts, xy, W, H });
  const last = xy[xy.length - 1], g = "g" + m.id;
  return `<div class="chart" data-chart="${m.id}">
    <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="График времени отклика">
      <defs><linearGradient id="${g}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="rgb(var(--c))" stop-opacity=".38"/><stop offset="1" stop-color="rgb(var(--c))" stop-opacity="0"/></linearGradient></defs>
      <path class="area" d="${d} L${W} ${H} L0 ${H} Z" fill="url(#${g})"/>
      <path class="line" d="${d}" pathLength="1" fill="none" stroke="rgb(var(--c))" stroke-width="2" stroke-linecap="round" vector-effect="non-scaling-stroke"/>
    </svg>
    <i class="cursor"></i><i class="dot hov"></i>
    <i class="dot" style="left:min(${(last[0] / W * 100).toFixed(2)}%, calc(100% - 8px));top:${(last[1] / H * 100).toFixed(2)}%"></i>
  </div>`;
}

function card(m, i) {
  const up = m.stats.uptime_percent, lat = m.stats.latency_ms_24h;
  const state = !m.is_active ? "paused" : m.status;
  const label = { up: "Работает", down: "Недоступен", unknown: "Ожидает проверки", paused: "На паузе" }[state];
  const recent = m.recent.slice(-SLOTS);
  const bars = Array(Math.max(0, SLOTS - recent.length)).fill("<i></i>").join("") + recent.map((c, n) =>
    `<i class="${c.ok ? "ok" : "fail"}" style="--n:${n}" data-tip="<b>${c.ok ? "Успешно" : "Сбой"}${c.latency_ms != null ? " · " + Math.round(c.latency_ms) + " мс" : ""}</b><span>${esc(dt(c.at))}</span>"></i>`).join("");
  const tls = m.tls_expires_at ? Math.floor((new Date(m.tls_expires_at) - Date.now()) / 864e5) : null;
  const tlsCls = tls == null ? "" : tls <= 3 ? "bad" : tls <= 14 ? "warn" : "";
  const good = v => v != null && v >= 99.9 ? "good" : "";
  return `<article class="mon glass rise ${state}" style="--i:${i + 2}">
    <div class="mon-head">
      ${avatar(m.name)}
      <div class="title"><b>${esc(m.name)}</b><a href="${esc(m.url)}" target="_blank" rel="noopener noreferrer">${esc(m.url.replace(/^https?:\/\//, "").replace(/\/$/, ""))}</a></div>
      <div class="acts">
        <button class="btn sm" data-toggle="${m.id}" data-active="${m.is_active}" title="${m.is_active ? "Поставить на паузу" : "Возобновить"}" aria-label="${m.is_active ? "Поставить на паузу" : "Возобновить"}">${m.is_active ? ICON.pause : ICON.play}</button>
        <button class="btn sm" data-edit="${m.id}" title="Изменить" aria-label="Изменить">${ICON.edit}</button>
        <button class="btn sm danger" data-del="${m.id}" data-name="${esc(m.name)}" title="Удалить" aria-label="Удалить">${ICON.trash}</button>
      </div>
    </div>
    ${state === "down" ? `<div class="err">Сайт не отвечает. Неудачных проверок подряд: ${m.consecutive_failures}</div>` : ""}
    <div class="now">
      <div><small>Время отклика</small><b class="num">${m.last_latency_ms != null ? Math.round(m.last_latency_ms) + "<span>мс</span>" : "—"}</b></div>
      <span class="pill">${label}</span>
    </div>
    ${chart(m)}
    <div class="hist">${bars}</div>
    <div class="cap"><span>последние ${SLOTS} проверок · каждые ${every(m.interval_seconds)}</span><span>${esc(ago(m.last_checked_at))}</span></div>
    <div class="kv num">
      <div class="${good(up["24h"])}">Uptime 24 ч<b>${pct(up["24h"])}</b></div>
      <div class="${good(up["30d"])}">30 дней<b>${pct(up["30d"])}</b></div>
      <div>p95 отклика<b>${ms(lat.p95)}</b></div>
      <div class="${tlsCls}">SSL<b>${tls == null ? "—" : tls < 0 ? "истёк" : tls + " дн"}</b></div>
    </div>
  </article>`;
}

/* плавный набор числа — только при первой загрузке */
function countUp(el, to, fmt) {
  if (!firstPaint || matchMedia("(prefers-reduced-motion: reduce)").matches) { el.innerHTML = fmt(to); return; }
  const t0 = performance.now(), dur = 1100;
  const step = t => { const k = Math.min(1, (t - t0) / dur), e = 1 - Math.pow(1 - k, 3); el.innerHTML = fmt(to * e); if (k < 1) requestAnimationFrame(step); else el.innerHTML = fmt(to); };
  requestAnimationFrame(step);
}
const SI = {
  up: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>',
  down: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><path d="M12 7v6M12 17v.5"/></svg>',
  bolt: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M13 3L5 14h6l-1 7 8-11h-6z"/></svg>',
  flag: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 21V4M5 4h11l-2 4 2 4H5"/></svg>',
};
let firstPaint = true;

function render(inc) {
  const active = monitors.filter(m => m.is_active);
  const down = active.filter(m => m.status === "down");
  const upN = active.filter(m => m.status === "up").length;
  const paused = monitors.length - active.length;
  const state = !monitors.length || !active.length ? "idle" : down.length ? "bad" : "ok";
  document.body.dataset.state = state;
  $("#live").textContent = { idle: "Ожидание", ok: "В эфире", bad: "Инцидент" }[state];
  $("#hero-title").textContent = !monitors.length ? "Добро пожаловать" : !active.length ? "Все мониторы на паузе"
    : down.length === 1 ? `Недоступен: ${down[0].name}` : down.length ? `Недоступно сервисов: ${down.length} из ${active.length}` : "Все системы работают";
  heroBase = !monitors.length ? "Добавьте первый сайт — мониторинг начнётся сразу" : down.length ? "Уведомление уже отправлено." : "";
  tick();

  const ups = active.map(m => m.stats.uptime_percent["24h"]).filter(v => v != null);
  const avgUp = ups.length ? ups.reduce((a, b) => a + b, 0) / ups.length : null;
  const lats = active.map(m => m.stats.latency_ms_24h.avg).filter(v => v != null);
  const avgLat = lats.length ? lats.reduce((a, b) => a + b, 0) / lats.length : null;
  $("#ring-val").style.strokeDashoffset = avgUp == null ? 377 : 377 * (1 - avgUp / 100);
  if (avgUp == null) $("#ring-num").textContent = "—"; else countUp($("#ring-num"), avgUp, v => pct(v));
  const open = inc.filter(i => !i.resolved_at).length;
  $("#stats").innerHTML = `
    <div class="stat up"><span class="ic">${SI.up}</span><div><small>Работают</small><b class="num">${upN}<span>из ${active.length}</span></b></div></div>
    <div class="stat ${down.length ? "down" : ""}"><span class="ic">${SI.down}</span><div><small>Недоступны</small><b class="num">${down.length}</b></div></div>
    <div class="stat"><span class="ic">${SI.bolt}</span><div><small>Средний отклик</small><b class="num">${avgLat == null ? "—" : Math.round(avgLat) + "<span>мс</span>"}</b></div></div>
    <div class="stat ${open ? "down" : ""}"><span class="ic">${SI.flag}</span><div><small>Инциденты</small><b class="num">${open}</b></div></div>`;

  const counts = { all: monitors.length, down: down.length, paused };
  $("#chips").innerHTML = [["all", "Все"], ["down", "Проблемы"], ["paused", "На паузе"]].map(([k, t]) =>
    `<button class="chip" data-filter="${k}" aria-pressed="${filter === k}">${t}<i class="num">${counts[k]}</i></button>`).join("");
  $("#chips").style.display = monitors.length ? "" : "none";

  charts.clear();
  const shown = monitors.filter(m => filter === "all" || (filter === "down" ? m.is_active && m.status === "down" : !m.is_active));
  $("#list").innerHTML = !monitors.length
    ? `<div class="empty glass rise" style="--i:2"><div class="big"><svg width="30" height="30" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 16l5-7 4 4 7-9"/></svg></div>
        <h3>Пока нет ни одного монитора</h3><p>Укажите адрес сайта или API — остальное сервис сделает сам.</p>
        <button class="btn primary" data-open-add>Добавить монитор</button></div>`
    : shown.length ? `<div class="grid ${shown.length === 1 ? "single" : ""}">${shown.map(card).join("")}</div>`
    : `<div class="empty glass" style="padding:34px"><p style="margin:0">${filter === "down" ? "Проблем нет — все сервисы отвечают" : "Нет мониторов на паузе"}</p></div>`;

  const names = Object.fromEntries(monitors.map(m => [m.id, m.name]));
  $("#incidents").innerHTML = inc.length ? `<div class="tl glass rise" style="--i:6">${inc.map(i => `<div class="inc ${i.resolved_at ? "" : "open"}">
      <span class="mark"></span>
      <div class="what"><b>${esc(names[i.monitor_id] ?? "Монитор #" + i.monitor_id)}</b><small>${esc(i.last_error ?? "")}</small></div>
      <div class="when"><b>${i.resolved_at ? "Длился " + span(i.started_at, i.resolved_at) : "Идёт " + span(i.started_at, new Date())}</b>${esc(dt(i.started_at))}</div>
    </div>`).join("")}</div>`
    : `<div class="empty glass" style="padding:30px"><p style="margin:0">Инцидентов не было — так держать</p></div>`;

  if (firstPaint) { firstPaint = false; setTimeout(() => document.body.classList.remove("first"), 2200); }
}

let heroBase = "";
function tick() {
  if (!loadedAt) return;
  const s = Math.round((Date.now() - loadedAt) / 1000);
  $("#hero-sub").textContent = monitors.length ? (heroBase + " " + (s < 3 ? "Обновлено только что" : `Обновлено ${s} сек назад`)).trim() : heroBase;
}

/* подсказки: одна на всю страницу */
const tip = $("#tip");
const showTip = (html, x, y) => { tip.innerHTML = html; tip.style.left = Math.max(90, Math.min(innerWidth - 90, x)) + "px"; tip.style.top = Math.max(60, y) + "px"; tip.classList.add("show"); };
document.addEventListener("mousemove", e => {
  const box = e.target.closest?.("[data-chart]");
  if (box) {
    const c = charts.get(box.dataset.chart); if (!c) return;
    const r = box.getBoundingClientRect(), k = Math.max(0, Math.min(1, (e.clientX - r.left) / r.width));
    const i = Math.round(k * (c.pts.length - 1)), [x, y] = c.xy[i], cur = box.querySelector(".cursor"), hov = box.querySelector(".hov");
    cur.style.display = hov.style.display = "block"; cur.style.left = hov.style.left = (x / c.W * 100) + "%"; hov.style.top = (y / c.H * 100) + "%";
    showTip(`<b>${Math.round(c.pts[i].latency_ms)} мс</b><span>${esc(dt(c.pts[i].at))}</span>`, r.left + x / c.W * r.width, r.top + y / c.H * r.height);
    return;
  }
  document.querySelectorAll(".chart .cursor, .chart .hov").forEach(el => el.style.display = "none");
  const t = e.target.closest?.("[data-tip]");
  if (t) { const r = t.getBoundingClientRect(); showTip(t.dataset.tip, r.left + r.width / 2, r.top); } else tip.classList.remove("show");
});
document.addEventListener("scroll", () => tip.classList.remove("show"), { passive: true });

let lastInc = [];
async function refresh() {
  if (keyWaiters) return;  // ждём ключ — не шлём запросы, которые заведомо не пройдут
  try {
    [monitors, lastInc] = await Promise.all([api("/summary"), api("/incidents?limit=8")]);
    loadedAt = Date.now();
    render(lastInc);
  } catch (e) { toast(e.message, true); }
}
function toast(text, bad) {
  const t = $("#toast"); t.textContent = text; t.className = "toast show" + (bad ? " bad" : "");
  clearTimeout(toast.timer); toast.timer = setTimeout(() => t.className = "toast", 3200);
}

/* действия */
let editId = null;
function openForm(m) {
  const form = $("#add"); form.reset(); $("#add-err").textContent = ""; editId = m ? m.id : null;
  $("#form-title").textContent = m ? "Изменить монитор" : "Новый монитор";
  $("#form-hint").textContent = m ? "История проверок сохранится. Адрес изменить нельзя: история относится именно к нему."
    : "Сервис будет проверять адрес по расписанию и сообщит, если он перестанет отвечать.";
  $("#add-submit").textContent = m ? "Сохранить" : "Добавить монитор";
  $("#f-url").disabled = !!m;
  if (m) {
    const sel = $("#f-int");
    if (![...sel.options].some(o => +o.value === m.interval_seconds)) sel.add(new Option("каждые " + every(m.interval_seconds), m.interval_seconds));
    $("#f-name").value = m.name; $("#f-url").value = m.url; sel.value = m.interval_seconds; $("#f-kw").value = m.keyword ?? "";
    $("#f-thr").value = m.failure_threshold; $("#f-to").value = m.timeout_seconds; $("#f-st").value = m.expected_status;
  }
  $("#add-dlg").showModal(); $("#f-name").focus();
}
const openAdd = () => openForm(null);
$("#open-add").onclick = openAdd;
document.addEventListener("click", e => {
  if (e.target.closest("[data-close]")) e.target.closest("dialog").close();
  if (e.target.closest("[data-open-add]")) openAdd();
  const chip = e.target.closest("[data-filter]");
  if (chip) { filter = chip.dataset.filter; render(lastInc); }
});
document.querySelectorAll("dialog").forEach(d => d.addEventListener("click", e => { if (e.target === d && d.id !== "key-dlg") d.close(); }));

$("#add").addEventListener("submit", async e => {
  e.preventDefault();
  const form = e.target, err = $("#add-err");
  if (!form.reportValidity()) return;
  const f = Object.fromEntries(new FormData(form));
  const body = { name: f.name.trim(), interval_seconds: +f.interval_seconds,
    failure_threshold: +f.failure_threshold, timeout_seconds: +f.timeout_seconds, expected_status: +f.expected_status };
  const keyword = f.keyword.trim();
  if (editId) body.keyword = keyword || null;  // null сбрасывает ключевое слово
  else { body.url = $("#f-url").value.trim(); if (keyword) body.keyword = keyword; }
  $("#add-submit").disabled = true; err.textContent = "";
  try {
    if (editId) await api("/monitors/" + editId, { method: "PATCH", body: JSON.stringify(body) });
    else await api("/monitors", { method: "POST", body: JSON.stringify(body) });
    $("#add-dlg").close(); toast(editId ? "Изменения сохранены" : "Монитор добавлен, первая проверка — через несколько секунд");
    if (!editId) filter = "all";
    refresh();
  } catch (er) {
    err.textContent = { "target resolves to a non-public address": "Этот адрес ведёт во внутреннюю сеть — такие проверять нельзя",
      "only http(s) URLs are allowed": "Адрес должен начинаться с http:// или https://",
      "keyword check requires GET: HEAD responses have no body": "Ключевое слово нельзя задать для монитора с методом HEAD" }[er.message]
      ?? (er.message.startsWith("cannot resolve host") ? "Не удалось найти такой сайт — проверьте адрес" : er.message);
  } finally { $("#add-submit").disabled = false; }
});

let pendingDelete = null;
$("#list").addEventListener("click", async e => {
  const b = e.target.closest("button"); if (!b) return;
  try {
    if (b.dataset.edit) {
      openForm(monitors.find(m => String(m.id) === b.dataset.edit));
    } else if (b.dataset.del) {
      pendingDelete = b.dataset.del;
      $("#del-text").textContent = `«${b.dataset.name}» будет удалён вместе с историей проверок и инцидентов. Отменить это нельзя.`;
      $("#del-dlg").showModal();
    } else if (b.dataset.toggle) {
      const on = b.dataset.active !== "true";
      await api("/monitors/" + b.dataset.toggle, { method: "PATCH", body: JSON.stringify({ is_active: on }) });
      toast(on ? "Мониторинг возобновлён" : "Монитор поставлен на паузу"); refresh();
    }
  } catch (er) { toast(er.message, true); }
});
$("#del-ok").addEventListener("click", async e => {
  e.preventDefault(); $("#del-dlg").close();
  try { await api("/monitors/" + pendingDelete, { method: "DELETE" }); toast("Монитор удалён"); refresh(); }
  catch (er) { toast(er.message, true); }
});

refresh();
setInterval(refresh, 10000);
setInterval(tick, 1000);
