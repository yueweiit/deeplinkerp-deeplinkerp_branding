#!/usr/bin/env python3
"""Dedicated user timer installer/launcher; fixed host, site, container and jobs."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

STATE = Path("/home/yuewei/.local/state/deeplinkerp-source-sync")
SHARE = Path("/home/yuewei/.local/share/deeplinkerp-source-sync")
UNITS = Path("/home/yuewei/.config/systemd/user")
RELEASE_LOCK = Path("/tmp/deeplinkerp-erp-release.lock")
MODULE = "deeplinkerp_branding.services.dedicated_source_sync"
METHODS = ("deeplinkerp_branding.services.operating_expenses.scheduled_sync",
           "deeplinkerp_branding.services.purchase_source_service.scheduled_sync")
TASKS = ("operating", "purchase")
UNIT_NAMES = ("deeplinkerp-source-sync.service", "deeplinkerp-source-sync.timer")
CONTAINER = "frappe_docker-backend-1"
CONTAINERS = (CONTAINER, "deeplinkerp_main-backend-1")
BENCH = "/home/frappe/frappe-bench"
PYTHON = BENCH + "/env/bin/python"
RUNNER_FILE = BENCH + "/apps/deeplinkerp_branding/deeplinkerp_branding/services/dedicated_source_sync.py"


class CleanupUnconfirmed(RuntimeError):
    pass


class ParentInterrupted(BaseException):
    pass


def atomic_write(path, content):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.is_symlink():
        raise RuntimeError("Refusing symlink installation target")
    if path.exists() and path.read_bytes() == content:
        return
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def save_json(path, value):
    atomic_write(path, (json.dumps(value, sort_keys=True) + "\n").encode())


@contextmanager
def locked():
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    files = []
    try:
        for path in (RELEASE_LOCK, STATE / "run.lock"):
            fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            files.append(fd)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        for fd in reversed(files):
            os.close(fd)


def container_command(action, task=None, run_id=None, *, container=CONTAINER):
    if container not in CONTAINERS:
        raise ValueError("Backend container is not allowlisted")
    if action not in {"run", "cancel", "finalize", "logging-snapshot", "logging-apply", "logging-restore"}:
        raise ValueError("Unsupported fixed runner action")
    allowed_tasks = TASKS + (("reversal",) if container == CONTAINERS[1] else ())
    if task is not None and task not in allowed_tasks:
        raise ValueError("Source task is not allowlisted")
    command = ["docker", "exec", "-i", "--user", "frappe", "--workdir", BENCH, container, PYTHON, "-m", MODULE, action]
    if task:
        command += ["--task", task, "--run-id", run_id]
    return command


def reply(output):
    try:
        value = json.loads(output.strip().splitlines()[-1])
        if not isinstance(value, dict) or "outcome" not in value:
            raise ValueError()
        return value
    except (IndexError, ValueError, TypeError):
        raise RuntimeError("Runner returned no confirmed safe outcome") from None


def container_call(action, task=None, run_id=None, payload=None, *, container=CONTAINER):
    result = subprocess.run(container_command(action, task, run_id, container=container), input=json.dumps(payload) if payload is not None else "",
                            capture_output=True, text=True, timeout=40)
    value = reply(result.stdout)
    if result.returncode not in (0, 1) or value["outcome"] == "Refused":
        raise RuntimeError("Container control action was refused")
    return value


def validate_config(config):
    if (set(config) not in ({"version", "revision", "image_id", "runner_sha256"}, {"version", "revision", "image_id", "runner_sha256", "container"}) or config["version"] != 1
            or config.get("container", CONTAINER) not in CONTAINERS
            or not re.fullmatch(r"[0-9a-f]{40}", config["revision"])
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", config["image_id"])
            or not re.fullmatch(r"[0-9a-f]{64}", config["runner_sha256"])):
        raise ValueError("Verified revision, image ID and runner checksum are required")


def verify_identity(config):
    validate_config(config)
    container = config.get("container", CONTAINER)
    format_value = '{"image":"{{.Image}}","running":{{.State.Running}},"revision":"{{index .Config.Labels "org.deeplinkerp.branding.revision"}}"}'
    result = subprocess.run(["docker", "inspect", container, "--format", format_value], capture_output=True, text=True, check=True, timeout=15)
    actual = json.loads(result.stdout)
    expected = {"image": config["image_id"], "running": True, "revision": config["revision"]}
    if actual not in (expected, {**expected, "revision": ""}):
        raise RuntimeError("Running backend differs from the verified source release")
    # Layered releases record every app's commit in the image manifest. A
    # legacy branding label may be absent, but a conflicting label must fail.
    revision_result = subprocess.run(
        ["docker", "exec", "--user", "frappe", container, "cat", BENCH + "/app-revisions.json"],
        capture_output=True, text=True, check=True, timeout=15,
    )
    try:
        revisions = json.loads(revision_result.stdout)
    except (TypeError, ValueError):
        raise RuntimeError("Installed app revision metadata is invalid") from None
    if not isinstance(revisions, dict) or revisions.get("deeplinkerp_branding") != config["revision"]:
        raise RuntimeError("Installed Branding revision differs from the verified source release")
    checksum = subprocess.run(["docker", "exec", "--user", "frappe", container, "sha256sum", RUNNER_FILE],
                              capture_output=True, text=True, check=True, timeout=15)
    if checksum.stdout.split()[0] != config["runner_sha256"]:
        raise RuntimeError("Installed source runner differs from reviewed code")


def recover_child(task, run_id, *, container=CONTAINER):
    try:
        container_call("cancel", task=task, run_id=run_id, container=container)
        outcome = container_call("finalize", task=task, run_id=run_id, container=container)["outcome"]
        if outcome not in {"Complete", "Failed"}:
            raise RuntimeError("No terminal native job log")
        return outcome
    except Exception:
        raise CleanupUnconfirmed("Container termination or native log recovery is unconfirmed") from None


def invoke_child(config, task, run_id):
    verify_identity(config)
    container = config.get("container", CONTAINER)
    command = container_command("run", task=task, run_id=run_id, container=container)
    command += ["--deadline", str(time.time() + 300)]
    child = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    recovery = False
    try:
        output, _ = child.communicate(timeout=330)  # 300 s in child; bounded cleanup grace.
        value = reply(output)
        if value["outcome"] in {"Complete", "Failed"} and child.returncode in (0, 1):
            return value["outcome"]
        if value["outcome"] == "Refused" and not value.get("recover"):
            return "Refused"
        recovery = True
        return recover_child(task, run_id, container=container)
    except ParentInterrupted:
        recovery = True
        recover_child(task, run_id, container=container)
        raise
    except CleanupUnconfirmed:
        raise
    except Exception:
        recovery = True
        return recover_child(task, run_id, container=container)
    finally:
        if recovery:
            # Killing a docker exec client alone does not kill its container child.
            child.kill()
            try:
                child.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                pass


def run_tasks(config):
    ledger = STATE / "run.json"
    state = json.loads(ledger.read_text()) if ledger.exists() else {"active": None}
    if state["active"]:
        recovery_args = {} if state.get("container", CONTAINER) == CONTAINER else {"container": state["container"]}
        recover_child(**state["active"], **recovery_args)
        save_json(ledger, {"active": None})
    results = []
    tasks = TASKS + (("reversal",) if config.get("container") == CONTAINERS[1] else ())
    for task in tasks:
        active = {"task": task, "run_id": str(uuid.uuid4())}
        save_json(ledger, {"active": active, "container": config.get("container", CONTAINER)})
        try:
            outcome = invoke_child(config, **active)
        except CleanupUnconfirmed:
            raise  # Keep durable intent; never overlap a child with unknown state.
        except Exception:
            outcome = "Failed"
        item = {**active, "outcome": outcome}
        results.append(item)
        save_json(ledger, {"active": None, "last": item})
        print(json.dumps(item, sort_keys=True), flush=True)
    return results


def systemctl(*args):
    result = subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True, timeout=90)
    # First installation has no unit to stop. Do not hide other systemctl errors.
    if result.returncode and not (args[0] == "stop" and result.returncode == 5):
        raise RuntimeError("User service control action failed")


def install(config, start_timer=False):
    validate_config(config)
    with locked():
        verify_identity(config)
        systemctl("stop", UNIT_NAMES[1])
        receipt_path = STATE / "installation.json"
        if receipt_path.exists():
            receipt = json.loads(receipt_path.read_text())
            if receipt.get("version") != 1:
                raise RuntimeError("Unknown installation evidence version")
            receipt.update(config=config)
        else:
            receipt = {"version": 1, "config": config, "status": "pending",
                       "logging_before": container_call("logging-snapshot", container=config.get("container", CONTAINER))["outcome"]}
        # Evidence reaches durable storage before any metadata or unit mutation.
        save_json(receipt_path, receipt)
        container_call("logging-apply", payload=receipt["logging_before"], container=config.get("container", CONTAINER))
        source = Path(__file__)
        atomic_write(SHARE / "launcher.py", source.read_bytes())
        save_json(STATE / "config.json", config)
        for name in UNIT_NAMES:
            atomic_write(UNITS / name, (source.parent / "systemd" / name).read_bytes())
        systemctl("daemon-reload")
        if start_timer:
            container_call("logging-snapshot", container=config.get("container", CONTAINER))  # Recheck fixed native jobs before enable.
            systemctl("enable", "--now", UNIT_NAMES[1])
        receipt["status"] = "installed" if start_timer else "staged"
        save_json(receipt_path, receipt)
    return receipt


def retarget(config, start_timer=False):
    """Update an already installed idle timer's pins, preserving logging/queues."""
    validate_config(config)
    with locked():
        verify_identity(config)
        receipt_path = STATE / "installation.json"
        receipt = json.loads(receipt_path.read_bytes())
        previous = json.loads((STATE / "config.json").read_bytes())
        validate_config(previous)
        if receipt.get("version") != 1 or receipt.get("status") not in {"installed", "staged"} or receipt["config"] != previous:
            raise RuntimeError("Existing source installation evidence differs; HOLD")
        ledger = STATE / "run.json"
        if ledger.exists() and json.loads(ledger.read_bytes()).get("active"):
            raise RuntimeError("Existing source execution is unconfirmed; HOLD")
        # No logging-snapshot/apply: queued fixed jobs must remain queued.
        if previous != config:
            receipt.update(config=config, previous_config=previous)
        receipt["status"] = "installed" if start_timer else "staged"
        save_json(receipt_path, receipt)
        atomic_write(SHARE / "launcher.py", Path(__file__).read_bytes())
        save_json(STATE / "config.json", config)
        if start_timer:
            systemctl("start", UNIT_NAMES[1])  # Never enable a previously absent timer.
    return receipt


