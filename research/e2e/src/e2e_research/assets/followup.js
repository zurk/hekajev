const followupData = JSON.parse(document.getElementById("af-data").textContent);

function formatNumber(value, digits = 1) {
  if (value == null) return "—";
  return value.toLocaleString("ru-RU", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function fillTable(id, headers, rows) {
  const table = document.getElementById(id);
  table.replaceChildren();
  function addRow(values, tag) {
    const row = document.createElement("tr");
    for (const value of values) {
      const cell = document.createElement(tag);
      cell.textContent = value;
      row.append(cell);
    }
    table.append(row);
  }
  addRow(headers, "th");
  rows.forEach((row) => addRow(row, "td"));
}

function updateFollowup() {
  const value = (id) => document.getElementById(id).value;
  const cohort = followupData.cohorts[value("af-cohort")][value("af-window")][value("af-scope")];
  const comparison = cohort.methods[value("af-method")];
  document.getElementById("af-support").textContent =
    `Сопоставлено ${comparison.n_ai} / ${comparison.n_unmarked} исходников; ` +
    `${comparison.n_projects} проектов. До отбора: ` +
    `${comparison.total_ai} / ${comparison.total_unmarked}.` +
    (comparison.n_projects < 3 ? " Поддержка недостаточна для общего вывода." : "");

  fillTable("af-metrics", ["Показатель", "AI", "Нет отметки", "Разница", "95% bootstrap"],
    Object.entries(followupData.metrics).map(([key, name]) => {
      const metric = comparison.metrics[key];
      const unit = key.startsWith("days_") ? " дн." : "%";
      return [name,
        metric ? formatNumber(metric.ai) + unit : "—",
        metric ? formatNumber(metric.unmarked) + unit : "—",
        metric ? formatNumber(metric.delta) + (metric.unit === "days" ? " дн." : " п.п.") : "—",
        metric?.bootstrap_95
          ? metric.bootstrap_95.map((v) => formatNumber(v)).join(" … ")
          : "Недостаточно проектов",
      ];
    }));

  fillTable("af-raw", ["Группа", "Исходников", "Сопровождений", "Доля", "Ограниченное время"],
    [["ai", "AI-атрибуция"], ["unmarked", "Без отметки"]].map(([key, name]) => {
      const group = cohort.raw[key];
      return [name, group.n, group.sums.maintenance_window,
        formatNumber(group.estimates.maintenance_window) + "%",
        formatNumber(group.estimates.days_without_maintenance) + " дн."];
    }));

  fillTable("af-projects", ["Проект", "AI / без отметки", "Сопровождение AI", "Без отметки"],
    Object.entries(comparison.by_project).map(([slug, project]) => [
      slug, `${project.n_ai} / ${project.n_unmarked}`,
      formatNumber(project.estimates.ai.maintenance_window) + "%",
      formatNumber(project.estimates.unmarked.maintenance_window) + "%",
    ]));

  const chart = document.getElementById("af-bars");
  chart.replaceChildren();
  for (const [key, name] of [
    ["maintenance_7d", "За 7 дней"],
    ["maintenance_14d", "За 14 дней"],
    ["maintenance_window", "За всё окно"],
  ]) {
    const metric = comparison.metrics[key];
    if (!metric) continue;
    const column = document.createElement("div");
    const label = document.createElement("b");
    label.textContent = name;
    column.append(label);
    for (const [group, title, color] of [
      ["ai", "AI", "#2476a8"],
      ["unmarked", "Нет отметки", "#a7b7c2"],
    ]) {
      const text = document.createElement("p");
      const bar = document.createElement("span");
      text.textContent = `${title}: ${formatNumber(metric[group])}% `;
      Object.assign(bar.style, {
        display: "inline-block", height: "12px", background: color,
        width: `${metric[group]}%`, maxWidth: "260px",
      });
      text.append(bar);
      column.append(text);
    }
    chart.append(column);
  }
}

for (const id of ["af-cohort", "af-window", "af-scope", "af-method"]) {
  document.getElementById(id).onchange = updateFollowup;
}
updateFollowup();
