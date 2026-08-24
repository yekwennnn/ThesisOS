"""Deterministic subprocess fixture for the provider-neutral adapter tests."""

from __future__ import annotations

import json
import os
import sys
import time


mode = sys.argv[1] if len(sys.argv) > 1 else "echo"

if mode == "nonzero":
    sys.stderr.write("intentional adapter failure\n")
    raise SystemExit(17)

if mode == "invalid-json":
    sys.stdout.write("not json")
    raise SystemExit(0)

if mode == "oversize":
    sys.stdout.write(json.dumps("x" * 100_000))
    raise SystemExit(0)

if mode == "sleep":
    time.sleep(5)
    sys.stdout.write("{}")
    raise SystemExit(0)

request = json.load(sys.stdin)
task = request.get("task")

if mode == "counted-file-output":
    count_path = sys.argv[3]
    descriptor = os.open(
        count_path,
        os.O_APPEND | os.O_CREAT | os.O_WRONLY,
        0o600,
    )
    try:
        os.write(descriptor, b"adapter-called\n")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    time.sleep(float(sys.argv[4]))
    with open(sys.argv[2], encoding="utf-8") as handle:
        response = json.load(handle)
elif mode == "file-output":
    with open(sys.argv[2], encoding="utf-8") as handle:
        response = json.load(handle)
elif mode == "wrong-shape":
    response = {} if task == "evidence-extraction" else []
elif mode == "duplicate-key":
    sys.stdout.write('{"same":1,"same":2}')
    raise SystemExit(0)
else:
    response = {
        "received_protocol": request.get("protocol"),
        "received_protocol_version": request.get("protocol_version"),
        "received_task": task,
        "received_contract": request.get("contract"),
        "received_model_identifier": request.get("model_identifier"),
        "received_request_metadata": request.get("request_metadata"),
        "received_inputs": request.get("inputs"),
    }
    if task == "evidence-extraction":
        response = [response]

json.dump(response, sys.stdout, ensure_ascii=False, sort_keys=True)