def rollback():
    # Stop new invocations and let the launcher cancel any active container child.
    systemctl("disable", "--now", UNIT_NAMES[1])
    systemctl("stop", UNIT_NAMES[0])
    with locked():
        receipt_path = STATE / "installation.json"
        receipt = json.loads(receipt_path.read_text())
        verify_identity(receipt["config"])
        ledger = STATE / "run.json"
        if ledger.exists():
            active = json.loads(ledger.read_text())["active"]
            if active:
                recover_child(**active, container=receipt["config"].get("container", CONTAINER))
                save_json(ledger, {"active": None})
        container_call("logging-restore", payload=receipt["logging_before"], container=receipt["config"].get("container", CONTAINER))
        receipt["status"] = "rolled-back"
        save_json(receipt_path, receipt)
    return receipt


def main(argv=None):
	parser = argparse.ArgumentParser(description=__doc__)
	parser.add_argument("action", choices=("install", "retarget", "run", "rollback"))
	parser.add_argument("--revision")
	parser.add_argument("--image-id")
	parser.add_argument("--runner-sha256")
	parser.add_argument("--container", choices=CONTAINERS)
	parser.add_argument("--start-timer", action="store_true")
	parser.add_argument("--config", type=Path, default=STATE / "config.json")
	args = parser.parse_args(argv)
	if Path.home() != Path("/home/yuewei"):
		parser.error("Run as the existing yuewei host user")
	try:
		if args.action in {"install", "retarget"}:
			config = {
				"version": 1,
				"revision": args.revision,
				"image_id": args.image_id,
				"runner_sha256": args.runner_sha256,
			}
			if args.container:
				config["container"] = args.container
			(install if args.action == "install" else retarget)(config, start_timer=args.start_timer)
		elif args.action == "rollback":
			rollback()
		else:
			config = json.loads(args.config.read_text())
			validate_config(config)

			def interrupted(signum, frame):
				raise ParentInterrupted()

			signal.signal(signal.SIGTERM, interrupted)
			signal.signal(signal.SIGINT, interrupted)
			with locked():
				verify_identity(config)
				result = run_tasks(config)
			return int(any(item["outcome"] != "Complete" for item in result))
		return 0
	except BlockingIOError:
		outcome = "Skipped" if args.action == "run" else "Failed"
		print(json.dumps({"outcome": outcome, "reason": "Release or source sync owns the mutex"}))
		return int(args.action != "run")
	except (Exception, ParentInterrupted):
		print('{"outcome":"Failed","reason":"Source sync or control action unconfirmed"}')
		return 1


if __name__ == "__main__":
    sys.exit(main())
