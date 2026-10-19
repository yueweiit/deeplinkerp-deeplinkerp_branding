"""Host installation/rollback and actual container-child cancellation contracts."""

import importlib.util
import io
import json
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).parents[1]


class DedicatedSourceHostTests(unittest.TestCase):
	def setUp(self):
		source = ROOT / "deploy/production/dedicated_source_sync.py"
		self.assertTrue(source.exists(), "Durable source-sync host launcher is missing")
		spec = importlib.util.spec_from_file_location("dedicated_source_sync_host", source)
		self.host = importlib.util.module_from_spec(spec)
		spec.loader.exec_module(self.host)
		self.tmp = tempfile.TemporaryDirectory()
		self.addCleanup(self.tmp.cleanup)
		self.base = Path(self.tmp.name)
		self.config = {
			"version": 1,
			"revision": "a" * 40,
			"image_id": "sha256:" + "b" * 64,
			"runner_sha256": "c" * 64,
		}
		for key, suffix in (("STATE", "state"), ("SHARE", "share"), ("UNITS", "units")):
			item = patch.object(self.host, key, self.base / suffix)
			item.start()
			self.addCleanup(item.stop)
		item = patch.object(self.host, "RELEASE_LOCK", self.base / "release.lock")
		item.start()
		self.addCleanup(item.stop)

	def test_mutex_rejects_overlap_without_launching_another_child(self):
		with self.host.locked():
			with self.assertRaises(BlockingIOError), self.host.locked():
				self.fail("Concurrent source sync entered")
		with self.host.locked():
			pass

	def test_tasks_are_serial_and_second_continues_after_first_failure(self):
		events = []

		def child(config, task, run_id):
			events.append(task)
			return "Failed" if task == "operating" else "Complete"

		with patch.object(self.host, "invoke_child", side_effect=child):
			results = self.host.run_tasks(self.config)
		self.assertEqual(events, ["operating", "purchase"])
		self.assertEqual([item["outcome"] for item in results], ["Failed", "Complete"])
		self.assertIsNone(json.loads((self.host.STATE / "run.json").read_text())["active"])

	def test_parent_watchdog_cancels_and_finalizes_actual_container_child(self):
		child = Mock()
		child.communicate.side_effect = subprocess.TimeoutExpired("docker", 330)
		with (
			patch.object(self.host, "verify_identity"),
			patch.object(self.host.subprocess, "Popen", return_value=child),
			patch.object(
				self.host, "container_call", side_effect=[{"outcome": "Cancelled"}, {"outcome": "Failed"}]
			) as container,
		):
			self.assertEqual(self.host.invoke_child(self.config, "operating", "run-token"), "Failed")
		self.assertEqual([call.args[0] for call in container.call_args_list], ["cancel", "finalize"])
		self.assertTrue(all(call.kwargs["run_id"] == "run-token" for call in container.call_args_list))
		child.kill.assert_called_once_with()  # Only after actual container cancellation.

	def test_unconfirmed_container_termination_stops_before_second_task(self):
		with patch.object(
			self.host, "invoke_child", side_effect=self.host.CleanupUnconfirmed("failed to cancel")
		) as child:
			with self.assertRaises(self.host.CleanupUnconfirmed):
				self.host.run_tasks(self.config)
		self.assertEqual(child.call_count, 1)
		self.assertEqual(
			json.loads((self.host.STATE / "run.json").read_text())["active"]["task"], "operating"
		)

	def test_interrupted_parent_is_recovered_before_new_tasks(self):
		self.host.STATE.mkdir()
		(self.host.STATE / "run.json").write_text(
			json.dumps({"active": {"task": "operating", "run_id": "old-run"}})
		)
		events = []
		with (
			patch.object(
				self.host,
				"recover_child",
				side_effect=lambda task, run_id: events.append("recover") or "Failed",
			),
			patch.object(
				self.host,
				"invoke_child",
				side_effect=lambda config, task, run_id: events.append(task) or "Complete",
			),
		):
			self.host.run_tasks(self.config)
		self.assertEqual(events, ["recover", "operating", "purchase"])

	def test_container_command_only_selects_existing_fixed_source_tasks(self):
		cmd = self.host.container_command("run", task="purchase", run_id="token")
		self.assertIn("frappe_docker-backend-1", cmd)
		self.assertIn("/home/frappe/frappe-bench/env/bin/python", cmd)
		self.assertIn("deeplinkerp_branding.services.dedicated_source_sync", cmd)
		self.assertEqual(cmd[-4:], ["--task", "purchase", "--run-id", "token"])
		with self.assertRaises(ValueError):
			self.host.container_command("run", task="frappe.email.send", run_id="token")

	def test_main_backend_retarget_accepts_only_two_fixed_container_identities(self):
		self.assertTrue(hasattr(self.host, "CONTAINERS"), "Fixed main backend migration is missing")
		config = {**self.config, "container": "deeplinkerp_main-backend-1"}
		self.host.validate_config(config)
		command = self.host.container_command(
			"run", task="purchase", run_id="token", container=config["container"]
		)
		self.assertIn(config["container"], command)
		for container in (
			"other-backend",
			"frappe_docker-backend-2",
			"deeplinkerp_main-backend-1 --privileged",
		):
			with self.subTest(container=container), self.assertRaises(ValueError):
				self.host.validate_config({**config, "container": container})

	def test_main_lane_drives_the_fixed_accepted_reversal_job_with_scheduler_disabled(self):
		seen = []
		with patch.object(
			self.host,
			"invoke_child",
			side_effect=lambda config, task, run_id: seen.append(task) or "Complete",
		):
			self.host.run_tasks({**self.config, "container": "deeplinkerp_main-backend-1"})
		self.assertEqual(seen, ["operating", "purchase", "reversal"])
		self.assertEqual(
			self.host.METHODS,
			(
				"deeplinkerp_branding.services.operating_expenses.scheduled_sync",
				"deeplinkerp_branding.services.purchase_source_service.scheduled_sync",
			),
		)

	def test_installer_is_idempotent_and_keeps_first_logging_evidence(self):
		before = {
			"version": 1,
			"jobs": [
				{"name": "op", "method": self.host.METHODS[0], "create_log": 0},
				{"name": "pur", "method": self.host.METHODS[1], "create_log": 1},
			],
		}
		with (
			patch.object(self.host, "verify_identity"),
			patch.object(self.host, "systemctl") as systemctl,
			patch.object(self.host, "container_call", return_value={"outcome": before}),
		):
			self.host.install(self.config)
			receipt = (self.host.STATE / "installation.json").read_bytes()
			unit = self.host.UNITS / "deeplinkerp-source-sync.service"
			inode = unit.stat().st_ino
			self.host.install(self.config)
		self.assertEqual((self.host.STATE / "installation.json").read_bytes(), receipt)
		self.assertEqual(unit.stat().st_ino, inode)
		self.assertEqual(json.loads(receipt)["logging_before"], before)
		self.assertNotIn(
			("enable", "--now", "deeplinkerp-source-sync.timer"),
			[call.args for call in systemctl.call_args_list],
		)
		self.assertEqual(json.loads(receipt)["status"], "staged")

	def test_rollback_stops_timer_and_restores_only_evidenced_logging_flags(self):
		before = {
			"version": 1,
			"jobs": [
				{"name": "op", "method": self.host.METHODS[0], "create_log": 0},
				{"name": "pur", "method": self.host.METHODS[1], "create_log": 1},
			],
		}
		self.host.STATE.mkdir()
		(self.host.STATE / "installation.json").write_text(
			json.dumps({"version": 1, "config": self.config, "logging_before": before, "status": "installed"})
		)
		with (
			patch.object(self.host, "verify_identity"),
			patch.object(self.host, "systemctl") as systemctl,
			patch.object(self.host, "container_call", return_value={"outcome": before}) as container,
		):
			self.host.rollback()
		self.assertEqual(
			systemctl.call_args_list[0].args, ("disable", "--now", "deeplinkerp-source-sync.timer")
		)
		self.assertEqual(container.call_args.args[0], "logging-restore")
		self.assertEqual(container.call_args.kwargs["payload"], before)
		self.assertEqual(
			json.loads((self.host.STATE / "installation.json").read_text())["status"], "rolled-back"
		)

	def test_existing_timer_retarget_preserves_logging_queue_and_first_previous_config(self):
		for scenario in ("idle", "active", "config-drift"):
			with self.subTest(scenario=scenario):
				original = {
					"version": 1,
					"config": self.config,
					"logging_before": {"jobs": [{"name": "old", "create_log": 0}]},
					"status": "installed",
				}
				self.host.save_json(self.host.STATE / "installation.json", original)
				self.host.save_json(self.host.STATE / "config.json", self.config)
				self.host.save_json(
					self.host.STATE / "run.json",
					{"active": {"run_id": "unknown"} if scenario == "active" else None},
				)
				if scenario == "config-drift":
					self.host.save_json(
						self.host.STATE / "config.json", {**self.config, "revision": "e" * 40}
					)
				target = {**self.config, "revision": "d" * 40}
				before = (self.host.STATE / "installation.json").read_bytes()
				with (
					patch.object(self.host, "verify_identity"),
					patch.object(self.host, "systemctl") as timer,
					patch.object(
						self.host,
						"container_call",
						side_effect=AssertionError(
							"Retarget must not require empty queued jobs or mutate logging/cancel/finalize"
						),
					),
				):
					if scenario != "idle":
						with self.assertRaisesRegex(RuntimeError, "HOLD"):
							self.host.retarget(target, start_timer=True)
						self.assertEqual((self.host.STATE / "installation.json").read_bytes(), before)
						timer.assert_not_called()
					else:
						self.host.retarget(target, start_timer=True)
						receipt = (self.host.STATE / "installation.json").read_bytes()
						self.host.retarget(target, start_timer=True)
						self.assertEqual((self.host.STATE / "installation.json").read_bytes(), receipt)
						state = json.loads(receipt)
						self.assertEqual(state["logging_before"], original["logging_before"])
						self.assertEqual(state["previous_config"], self.config)
						self.assertEqual(
							[call.args for call in timer.call_args_list],
							[("start", "deeplinkerp-source-sync.timer")] * 2,
						)

	def test_unit_timer_uses_user_service_quarter_hours_without_catch_up(self):
		timer = (ROOT / "deploy/production/systemd/deeplinkerp-source-sync.timer").read_text()
		service = (ROOT / "deploy/production/systemd/deeplinkerp-source-sync.service").read_text()
		self.assertIn("OnCalendar=*-*-* *:00,15,30,45:00", timer)
		self.assertIn("Persistent=false", timer)
		self.assertIn("%h/.local/share/deeplinkerp-source-sync/launcher.py run", service)
		self.assertIn("WorkingDirectory=/home/yuewei/ERPNext-Docker/frappe_docker", service)

	def test_release_archive_allows_only_exact_new_deploy_files_and_ci_runs_tests(self):
		release = (ROOT / "deploy/production/deploy_unified_purchase.sh").read_text()
		for name in (
			"deploy/production/dedicated_source_sync.py",
			"deploy/production/systemd/deeplinkerp-source-sync.service",
			"deploy/production/systemd/deeplinkerp-source-sync.timer",
		):
			self.assertIn("'" + name + "'", release)
		self.assertIn(
			"tests.test_dedicated_source_sync_host", (ROOT / ".github/workflows/ci.yml").read_text()
		)

	def test_first_install_accepts_only_stop_of_a_missing_user_unit(self):
		with patch.object(self.host.subprocess, "run", return_value=SimpleNamespace(returncode=5)):
			self.host.systemctl("stop", "deeplinkerp-source-sync.timer")
			with self.assertRaises(RuntimeError):
				self.host.systemctl("enable", "--now", "deeplinkerp-source-sync.timer")

	def test_explicit_start_flag_is_required_to_enable_staged_timer(self):
		before = {
			"version": 1,
			"jobs": [
				{"name": "op", "method": self.host.METHODS[0], "create_log": 0},
				{"name": "pur", "method": self.host.METHODS[1], "create_log": 0},
			],
		}
		with (
			patch.object(self.host, "verify_identity"),
			patch.object(self.host, "systemctl") as systemctl,
			patch.object(self.host, "container_call", return_value={"outcome": before}),
		):
			self.host.install(self.config, start_timer=True)
		self.assertIn(
			("enable", "--now", "deeplinkerp-source-sync.timer"),
			[call.args for call in systemctl.call_args_list],
		)

	def test_changed_image_revision_or_runner_checksum_refuses_launch(self):
		expected = {"image": self.config["image_id"], "running": True, "revision": self.config["revision"]}
		for field, value in (("image", "sha256:" + "d" * 64), ("revision", "e" * 40), ("running", False)):
			actual = {**expected, field: value}
			with (
				self.subTest(field=field),
				patch.object(
					self.host.subprocess, "run", return_value=SimpleNamespace(stdout=json.dumps(actual))
				),
				self.assertRaises(RuntimeError),
			):
				self.host.verify_identity(self.config)
		with (
			patch.object(
				self.host.subprocess,
				"run",
				side_effect=[
					SimpleNamespace(stdout=json.dumps(expected)),
					SimpleNamespace(stdout=json.dumps({"deeplinkerp_branding": self.config["revision"]})),
					SimpleNamespace(stdout="d" * 64 + " file"),
				],
			),
			self.assertRaisesRegex(RuntimeError, "runner"),
		):
			self.host.verify_identity(self.config)

	def identity_responses(self, *, label, manifest, image=None):
		"""Fixed Docker reads only; never execute a production container."""
		def read(command, **kwargs):
			if command[:2] == ["docker", "inspect"]:
				body = {"image": image or self.config["image_id"], "running": True, "revision": label}
				return SimpleNamespace(stdout=json.dumps(body))
			if command[-2:] == ["cat", self.host.BENCH + "/app-revisions.json"]:
				return SimpleNamespace(stdout=manifest)
			if "sha256sum" in command:
				return SimpleNamespace(stdout=self.config["runner_sha256"] + " runner.py")
			self.fail("Identity verification attempted an unexpected command")
		return read

	def test_runtime_app_revision_verifies_images_with_or_without_legacy_branding_label(self):
		for label in ("", self.config["revision"]):
			with (
				self.subTest(label_present=bool(label)),
				patch.object(self.host.subprocess, "run", side_effect=self.identity_responses(
					label=label, manifest=json.dumps({"deeplinkerp_branding": self.config["revision"]}),
				)) as reads,
			):
				self.host.verify_identity(self.config)
			self.assertTrue(any(call.args[0][-2:] == ["cat", self.host.BENCH + "/app-revisions.json"]
				for call in reads.call_args_list))

	def test_missing_invalid_or_conflicting_runtime_revision_never_uses_a_matching_label_as_bypass(self):
		for manifest in ("{}", "[]", "not-json", json.dumps({"china_finance": self.config["revision"]}),
			json.dumps({"deeplinkerp_branding": "d" * 40})):
			with (
				self.subTest(manifest=manifest),
				patch.object(self.host.subprocess, "run", side_effect=self.identity_responses(
					label=self.config["revision"], manifest=manifest,
				)),
				self.assertRaises(RuntimeError),
			):
				self.host.verify_identity(self.config)

	def test_conflicting_label_is_rejected_even_when_runtime_app_revision_matches(self):
		with (
			patch.object(self.host.subprocess, "run", side_effect=self.identity_responses(
				label="d" * 40, manifest=json.dumps({"deeplinkerp_branding": self.config["revision"]}),
			)) as reads,
			self.assertRaisesRegex(RuntimeError, "Running backend"),
		):
			self.host.verify_identity(self.config)
		self.assertEqual(reads.call_count, 1)

	def test_image_only_release_requires_retarget_and_preserves_existing_jobs_and_logging(self):
		original = {"version": 1, "config": self.config, "status": "installed",
			"logging_before": {"jobs": [{"name": "operating", "create_log": 0}]}}
		self.host.save_json(self.host.STATE / "installation.json", original)
		self.host.save_json(self.host.STATE / "config.json", self.config)
		self.host.save_json(self.host.STATE / "run.json", {"active": None})
		target = {**self.config, "image_id": "sha256:" + "d" * 64}
		with (
			patch.object(self.host.subprocess, "run", side_effect=self.identity_responses(
				label="", manifest=json.dumps({"deeplinkerp_branding": self.config["revision"]}),
				image=target["image_id"],
			)),
			patch.object(self.host, "systemctl") as timer,
			patch.object(self.host, "container_call", side_effect=AssertionError("No native job mutation during retarget")),
		):
			with self.assertRaises(RuntimeError):
				self.host.verify_identity(self.config)
			self.host.retarget(target, start_timer=True)
			self.host.verify_identity(json.loads((self.host.STATE / "config.json").read_text()))
		receipt = json.loads((self.host.STATE / "installation.json").read_text())
		self.assertEqual(receipt["config"], target)
		self.assertEqual(receipt["previous_config"], self.config)
		self.assertEqual(receipt["logging_before"], original["logging_before"])
		self.assertEqual([call.args for call in timer.call_args_list], [("start", "deeplinkerp-source-sync.timer")])

	def test_installer_rechecks_native_jobs_immediately_before_starting_timer(self):
		before = {
			"version": 1,
			"jobs": [
				{"name": "op", "method": self.host.METHODS[0], "create_log": 0},
				{"name": "pur", "method": self.host.METHODS[1], "create_log": 0},
			],
		}
		with (
			patch.object(self.host, "verify_identity"),
			patch.object(self.host, "systemctl") as systemctl,
			patch.object(
				self.host,
				"container_call",
				side_effect=[
					{"outcome": before},
					{"outcome": before},
					RuntimeError("native job became queued"),
				],
			),
		):
			with self.assertRaises(RuntimeError):
				self.host.install(self.config, start_timer=True)
		self.assertNotIn(
			("enable", "--now", "deeplinkerp-source-sync.timer"),
			[call.args for call in systemctl.call_args_list],
		)

	def test_control_action_mutex_refusal_is_nonzero_and_never_skipped(self):
		for action, function in (("install", "install"), ("rollback", "rollback")):
			with (
				self.subTest(action=action),
				patch.object(self.host.Path, "home", return_value=Path("/home/yuewei")),
				patch.object(self.host, function, side_effect=BlockingIOError()),
				patch.object(self.host.sys, "stdout", io.StringIO()) as output,
			):
				self.assertNotEqual(self.host.main([action]), 0)
				self.assertEqual(json.loads(output.getvalue())["outcome"], "Failed")

	def test_run_mutex_refusal_remains_skipped_with_zero_exit(self):
		self.host.save_json(self.host.STATE / "config.json", self.config)
		with (
			patch.object(self.host.Path, "home", return_value=Path("/home/yuewei")),
			patch.object(self.host.signal, "signal"),
			patch.object(self.host, "locked", side_effect=BlockingIOError()),
			patch.object(self.host.sys, "stdout", io.StringIO()) as output,
		):
			self.assertEqual(self.host.main(["run"]), 0)
			self.assertEqual(json.loads(output.getvalue())["outcome"], "Skipped")
