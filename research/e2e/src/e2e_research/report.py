"""Offline HTML and findings rendered from aggregate JSON, with no frozen numeric prose."""

import html
import json
import os
from pathlib import Path
from string import Template

from e2e_research import calendar_report
from e2e_research.common import load_json

ASSETS = Path(__file__).parent / "assets"

COHORTS = {
    "new_file_coverage": "Добавление покрытия в новых файлах",
    "coverage": "Все добавления покрытия",
    "all_e2e": "Все E2E-изменения тестовых файлов",
    "flakiness": "Исходные flakiness-правки",
}
METHOD_NAMES = {
    "quarter": "Проект + квартал",
    "project": "Только проект",
    "month": "Проект + месяц",
    "quarter_size": "Проект + квартал + число файлов",
    "quarter_activity": "Проект + квартал + прошлая активность",
    "quarter_activity_size": "Проект + квартал + активность + файлы",
    "author_halfyear": "Проект + полугодие + Git-автор",
}


def fmt(value, digits=1):
    return "—" if value is None else f"{value:.{digits}f}".replace(".", ",")


def findings(data: dict) -> str:
    new = data["cohorts"]["new_file_coverage"]["30"]["all"]
    raw, adjusted = new["raw"], new["methods"]["quarter"]
    broad = data["cohorts"]["coverage"]["30"]["all"]["methods"]["quarter"]
    broad_project = data["cohorts"]["coverage"]["30"]["all"]["methods"]["project"]
    strict = data["cohorts"]["new_file_coverage"]["30"]["strict_pair"]["raw"]
    metric = broad["metrics"].get("maintenance_window", {})
    timing = broad["metrics"].get("days_without_maintenance", {})
    broad_flakiness = broad["metrics"].get("flakiness_window", {})
    project_flakiness = broad_project["metrics"].get("flakiness_window", {})
    lines = [
        "# AI-атрибуция и дальнейшее сопровождение тестовых файлов",
        "",
        "## Результат проверки гипотезы",
        "",
        (
            "- Гипотеза: после AI-отмеченного добавления покрытия к файлам "
            "чаще и раньше возвращаются для сопровождения."
        ),
        "- Новые файлы, 30 дней, без подбора: AI "
        f"{fmt(raw['ai']['estimates']['maintenance_window'])}% "
        f"({int(raw['ai']['sums']['maintenance_window'])}/{raw['ai']['n']}); без отметки "
        f"{fmt(raw['unmarked']['estimates']['maintenance_window'])}% "
        f"({int(raw['unmarked']['sums']['maintenance_window'])}/{raw['unmarked']['n']}).",
        f"- После подбора проекта и квартала осталось {adjusted['n_projects']} проекта: "
        f"{adjusted['n_ai']} AI-отмеченных и {adjusted['n_unmarked']} неотмеченных исходников.",
        f"- Проекты: {', '.join(adjusted['by_project']) or 'нет общей поддержки'}.",
    ]
    if adjusted["metrics"]:
        m = adjusted["metrics"]["maintenance_window"]
        t = adjusted["metrics"]["days_without_maintenance"]
        lines.extend(
            [
                f"- Сопровождение: {fmt(m['ai'])}% против {fmt(m['unmarked'])}%; "
                f"разница {fmt(m['delta'])} п.п.",
                f"- Среднее ограниченное время до сопровождения: {fmt(t['ai'], 2)} против "
                f"{fmt(t['unmarked'], 2)} дня; разница {fmt(t['delta'], 2)} дня.",
            ]
        )
    oss = data["cohorts"]["new_file_coverage"]["30"]["oss"]["methods"]["quarter"]
    lines.extend(
        [
            f"- В отдельной OSS-группе этот подбор сохраняет {oss['n_projects']} проектов.",
            "- Для новых файлов поддержка слишком узкая для общего вывода о влиянии AI.",
            "",
            "## Flakiness отдельно",
            "",
            "- Новые файлы, 30 дней, без подбора: AI "
            f"{fmt(raw['ai']['estimates']['flakiness_window'])}% "
            f"({int(raw['ai']['sums']['flakiness_window'])}/{raw['ai']['n']}); без отметки "
            f"{fmt(raw['unmarked']['estimates']['flakiness_window'])}% "
            f"({int(raw['unmarked']['sums']['flakiness_window'])}/{raw['unmarked']['n']}).",
        ]
    )
    flaky = adjusted["metrics"].get("flakiness_window")
    if flaky:
        lines.extend(
            [
                f"- После подбора проекта и квартала: {fmt(flaky['ai'])}% / "
                f"{fmt(flaky['unmarked'])}%; разница {fmt(flaky['delta'])} п.п.",
                f"- Поддержка: {adjusted['n_projects']} проекта, "
                f"{adjusted['n_ai']} / {adjusted['n_unmarked']} исходников; "
                f"рост / спад по проектам: {flaky['up']} / {flaky['down']}.",
                "- При двух проектах bootstrap-интервал не публикуется.",
            ]
        )
    lines.extend(
        [
            f"- Строгая связь одного E2E-файла: AI "
            f"{fmt(strict['ai']['estimates']['flakiness_window'])}% "
            f"({int(strict['ai']['sums']['flakiness_window'])}/{strict['ai']['n']}); "
            f"без отметки {fmt(strict['unmarked']['estimates']['flakiness_window'])}% "
            f"({int(strict['unmarked']['sums']['flakiness_window'])}/{strict['unmarked']['n']}).",
        ]
    )
    if broad_flakiness and project_flakiness:
        ci = broad_flakiness["bootstrap_95"]
        pci = project_flakiness["bootstrap_95"]
        lines.extend(
            [
                f"- Все coverage-изменения, проект + квартал: разница "
                f"{fmt(broad_flakiness['delta'])} п.п.; 95% bootstrap "
                f"{fmt(ci[0])}…{fmt(ci[1])}.",
                f"- Только проект: разница {fmt(project_flakiness['delta'])} п.п.; "
                f"95% bootstrap {fmt(pci[0])}…{fmt(pci[1])}.",
                "- Направление меняется между спецификациями; устойчивой связи с flakiness нет.",
            ]
        )
    lines.extend(
        [
            "",
            "## Более широкая группа добавлений покрытия",
            "",
            f"- {broad['n_projects']} проектов; {broad['n_ai']} AI-отмеченных / "
            f"{broad['n_unmarked']} неотмеченных исходников.",
        ]
    )
    if metric:
        ci = metric["bootstrap_95"]
        ti = timing["bootstrap_95"]
        lines.extend(
            [
                f"- Сопровождение за 30 дней: {fmt(metric['ai'])}% / {fmt(metric['unmarked'])}%.",
                f"- Разница: {fmt(metric['delta'])} п.п.; 95% bootstrap {fmt(ci[0])}…{fmt(ci[1])}."
                if ci
                else "- Недостаточно проектов для интервала.",
                f"- Ограниченное время: {fmt(timing['ai'], 2)} / {fmt(timing['unmarked'], 2)} дня.",
                f"- Разница: {fmt(timing['delta'], 2)} дня; 95% bootstrap "
                f"{fmt(ti[0], 2)}…{fmt(ti[1], 2)}."
                if ti
                else "- Недостаточно проектов для интервала времени.",
            ]
        )
    lines.extend(
        [
            (
                "- Устойчивого подтверждения гипотезы «чаще и быстрее» нет; "
                "обратный причинный эффект тоже не установлен."
            ),
            (
                "- Поправки на размер и активность меняют и оценки, и состав "
                "сравнения; все варианты доступны в JSON и отчёте."
            ),
            "",
            "## Что именно измерено",
            "",
            (
                "- AI-признак взят из полного сообщения исходного коммита. Это не "
                "доказательство авторства каждого теста."
            ),
            (
                "- Без отметки не означает без AI. Участники, задачи и инструменты"
                " не назначались случайно."
            ),
            (
                "- Источники: E2E-положительные коммиты 2025–2026, с полным окном "
                "14/30/90 дней до заморозки репозитория."
            ),
            "- Связь требует общего пути, Git-родства и подходящей метки последующего коммита.",
            (
                "- Причины относятся к коммиту целиком; разные тест-кейсы одного "
                "файла могут дать такую связь."
            ),
            "- Добавленный файл — Git-статус A; не обязательно новый семантический сценарий.",
            (
                "- Новые имена после переименований не объединяются; наблюдаемое "
                "удаление или пересоздание разрывает эпизод."
            ),
            (
                "- Сопровождение объединяет adaptation, flakiness, environment, "
                "refactoring и test_bug; исходник учитывается один раз."
            ),
            "",
            "## Частота и скорость",
            "",
            "- Частота: доля исходников с наблюдаемым сопровождением за 7, 14 дней и всё окно.",
            (
                "- Время: среднее min(дни до первого сопровождения, длина окна). "
                "Без события исходник вносит полное окно."
            ),
            (
                "- Так отсутствие возврата не исчезает из расчёта. Меньше дней "
                "означает сочетание более частых и ранних возвратов."
            ),
            (
                "- Медиана только среди вернувшихся сохранена как дополнительная "
                "описательная величина, не основной тест скорости."
            ),
            "",
            "## Подбор и неопределённость",
            "",
            (
                "- Общие ячейки требуют ≥5 исходников каждой группы; месяцы — ≥3. "
                "На проект — ≥20 каждой группы."
            ),
            "- Ячейки взвешены по числу AI-исходников внутри проекта; проекты имеют равные веса.",
            "- Основной разрез: новые файлы / 30 дней / все проекты / проект + квартал.",
            (
                "- OSS, полные данные, один E2E-файл с обоих концов, размер, "
                "активность и Git-автор — проверки чувствительности."
            ),
            (
                "- Bootstrap по проектам: 10 000 повторов, seed 42; при <3 "
                "проектах интервал не публикуется."
            ),
            (
                "- Множество сравнений разведочное; ошибки разметки и полнота "
                "AI-атрибуции не входят в эти интервалы."
            ),
            "",
            "## Воспроизводимость",
            "",
            (
                "- Все значения вычислены из commits.enriched.jsonl.gz; агрегация "
                "не обращается к Git, сети или Jev."
            ),
            "- classification=null означает коммит вне отбора, а не отрицательный ответ модели.",
            "- Схема, код, команды, зависимости и правила расчётов находятся в research/e2e.",
        ]
    )
    return "\n".join(lines) + "\n"


