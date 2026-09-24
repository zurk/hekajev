"""Presentation of origin-year follow-ups, independent of AI attribution."""

import html
import json
from pathlib import Path
from string import Template

from e2e_research.calendar_followup import COHORTS, METRICS, SCOPES
from e2e_research.common import dt

ASSETS = Path(__file__).parent / "assets"
PERIODS = {
    "2025_2026": "2025 → 2026",
    "2024_2026": "2024 → 2026",
    "2024_2025": "2024 → 2025",
    "2019_2022_vs_2023_2025": "2019–2022 → 2023–2025",
}


def number(value, digits=1):
    return "—" if value is None else f"{value:.{digits}f}".replace(".", ",")


def options(values):
    return "".join(
        f'<option value="{html.escape(k)}">{html.escape(v)}</option>' for k, v in values.items()
    )


def findings(data: dict) -> str:
    window = data["windows"]["30"]
    group = window["cohorts"]["new_file_coverage"]["oss"]
    primary = group["comparisons"]["2025_2026"]["20"]
    secondary = group["comparisons"]["2024_2026"]["20"]
    cutoff = dt(window["last_origin_in_final_year"]).strftime("%d.%m")
    lines = [
        "# Динамика возвратов к новым E2E-файлам",
        "",
        "## Что сравнивается",
        "",
        "Год определяется датой добавления файла. AI-отметки в этом сравнении не используются.",
        f"Окно наблюдения — 30 дней; берём исходники от 1 января до {cutoff} каждого года.",
        (
            "Последняя дата оставляет полный срок наблюдения во всех репозиториях."
        ),
        "",
        (
            "Исходник — E2E-положительный коммит с меткой coverage, добавивший "
            "хотя бы один файл с тестовым именем."
        ),
        (
            "Считаем возвраты к добавленным путям; несколько файлов и правок "
            "дают один исходник в числителе."
        ),
        (
            "Git-добавление файла может быть копией или миграцией, а не новым тестом."
        ),
        (
            "Любая правка включает изменение, удаление и переименование; "
            "уверенная метка сопровождения считается отдельно."
        ),
        "",
        "## Сырые годовые доли OSS",
        "",
    ]
    for year in (2024, 2025, 2026):
        annual = group["annual"].get(str(year))
        if annual:
            lines.append(
                f"- {year}: любая правка {int(annual['sums']['any_touch_window'])}/{annual['n']} "
                f"({number(annual['estimates']['any_touch_window'])}%); сопровождение "
                f"{number(annual['estimates']['maintenance_window'])}%."
            )
    lines.extend(
        [
            "Состав проектов и размер исходных коммитов между годами различаются.",
            "",
            "## Те же проекты",
            "",
            f"- 2025 → 2026 при ≥20 исходниках в каждой группе: {primary['n_projects']} проекта; "
            f"{primary['n_before']} / {primary['n_after']} исходников.",
            "- При менее трёх проектах bootstrap-интервал не публикуется.",
            f"- 2024 → 2026: {secondary['n_projects']} проекта; "
            f"{secondary['n_before']} / {secondary['n_after']} исходников.",
            f"- Проекты: {', '.join(secondary['by_project']) or 'нет общей поддержки'}.",
        ]
    )
    for key in ("any_touch_window", "maintenance_window"):
        metric = secondary["metrics"].get(key)
        if metric:
            ci = metric["bootstrap_95"]
            lines.append(
                f"- {METRICS[key]}: {number(metric['before'])}% → "
                f"{number(metric['after'])}%; Δ {number(metric['delta'])} п.п."
            )
            if ci:
                lines.append(
                    f"  - 95% bootstrap: {number(ci[0])}…{number(ci[1])} п.п.; "
                    f"рост / спад: {metric['up']} / {metric['down']}."
                )
    lines.extend(
        [
            "",
            "## Проверки и границы вывода",
            "",
            (
                "- Доступны окна 14/30/90 дней, пороги 20/10 исходников, OSS/все "
                "проекты и одиночные исходные файлы."
            ),
            (
                "- Каждое окно задаёт свой общий сезон добавлений; проценты разных"
                " окон относятся к разным допустимым когортам."
            ),
            (
                "- Для одиночных новых файлов 2024 → 2026 интервал любой повторной"
                " правки включает ноль."
            ),
            "- Окно 14 дней: интервал 2024 → 2026 включает ноль. Окно 90 дней: один проект.",
            (
                "- В этом 90-дневном срезе направление положительное; состав когорт "
                "отличается, общего тренда это не устанавливает."
            ),
            (
                "- Доли относятся к уверенно отобранным добавлениям покрытия; "
                "полнота модельного распознавания тоже могла меняться."
            ),
            (
                "- Рост частоты возвратов не подтверждён. Наблюдаемое снижение "
                "нельзя переносить на все новые тесты."
            ),
            (
                "- Меньше правок не доказывает лучшее качество: могут меняться "
                "активность, миграции, задачи и способ разбиения файлов."
            ),
            (
                "- Календарное сравнение не выделяет причинный вклад AI; "
                "неизвестная атрибуция не заменяется годом коммита."
            ),
            (
                "- График фиксированного состава использует те же проекты в "
                "2024/2025/2026 и равные веса проектов."
            ),
            (
                "- Парные сравнения требуют порог в обоих периодах; bootstrap по "
                "проектам, seed 42, 10 000 повторов."
            ),
            (
                "- Несколько сравнений разведочные; интервалы не учитывают ошибки "
                "исходной разметки Jev."
            ),
            "",
            "## Воспроизводимость",
            "",
            (
                "- Расчёт: calendar_followup.py → calendar-followup.json; источник"
                " — существующий обогащённый JSONL."
            ),
            "- Новое обогащение Git и повторный запуск Jev не требуются.",
        ]
    )
    return "\n".join(lines) + "\n"


