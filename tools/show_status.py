"""Печатает статус последнего прогона Android-конвейера из уже скачанных JSON.

Зачем отдельный скрипт: `tools/run_build.py status` в этой среде виснет
(и `python -c` с пайпом тоже), а curl к GitHub работает нормально.
"""

import json
import pathlib
import sys

root = pathlib.Path(__file__).resolve().parent.parent.parent   # .../shapitogram
runs = json.loads((root / "stage/runs.json").read_text(encoding="utf-8"))
jobs = json.loads((root / "stage/jobs.json").read_text(encoding="utf-8"))

if "message" in runs:
    raise SystemExit("GitHub API: %s" % runs["message"])

for run in runs.get("workflow_runs", []):
    print("#%-3s %-12s %-9s %s" % (run["run_number"], run["status"],
                                   run.get("conclusion") or "-", run["html_url"]))

print()
for job in jobs.get("jobs", []):
    print("job %s: %s / %s" % (job["name"], job["status"], job.get("conclusion") or "-"))
    for s in job.get("steps", []):
        mark = {"success": "ok  ", "failure": "FAIL", "skipped": "skip",
                "in_progress": ">   ", "queued": "?   ", "cancelled": "can "}.get(s["status"], "    ")
        print("  %s %2d. %s" % (mark, s["number"], s["name"]))
