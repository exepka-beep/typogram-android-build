"""Пошаговый разбор: где именно виснет run_build.py status."""

import sys
import time

sys.path.insert(0, "android/tools")

print("шаг 1: импорт run_build", flush=True)
import run_build as rb

print("шаг 2: resolve_token", flush=True)
tok = rb.resolve_token(None)
print("  токен длиной", len(tok), flush=True)

print("шаг 3: latest_run", flush=True)
t0 = time.time()
run = rb.latest_run(tok, "exepka-beep", "shapitogram-android-build")
print("  %.1f с -> %s" % (time.time() - t0, run.get("run_number") if run else None), flush=True)
if run:
    print("  статус:", run["status"], "/", run.get("conclusion"), flush=True)
    print("  url:", run["html_url"], flush=True)

    print("шаг 4: run_steps", flush=True)
    t0 = time.time()
    rows, job = rb.run_steps(tok, "exepka-beep", "shapitogram-android-build", run["id"])
    print("  %.1f с -> %d строк" % (time.time() - t0, len(rows)), flush=True)
    rb.print_steps(rows)
