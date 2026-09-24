const calendarFollowup = JSON.parse(document.getElementById("cf-data").textContent);
const calendarNumber = (n) => n == null ? "—" : n.toLocaleString("ru-RU", {
  minimumFractionDigits: 1, maximumFractionDigits: 1,
});

function calendarTable(id, headers, rows) {
  const table = document.getElementById(id);
  table.replaceChildren();
  function append(values, tag) {
    const row = document.createElement("tr");
    for (const value of values) {
      const cell = document.createElement(tag);
      cell.textContent = value;
      row.append(cell);
    }
    table.append(row);
  }
  append(headers, "th");
  rows.forEach((row) => append(row, "td"));
}

function calendarPlot(annual, panel, metric, days, window) {
  const host = document.getElementById("cf-chart");
  host.replaceChildren();
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 860 335");
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", "Доля возвратов по году исходного добавления");
  svg.style.width = "100%";
  function add(tag, attributes, text) {
    const node = document.createElementNS(svg.namespaceURI, tag);
    Object.entries(attributes).forEach(([key, value]) => node.setAttribute(key, value));
    if (text != null) node.textContent = text;
    svg.append(node);
    return node;
  }
  const years = Object.keys(annual).map(Number).filter((year) => year >= 2019).sort();
  if (!years.length) return;
  const first = years[0], last = years[years.length - 1];
  const x = (year) => 55 + (year - first) / Math.max(1, last - first) * 740;
  const limit = days ? Number(window) : 100;
  const fontSize = 17;
  const y = (value) => 265 - value / limit * 220;
  for (let step = 0; step <= 4; step++) {
    const value = limit * step / 4;
    add("line", { x1: 55, x2: 805, y1: y(value), y2: y(value), stroke: "#e2e7eb" });
    add("text", { x: 46, y: y(value) + 5, "text-anchor": "end", "font-size": fontSize }, calendarNumber(value));
  }
  for (const year of years) add("text", {
    x: x(year), y: 291, "text-anchor": "middle", "font-size": fontSize,
  }, year);
  for (const [series, color, label] of [
    [annual, "#9babb7", "Все доступные проекты: суммарно"],
    [panel.annual, "#2476a8", "Те же проекты 2024–2026: равные веса"],
  ]) {
    let points = [];
    for (const year of years) {
      const value = series[year]?.estimates[metric];
      if (value == null) {
        if (points.length) add("polyline", { points: points.join(" "), fill: "none", stroke: color, "stroke-width": 3 });
        points = [];
        continue;
      }
      points.push(`${x(year)},${y(value)}`);
      const dot = add("circle", { cx: x(year), cy: y(value), r: 4, fill: color });
      const title = document.createElementNS(svg.namespaceURI, "title");
      title.textContent = `${label}: ${year}, ${calendarNumber(value)}${days ? " дней" : "%"}; n=${series[year].n}`;
      dot.append(title);
    }
    if (points.length) add("polyline", { points: points.join(" "), fill: "none", stroke: color, "stroke-width": 3 });
  }
  add("text", { x: 55, y: 20, fill: "#61717c", "font-size": fontSize }, "Серый: все доступные проекты · Синий: фиксированный состав");
  add("text", { x: 55, y: 324, "font-size": fontSize }, days ? "Дни, с ограничением выбранным окном" : "Исходники с повторной правкой, %");
  host.append(svg);
}

function updateCalendar() {
  const value = (id) => document.getElementById(id).value;
  const window = calendarFollowup.windows[value("cf-window")];
  const group = window.cohorts[value("cf-cohort")][value("cf-scope")];
  const metric = value("cf-metric"), minimum = value("cf-minimum");
  const pair = group.comparisons[value("cf-period")][minimum];
  const panel = group.balanced_recent[minimum];
  const days = metric.startsWith("days_"), unit = days ? " дн." : "%";
  const [month, day] = window.common_month_day.split("-");
  document.getElementById("cf-season").textContent =
    `Исходные добавления каждого года: 01.01–${day}.${month}. Окно после добавления: ${value("cf-window")} дней.`;
  document.getElementById("cf-panel").textContent =
    `Фиксированный состав на графике: проектов ${panel.projects.length}; ` +
    `${panel.projects.join(", ") || "нет проектов с достаточным числом исходников во всех трёх годах"}.`;
  const estimate = pair.metrics[metric];
  document.getElementById("cf-paired").textContent =
    `Парное сравнение: проектов ${pair.n_projects}; исходных коммитов ${pair.n_before} / ${pair.n_after}. ` +
    (estimate
      ? `${calendarNumber(estimate.before)}${unit} → ${calendarNumber(estimate.after)}${unit}; ` +
        `Δ ${calendarNumber(estimate.delta)}${days ? " дн." : " п.п."} ` +
        (estimate.bootstrap_95 ? `95% bootstrap: ${estimate.bootstrap_95.map(calendarNumber).join(" … ")}. ` : "Для интервала мало проектов. ") +
        `Рост / спад: ${estimate.up} / ${estimate.down}.`
      : "Недостаточно сопоставимых проектов.");
  calendarPlot(group.annual, panel, metric, days, value("cf-window"));
  calendarTable("cf-projects", ["Проект", "Исходники до / после", "До", "После"],
    Object.entries(pair.by_project).map(([slug, p]) => [slug, `${p.before.n} / ${p.after.n}`,
      calendarNumber(p.before.estimates[metric]) + unit, calendarNumber(p.after.estimates[metric]) + unit]));
  calendarTable("cf-annual", ["Год", "Проектов", "Исходников", days ? "Сумма дней" : "Исходников со связью", days ? "Среднее" : "Доля"],
    Object.entries(group.annual).map(([year, g]) => [year, g.projects, g.n,
      days ? calendarNumber(g.sums[metric]) : g.sums[metric], calendarNumber(g.estimates[metric]) + unit]));
}

for (const id of ["cf-cohort", "cf-scope", "cf-window", "cf-metric", "cf-period", "cf-minimum"]) {
  document.getElementById(id).onchange = updateCalendar;
}
updateCalendar();