def section(data: dict) -> str:
    group = data["windows"]["30"]["cohorts"]["new_file_coverage"]["oss"]
    annual = group["annual"]
    cards = "".join(
        f'<div class="card"><div class="big">'
        f"{number(annual[str(year)]['estimates']['any_touch_window'])}%</div>"
        f"<p>{year}: {int(annual[str(year)]['sums']['any_touch_window'])}/{annual[str(year)]['n']} "
        "исходников имеют повторную правку добавленного файла за 30 дней.</p></div>"
        for year in (2024, 2025, 2026)
        if str(year) in annual
    )
    return Template((ASSETS / "calendar-section.html").read_text()).substitute(
        cards=cards,
        cohorts=options(COHORTS),
        scopes=options(SCOPES),
        metrics=options(METRICS),
        periods=options(PERIODS),
        data=json.dumps(data, ensure_ascii=False).replace("</", "<\\/"),
    )


def script() -> str:
    return (ASSETS / "calendar.js").read_text()


def export_figure(data: dict, output: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    group = data["windows"]["30"]["cohorts"]["new_file_coverage"]["oss"]
    years = [year for year in (2024, 2025, 2026) if str(year) in group["annual"]]
    paired_data = group["comparisons"]["2024_2026"]["20"]
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 10,
            "axes.spines.top": False,
            "axes.spines.right": False,
        }
    )
    fig, axes = plt.subplots(1, 2, figsize=(12, 5.6), sharey=True)
    values = [group["annual"][str(y)]["estimates"]["any_touch_window"] for y in years]
    axes[0].plot(years, values, "o-", color="#2476a8")
    for year, value in zip(years, values, strict=True):
        row = group["annual"][str(year)]
        axes[0].annotate(
            f"{number(value)}%\n{int(row['sums']['any_touch_window'])}/{row['n']}",
            (year, value),
            xytext=(0, 10),
            textcoords="offset points",
            ha="center",
        )
    axes[0].set(
        title="Все доступные OSS-проекты\nСостав меняется",
        xticks=years,
        ylabel="Исходники с любой повторной правкой за 30 дней, %",
        ylim=(0, 100),
    )
    for slug, pair in paired_data["by_project"].items():
        axes[1].plot(
            [2024, 2026],
            [pair[p]["estimates"]["any_touch_window"] for p in ("before", "after")],
            "o-",
            label=slug,
        )
    axes[1].set(
        title=f"Те же {paired_data['n_projects']} OSS-проекта\n≥20 исходников в обоих годах",
        xticks=[2024, 2026],
    )
    axes[1].legend(loc="lower left", fontsize=8, frameon=False)
    fig.suptitle("Возвраты к новым E2E-файлам по году добавления")
    end = dt(data["windows"]["30"]["last_origin_in_final_year"]).strftime("%d.%m")
    fig.text(
        0.02,
        0.025,
        f"Исходники 01.01–{end} каждого года; одинаковое полное окно наблюдения. "
        "Коммит с добавлением файла ≠ отдельный test case.",
        fontsize=9,
    )
    fig.tight_layout(rect=(0, 0.06, 1, 0.92))
    for extension in ("png", "svg"):
        fig.savefig(output / f"calendar-followup.{extension}", dpi=170)
    plt.close(fig)
