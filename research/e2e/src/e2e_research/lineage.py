"""Same-path follow-ups; commit labels do not identify individual test cases."""

from collections import defaultdict

from e2e_research.common import DAY, MAINTENANCE, dt
from e2e_research.paths import path_kind


def ancestor(a, b):
    return bool(b["ancestors"] & a["bit"])


def file_events(records):
    result = defaultdict(list)
    for row in records:
        for f in row["files"]:
            if f["status"] == "R":
                result[f["path"]].append((row, "R_end"))
                result[f["new_path"]].append((row, "R_start"))
            elif f["status"] == "C":
                result[f["new_path"]].append((row, "A"))
            else:
                result[f["path"]].append((row, f["status"]))
    for events in result.values():
        events.sort(key=lambda e: (e[0]["timestamp"], e[0]["sha"]))
    return result


def eligible_targets(origin, events, horizon):
    """Exact path; same lineage; a delete/rename/recreation ends the episode."""
    limit = origin["timestamp"] + horizon * DAY
    descendants = [(r, s) for r, s in events if r["sha"] != origin["sha"] and ancestor(origin, r)]
    barriers = 0
    for row, status in descendants:
        if status in {"D", "R_end", "A", "R_start"}:
            barriers |= row["bit"]
    future = [(r, s) for r, s in descendants if origin["timestamp"] <= r["timestamp"] <= limit]
    result = []
    for target, status in future:
        if status in {"A", "R_start"}:
            continue
        broken = bool(target["ancestors"] & barriers)
        if not broken:
            result.append((target, status))
    return result


def followups(records, observed_until, slug):
    by_path = file_events(records)
    outputs, edges = [], []
    for origin in records:
        if origin["matched"] is not True:
            continue
        for scope in ("test", "code", "new_test"):
            selected = [
                f["path"]
                for f in origin["files"]
                if f["status"] in {"A", "M", "T"}
                and (
                    path_kind(f["path"]) == "test"
                    if scope != "code"
                    else path_kind(f["path"]) != "other"
                )
                and (scope != "new_test" or f["status"] == "A")
            ]
            if not selected:
                continue
            prior = {
                r["sha"]: r
                for path in selected
                for r, s in by_path[path]
                if origin["timestamp"] - 30 * DAY <= r["timestamp"] <= origin["timestamp"]
                and ancestor(r, origin)
            }
            hits = []
            for path in selected:
                for target, status in eligible_targets(origin, by_path[path], 90):
                    days = (target["timestamp"] - origin["timestamp"]) / DAY
                    hits.append((target, status, days))
                    if (
                        scope == "test"
                        and days <= 30
                        and origin["timestamp"] <= observed_until - 30 * DAY
                    ):
                        edges.append(
                            {
                                "slug": slug,
                                "source": origin["sha"],
                                "target": target["sha"],
                                "path": path,
                                "days": days,
                                "target_status": status,
                                "source_labels": origin["labels"],
                                "target_labels": target["labels"]
                                if target["matched"] is True
                                else [],
                                "target_matched": target["matched"],
                                "source_single": len(origin["files"]) == 1,
                                "target_single": len(target["files"]) == 1,
                                "target_complete": target["complete"],
                            }
                        )
            for horizon in (14, 30, 90):
                if origin["timestamp"] > observed_until - horizon * DAY:
                    continue
                within = [(r, s, d) for r, s, d in hits if d <= horizon]
                # Renames/deletions are revisits, not labeled repairs of retained code.
                labeled = [(r, d) for r, s, d in within if r["matched"] is True and s in {"M", "T"}]
                labels = sorted({label for r, _ in labeled for label in r["labels"]})
                maintenance_times = [d for r, d in labeled if MAINTENANCE & set(r["labels"])]
                flake_times = [d for r, d in labeled if "flakiness" in r["labels"]]
                strict_labels = sorted(
                    {label for r, _ in labeled if len(r["files"]) == 1 for label in r["labels"]}
                )
                strict_delays = [
                    d for r, d in labeled if len(r["files"]) == 1 and MAINTENANCE & set(r["labels"])
                ]
                outputs.append(
                    {
                        "slug": slug,
                        "sha": origin["sha"],
                        "year": origin["year"],
                        "scope": scope,
                        "window": horizon,
                        "date": origin["date"],
                        "labels": origin["labels"],
                        "source_stratum": origin["source_stratum"],
                        "complete": origin["complete"],
                        "files": len(selected),
                        "all_selected_files": len(origin["files"]),
                        "quarter": f"{origin['year']}-Q{(dt(origin['date']).month - 1) // 3 + 1}",
                        "prior_30d_commits": len(prior),
                        "prior_30d_flakiness": any(
                            r["matched"] is True and "flakiness" in r["labels"]
                            for r in prior.values()
                        ),
                        "flakiness_undecided": "flakiness" in origin["undecided_labels"],
                        "added_files": sum(
                            f["status"] == "A" and f["path"] in selected for f in origin["files"]
                        ),
                        "repeat_any": bool(within),
                        "repeat_e2e": bool(labeled),
                        "repeat_maintenance": bool(maintenance_times),
                        "repeat_flakiness": bool(flake_times),
                        "repeat_labels": labels,
                        "repeat_count": len({r["sha"] for r, _, _ in within}),
                        "strict_repeat_labels": strict_labels,
                        "strict_first_maintenance_days": min(strict_delays, default=None),
                        "unknown_followup": any(r["matched"] is None for r, _, _ in within),
                        "terminal_touch": any(s in {"D", "R_end"} for _, s, _ in within),
                        "first_repeat_days": min((d for _, _, d in within), default=None),
                        "first_maintenance_days": min(maintenance_times, default=None),
                        "first_flakiness_days": min(flake_times, default=None),
                    }
                )
    return outputs, edges, by_path
