import json

import pytest

from hekajev.reporting import summarize_records, write_summary_reports


def test_corpus_report_preserves_population_and_strata_without_embedding_sha_lists(tmp_path):
    shas = [f"{index:040x}" for index in range(100_000)]
    manifest = {
        "analysis_fingerprint": "analysis-identity",
        "config": {"classifications": {}},
        "repositories": [{"id": "repo", "name": "repo", "commits": shas}],
    }
    records = [
        {
            "repo_id": "repo",
            "sha": shas[0],
            "status": "ok",
            "matched": False,
            "complete": True,
            "chunks": 1,
        },
        {
            "repo_id": "repo",
            "sha": shas[1],
            "status": "ok",
            "matched": None,
            "complete": False,
            "chunks": 20,
        },
        {"repo_id": "repo", "sha": shas[2], "status": "error", "complete": False},
    ]
    summary = summarize_records(records, manifest)
    summary["strata"] = {"merge": {"expected": 10_000}, "nonmerge": {"expected": 90_000}}
    summary["cohort_status"] = "partial"
    links = [{"path": "batches/first & second/report.html", "label": "<first batch>"}]
    output = tmp_path / "corpus"
    write_summary_reports(output, summary, manifest, batch_reports=links)

    saved = json.loads((output / "summary.json").read_text())
    assert saved == summary
    assert saved["counts"]["expected"] == 100_000
    assert saved["counts"]["missing"] == 99_997
    assert saved["counts"]["error"] == 1
    assert saved["counts"]["incomplete"] == 2
    report = (output / "report.html").read_text()
    assert len(report.encode()) < 25_000
    assert 'href="batches/first%20%26%20second/report.html"' in report
    assert "&lt;first batch&gt;" in report and "<first batch>" not in report
    assert "analysis-identity" in report and "nonmerge" in report and "partial" in report
    assert "99997" in report and "100000" in report
    assert shas[-1] not in report
    assert "No records yet" not in report
    assert len(manifest["repositories"][0]["commits"]) == 100_000

    for unsafe in ("../elsewhere/report.html", str(tmp_path / "outside.html")):
        with pytest.raises(ValueError, match="relative paths inside output"):
            write_summary_reports(
                output, summary, manifest, batch_reports=[{"path": unsafe, "label": "bad"}]
            )
    assert json.loads((output / "summary.json").read_text()) == saved

    write_summary_reports(output, summary, manifest, batch_reports=[])
    report = (output / "report.html").read_text()
    assert "No batch reports are available yet" in report
    assert "99997" in report