def option_html(values: dict) -> str:
    return "".join(
        f'<option value="{html.escape(k)}">{html.escape(v)}</option>' for k, v in values.items()
    )


def ai_section(data: dict) -> str:
    primary = data["cohorts"]["new_file_coverage"]["30"]["all"]
    raw, adjusted = primary["raw"], primary["methods"]["quarter"]
    return Template((ASSETS / "section.html").read_text()).substitute(
        ai_rate=fmt(raw["ai"]["estimates"]["maintenance_window"]),
        unmarked_rate=fmt(raw["unmarked"]["estimates"]["maintenance_window"]),
        ai_flaky=fmt(raw["ai"]["estimates"]["flakiness_window"]),
        unmarked_flaky=fmt(raw["unmarked"]["estimates"]["flakiness_window"]),
        ai_n=raw["ai"]["n"],
        unmarked_n=raw["unmarked"]["n"],
        projects=adjusted["n_projects"],
        project_names=html.escape(", ".join(adjusted["by_project"]) or "нет общей поддержки"),
        matched_ai=adjusted["n_ai"],
        matched_unmarked=adjusted["n_unmarked"],
        cohorts=option_html(COHORTS),
        methods=option_html(METHOD_NAMES),
        scopes=option_html(
            {
                "all": "Все 21 проекта",
                "oss": "Только 19 OSS",
                "complete": "Полные данные исходника",
                "strict_pair": "Один E2E-файл на обоих концах",
            }
        ),
        data=json.dumps(data, ensure_ascii=False).replace("</", "<\\/"),
    )


