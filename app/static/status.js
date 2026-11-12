"use strict";
const $ = s => document.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const TITLES = { operational: "Все системы работают", degraded: "Часть сервисов недоступна", outage: "Сервисы недоступны", unknown: "Данных пока нет" };
const STATES = { up: "Работает", down: "Недоступен", unknown: "Нет данных" };
const OK = '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>';
const BAD = '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2.6" stroke-linecap="round"><path d="M12 7v6M12 17v.5"/></svg>';
const cls = u => u == null ? "" : u >= 99.9 ? "g" : u >= 98 ? "y" : "r";
const pct = v => (v >= 99.995 ? "100" : v.toFixed(2)) + "%";
const day = d => new Date(d + "T00:00:00").toLocaleDateString("ru-RU", { day: "numeric", month: "long" });
const dt = iso => new Date(iso).toLocaleString("ru-RU", { day: "numeric", month: "long", hour: "2-digit", minute: "2-digit" });
function span(a, b) {
  const m = Math.max(1, Math.round((new Date(b) - new Date(a)) / 60000));
  return m < 60 ? m + " мин" : m < 1440 ? Math.floor(m / 60) + " ч" + (m % 60 ? " " + (m % 60) + " мин" : "") : Math.floor(m / 1440) + " дн " + Math.floor(m % 1440 / 60) + " ч";
}
async function load() {
  try {
    const r = await fetch("/api/public/status");
    if (!r.ok) throw new Error();
    const s = await r.json();
    document.body.dataset.state = s.overall;
    $("#ic").innerHTML = s.overall === "operational" ? OK : BAD;
    $("#title").textContent = TITLES[s.overall];
    $("#sub").textContent = "Обновлено в " + new Date(s.generated_at).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
    $("#legend").hidden = !s.monitors.length;
    setTimeout(() => document.body.classList.remove("first"), 1500);
    $("#list").innerHTML = s.monitors.length ? s.monitors.map(m => `<div class="card glass ${esc(m.status)}">
      <div class="row"><b>${esc(m.name)}</b><span class="state">${STATES[m.status] ?? ""}</span></div>
      <div class="bars">${m.days.map((d, n) => `<i class="${cls(d.uptime)}" style="--n:${n}" data-tip="<b>${d.uptime == null ? "Нет данных" : pct(d.uptime) + " доступности"}</b><span>${esc(day(d.date))}</span>"></i>`).join("")}</div>
      <div class="cap"><span>30 дней назад</span>${m.uptime_30d == null ? "" : `<b class="num">${pct(m.uptime_30d)} <span>доступности за 30 дней</span></b>`}<span>сегодня</span></div>
    </div>`).join("") : '<div class="empty glass">Сервисы пока не добавлены</div>';
    $("#incidents").innerHTML = s.incidents.length ? `<div class="tl glass">${s.incidents.map(i => `<div class="inc ${i.resolved_at ? "" : "open"}">
      <span class="mark"></span>
      <div class="what"><b>${esc(i.monitor)}</b><small>${esc(dt(i.started_at))}</small></div>
      <div class="dur">${i.resolved_at ? "устранён за " + span(i.started_at, i.resolved_at) : "продолжается"}</div></div>`).join("")}</div>`
      : '<div class="empty glass">За последнее время инцидентов не было</div>';
  } catch { $("#title").textContent = "Не удалось загрузить статус"; $("#sub").textContent = "Попробуйте обновить страницу"; }
}
const tip = $("#tip");
document.addEventListener("mousemove", e => {
  const t = e.target.closest?.("[data-tip]");
  if (!t) return tip.classList.remove("show");
  const r = t.getBoundingClientRect(); tip.innerHTML = t.dataset.tip;
  tip.style.left = Math.max(90, Math.min(innerWidth - 90, r.left + r.width / 2)) + "px"; tip.style.top = Math.max(50, r.top) + "px"; tip.classList.add("show");
});
load(); setInterval(load, 30000);
