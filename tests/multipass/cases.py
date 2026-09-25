"""Multipass 编排器与来宾机辅助程序的离线行为测试。"""

from contextlib import contextmanager, redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock


REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts/multipass"))
import guest
import host
import live
import runtime
import ssh_config
import ssh_proxy


PUB = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIG3Z+/i5KijOrBwCDnV+BYuzxq76WhXpuN4V7UZckucA"
REF = "a" * 40
RECONNECT_ERROR = "exec failed: ssh connection failed: 'Timeout connecting to 192.168.252.43'"


def args(**overrides):
    values = dict(action="create", name=None, config=None, dry_run=True, apply=False, ref=REF,
                  ssh_public_key=None, image=None, cpus=None, memory=None, disk=None, repo_url=None)
    values.update(overrides)
    return SimpleNamespace(**values)


class HelpTests(unittest.TestCase):
    def help_output(self, *command):
        result = subprocess.run(["bash", str(REPO / "scripts/multipass.sh"), *command, "--help"],
                                cwd=REPO, env={**os.environ, "COLUMNS": "120"},
                                capture_output=True, text=True, check=True)
        self.assertEqual(result.stderr, "")
        self.assertNotIn("runtime.py", result.stdout)
        return result.stdout

    def test_public_entrypoint_names_and_explains_commands_and_options(self):
        root = self.help_output()
        self.assertIn("usage: multipass.sh", root)
        for action, summary in (("create", "create or resume a pinned Ubuntu instance"),
                                ("provision", "reconfigure a managed instance"),
                                ("destroy", "permanently remove a managed instance"),
                                ("check", "inspect a managed instance"),
                                ("ssh", "connect to a running managed instance")):
            self.assertIn(summary, root)
            output = self.help_output(action)
            self.assertIn(f"usage: multipass.sh {action}", output)
            self.assertIn("--name NAME", output)
            self.assertIn("--config FILE", output)

        create = self.help_output("create")
        for explanation in ("preview only (the default when --apply is absent)",
                            "40 lowercase hex digits", "existing absolute public key file",
                            "Ubuntu release: 24.04 or 26.04", "CPU count: 1-64",
                            "RAM: integer M or G units, at least 2G",
                            "disk: integer M or G units, at least 20G",
                            "public HTTPS Git repository URL without credentials"):
            self.assertIn(explanation, create)
        self.assertRegex(create, r"--creation-record FILE +on --apply for a fresh instance")
        self.assertRegex(create, r"new absolute file in an\s+existing directory")

        provision = self.help_output("provision")
        self.assertIn("Use --ref to select a new commit", provision)
        destroy = self.help_output("destroy")
        self.assertIn("--dry-run", destroy)
        self.assertIn("--apply", destroy)
        self.assertNotIn("--ref", destroy)
        self.assertNotIn("--creation-record", provision)
        self.assertIn("run guest doctor diagnostics", self.help_output("check"))

    def test_argument_errors_use_public_entrypoint_name(self):
        result = subprocess.run(["bash", str(REPO / "scripts/multipass.sh"), "unknown"],
                                cwd=REPO, capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("usage: multipass.sh", result.stderr)
        self.assertNotIn("runtime.py", result.stderr)


class Inputs(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.key = self.home / "key.pub"
        self.key.write_text(PUB + " comment with quotes ' \"\n")

    def test_defaults_and_both_images_render_without_secret(self):
        for image in ("24.04", "26.04"):
            data = runtime.declaration_from(args(image=image, ssh_public_key=self.key), {}, None,
                                            PUB, "SHA256:test")
            self.assertEqual(data["image"], image)
            self.assertEqual(data["memory"], "8G")
            cloud = runtime.render_cloud(data, PUB)
            self.assertIn("  - default", cloud)
            self.assertIn("package_reboot_if_required: false", cloud)
            self.assertIn("chmod 0600 /var/lib/dotfiles-multipass/instance.json", cloud)
            self.assertNotIn("/etc/sudoers.d/", cloud)
            self.assertIn(data["uuid"], cloud)
            self.assertNotIn("comment with quotes", cloud)
            self.assertNotIn("PRIVATE", cloud)

    def test_invalid_resource_ref_url_and_name(self):
        mutations = (("cpus", "0"), ("memory", "1G"), ("disk", "5G"),
                     ("image", "latest"), ("ref", "main"),
                     ("repo_url", "https://user:token@example.com/x.git"),
                     ("name", "primary"))
        for field, bad in mutations:
            with self.subTest(field=field), self.assertRaises(runtime.Failure):
                runtime.declaration_from(args(ssh_public_key=self.key, **{field: bad}), {},
                                         None, PUB, "SHA256:test")

    def test_public_key_rejects_private_and_multiple_keys(self):
        runner = runtime.Runner()
        canonical, fingerprint = runtime.public_key(self.key, runner)
        self.assertEqual(canonical, PUB)
        self.assertTrue(fingerprint.startswith("SHA256:"))
        for text in ("-----BEGIN OPENSSH PRIVATE KEY-----\n", PUB + "\n" + PUB + "\n"):
            self.key.write_text(text)
            with self.assertRaises(runtime.Failure):
                runtime.public_key(self.key, runner)

    def test_cloud_status_json_survives_multipass_restart_progress(self):
        output = "\x1b[2KStarting instance / -\\ |\n" + json.dumps(
            {"status": "done", "extended_status": "done", "errors": []}) + "\n"
        self.assertEqual(runtime.parse_json_output(output, "cloud-init")["status"], "done")

    def test_existing_declaration_rejects_changed_resources_key_and_create_ref(self):
        saved = runtime.declaration_from(args(ssh_public_key=self.key), {}, None, PUB,
                                         "SHA256:test")
        with self.assertRaisesRegex(runtime.Failure, "existing memory"):
            runtime.declaration_from(args(memory="16G"), {}, saved, PUB, "SHA256:test")
        with self.assertRaisesRegex(runtime.Failure, "fingerprint"):
            runtime.declaration_from(args(), {}, saved, PUB, "SHA256:other")
        with self.assertRaisesRegex(runtime.Failure, "use provision"):
            runtime.declaration_from(args(ref="b" * 40), {}, saved, PUB, "SHA256:test")

    def test_preview_does_not_write_state_or_ssh_files(self):
        (self.home / ".ssh").mkdir()
        (self.home / ".ssh/config").write_text("Host github.com\n    User git\n")
        before = sorted(str(path.relative_to(self.home)) for path in self.home.rglob("*"))
        with mock.patch.object(runtime, "host_preflight"), \
                mock.patch.object(runtime, "public_key", return_value=(PUB, "SHA256:test")), \
                mock.patch.object(runtime, "require_agent"), \
                mock.patch.object(host.Host, "probe", return_value={"qualified": True,
                                                                    "client": "1.16.4"}), \
                mock.patch.object(host.Host, "instances", return_value={}), \
                mock.patch.object(host.Host, "verify_service"):
            runtime.create_or_provision(args(ssh_public_key=self.key,
                                            creation_record=self.home / "creation.json"), {}, self.home,
                                        runtime.Runner())
        after = sorted(str(path.relative_to(self.home)) for path in self.home.rglob("*"))
        self.assertEqual(before, after)

    def test_unmanaged_same_name_instance_is_a_conflict(self):
        (self.home / ".ssh").mkdir()
        with mock.patch.object(runtime, "host_preflight"), \
                mock.patch.object(runtime, "public_key", return_value=(PUB, "SHA256:test")), \
                mock.patch.object(runtime, "require_agent"), \
                mock.patch.object(host.Host, "probe", return_value={"qualified": True,
                                                                    "client": "1.16.4"}), \
                mock.patch.object(host.Host, "instances", return_value={"ubuntu-dev":
                                                                         {"state": "Running"}}):
            with self.assertRaisesRegex(runtime.Failure, "not managed"):
                runtime.create_or_provision(args(ssh_public_key=self.key), {}, self.home,
                                            runtime.Runner())

    def test_agent_must_offer_the_selected_public_key(self):
        fake = lambda *_args, **_kwargs: SimpleNamespace(stdout=PUB.replace("ucA", "ucB"),
                                                         returncode=0)
        with self.assertRaisesRegex(runtime.Failure, "not loaded"):
            runtime.require_agent(PUB, fake)


class HostPolicy(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def test_qualified_version_does_not_call_installer(self):
        calls = []

        def run(argv, **_kwargs):
            calls.append(argv)
            if argv[1] == "get":
                return SimpleNamespace(stdout="qemu", returncode=0)
            return SimpleNamespace(stdout='{"list": []}', returncode=0)

        machine = host.Host(run, runtime.RELEASES, Path(self.temp.name))
        machine.cli = "/usr/local/bin/multipass"
        with mock.patch.object(machine, "install_pkg", side_effect=AssertionError("installer called")):
            machine.ensure({"qualified": True, "client": "1.16.4"})
        self.assertFalse(any("brew" in str(part) or "installer" in str(part)
                             for argv in calls for part in argv))

    def test_version_mismatch_fails(self):
        def run(_argv, **_kwargs):
            return SimpleNamespace(stdout='{"multipass":"1.16.4","multipassd":"1.16.3"}',
                                   returncode=0)
        machine = host.Host(run, runtime.RELEASES, Path(self.temp.name))
        with mock.patch.object(host.shutil, "which", return_value="/usr/local/bin/multipass"):
            with self.assertRaisesRegex(ValueError, "mismatch"):
                machine.probe()

    def test_path_shadow_or_missing_cli_receipt_is_not_treated_as_fresh_install(self):
        standard = Path(self.temp.name) / "multipass"
        standard.write_text("stub")
        machine = host.Host(lambda *_args, **_kwargs: None, runtime.RELEASES,
                            Path(self.temp.name))
        with mock.patch.object(host, "STANDARD_CLI", standard), \
                mock.patch.object(host.shutil, "which", return_value=None):
            with self.assertRaisesRegex(ValueError, "missing from PATH"):
                machine.probe()
        def receipt(_argv, **_kwargs):
            return SimpleNamespace(stdout="com.canonical.multipass.multipass", returncode=0)
        machine.run = receipt
        with self.assertRaisesRegex(ValueError, "receipts exist"):
            machine.ensure(None)

    def test_old_homebrew_install_uses_only_targeted_cask_upgrade(self):
        calls = []
        def run(argv, **_kwargs):
            calls.append(argv)
            return SimpleNamespace(stdout="multipass 1.15.0", returncode=0)
        machine = host.Host(run, runtime.RELEASES, Path(self.temp.name))
        machine.cli = "/opt/homebrew/bin/multipass"
        with mock.patch.object(host.shutil, "which", return_value="/opt/homebrew/bin/brew"), \
                mock.patch.object(machine, "instances", return_value={}), \
                mock.patch.object(machine, "wait_service", return_value={"qualified": True}):
            machine.ensure({"qualified": False, "source": "unknown", "client": "1.15.0"})
        self.assertIn(["/opt/homebrew/bin/brew", "upgrade", "--cask", "multipass"], calls)
        self.assertFalse(any("--zap" in argv or "uninstall" in argv for argv in calls))

    def test_old_official_pkg_upgrade_does_not_use_homebrew(self):
        machine = host.Host(lambda *_args, **_kwargs: None, runtime.RELEASES,
                            Path(self.temp.name))
        with mock.patch.object(host.shutil, "which", return_value=None), \
                mock.patch.object(machine, "instances", return_value={}), \
                mock.patch.object(machine, "install_pkg") as installer, \
                mock.patch.object(machine, "wait_service", return_value={"qualified": True}):
            machine.ensure({"qualified": False, "source": "official-pkg", "client": "1.15.0"})
        installer.assert_called_once()

    def test_pkg_hash_failure_stops_before_sudo(self):
        calls = []
        steps = []

        @contextmanager
        def progress(label, _description, **_kwargs):
            steps.append(label)
            yield

        def run(argv, **_kwargs):
            calls.append(argv)
            if argv[0] == "curl":
                Path(argv[argv.index("--output") + 1]).write_bytes(b"corrupt pkg")
            return SimpleNamespace(stdout="", returncode=0)

        machine = host.Host(run, runtime.RELEASES, Path(self.temp.name), progress=progress)
        with self.assertRaisesRegex(ValueError, "SHA256 mismatch"):
            machine.install_pkg()
        self.assertEqual(steps, ["package", "download", "checksum"])
        self.assertFalse(any(argv[0] in ("sudo", "installer") for argv in calls))

    def test_size_parser_and_proxy_address_change(self):
        self.assertEqual(host.size_bytes("8.0GiB"), 8 * 1024 ** 3)
        for ip in ("192.168.64.2", "192.168.64.9"):
            self.assertEqual(ssh_proxy.address({"info": {"test": {"state": "Running",
                                                               "ipv4": [ip]}}}, "test"), ip)
        with self.assertRaisesRegex(ValueError, "exactly one"):
            ssh_proxy.address({"info": {"test": {"state": "Running",
                                                   "ipv4": ["10.0.0.2", "10.0.0.3"]}}}, "test")
        with self.assertRaisesRegex(ValueError, "not running"):
            ssh_proxy.address({"info": {"test": {"state": "Stopped"}}}, "test")
        for state in ("Restarting", "Starting", "Unknown", "Deleted"):
            with self.subTest(state=state), self.assertRaises(ValueError) as failure:
                ssh_proxy.address({"info": {"test": {"state": state}}}, "test")
            self.assertIn(f"state={state}", str(failure.exception))
            self.assertNotIn("use multipass start", str(failure.exception))

    def test_cloud_init_degraded_is_not_accepted_as_done(self):
        machine = runtime.Machine(Path(self.temp.name), "test", runtime.Runner())
        machine.m = lambda *_args, **_kwargs: SimpleNamespace(
            stdout=json.dumps({"status": "done", "extended_status": "degraded done",
                               "errors": []}))
        with self.assertRaisesRegex(runtime.Failure, "did not finish cleanly"):
            machine.cloud_wait()

    def test_cloud_init_exit_two_collects_failure_log(self):
        machine = runtime.Machine(Path(self.temp.name), "test", runtime.Runner())
        calls = []
        def call(*argv, **_kwargs):
            calls.append(argv)
            if "status" in argv:
                raise runtime.CommandFailure(["multipass", "exec", "cloud-init"], 2,
                                             "degraded done")
            if argv[0] == "list":
                return SimpleNamespace(stdout=json.dumps({"list": [{"name": "test", "state": "Running"}]}))
            return SimpleNamespace(stdout="cloud-init error")
        machine.m = call
        with self.assertRaises(runtime.CommandFailure):
            machine.cloud_wait()
        self.assertEqual(len(calls), 3)
        self.assertEqual(calls[1][0], "list")
        self.assertTrue(any(part.endswith("cloud-init-output.log") for part in calls[2]))

    def test_timeout_connecting_is_only_a_transport_failure_with_multipass_context(self):
        self.assertTrue(runtime.transient_guest_connection(
            runtime.CommandFailure(["multipass", "exec"], 2, RECONNECT_ERROR)))
        for error in (runtime.Failure(RECONNECT_ERROR),
                      runtime.CommandFailure(["multipass", "exec"], 1, RECONNECT_ERROR),
                      runtime.CommandFailure(["multipass", "exec"], 2,
                                             "cloud-init failed: Timeout connecting to 192.168.252.43"),
                      runtime.CommandFailure(["multipass", "exec"], 2,
                                             "exec failed: ssh connection failed: Permission denied (publickey)")):
            with self.subTest(error=str(error)):
                self.assertFalse(runtime.transient_guest_connection(error))

    def test_cloud_init_retries_only_a_transient_guest_connection_failure(self):
        machine = runtime.Machine(Path(self.temp.name), "test", runtime.Runner())
        machine.path.mkdir(parents=True)
        calls = []

        def call(*argv, **_kwargs):
            calls.append(argv)
            if argv[0] == "list":
                return SimpleNamespace(stdout=json.dumps({"list": [{"name": "test", "state": "Running"}]}))
            if len(calls) == 1:
                raise runtime.CommandFailure(["multipass", "exec"], 2,
                                             "ssh connection failed: No route to host")
            return SimpleNamespace(stdout=json.dumps({"status": "done", "extended_status": "done",
                                                      "errors": []}))

        machine.m = call
        with mock.patch.object(runtime.time, "sleep") as sleep:
            machine.cloud_wait()
        self.assertEqual(len(calls), 3)
        self.assertEqual([argv[0] for argv in calls], ["exec", "list", "exec"])
        sleep.assert_called_once()
        self.assertEqual(machine.receipt["cloud_init"]["extended_status"], "done")

    def test_cloud_init_retries_timeout_connecting_without_failure_diagnostic(self):
        machine = runtime.Machine(Path(self.temp.name), "test", runtime.Runner())
        machine.path.mkdir(parents=True)
        calls = []

        def call(*argv, **_kwargs):
            calls.append(argv)
            if argv[0] == "list":
                return SimpleNamespace(stdout=json.dumps({"list": [{"name": "test", "state": "Running"}]}))
            if len(calls) == 1:
                raise runtime.CommandFailure(["multipass", "exec"], 2, RECONNECT_ERROR)
            return SimpleNamespace(stdout=json.dumps({"status": "done", "errors": []}))

        machine.m = call
        output = io.StringIO()
        with mock.patch.object(runtime.time, "sleep") as sleep, redirect_stderr(output):
            machine.cloud_wait()
        self.assertEqual(len(calls), 3)
        self.assertEqual([argv[0] for argv in calls], ["exec", "list", "exec"])
        sleep.assert_called_once()
        self.assertIn("STEP RETRY", output.getvalue())
        self.assertNotIn("STEP DIAG", output.getvalue())
        self.assertEqual(machine.receipt["cloud_init"]["status"], "done")

    def test_cloud_init_connection_retry_has_a_deadline(self):
        machine = runtime.Machine(Path(self.temp.name), "test", runtime.Runner())
        clock = [0]
        calls = []

        def fail(*argv, **_kwargs):
            calls.append(argv)
            if argv[0] == "list":
                return SimpleNamespace(stdout=json.dumps({"list": [{"name": "test", "state": "Running"}]}))
            raise runtime.CommandFailure(["multipass", "exec"], 2,
                                         "ssh connection failed: No route to host")

        def advance(seconds):
            clock[0] += seconds

        machine.m = fail
        with mock.patch.object(runtime, "CONNECT_RETRY_SECONDS", 11), \
                mock.patch.object(runtime.time, "monotonic", side_effect=lambda: clock[0]), \
                mock.patch.object(runtime.time, "sleep", side_effect=advance), \
                self.assertRaises(runtime.CommandFailure):
            machine.cloud_wait()
        self.assertEqual(sum(argv[0] == "exec" for argv in calls), 4)
        self.assertEqual(clock[0], 11)

    def test_cloud_init_guest_error_text_is_not_a_transport_retry(self):
        for reason in ("No route to host", "Timeout connecting to 192.168.252.43"):
            with self.subTest(reason=reason):
                machine = runtime.Machine(Path(self.temp.name), "test", runtime.Runner())
                calls = []

                def call(*argv, **_kwargs):
                    calls.append(argv)
                    if "status" in argv:
                        raise runtime.CommandFailure(["multipass", "exec"], 2,
                                                     f"cloud-init task failed: {reason}")
                    if argv[0] == "list":
                        return SimpleNamespace(stdout=json.dumps({"list": [{"name": "test", "state": "Running"}]}))
                    return SimpleNamespace(stdout="guest cloud-init output")

                machine.m = call
                with mock.patch.object(runtime.time, "sleep") as sleep, \
                        self.assertRaises(runtime.CommandFailure):
                    machine.cloud_wait()
                sleep.assert_not_called()
                self.assertEqual(len(calls), 3)
                self.assertIn("tail", calls[2])

    def test_cloud_wait_limits_retries_and_commands_to_remaining_time(self):
        machine = runtime.Machine(Path(self.temp.name), "test", runtime.Runner())
        clock = [0]
        limits = []

        def call(*_argv, **kwargs):
            if _argv[0] == "list":
                return SimpleNamespace(stdout=json.dumps({"list": [{"name": "test", "state": "Running"}]}))
            limits.append(kwargs["timeout"])
            clock[0] += min(3, kwargs["timeout"])
            raise runtime.CommandFailure(["multipass", "exec"], 2, RECONNECT_ERROR)

        machine.m = call
        with mock.patch.object(runtime.time, "monotonic", side_effect=lambda: clock[0]), \
                mock.patch.object(runtime.time, "sleep", side_effect=lambda seconds: clock.__setitem__(0, clock[0] + seconds)), \
                self.assertRaises(runtime.CommandFailure):
            machine.cloud_wait(timeout=10)
        self.assertLessEqual(clock[0], 10)
        self.assertEqual(limits, [10, 2])

    def test_state_observation_command_uses_substep_remaining_time(self):
        machine = runtime.Machine(Path(self.temp.name), "test", runtime.Runner())
        clock = [0]
        limits = []

        def call(*argv, **kwargs):
            self.assertEqual(argv[0], "list")
            limits.append(kwargs["timeout"])
            clock[0] += kwargs["timeout"]
            return SimpleNamespace(stdout=json.dumps({"list": [{"name": "test", "state": "Running"}]}))

        machine.m = call
        with mock.patch.object(runtime.time, "monotonic", side_effect=lambda: clock[0]), \
                self.assertRaisesRegex(runtime.Failure, "did not reach Stopped"):
            machine.wait_management_state("Stopped", runtime.Deadline(10, "reboot"), 6)
        self.assertEqual(limits, [6])
        self.assertEqual(clock[0], 6)

    def test_restart_diagnostic_reports_only_target_instance_state(self):
        machine = runtime.Machine(Path(self.temp.name), "test", runtime.Runner())

        def call(*argv, **_kwargs):
            if argv[0] == "list":
                return SimpleNamespace(returncode=0, stdout=json.dumps({"list": [
                    {"name": "other", "state": "Running", "ipv4": ["10.0.0.2"]},
                    {"name": "test", "state": "Restarting", "ipv4": []}]}))
            return SimpleNamespace(returncode=0, stdout=json.dumps({"info": {
                "test": {"state": "Restarting", "ipv4": []}}}))

        machine.m = call
        output = io.StringIO()
        with redirect_stderr(output):
            machine.management_snapshot()
        self.assertIn("state=Restarting", output.getvalue())
        self.assertNotIn("10.0.0.2", output.getvalue())

    def test_cleanup_failure_does_not_replace_the_original_stage_error(self):
        machine = runtime.Machine(Path(self.temp.name), "test", runtime.Runner())
        machine.helper = "/tmp/dotfiles-multipass-test.py"
        machine.cleanup_helper = mock.Mock(side_effect=runtime.CommandFailure(
            ["multipass", "exec"], 124, "cleanup timed out"))
        output = io.StringIO()
        with redirect_stderr(output), self.assertRaisesRegex(runtime.Failure, "primary failure"):
            try:
                raise runtime.Failure("primary failure")
            finally:
                machine.cleanup_helper_on_exit()
        self.assertIn("CLEANUP FAIL", output.getvalue())

    def test_pending_reboot_requires_new_boot_id_and_no_reboot_flag(self):
        for boot_id, required, message in (("first", False, "boot ID did not change"),
                                           ("second", True, "still requires reboot")):
            with self.subTest(boot_id=boot_id, required=required):
                machine = runtime.Machine(Path(self.temp.name), "test", runtime.Runner())
                machine.path.mkdir(parents=True, exist_ok=True)
                machine.receipt["reboot_from"] = "first"
                machine.m = mock.Mock(side_effect=AssertionError("must not issue another reboot"))
                with self.assertRaisesRegex(runtime.Failure, message):
                    machine.reboot_if_required({"boot_id": boot_id, "reboot_required": required})
                machine.m.assert_not_called()
                self.assertEqual(machine.receipt["reboot_from"], "first")
                self.assertNotIn("reboot_to", machine.receipt)

    def test_cleanup_does_not_exec_when_management_state_cannot_be_read(self):
        machine = runtime.Machine(Path(self.temp.name), "test", runtime.Runner())
        machine.path.mkdir(parents=True)
        machine.helper = "/tmp/dotfiles-multipass-test.py"
        machine.host.instances = mock.Mock(side_effect=runtime.CommandFailure(
            ["multipass", "list"], 124, "state query timed out"))
        machine.m = mock.Mock(side_effect=AssertionError("exec could implicitly start the VM"))
        with self.assertRaisesRegex(runtime.Failure, "original error"):
            try:
                raise runtime.Failure("original error")
            finally:
                machine.cleanup_helper_on_exit()
        machine.m.assert_not_called()
        receipt = json.loads(machine.receipt_file.read_text())
        self.assertEqual(receipt["pending_helper_cleanup"]["path"], "/tmp/dotfiles-multipass-test.py")
        self.assertIsNone(machine.helper)

    def test_cleanup_nonzero_exit_is_retained_and_blocks_success(self):
        def run(argv, **kwargs):
            if kwargs["check"]:
                raise runtime.CommandFailure(argv, 1, "rm: permission denied")
            return SimpleNamespace(returncode=1, stdout="", stderr="rm: permission denied")

        machine = runtime.Machine(Path(self.temp.name), "test", mock.Mock(side_effect=run, log=None))
        machine.path.mkdir(parents=True)
        machine.host.cli = "/fixture/multipass"
        machine.host.instances = mock.Mock(return_value={"test": {"state": "Running"}})
        machine.helper = "/tmp/dotfiles-multipass-test.py"
        with self.assertRaisesRegex(runtime.Failure, "cleanup deferred.*permission denied"):
            machine.cleanup_helper()
        self.assertIn("pending_helper_cleanup", json.loads(machine.receipt_file.read_text()))

    def test_instance_lock_rejects_concurrent_run(self):
        path = Path(self.temp.name) / "lock"
        with runtime.lock(path):
            with self.assertRaisesRegex(runtime.Failure, "lock exists"):
                with runtime.lock(path):
                    pass


class HostReadiness(unittest.TestCase):
    """仅替换底层命令执行，保留 Runner 真实的错误转换逻辑。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.machine = host.Host(runtime.Runner(), runtime.RELEASES, Path(self.temp.name))
        self.calls = []
        self.responses = {}
        self.clock = 0
        patchers = [mock.patch.object(host.shutil, "which", return_value="/review/multipass"),
                    mock.patch.object(runtime.subprocess, "run", side_effect=self.command),
                    mock.patch.object(host.time, "monotonic", side_effect=lambda: self.clock),
                    mock.patch.object(host.time, "sleep", side_effect=self.sleep)]
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def sleep(self, seconds):
        self.clock += seconds

    def command(self, argv, **_kwargs):
        self.calls.append(argv)
        operation = tuple(argv[1:])
        defaults = {("--pkgs",): "", ("version", "--format", "json"):
                    '{"multipass":"1.16.4","multipassd":"1.16.4"}',
                    ("get", "local.driver"): "qemu", ("list", "--format", "json"): '{"list":[]}'}
        if self.responses.get(operation):
            code, output = self.responses[operation].pop(0)
        elif operation in defaults:
            code, output = 0, defaults[operation]
        else:
            raise AssertionError(f"unexpected command: {argv}")
        return subprocess.CompletedProcess(argv, code, output if code == 0 else "",
                                           output if code else "")

    def test_install_waits_through_version_and_driver_connection_failures(self):
        self.responses = {("version", "--format", "json"): [(1, "daemon is starting")],
                          ("version",): [(1, "daemon is starting")],
                          ("get", "local.driver"): [(1, "cannot connect to daemon")]}
        with mock.patch.object(self.machine, "install_pkg") as install:
            result = self.machine.ensure(None)
        install.assert_called_once()
        self.assertTrue(result["qualified"])
        self.assertGreater(self.clock, 0)
        self.assertEqual(self.calls[-1][1:], ["list", "--format", "json"])

    def test_legacy_version_fallback_handles_runner_exception(self):
        self.responses = {("version", "--format", "json"): [(2, "unknown option --format")],
                          ("version",): [(0, "multipass 1.15.0\nmultipassd 1.15.0\n")]}
        result = self.machine.probe()
        self.assertEqual(result["client"], "1.15.0")
        self.assertFalse(result["qualified"])

    def test_permanent_version_and_driver_errors_are_not_retried(self):
        for operation, output, message in (
            (("version", "--format", "json"),
             '{"multipass":"1.16.4","multipassd":"1.15.0"}', "mismatch"),
            (("get", "local.driver"), "hyperkit", "unsupported"),
        ):
            with self.subTest(operation=operation):
                self.responses = {operation: [(0, output)]}
                with self.assertRaisesRegex(ValueError, message):
                    self.machine.wait_service()
                self.assertEqual(self.clock, 0)

    def test_service_timeout_keeps_last_error_and_has_a_deadline(self):
        self.responses = {("version", "--format", "json"): [(1, "daemon unavailable")] * 50,
                          ("version",): [(1, "daemon unavailable")] * 50}
        with self.assertRaisesRegex(ValueError, "did not become ready.*daemon unavailable"):
            self.machine.wait_service()
        self.assertEqual(self.clock, 120)


class RunnerProgress(unittest.TestCase):
    def test_silent_command_reports_wait_and_keeps_output_in_log(self):
        with tempfile.TemporaryDirectory() as directory:
            runner = runtime.Runner()
            runner.log = Path(directory) / "command.log"
            waits = []
            terminal = io.StringIO()
            with redirect_stderr(terminal):
                result = runner([sys.executable, "-c", "import time; time.sleep(0.2); print('ready')"],
                                timeout=2, stream=True, show_output=False, heartbeat=0.05,
                                on_wait=lambda elapsed, _limit: waits.append(elapsed))
            self.assertEqual(result.returncode, 0)
            self.assertGreaterEqual(len(waits), 2)
            self.assertNotIn("ready", terminal.getvalue())
            self.assertIn("ready", runner.log.read_text())

    def test_stage_heartbeat_identifies_the_active_substep(self):
        with tempfile.TemporaryDirectory() as directory:
            machine = runtime.Machine(Path(directory), "test", runtime.Runner())
            machine.path.mkdir(parents=True)
            terminal = io.StringIO()
            with redirect_stderr(terminal), machine.attempt("create"), machine.stage("cloud-init"):
                with machine.step("status", "Wait for cloud-init", timeout=2):
                    machine.runner([sys.executable, "-c", "import time; time.sleep(0.2); print('done')"],
                                   timeout=2, heartbeat=0.05)
            self.assertIn("STEP WAIT [cloud-init/status]", terminal.getvalue())
            receipt = json.loads(machine.receipt_file.read_text())
            self.assertEqual(receipt["stages"]["cloud-init"]["steps"][0]["status"], "ok")


class Orchestration(unittest.TestCase):
    """用夹具模拟虚拟机操作，验证 apply 流程及状态写入。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        (self.home / ".ssh").mkdir()
        self.key = self.home / "key.pub"
        self.key.write_text(PUB + "\n")
        self.state = self.home / ".local/state/dotfiles-multipass/instances/ubuntu-dev"
        self.record = self.home / "creation.json"
        self.instances = {}
        self.events = []
        self.busy = False
        self.fail_launch = False
        self.generation = 0
        self.shell = "/bin/bash"
        self.boot_id = "fixture-boot"
        self.reboot_required = False
        self.next_boot_id = "second-boot"
        self.clear_reboot_required_on_start = True
        case = self

        def probe(machine):
            machine.cli = "/review/multipass"
            return {"qualified": True, "client": "1.16.4"}

        def ensure(machine, _observed):
            with machine.progress("service", "Verify the existing Multipass daemon and driver"):
                return probe(machine)

        def multipass(machine, *argv, **_kwargs):
            case.events.append(argv)
            if argv[0] == "launch":
                case.generation += 1
                case.instances[machine.name] = {"state": "Running"}
                if case.record.exists():
                    case.assertEqual(json.loads(case.record.read_text()),
                                     {"name": machine.name, "uuid": machine.declaration["uuid"]})
                if case.fail_launch:
                    raise runtime.CommandFailure(["multipass", "launch"], 124, "launch timed out")
            elif argv[0] == "list":
                return SimpleNamespace(stdout=json.dumps({"list": [
                    {"name": name, **entry} for name, entry in case.instances.items()]}), returncode=0)
            elif argv[0] == "stop":
                case.instances[machine.name]["state"] = "Stopped"
            elif argv[0] == "start":
                case.instances[machine.name]["state"] = "Running"
                case.boot_id = case.next_boot_id
                if case.clear_reboot_required_on_start:
                    case.reboot_required = False
            return SimpleNamespace(stdout="", returncode=0)

        def guest_command(machine, action, *argv, **_kwargs):
            case.events.append((action, *argv))
            if action == "bootstrap-idle" and case.busy:
                raise runtime.CommandFailure(["multipass", "exec"], 1, "guest bootstrap is still running")
            if action == "probe":
                output = {"marker": {"name": machine.name, "uuid": machine.declaration["uuid"]},
                          "os_id": "ubuntu", "os_version": "24.04", "arch": "aarch64",
                          "ubuntu_home": "/home/ubuntu", "ubuntu_shell": case.shell,
                          "machine_id": f"fixture-machine-{case.generation}",
                          "cloud_instance_id": f"fixture-cloud-{case.generation}",
                          "boot_id": case.boot_id, "reboot_required": case.reboot_required}
            elif action == "versions":
                output = {"tools": {}, "doctor": {"pass": 1, "warn": 0, "fail": 0, "skip": 0}}
            else:
                output = {}
                if action == "finalize":
                    case.shell = "/usr/bin/zsh"
            return SimpleNamespace(stdout=json.dumps(output), returncode=0)

        # 只替换宿主与虚拟机边界，保留实际阶段编排和收据写入供断言。
        patchers = [mock.patch.object(runtime, "host_preflight"),
                    mock.patch.object(runtime, "require_agent"),
                    mock.patch.object(runtime.shutil, "disk_usage", return_value=SimpleNamespace(free=10**13)),
                    mock.patch.object(host.Host, "probe", probe),
                    mock.patch.object(host.Host, "ensure", ensure),
                    mock.patch.object(host.Host, "verify_service"),
                    mock.patch.object(host.Host, "instances", side_effect=lambda: dict(self.instances)),
                    mock.patch.object(host.Host, "image", return_value={}),
                    mock.patch.object(host.Host, "info", return_value={"cpu_count": 4, "image_hash": "fixture"}),
                    mock.patch.object(host.Host, "resources", return_value={"cpus": "4", "memory": "8G", "disk": "40G"}),
                    mock.patch.object(runtime.Machine, "m", multipass),
                    mock.patch.object(runtime.Machine, "guest", guest_command),
                    mock.patch.object(runtime.Machine, "cloud_wait", side_effect=lambda **_kwargs: self.events.append(("cloud-wait",))),
                    mock.patch.object(runtime.Machine, "ssh_config", side_effect=lambda _info: self.events.append(("ssh",)))]
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def apply(self, **kwargs):
        runtime.create_or_provision(args(apply=True, dry_run=False, ssh_public_key=self.key, **kwargs),
                                    {}, self.home, runtime.Runner())

    def test_creation_record_precedes_launch_and_survives_launch_timeout(self):
        self.fail_launch = True
        with self.assertRaisesRegex(runtime.CommandFailure, "launch timed out") as failure:
            self.apply(creation_record=self.record)
        self.assertEqual(failure.exception.progress_path, "launch/create")
        declaration = json.loads((self.state / "declaration.json").read_text())
        self.assertEqual(json.loads(self.record.read_text()),
                         {"name": "ubuntu-dev", "uuid": declaration["uuid"]})
        self.assertEqual(self.record.stat().st_mode & 0o777, 0o600)
        self.assertIn("ubuntu-dev", self.instances)
        self.assertFalse((self.state / "lock").exists())
        self.assertNotIn(("cloud-wait",), self.events)

    def test_prelaunch_failure_still_registers_its_own_state_for_cleanup(self):
        with mock.patch.object(host.Host, "image", side_effect=ValueError("image unavailable")), \
                self.assertRaisesRegex(ValueError, "image unavailable"):
            self.apply(creation_record=self.record)
        declaration = json.loads((self.state / "declaration.json").read_text())
        self.assertEqual(json.loads(self.record.read_text()),
                         {"name": "ubuntu-dev", "uuid": declaration["uuid"]})
        self.assertEqual(self.instances, {})
        self.assertFalse(any(event[0] == "launch" for event in self.events))

    def test_successful_creation_and_retry_keep_initialization_and_identity(self):
        self.apply(creation_record=self.record)
        first = json.loads((self.state / "receipt.json").read_text())
        self.assertEqual(first["last_successful_ref"], REF)
        self.assertFalse(any(event[0] in ("stop", "start", "restart") for event in self.events))
        self.assertEqual(self.events.count(("cloud-wait",)), 1)
        self.apply()
        self.assertEqual(sum(event[0] == "launch" for event in self.events), 1)
        self.assertEqual(self.events.count(("cloud-wait",)), 1)
        self.assertEqual(json.loads((self.state / "receipt.json").read_text())["machine_id"], first["machine_id"])

    def require_reboot(self):
        self.reboot_required = True
        self.boot_id = "first-boot"

    def receipt(self):
        return json.loads((self.state / "receipt.json").read_text())

    def interrupt_at_reboot_phase(self, phase):
        self.require_reboot()
        original = runtime.Machine.reboot_phase

        def interrupt(machine, new_phase):
            original(machine, new_phase)
            if new_phase == phase:
                raise runtime.Failure(f"interrupted after {phase}")

        with mock.patch.object(runtime.Machine, "reboot_phase", interrupt), \
                self.assertRaisesRegex(runtime.Failure, f"interrupted after {phase}"):
            self.apply(creation_record=self.record)
        return self.receipt()

    def test_required_reboot_stops_starts_and_retransfers_helper(self):
        self.require_reboot()
        self.apply(creation_record=self.record)
        receipt = self.receipt()
        self.assertEqual(receipt["reboot_from"], "first-boot")
        self.assertEqual(receipt["reboot_to"], "second-boot")
        self.assertEqual(receipt["reboot_operation"]["method"], "stop-start")
        self.assertEqual(receipt["reboot_operation"]["phase"], "verified")
        self.assertEqual([event[0] for event in self.events].count("stop"), 1)
        self.assertEqual([event[0] for event in self.events].count("start"), 1)
        self.assertFalse(any(event[0] == "restart" for event in self.events))
        self.assertEqual(self.events.count(("cloud-wait",)), 2)
        self.assertGreaterEqual(sum(event[0] == "transfer" for event in self.events), 2)
        self.assertEqual(self.instances["ubuntu-dev"]["state"], "Running")

    def test_stop_failure_without_stopped_state_never_starts(self):
        self.require_reboot()
        original = runtime.Machine.m

        def command(machine, *argv, **kwargs):
            if argv[0] == "stop":
                self.events.append(argv)
                raise runtime.CommandFailure(["multipass", *argv], 5, "stop failed")
            return original(machine, *argv, **kwargs)

        real_wait = runtime.Machine.wait_management_state
        def observe(machine, expected, budget, seconds):
            if expected == "Stopped":
                self.assertEqual(machine.read_management_state(budget), "Running")
                raise runtime.Failure("did not reach Stopped; last observed state=Running")
            return real_wait(machine, expected, budget, seconds)

        with mock.patch.object(runtime.Machine, "m", command), \
                mock.patch.object(runtime.Machine, "wait_management_state", observe), \
                self.assertRaisesRegex(runtime.Failure, "did not reach Stopped"):
            self.apply(creation_record=self.record)
        self.assertFalse(any(event[0] == "start" for event in self.events))
        self.assertEqual(self.receipt()["reboot_operation"]["phase"], "stop_requested")
        self.assertEqual(self.receipt()["attempts"][-1]["failed_at"], "cloud-init/stopped")
        self.events.clear()
        self.apply()
        self.assertEqual(self.receipt()["reboot_operation"]["phase"], "verified")
        self.assertEqual(sum(event[0] == "stop" for event in self.events), 1)
        self.assertEqual(sum(event[0] == "start" for event in self.events), 1)

    def test_nonzero_stop_and_start_are_reconciled_with_observed_state(self):
        self.require_reboot()
        original = runtime.Machine.m
        def command(machine, *argv, **kwargs):
            result = original(machine, *argv, **kwargs)
            if argv[0] in ("stop", "start"):
                raise runtime.CommandFailure(["multipass", *argv], 5, f"{argv[0]} timed out")
            return result
        with mock.patch.object(runtime.Machine, "m", command):
            self.apply(creation_record=self.record)
        self.assertEqual(self.receipt()["reboot_operation"]["phase"], "verified")
        returns = [event for event in self.receipt()["reboot_operation"]["events"]
                   if event["kind"].endswith("-return")]
        self.assertEqual([event["exit_code"] for event in returns], [5, 5])

    def test_start_timeout_in_starting_state_resumes_without_second_start(self):
        self.require_reboot()
        original = runtime.Machine.m
        real_wait = runtime.Machine.wait_management_state

        def command(machine, *argv, **kwargs):
            if argv[0] == "start":
                self.events.append(argv)
                self.instances[machine.name]["state"] = "Starting"
                self.boot_id = "second-boot"
                self.reboot_required = False
                raise runtime.CommandFailure(["multipass", *argv], 124, "start timed out")
            return original(machine, *argv, **kwargs)

        def observe(machine, expected, budget, seconds):
            if expected == "Running" or isinstance(expected, tuple):
                self.assertEqual(machine.read_management_state(budget), "Starting")
                raise runtime.Failure("did not reach Running; last observed state=Starting")
            return real_wait(machine, expected, budget, seconds)

        with mock.patch.object(runtime.Machine, "m", command), \
                mock.patch.object(runtime.Machine, "wait_management_state", observe), \
                self.assertRaisesRegex(runtime.Failure, "last observed state=Starting"):
            self.apply(creation_record=self.record)
        self.assertEqual(self.receipt()["reboot_operation"]["phase"], "start_requested")
        self.assertEqual(sum(event[0] == "start" for event in self.events), 1)
        self.events.clear()
        with mock.patch.object(runtime.Machine, "wait_management_state", observe), \
                self.assertRaisesRegex(runtime.Failure, "last observed state=Starting"):
            self.apply()
        self.assertFalse(any(event[0] in ("exec", "start", "stop") for event in self.events))
        self.events.clear()
        self.instances["ubuntu-dev"]["state"] = "Running"
        self.apply()
        self.assertFalse(any(event[0] in ("start", "stop", "restart") for event in self.events))
        self.assertEqual(self.receipt()["reboot_to"], "second-boot")

    def test_stopped_without_matching_operation_is_not_auto_started(self):
        self.apply()
        self.instances["ubuntu-dev"]["state"] = "Stopped"
        before = (self.state / "receipt.json").read_bytes()
        self.events.clear()
        with self.assertRaisesRegex(runtime.Failure, "use multipass start"):
            self.apply()
        self.assertEqual((self.state / "receipt.json").read_bytes(), before)
        self.assertEqual(self.events, [])

    def test_pending_operation_with_changed_declaration_is_rejected_before_start(self):
        self.interrupt_at_reboot_phase("stopped")
        receipt = self.receipt()
        receipt["reboot_operation"]["declaration"]["uuid"] = "replacement"
        runtime.save_json(self.state / "receipt.json", receipt)
        self.events.clear()
        with self.assertRaisesRegex(runtime.Failure, "does not match the host declaration"):
            self.apply()
        self.assertFalse(any(event[0] in ("start", "stop", "exec") for event in self.events))

    def test_pending_operation_rejects_changed_saved_host_resources(self):
        self.interrupt_at_reboot_phase("stopped")
        declaration = runtime.load_json(self.state / "declaration.json")
        declaration["disk"] = "50G"
        runtime.save_json(self.state / "declaration.json", declaration)
        self.events.clear()
        with self.assertRaisesRegex(runtime.Failure, "does not match the host declaration"):
            self.apply()
        self.assertFalse(any(event[0] in ("start", "stop", "exec") for event in self.events))

    def test_legacy_reboot_from_is_verified_without_new_stop_start_operation(self):
        self.apply()
        receipt = self.receipt()
        receipt["reboot_from"] = "first-boot"
        receipt["stages"]["cloud-init"]["status"] = "failed"
        runtime.save_json(self.state / "receipt.json", receipt)
        self.boot_id = "second-boot"
        self.events.clear()
        self.apply()
        updated = self.receipt()
        self.assertEqual(updated["reboot_to"], "second-boot")
        self.assertNotIn("reboot_operation", updated)
        self.assertFalse(any(event[0] in ("stop", "start", "restart") for event in self.events))

    def test_unchanged_boot_id_fails_after_one_cycle(self):
        self.require_reboot()
        self.next_boot_id = "first-boot"
        with self.assertRaisesRegex(runtime.Failure, "boot ID did not change"):
            self.apply(creation_record=self.record)
        self.assertNotIn("reboot_to", self.receipt())
        self.assertEqual(sum(event[0] == "stop" for event in self.events), 1)
        self.assertEqual(sum(event[0] == "start" for event in self.events), 1)
        self.events.clear()
        with self.assertRaisesRegex(runtime.Failure, "boot ID did not change"):
            self.apply()
        self.assertFalse(any(event[0] in ("stop", "start") for event in self.events))

    def test_remaining_reboot_flag_fails_after_one_cycle(self):
        self.require_reboot()
        self.clear_reboot_required_on_start = False
        with self.assertRaisesRegex(runtime.Failure, "still requires reboot"):
            self.apply(creation_record=self.record)
        self.assertNotIn("reboot_to", self.receipt())
        self.assertEqual(sum(event[0] == "stop" for event in self.events), 1)
        self.assertEqual(sum(event[0] == "start" for event in self.events), 1)
        self.events.clear()
        with self.assertRaisesRegex(runtime.Failure, "still requires reboot"):
            self.apply()
        self.assertFalse(any(event[0] in ("stop", "start") for event in self.events))

    def test_cloud_init_error_after_start_is_preserved_without_second_cycle(self):
        self.require_reboot()
        def status(**kwargs):
            self.events.append(("cloud-wait",))
            if kwargs.get("budget") is not None:
                raise runtime.Failure("cloud-init reported errors")
        with mock.patch.object(runtime.Machine, "cloud_wait", side_effect=status), \
                self.assertRaisesRegex(runtime.Failure, "cloud-init reported errors"):
            self.apply(creation_record=self.record)
        self.assertEqual(self.receipt()["reboot_operation"]["phase"], "running")
        self.events.clear()
        with mock.patch.object(runtime.Machine, "cloud_wait", side_effect=status), \
                self.assertRaisesRegex(runtime.Failure, "cloud-init reported errors"):
            self.apply()
        self.assertFalse(any(event[0] in ("stop", "start") for event in self.events))

    def test_reboot_budget_is_shared_across_commands_and_reconnect(self):
        self.require_reboot()
        clock = [0]
        limits = []
        original = runtime.Machine.m

        def command(machine, *argv, **kwargs):
            if argv[0] in ("stop", "start"):
                limits.append((argv[0], kwargs["timeout"]))
                clock[0] += 4
            return original(machine, *argv, **kwargs)

        def wait(machine, **kwargs):
            self.events.append(("cloud-wait",))
            if kwargs.get("budget") is not None:
                clock[0] += 2

        with mock.patch.dict(runtime.DEFAULTS["timeouts"], {"reboot": 10}), \
                mock.patch.object(runtime.time, "monotonic", side_effect=lambda: clock[0]), \
                mock.patch.object(runtime.Machine, "m", command), \
                mock.patch.object(runtime.Machine, "cloud_wait", wait), \
                self.assertRaisesRegex(runtime.Failure, "time budget exhausted"):
            self.apply(creation_record=self.record)
        self.assertEqual(clock[0], 10)
        self.assertEqual(limits, [("stop", 10), ("start", 6)])
        self.assertEqual(self.receipt()["reboot_operation"]["phase"], "running")

    def test_resume_after_stopped_only_starts_once_and_preserves_history(self):
        first = self.interrupt_at_reboot_phase("stopped")
        self.assertFalse(any(event[0] == "exec" and "rm" in event for event in self.events))
        declaration = (self.state / "declaration.json").read_bytes()
        creation = self.record.read_bytes()
        old_log = Path(first["stages"]["cloud-init"]["log"])
        old_contents = old_log.read_bytes()
        self.assertEqual(first["reboot_from"], "first-boot")
        self.assertIn("pending_helper_cleanup", first)
        self.assertEqual(self.instances["ubuntu-dev"]["state"], "Stopped")
        self.events.clear()
        self.apply()
        second = self.receipt()
        self.assertEqual([row["status"] for row in second["attempts"]], ["failed", "ok"])
        self.assertEqual(second["attempts"][0], first["attempts"][0])
        self.assertEqual(second["reboot_from"], "first-boot")
        self.assertEqual(second["reboot_to"], "second-boot")
        self.assertEqual(second["machine_id"], first["machine_id"])
        self.assertEqual(second["cloud_instance_id"], first["cloud_instance_id"])
        self.assertNotIn("pending_helper_cleanup", second)
        self.assertEqual(second["last_successful_ref"], REF)
        self.assertEqual((self.state / "declaration.json").read_bytes(), declaration)
        self.assertEqual(self.record.read_bytes(), creation)
        self.assertEqual(old_log.read_bytes(), old_contents)
        self.assertEqual(sum(event[0] == "start" for event in self.events), 1)
        self.assertFalse(any(event[0] in ("launch", "restart", "stop") for event in self.events))
        self.assertTrue(any(event[0] == "exec" and "rm" in event for event in self.events))

    def test_resume_after_running_does_not_repeat_lifecycle(self):
        first = self.interrupt_at_reboot_phase("running")
        self.assertEqual(first["reboot_operation"]["phase"], "running")
        self.events.clear()
        self.apply()
        self.assertEqual(self.receipt()["reboot_to"], "second-boot")
        self.assertFalse(any(event[0] in ("stop", "start", "restart", "launch") for event in self.events))

    def test_resume_stopping_operation_probes_identity_before_lifecycle(self):
        first = self.interrupt_at_reboot_phase("stop_requested")
        self.generation += 1
        for phase in ("planned", "stop_requested"):
            with self.subTest(phase=phase):
                first["reboot_operation"]["phase"] = phase
                runtime.save_json(self.state / "receipt.json", first)
                self.events.clear()
                with self.assertRaisesRegex(runtime.Failure, "machine-id changed"):
                    self.apply()
                self.assertFalse(any(event[0] in ("stop", "start", "ssh", "repository", "bootstrap")
                                     for event in self.events))
                self.assertEqual(self.receipt()["machine_id"], first["machine_id"])
                self.assertNotIn("reboot_to", self.receipt())

    def test_resume_stopping_operation_recognizes_manual_reboot(self):
        first = self.interrupt_at_reboot_phase("stop_requested")
        self.boot_id = "manually-recovered-boot"
        self.reboot_required = False
        for phase in ("planned", "stop_requested"):
            with self.subTest(phase=phase):
                first["reboot_operation"]["phase"] = phase
                runtime.save_json(self.state / "receipt.json", first)
                self.events.clear()
                self.apply()
                self.assertEqual(self.receipt()["reboot_to"], "manually-recovered-boot")
                self.assertEqual(self.receipt()["reboot_operation"]["phase"], "verified")
                self.assertFalse(any(event[0] in ("stop", "start", "restart") for event in self.events))

    def test_manual_reboot_with_pending_flag_is_not_repeated(self):
        self.interrupt_at_reboot_phase("stop_requested")
        self.boot_id = "manually-recovered-boot"
        self.events.clear()
        with self.assertRaisesRegex(runtime.Failure, "still requires reboot"):
            self.apply()
        self.assertFalse(any(event[0] in ("stop", "start", "restart") for event in self.events))
        self.assertNotIn("reboot_to", self.receipt())

    def test_resume_with_unreadable_boot_id_never_stops(self):
        self.interrupt_at_reboot_phase("stop_requested")
        self.boot_id = ""
        self.events.clear()
        with self.assertRaisesRegex(runtime.Failure, "boot ID is missing"):
            self.apply()
        self.assertFalse(any(event[0] in ("stop", "start", "restart") for event in self.events))

    def transient_reboot_failure(self, method, message=RECONNECT_ERROR):
        self.require_reboot()
        original = getattr(runtime.Machine, method)
        attempts = []

        def transient(machine, *argv, **kwargs):
            if machine.receipt.get("reboot_operation", {}).get("phase") == "running":
                attempts.append(method)
                if len(attempts) == 1:
                    raise runtime.CommandFailure(["multipass", "exec"], 2, message)
            return original(machine, *argv, **kwargs)

        with mock.patch.object(runtime.Machine, method, transient), \
                mock.patch.object(runtime.time, "sleep") as sleep:
            self.apply(creation_record=self.record)
        self.assertEqual(attempts, [method, method])
        sleep.assert_called_once()
        self.assertEqual(self.receipt()["reboot_to"], "second-boot")
        self.assertEqual(sum(event[0] == "stop" for event in self.events), 1)
        self.assertEqual(sum(event[0] == "start" for event in self.events), 1)

    def test_reboot_retries_transient_helper_transfer_failure(self):
        self.transient_reboot_failure("transfer_helper")

    def test_reboot_retries_transient_identity_probe_failure(self):
        self.transient_reboot_failure("verify_guest")

    def test_reboot_probe_transport_retries_share_the_total_budget(self):
        self.require_reboot()
        original = runtime.Machine.guest
        clock = [0]
        limits = []

        def command(machine, action, *argv, **kwargs):
            if action == "probe" and self.boot_id == "second-boot":
                limits.append(kwargs["timeout"])
                clock[0] += min(3, kwargs["timeout"])
                raise runtime.CommandFailure(["multipass", "exec"], 2, RECONNECT_ERROR)
            return original(machine, action, *argv, **kwargs)

        with mock.patch.dict(runtime.DEFAULTS["timeouts"], {"reboot": 11}), \
                mock.patch.object(runtime.time, "monotonic", side_effect=lambda: clock[0]), \
                mock.patch.object(runtime.time, "sleep", side_effect=lambda seconds: clock.__setitem__(0, clock[0] + seconds)), \
                mock.patch.object(runtime.Machine, "guest", command), \
                self.assertRaises(runtime.CommandFailure):
            self.apply(creation_record=self.record)
        self.assertEqual(limits, [11, 3])
        self.assertEqual(clock[0], 11)
        self.assertEqual(self.receipt()["reboot_operation"]["phase"], "running")
        self.assertNotIn("reboot_to", self.receipt())

    def test_reboot_probe_retry_refuses_non_running_management_state(self):
        self.require_reboot()
        original = runtime.Machine.guest
        attempts = []

        def command(machine, action, *argv, **kwargs):
            if action == "probe" and self.boot_id == "second-boot":
                attempts.append(action)
                self.instances[machine.name]["state"] = "Starting"
                raise runtime.CommandFailure(["multipass", "exec"], 2, RECONNECT_ERROR)
            return original(machine, action, *argv, **kwargs)

        with mock.patch.object(runtime.Machine, "guest", command), \
                mock.patch.object(runtime.time, "sleep") as sleep, \
                self.assertRaisesRegex(runtime.Failure, "is Starting"):
            self.apply(creation_record=self.record)
        sleep.assert_not_called()
        self.assertEqual(attempts, ["probe"])
        self.assertIn("pending_helper_cleanup", self.receipt())
        self.assertFalse(any(event[0] == "exec" and "rm" in event for event in self.events))

    def test_reboot_identity_command_errors_are_not_retried(self):
        with self.assertRaisesRegex(runtime.CommandFailure, "Permission denied"):
            self.transient_reboot_failure("verify_guest", "probe failed: Permission denied")
        self.assertNotIn("reboot_to", self.receipt())

    def test_resume_rejects_replacement_identity_before_ssh_or_repository(self):
        first = self.interrupt_at_reboot_phase("running")
        self.generation += 1
        self.events.clear()
        with self.assertRaisesRegex(runtime.Failure, "machine-id changed"):
            self.apply()
        self.assertFalse(any(event[0] in ("ssh", "repository", "stop", "start", "bootstrap")
                             for event in self.events))
        receipt = self.receipt()
        self.assertEqual(receipt["machine_id"], first["machine_id"])
        self.assertEqual(receipt["reboot_from"], "first-boot")
        self.assertNotIn("reboot_to", receipt)

    def test_provision_cannot_skip_incomplete_first_boot_or_change_saved_ref(self):
        self.interrupt_at_reboot_phase("running")
        before = {str(path): path.read_bytes() for path in self.state.rglob("*") if path.is_file()}
        self.events.clear()
        with self.assertRaisesRegex(runtime.Failure, "first-boot checks are incomplete.*create"):
            self.apply(action="provision", ref="b" * 40)
        self.assertEqual(self.events, [])
        self.assertEqual({str(path): path.read_bytes() for path in self.state.rglob("*") if path.is_file()}, before)

    def test_final_cleanup_failure_does_not_publish_successful_ref(self):
        original = runtime.Machine.m

        def command(machine, *argv, **kwargs):
            if machine.current_stage == "verified" and argv[0] == "exec" and "rm" in argv:
                raise runtime.CommandFailure(["multipass", *argv], 1, "rm failed")
            return original(machine, *argv, **kwargs)

        with mock.patch.object(runtime.Machine, "m", command), self.assertRaises(runtime.Failure) as failure:
            self.apply()
        receipt = json.loads((self.state / "receipt.json").read_text())
        self.assertNotIn("last_successful_ref", receipt)
        self.assertEqual(receipt["stages"]["verified"]["status"], "failed")
        self.assertEqual(receipt["attempts"][-1]["failed_at"], "verified/helper-cleanup")
        self.assertIn("pending_helper_cleanup", receipt)
        self.assertIn("cleanup deferred", str(failure.exception))
        self.apply()
        receipt = json.loads((self.state / "receipt.json").read_text())
        self.assertEqual(receipt["last_successful_ref"], REF)
        self.assertNotIn("pending_helper_cleanup", receipt)

    def test_restarting_state_without_valid_operation_is_rejected_before_guest_commands(self):
        self.apply()
        self.instances["ubuntu-dev"]["state"] = "Restarting"
        before = {str(path): path.read_bytes() for path in self.state.rglob("*") if path.is_file()}
        self.events.clear()
        for action in ("create", "provision", "check", "ssh"):
            with self.subTest(action=action), self.assertRaises(runtime.Failure) as failure:
                if action in ("create", "provision"):
                    self.apply(action=action)
                else:
                    getattr(runtime, action)(args(action=action, runtime=False), {}, self.home, runtime.Runner())
            self.assertIn("Restarting", str(failure.exception))
            self.assertNotIn("use multipass start", str(failure.exception))
            self.assertNotIn("is stopped", str(failure.exception))
        self.assertEqual(self.events, [])
        self.assertEqual({str(path): path.read_bytes() for path in self.state.rglob("*") if path.is_file()}, before)

    def test_deleted_instance_is_rejected_without_launch_or_state_changes(self):
        self.apply()
        self.instances.clear()
        before = {str(path): path.read_bytes() for path in self.state.rglob("*") if path.is_file()}
        self.events.clear()
        with self.assertRaisesRegex(runtime.Failure, "previously created instance.*different --name"):
            self.apply()
        self.assertEqual(self.events, [])
        self.assertEqual({str(path): path.read_bytes() for path in self.state.rglob("*") if path.is_file()}, before)

    def test_missing_instance_is_reported_before_changed_create_ref(self):
        self.apply()
        self.instances.clear()
        with self.assertRaisesRegex(runtime.Failure, "Run destroy to retire"):
            self.apply(ref="b" * 40)

    def destroy(self, apply=False):
        runtime.destroy(args(action="destroy", name="ubuntu-dev", apply=apply, dry_run=not apply),
                        {}, self.home, runtime.Runner())

    def test_destroy_preview_and_manually_purged_instance_can_reuse_name(self):
        self.apply()
        old = json.loads((self.state / "declaration.json").read_text())
        self.instances.clear()
        before = {str(path): path.read_bytes() for path in self.state.rglob("*") if path.is_file()}
        self.destroy()
        self.assertEqual({str(path): path.read_bytes() for path in self.state.rglob("*") if path.is_file()},
                         before)
        self.assertFalse((self.state.parent.parent / "retired").exists())
        self.destroy(apply=True)
        archive = self.state.parent.parent / "retired" / f"ubuntu-dev-{old['uuid']}"
        self.assertFalse(self.state.exists())
        self.assertEqual(json.loads((archive / "declaration.json").read_text()), old)
        self.assertEqual(json.loads((archive / "retirement.json").read_text())["vm_state_before_apply"],
                         "absent")
        self.apply(ref="b" * 40)
        new = json.loads((self.state / "declaration.json").read_text())
        self.assertNotEqual(new["uuid"], old["uuid"])
        self.assertEqual(new["target_ref"], "b" * 40)

    def test_destroy_live_instance_checks_identity_and_only_purges_its_name(self):
        self.apply()
        old = json.loads((self.state / "declaration.json").read_text())
        self.instances["ubuntu-dev"]["state"] = "Stopped"
        self.instances["another-vm"] = {"state": "Running"}

        def command(machine, *argv, **_kwargs):
            self.events.append(argv)
            if argv[0] == "exec" and argv[-1] == "/var/lib/dotfiles-multipass/instance.json":
                return SimpleNamespace(stdout=json.dumps({"name": machine.name, "uuid": old["uuid"]}))
            if argv[0] == "exec" and argv[-1] == "/etc/machine-id":
                return SimpleNamespace(stdout="fixture-machine-1\n")
            if argv[0] == "exec" and argv[-1] == "instance_id":
                return SimpleNamespace(stdout="fixture-cloud-1\n")
            if argv[0] == "start":
                self.instances[machine.name]["state"] = "Running"
            if argv[:2] == ("delete", "--purge"):
                self.instances.pop(machine.name)
            return SimpleNamespace(stdout="")

        with mock.patch.object(runtime.Machine, "m", command):
            self.destroy(apply=True)
        self.assertIn(("start", "ubuntu-dev"), self.events)
        self.assertIn(("delete", "--purge", "ubuntu-dev"), self.events)
        self.assertEqual(self.instances, {"another-vm": {"state": "Running"}})
        self.assertFalse(self.state.exists())

    def test_destroy_identity_mismatch_preserves_vm_and_state(self):
        self.apply()
        old = json.loads((self.state / "declaration.json").read_text())
        before = {str(path): path.read_bytes() for path in self.state.rglob("*") if path.is_file()}

        def wrong_marker(_machine, *argv, **_kwargs):
            self.events.append(argv)
            return SimpleNamespace(stdout=json.dumps({"name": "ubuntu-dev", "uuid": "replacement"}))

        self.events.clear()
        with mock.patch.object(runtime.Machine, "m", wrong_marker), \
                self.assertRaisesRegex(runtime.Failure, "marker does not match"):
            self.destroy(apply=True)
        self.assertNotIn(("delete", "--purge", "ubuntu-dev"), self.events)
        self.assertIn("ubuntu-dev", self.instances)
        self.assertEqual(json.loads((self.state / "declaration.json").read_text()), old)
        self.assertEqual({str(path): path.read_bytes() for path in self.state.rglob("*") if path.is_file()},
                         before)

    def test_destroy_refuses_unrecorded_ssh_file_before_vm_delete(self):
        self.apply()
        host_file = self.home / ".ssh/dotfiles-multipass/hosts/ubuntu-dev.conf"
        host_file.parent.mkdir(parents=True)
        identity = json.loads((self.state / "declaration.json").read_text())["uuid"]
        host_file.write_text(f"# dotfiles-multipass instance {identity}\nHost ubuntu-dev\n")
        self.events.clear()
        with self.assertRaisesRegex(ValueError, "no matching receipt hash"):
            self.destroy(apply=True)
        self.assertIn("ubuntu-dev", self.instances)
        self.assertNotIn(("delete", "--purge", "ubuntu-dev"), self.events)
        self.assertTrue(host_file.is_file())

    def test_destroy_retries_host_cleanup_after_the_vm_was_purged(self):
        self.apply()
        self.instances.clear()
        with mock.patch.object(ssh_config.SSHConfig, "remove", side_effect=ValueError("disk error")), \
                self.assertRaisesRegex(ValueError, "disk error"):
            self.destroy(apply=True)
        self.assertTrue((self.state / "declaration.json").is_file())
        self.assertFalse((self.state / "lock").exists())
        self.destroy(apply=True)
        self.assertFalse(self.state.exists())

    def test_destroy_rejects_recoverable_deleted_instance(self):
        self.apply()
        self.instances["ubuntu-dev"]["state"] = "Deleted"
        with self.assertRaisesRegex(runtime.Failure, "multipass recover"):
            self.destroy(apply=True)
        self.assertTrue((self.state / "declaration.json").is_file())

    def test_disappearance_after_preflight_cannot_launch_using_old_receipt(self):
        self.apply()
        self.events.clear()
        with mock.patch.object(host.Host, "instances", side_effect=[dict(self.instances), {}]), \
                self.assertRaisesRegex(runtime.Failure, "previously created instance"):
            self.apply()
        self.assertFalse(any(event[0] == "launch" for event in self.events))

    def test_failed_launch_without_guest_can_retry(self):
        self.fail_launch = True
        with self.assertRaises(runtime.CommandFailure):
            self.apply()
        first = json.loads((self.state / "receipt.json").read_text())
        failed_log = Path(first["stages"]["launch"]["log"])
        self.assertTrue(failed_log.is_file())
        self.assertEqual(first["attempts"][-1]["status"], "failed")
        self.instances.clear()
        self.fail_launch = False
        self.apply()
        second = json.loads((self.state / "receipt.json").read_text())
        self.assertEqual(second["last_successful_ref"], REF)
        self.assertEqual([attempt["status"] for attempt in second["attempts"]], ["failed", "ok"])
        self.assertNotEqual(failed_log, Path(second["stages"]["launch"]["log"]))
        self.assertTrue(failed_log.is_file())
        self.assertEqual(self.events.count(("cloud-wait",)), 1)

    def test_progress_distinguishes_stage_step_and_guest_bootstrap_output(self):
        output = io.StringIO()
        with redirect_stderr(output):
            self.apply()
        lines = output.getvalue()
        self.assertIn("  STEP RUN  [host/service] Verify the existing Multipass daemon", lines)
        self.assertIn("STAGE RUN  [cloud-init] Verify first boot", lines)
        self.assertIn("  STEP RUN  [cloud-init/status] Wait for cloud-init", lines)
        self.assertIn("  STEP SKIP [cloud-init/reboot] guest does not require a reboot", lines)
        self.assertIn("    CHILD BEGIN [bootstrap] Guest bootstrap output follows unchanged", lines)
        self.assertIn("    CHILD END   [bootstrap] See guest bootstrap log", lines)
        self.assertIn("STAGE OK   [verified]", lines)

    def test_check_keeps_json_stdout_separate_from_progress(self):
        with redirect_stderr(io.StringIO()):
            self.apply()
        declaration = json.loads((self.state / "declaration.json").read_text())

        def command(_machine, *argv, **_kwargs):
            if "instance.json" in " ".join(argv):
                return SimpleNamespace(stdout=json.dumps({"uuid": declaration["uuid"]}))
            return SimpleNamespace(stdout=REF + "\n")

        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch.object(runtime.Machine, "m", command), redirect_stdout(stdout), \
                redirect_stderr(stderr):
            runtime.check(SimpleNamespace(action="check", name="ubuntu-dev", runtime=False),
                          {}, self.home, runtime.Runner())
        self.assertEqual(json.loads(stdout.getvalue())["current_ref"], REF)
        self.assertEqual(stderr.getvalue(), "")

    def test_running_guest_bootstrap_stops_before_ssh_or_repository(self):
        self.apply()
        self.busy = True
        self.events.clear()
        with self.assertRaisesRegex(runtime.CommandFailure, "still running"):
            self.apply(action="provision", ref="b" * 40)
        self.assertIn(("bootstrap-idle",), self.events)
        self.assertFalse(any(event[0] in ("ssh", "repository", "bootstrap", "packages-ready") for event in self.events))
        receipt = json.loads((self.state / "receipt.json").read_text())
        self.assertEqual(receipt["current_ref"], REF)
        self.assertEqual(receipt["last_successful_ref"], REF)
        self.assertEqual(receipt["stages"]["guest"]["status"], "failed")

    def test_existing_instance_cannot_be_registered_for_cleanup(self):
        self.apply()
        before = (self.state / "declaration.json").read_bytes()
        with self.assertRaisesRegex(runtime.Failure, "fresh instance name"):
            self.apply(creation_record=self.record)
        self.assertFalse(self.record.exists())
        self.assertEqual((self.state / "declaration.json").read_bytes(), before)

    def test_existing_creation_record_is_preserved_before_any_state_writes(self):
        self.record.write_text("keep previous run evidence\n")
        with self.assertRaisesRegex(runtime.Failure, "must be a new absolute file"):
            self.apply(creation_record=self.record)
        self.assertEqual(self.record.read_text(), "keep previous run evidence\n")
        self.assertFalse(self.state.exists())
        self.assertEqual(self.events, [])

    def test_racing_instance_state_cannot_be_registered_or_overwritten(self):
        def other_creator():
            self.state.mkdir(parents=True)
            runtime.save_json(self.state / "declaration.json", {"uuid": "another-run"})
        with mock.patch.object(host.Host, "verify_service", side_effect=other_creator), \
                self.assertRaisesRegex(runtime.Failure, "state appeared during preflight"):
            self.apply(creation_record=self.record)
        self.assertEqual(json.loads((self.state / "declaration.json").read_text()), {"uuid": "another-run"})
        self.assertFalse(self.record.exists())
        self.assertEqual(self.events, [])


class SSHConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        (self.home / ".ssh").mkdir()
        (self.home / ".ssh/config").write_text("Host github.com\n    User git\n")
        self.pub = self.home / "key.pub"
        self.pub.write_text(PUB + "\n")
        self.setting = ssh_config.SSHConfig(self.home, "ubuntu-dev", "test-uuid", self.pub,
                                            REPO / "scripts/multipass/ssh_proxy.py",
                                            "/usr/local/bin/multipass")

    def test_include_idempotence_and_existing_host_semantics(self):
        first = self.setting.publish(PUB)
        content = (self.home / ".ssh/config").read_text()
        second = self.setting.publish(PUB, first)
        self.assertEqual(first, second)
        self.assertEqual((self.home / ".ssh/config").read_text(), content)
        self.assertEqual(content.count(ssh_config.SENTINEL), 1)
        old = subprocess.run(["ssh", "-G", "-F", str(self.home / ".ssh/config"), "github.com"],
                             capture_output=True, text=True, check=True)
        self.assertIn("user git\n", old.stdout)
        self.assertIn("stricthostkeychecking true\n",
                      subprocess.run(["ssh", "-G", "-F", str(self.home / ".ssh/config"),
                                      "ubuntu-dev"], capture_output=True, text=True, check=True).stdout)

    def test_collision_and_host_key_change_preserve_existing_files(self):
        (self.home / ".ssh/config").write_text("Host ubuntu-*\n    User alice\n")
        with self.assertRaisesRegex(ValueError, "conflicts"):
            self.setting.preflight()
        (self.home / ".ssh/config").write_text("Host github.com\n    User git\n")
        first = self.setting.publish(PUB)
        with self.assertRaisesRegex(ValueError, "host key changed"):
            self.setting.publish(PUB.replace("ucA", "ucB"), first)
        self.assertEqual((self.home / ".ssh/dotfiles-multipass/known_hosts/ubuntu-dev").read_text(),
                         "dotfiles-multipass-test-uuid " + PUB + "\n")

    def test_modified_managed_host_is_not_overwritten(self):
        hashes = self.setting.publish(PUB)
        self.setting.host.write_text(self.setting.host.read_text() + "# 用户修改\n")
        with self.assertRaisesRegex(ValueError, "changed outside"):
            self.setting.publish(PUB, hashes)

    def test_another_managed_host_can_update_aggregate(self):
        first = self.setting.publish(PUB)
        other = ssh_config.SSHConfig(self.home, "ubuntu-dev-2", "other-uuid", self.pub,
                                     REPO / "scripts/multipass/ssh_proxy.py",
                                     "/usr/local/bin/multipass")
        other.publish(PUB)
        self.setting.publish(PUB, first)
        self.assertEqual((self.home / ".ssh/config").read_text().count(ssh_config.SENTINEL), 1)

    def test_removal_restores_root_and_preserves_other_managed_host(self):
        original = (self.home / ".ssh/config").read_bytes()
        first = self.setting.publish(PUB)
        other = ssh_config.SSHConfig(self.home, "ubuntu-dev-2", "other-uuid", self.pub,
                                     REPO / "scripts/multipass/ssh_proxy.py",
                                     "/usr/local/bin/multipass")
        second = other.publish(PUB)
        self.setting.remove(first)
        self.assertFalse(self.setting.host.exists())
        self.assertFalse(self.setting.known.exists())
        self.assertTrue(other.host.exists())
        self.assertIn(str(other.host), self.setting.aggregate.read_text())
        self.assertEqual((self.home / ".ssh/config").read_text().count(ssh_config.SENTINEL), 1)
        self.assertFalse((self.home / ".ssh/config.dotfiles-multipass.test-uuid.bak").exists())
        other.remove(second)
        self.assertEqual((self.home / ".ssh/config").read_bytes(), original)
        self.assertFalse(other.aggregate.exists())
        self.assertFalse(other.proxy.exists())

    def test_removal_accepts_manually_absent_files_and_preserves_user_config(self):
        hashes = self.setting.publish(PUB)
        self.setting.host.unlink()
        self.setting.known.unlink()
        self.setting.aggregate.unlink()
        self.setting.proxy.unlink()
        (self.home / ".ssh/config").write_text("Host github.com\n    User git\nHost personal\n")
        self.setting.removal_plan(hashes)
        self.setting.remove(hashes)
        self.assertEqual((self.home / ".ssh/config").read_text(),
                         "Host github.com\n    User git\nHost personal\n")

    def test_removal_rejects_changed_trust_without_touching_files(self):
        hashes = self.setting.publish(PUB)
        self.setting.known.write_text(self.setting.known.read_text() + "# modified\n")
        before = {str(path): path.read_bytes() for path in (self.setting.host, self.setting.known,
                                                           self.setting.aggregate, self.setting.root)}
        with self.assertRaisesRegex(ValueError, "matching receipt hash"):
            self.setting.remove(hashes)
        self.assertEqual({path: Path(path).read_bytes() for path in before}, before)

    def test_removal_retry_repairs_aggregate_after_host_unlink(self):
        hashes = self.setting.publish(PUB)
        self.setting.host.unlink()
        self.setting.remove(hashes)
        self.assertFalse(self.setting.aggregate.exists())
        self.assertFalse(self.setting.known.exists())


class LiveCleanup(unittest.TestCase):
    """模拟在线验收的归属冲突与清理路径，不启动真实虚拟机。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.state = self.home / ".local/state/dotfiles-multipass/instances"
        self.report = self.home / "report"
        self.stamp = "20260923180000"
        self.name = f"dotfiles-test-2404-{self.stamp}"
        self.instances = {}
        self.markers = {}
        self.calls = []
        self.key = self.home / "key.pub"
        self.key.write_text(PUB + "\n")
        (self.home / ".ssh").mkdir()
        (self.home / ".ssh/config").write_text("Host github.com\n    User git\n")
        patchers = [mock.patch.object(live, "HOME", self.home),
                    mock.patch.object(live, "STATE", self.state),
                    mock.patch.object(live, "SSH_BASE", self.home / ".ssh/dotfiles-multipass"),
                    mock.patch.object(live, "run", side_effect=self.command),
                    mock.patch.object(live, "arguments", return_value=SimpleNamespace(
                        ref=REF, ssh_public_key=self.key, report_dir=self.report)),
                    mock.patch.object(live.time, "strftime", return_value=self.stamp),
                    mock.patch.object(live.signal, "signal")]
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def command(self, argv, *_args, **_kwargs):
        self.calls.append(argv)
        if argv[0] != "multipass":
            raise AssertionError(f"unexpected command: {argv}")
        if argv[1] == "list":
            return json.dumps({"list": list(self.instances.values())})
        if argv[1] == "exec":
            return json.dumps(self.markers[argv[2]]) if argv[-1].endswith("instance.json") else "{}"
        if argv[1] == "info":
            return "{}"
        if argv[1] == "transfer":
            return ""
        if argv[1] == "start":
            self.instances[argv[2]]["state"] = "Running"
            return ""
        if argv[1] == "delete":
            self.instances.pop(argv[-1])
            return ""
        raise AssertionError(f"unexpected command: {argv}")

    def seed(self, uuid="existing-uuid", register=False):
        state = self.state / self.name
        state.mkdir(parents=True)
        declaration = {"name": self.name, "uuid": uuid}
        runtime.save_json(state / "declaration.json", declaration)
        self.instances[self.name] = {"name": self.name, "state": "Running"}
        self.markers[self.name] = declaration
        setting = ssh_config.SSHConfig(self.home, self.name, uuid, self.key,
                                       REPO / "scripts/multipass/ssh_proxy.py", "/review/multipass")
        setting.publish(PUB)
        if register:
            target = self.report / "2404"
            target.mkdir(parents=True, exist_ok=True)
            runtime.record_creation(target / "creation.json", declaration)

    def assert_preserved(self, before):
        self.assertFalse(any(argv[1] == "delete" for argv in self.calls))
        for path, content in before.items():
            self.assertEqual(path.read_bytes(), content)

    def snapshot(self):
        return {path: path.read_bytes() for root in (self.state, self.home / ".ssh")
                for path in root.rglob("*") if path.is_file()}

    def test_preexisting_vm_and_state_are_not_deleted_after_name_conflict(self):
        self.seed()
        before = self.snapshot()
        with mock.patch.object(live, "acceptance") as acceptance:
            self.assertTrue(live.main())
        acceptance.assert_not_called()
        self.assertIn("already exists", (self.report / "2404/failure.txt").read_text())
        self.assert_preserved(before)
        self.assertIn(self.name, self.instances)
        self.assertFalse((self.report / "2404/cleanup-ok.txt").exists())

    def test_single_image_retry_never_runs_the_other_image(self):
        selected = SimpleNamespace(ref=REF, ssh_public_key=self.key, report_dir=self.report,
                                   only_image="26.04")
        with mock.patch.object(live, "arguments", return_value=selected), \
                mock.patch.object(live, "acceptance") as acceptance:
            self.assertFalse(live.main())
        self.assertEqual(acceptance.call_count, 1)
        self.assertEqual(acceptance.call_args.args[:2],
                         (f"dotfiles-test-2604-{self.stamp}", "26.04"))
        self.assertEqual(json.loads((self.report / "summary.json").read_text())["images"], ["26.04"])
        self.assertFalse((self.report / "2404").exists())

    def test_saved_state_without_vm_is_not_claimed_by_acceptance(self):
        self.seed()
        self.instances.clear()
        before = self.snapshot()
        with mock.patch.object(live, "acceptance") as acceptance:
            self.assertTrue(live.main())
        acceptance.assert_not_called()
        self.assertIn("state already exists", (self.report / "2404/failure.txt").read_text())
        self.assert_preserved(before)

    def test_racing_creator_without_this_runs_registration_is_preserved(self):
        def collided(*_args):
            self.seed()
            raise RuntimeError("another creator won the instance lock")
        with mock.patch.object(live, "acceptance", side_effect=collided):
            self.assertTrue(live.main())
        self.assertIn(self.name, self.instances)
        self.assertTrue((self.state / self.name / "declaration.json").is_file())
        self.assertFalse(any(argv[1] == "delete" for argv in self.calls))

    def test_this_runs_registered_vm_is_cleaned_after_creation_failure(self):
        def failed(argv, *positional, **keywords):
            if argv[:3] == ["bash", str(live.ENTRY), "create"]:
                self.seed(uuid="this-run-uuid")
                record = Path(argv[argv.index("--creation-record") + 1])
                self.assertTrue(record.is_absolute())
                runtime.record_creation(record, self.markers[self.name])
                raise RuntimeError("bootstrap failed after launch")
            return self.command(argv, *positional, **keywords)
        with mock.patch.object(live, "run", side_effect=failed):
            self.assertTrue(live.main())
        self.assertEqual([argv for argv in self.calls if argv[1] == "delete"],
                         [["multipass", "delete", "--purge", self.name]])
        self.assertFalse((self.state / self.name).exists())
        self.assertTrue((self.report / "2404/state/declaration.json").is_file())
        self.assertTrue((self.report / "2404/cleanup-ok.txt").is_file())
        self.assertIn("bootstrap failed after launch", (self.report / "2404/failure.txt").read_text())
        self.assertEqual((self.home / ".ssh/config").read_text(), "Host github.com\n    User git\n")
        self.assertEqual(self.key.read_text(), PUB + "\n")

    def test_registered_state_without_vm_is_cleaned(self):
        self.seed(register=True)
        self.instances.clear()
        live.cleanup(self.name, self.report / "2404", True)
        self.assertFalse((self.state / self.name).exists())
        self.assertTrue((self.report / "2404/cleanup-ok.txt").is_file())
        self.assertFalse(any(argv[1] == "delete" for argv in self.calls))

    def test_registration_mismatch_preserves_state_and_ssh(self):
        self.seed(register=True)
        runtime.save_json(self.report / "2404/creation.json", {"name": self.name, "uuid": "another-run"})
        before = self.snapshot()
        with self.assertRaisesRegex(RuntimeError, "creation record does not match"):
            live.cleanup(self.name, self.report / "2404", True)
        self.assert_preserved(before)

    def test_guest_marker_mismatch_prevents_delete(self):
        self.seed(register=True)
        self.markers[self.name] = {"name": self.name, "uuid": "replacement-vm"}
        before = self.snapshot()
        with self.assertRaisesRegex(RuntimeError, "ownership marker mismatch"):
            live.cleanup(self.name, self.report / "2404", True)
        self.assert_preserved(before)

    def test_unstable_registered_instance_is_only_observed_and_retained(self):
        self.seed(register=True)
        self.instances[self.name]["state"] = "Starting"
        self.calls.clear()
        with self.assertRaisesRegex(RuntimeError, "retaining it"):
            live.cleanup(self.name, self.report / "2404", True)
        self.assertTrue((self.state / self.name / "declaration.json").exists())
        self.assertFalse(any(argv[1] in ("exec", "transfer", "start", "delete") for argv in self.calls))
        self.assertIn("state=Starting", (self.report / "2404/guest-collection-skipped.txt").read_text())

    def test_active_creator_lock_prevents_cleanup(self):
        self.seed(register=True)
        before = self.snapshot()
        with runtime.lock(self.state / self.name / "lock"):
            with self.assertRaisesRegex(runtime.Failure, "lock exists"):
                live.cleanup(self.name, self.report / "2404", True)
        self.assert_preserved(before)


class LiveAcceptance(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.state = self.home / "state"
        self.name = "acceptance-test"
        self.calls = []
        self.interruption_exit = None
        self.reboot_required = False
        self.boot_id = "initial-boot"
        self.current_state = "Running"
        (self.home / ".ssh").mkdir()
        (self.home / ".ssh/config").write_text(live.INCLUDE_MARKER)
        for patcher in (mock.patch.object(live, "HOME", self.home),
                        mock.patch.object(live, "STATE", self.state),
                        mock.patch.object(live, "run", side_effect=self.command),
                        mock.patch.object(live, "interrupt_after_first_stop", return_value={})):
            patcher.start()
            self.addCleanup(patcher.stop)

    def command(self, argv, log=None, **_kwargs):
        self.calls.append(argv)
        if log:
            log.write_text("$ " + " ".join(map(str, argv)) + "\n")
        if argv[:3] == ["bash", str(live.ENTRY), "create"]:
            directory = self.state / self.name
            if "--creation-record" in argv:
                directory.mkdir(parents=True, exist_ok=True)
                runtime.save_json(directory / "declaration.json", {"name": self.name, "uuid": "owned"})
                receipt = {"machine_id": "machine", "cloud_instance_id": "cloud",
                           "stages": {"cloud-init": {"status": "ok"}}, "guest": {"boot_id": self.boot_id},
                           "attempts": [{"status": "ok"}]}
                if self.interruption_exit is not None:
                    self.current_state = "Stopped"
                    receipt.update(reboot_from="initial-boot", reboot_operation={"phase": "stop_requested"},
                                   stages={"cloud-init": {"status": "failed"}}, attempts=[{"status": "failed"}])
                runtime.save_json(directory / "receipt.json", receipt)
                if self.interruption_exit is not None:
                    raise RuntimeError(f"command failed (exit {self.interruption_exit}); see {log}")
            else:
                receipt = runtime.load_json(directory / "receipt.json")
                self.boot_id = "new-boot"
                self.current_state = "Running"
                receipt.update(reboot_to=self.boot_id, guest={"boot_id": self.boot_id},
                               reboot_operation={"method": "stop-start", "phase": "verified", "events": [
                                   {"kind": kind} for kind in ("stop-reconciled", "start-observed", "verified")]})
                receipt["stages"]["cloud-init"]["status"] = "ok"
                receipt["attempts"].append({"status": "ok"})
                runtime.save_json(directory / "receipt.json", receipt)
            return ""
        if argv[0] in ("bash", "ssh"):
            return ""
        if argv[:2] == ["multipass", "list"]:
            return json.dumps({"list": [{"name": self.name, "state": self.current_state}]})
        if argv[:2] == ["multipass", "info"]:
            return "{}"
        if argv[:2] == ["multipass", "stop"]:
            self.current_state = "Stopped"
            return ""
        if argv[:2] == ["multipass", "start"]:
            self.current_state = "Running"
            return ""
        if argv[:2] == ["multipass", "exec"]:
            if argv[-1] == "/var/lib/dotfiles-multipass/instance.json":
                return json.dumps({"name": self.name, "uuid": "owned"})
            if argv[-1] == "/etc/machine-id":
                return "machine"
            if argv[-1] == "instance_id":
                return "cloud"
            if argv[-1] == "/proc/sys/kernel/random/boot_id":
                return self.boot_id
            if "status" in argv:
                return json.dumps({"status": "done", "errors": []})
            if "reboot-required" in argv[-1]:
                return "true" if self.reboot_required else "false"
            return ""
        raise AssertionError(argv)

    def test_no_required_reboot_records_missing_coverage_and_completes_other_checks(self):
        report = self.home / "report"
        live.acceptance(self.name, "24.04", REF, self.home / "key.pub", report)
        self.assertEqual(runtime.load_json(report / "reboot-coverage.json")["status"], "skipped")
        self.assertTrue(all(runtime.load_json(report / "first-boot-assertions.json").values()))
        self.assertFalse((report / "first-reboot-assertions.json").exists())
        self.assertEqual(sum(argv[:3] == ["bash", str(live.ENTRY), "create"] for argv in self.calls), 1)
        self.assertTrue(any(argv[:3] == ["bash", str(live.ENTRY), "provision"] for argv in self.calls))
        self.assertTrue(any(argv[:2] == ["multipass", "stop"] for argv in self.calls))
        self.assertTrue((report / "ssh-after-restart.log").is_file())

    def test_controlled_interruption_accepts_direct_and_bash_signal_exit_codes(self):
        for code in (-15, 143):
            with self.subTest(code=code):
                self.interruption_exit = code
                report = self.home / f"report-{code}"
                live.acceptance(self.name, "24.04", REF, self.home / "key.pub", report)
                self.assertEqual(runtime.load_json(report / "reboot-coverage.json")["status"], "passed")
                self.assertTrue(all(runtime.load_json(report / "first-reboot-assertions.json").values()))
                self.assertTrue((report / "create-resumed.log").is_file())

    def test_other_failed_exit_is_not_accepted_as_controlled_interruption(self):
        self.interruption_exit = 1
        with self.assertRaisesRegex(RuntimeError, "interruption ended unexpectedly"):
            live.acceptance(self.name, "24.04", REF, self.home / "key.pub", self.home / "report")
        self.assertFalse(any(argv[:3] == ["bash", str(live.ENTRY), "provision"] for argv in self.calls))

    def test_missing_reboot_operation_does_not_hide_a_live_reboot_requirement(self):
        self.reboot_required = True
        report = self.home / "report"
        with self.assertRaisesRegex(RuntimeError, "first boot assertions failed"):
            live.acceptance(self.name, "24.04", REF, self.home / "key.pub", report)
        self.assertFalse((report / "reboot-coverage.json").exists())


class GuestGit(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.source.mkdir()
        subprocess.run(["git", "init", "-q", str(self.source)], check=True)
        for item in guest.REQUIRED:
            path = self.source / item
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("#!/bin/sh\n")
        subprocess.run(["git", "-C", str(self.source), "add", "."], check=True)
        subprocess.run(["git", "-C", str(self.source), "-c", "user.name=Test", "-c",
                        "user.email=test@example.invalid", "commit", "-qm", "first"], check=True)
        self.ref = subprocess.check_output(["git", "-C", str(self.source), "rev-parse", "HEAD"],
                                           text=True).strip()
        home = self.root / "home"
        home.mkdir()
        self.patchers = [mock.patch.object(guest, "HOME", home),
                         mock.patch.object(guest, "REPO", home / ".dotfiles"),
                         mock.patch.object(guest, "WORKSPACE", home / "workspace"),
                         mock.patch.object(guest, "require_ubuntu"),
                         mock.patch.object(guest, "ensure_owned_directory",
                                           side_effect=lambda path, create=False:
                                           path.mkdir(exist_ok=True) if create else None)]
        for patcher in self.patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_pinned_history_dirty_tree_and_workspace_preserved(self):
        guest.repository(str(self.source), self.ref)
        sentinel = guest.WORKSPACE / "sentinel"
        sentinel.write_text("keep")
        guest.repository(str(self.source), self.ref)
        self.assertEqual(sentinel.read_text(), "keep")
        (guest.REPO / "user-file").write_text("change")
        with self.assertRaisesRegex(ValueError, "user changes"):
            guest.repository(str(self.source), self.ref)
        self.assertEqual(sentinel.read_text(), "keep")

    def test_running_bootstrap_blocks_checkout_before_fetch_and_workspace_changes(self):
        guest.repository(str(self.source), self.ref)
        subprocess.run(["git", "-C", str(self.source), "-c", "user.name=Test", "-c",
                        "user.email=test@example.invalid", "commit", "--allow-empty", "-qm", "next"],
                       check=True)
        next_ref = subprocess.check_output(["git", "-C", str(self.source), "rev-parse", "HEAD"],
                                           text=True).strip()
        lock = guest.HOME / ".local/state/dotfiles-bootstrap/lock"
        lock.mkdir(parents=True)
        (lock / "pid").write_text(f"{os.getpid()}\n")
        sentinel = guest.WORKSPACE / "sentinel"
        sentinel.write_text("keep")
        with self.assertRaisesRegex(ValueError, "still running"):
            guest.repository(str(self.source), next_ref)
        self.assertEqual(guest.git("rev-parse", "HEAD", capture=True), self.ref)
        self.assertEqual(guest.git("rev-parse", "origin/HEAD", capture=True), self.ref)
        self.assertEqual(sentinel.read_text(), "keep")
        self.assertEqual((lock / "pid").read_text(), f"{os.getpid()}\n")
        (lock / "pid").unlink()
        lock.rmdir()
        guest.repository(str(self.source), next_ref)
        self.assertEqual(guest.git("rev-parse", "HEAD", capture=True), next_ref)

    def test_incomplete_bootstrap_lock_blocks_first_clone_and_preview(self):
        lock = guest.HOME / ".local/state/dotfiles-bootstrap/lock"
        lock.mkdir(parents=True)
        with self.assertRaisesRegex(ValueError, "incomplete.*lock"):
            guest.repository(str(self.source), self.ref)
        self.assertFalse(guest.REPO.exists())
        self.assertFalse(guest.WORKSPACE.exists())
        with self.assertRaisesRegex(ValueError, "incomplete.*lock"):
            guest.bootstrap("preview")
        self.assertTrue(lock.is_dir())

    def test_stale_bootstrap_lock_is_reported_and_preserved(self):
        lock = guest.HOME / ".local/state/dotfiles-bootstrap/lock"
        lock.mkdir(parents=True)
        (lock / "pid").write_text("2147483647\n")
        with mock.patch.object(guest.os, "kill", side_effect=ProcessLookupError), \
                self.assertRaisesRegex(ValueError, "abandoned.*lock"):
            guest.repository(str(self.source), self.ref)
        self.assertEqual((lock / "pid").read_text(), "2147483647\n")
        self.assertFalse(guest.REPO.exists())


class GuestEnvironment(unittest.TestCase):
    @unittest.skipUnless(shutil.which("zsh"), "native Zsh is not installed")
    def test_version_receipt_uses_jdk_from_zsh_startup(self):
        with tempfile.TemporaryDirectory() as root:
            home = Path(root)
            binary = home / "jdk/bin"
            binary.mkdir(parents=True)
            for name in ("java", "javac", "mvn"):
                tool = binary / name
                tool.write_text("#!/bin/sh\nprintf '%s:%s\\n' " + name + ' "$JAVA_HOME"\n')
                tool.chmod(0o755)
            (home / ".zshenv").write_text(
                f"export JAVA_HOME={shlex.quote(str(binary.parent))}\n"
                f"export PATH={shlex.quote(str(binary))}:$PATH\n")
            log_dir = home / ".local/state/dotfiles-bootstrap"
            log_dir.mkdir(parents=True)
            (log_dir / "run.test").write_text("result: dry run completed\n"
                                              "result: PASS=207 WARN=0 FAIL=0 SKIP=7\n")
            with mock.patch.object(guest, "HOME", home), \
                    mock.patch.object(guest, "require_ubuntu"), \
                    mock.patch.object(guest, "git", return_value=REF), \
                    mock.patch("builtins.print") as output:
                guest.versions()
            receipt = json.loads(output.call_args.args[0])
            tools = receipt["tools"]
            self.assertEqual(receipt["doctor"], {"pass": 207, "warn": 0, "fail": 0, "skip": 7})
            for name in ("java", "javac", "maven"):
                executable = "mvn" if name == "maven" else name
                self.assertEqual(tools[name], [f"{executable}:{binary.parent}"])

    def test_doctor_summary_requires_completed_result(self):
        with tempfile.TemporaryDirectory() as root:
            log = Path(root) / "run.test"
            log.write_text("result: deployment completed\n")
            with self.assertRaisesRegex(ValueError, "no doctor result"):
                guest.doctor_summary(log)


if __name__ == "__main__":
    unittest.main(verbosity=2)