def ai_script() -> str:
    return (ASSETS / "followup.js").read_text()


def render_report(aggregates: Path, output: Path, *, figures: bool = False) -> None:
    data = load_json(aggregates / "ai-followup.json")
    calendar = load_json(aggregates / "calendar-followup.json")
    basic = load_json(aggregates / "basic.json")
    chains = load_json(aggregates / "file-chains.json")
    output.mkdir(parents=True, exist_ok=True)
    calendar_findings = calendar_report.findings(calendar)
    (output / "calendar-findings.md").write_text(calendar_findings)
    (output / "findings.md").write_text(calendar_findings + "\n" + findings(data))
    section = calendar_report.section(calendar) + ai_section(data)
    script = calendar_report.script() + "\n" + ai_script()
    (output / "section.html").write_text(section)
    (output / "section.js").write_text(script)
    totals = basic["total"]
    contrast = chains["contrasts"]["2025_2026"]["oss"]
    metric_rows = "".join(
        f"<tr><td>{html.escape(name)}</td><td>{fmt(v['before'], 2)}</td>"
        f"<td>{fmt(v['after'], 2)}</td><td>"
        f"{fmt(v['pooled_before'], 2)} → {fmt(v['pooled_after'], 2)}</td></tr>"
        for key, name in (
            ("maintenance", "Сопровождение"),
            ("flakiness", "Нестабильность"),
            ("coverage", "Новое покрытие"),
        )
        for v in [contrast["metrics"][key]]
    )
    page = Template((ASSETS / "page.html").read_text()).substitute(
        styles=(ASSETS / "report.css").read_text(),
        script=script,
        section=section,
        all_commits=f"{basic['all_nonmerge']:,}",
        candidates=f"{totals['candidates']:,}",
        matched=f"{totals['matched']:,}",
        projects=contrast["n_projects"],
        normalized_rows=metric_rows,
        aggregates=html.escape(os.path.relpath(aggregates.resolve(), output.resolve())),
    )
    (output / "report.html").write_text(page)
    if figures:
        calendar_report.export_figure(calendar, output)
    print(f"Rendered {output / 'report.html'}")
