import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import tomlkit
import yaml

import cc_simple as cli


class SwitcherTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.store = self.root / "store"
        self.target = self.root / "target"
        self.config = {"name": "../中文 配置", "base_url": "https://example.test/v1",
                       "type": "openai", "api_key": "test-only-SECRET", "model_name": "test-model"}
        self.source = self.write_source(self.config)

    def write_source(self, config, name="provider.yaml"):
        path = self.root / name
        path.write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
        return path

    def run_cli(self, *arguments):
        output = io.StringIO()
        errors = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            code = cli.main(["--config-dir", str(self.store), "--json", *map(str, arguments)])
        return code, json.loads(output.getvalue()), errors.getvalue()

    def test_yaml_roundtrip_safe_ids_and_gaps(self):
        code, added, _ = self.run_cli("add", self.source)
        self.assertEqual(code, 0)
        self.assertEqual(Path(added["path"]).parent, self.store)
        self.assertEqual(Path(added["path"]).name, "1_config.yaml")
        (self.store / "8_legacy.yaml").write_text(self.source.read_text(encoding="utf-8"), encoding="utf-8")
        code, added, _ = self.run_cli("add", self.source)
        self.assertEqual(added["id"], 9)
        code, items, _ = self.run_cli("list")
        self.assertEqual([item["id"] for item in items], [1, 8, 9])
        self.assertNotIn(self.config["api_key"], json.dumps(items))
        code, used, _ = self.run_cli("use", "9", "--target-dir", self.target)
        self.assertEqual(code, 0)
        self.assertEqual(used["target"], "codex")

    def test_legacy_key_equals_and_model_credential(self):
        self.source.write_text("name=legacy\nbase_url=https://example.test/v1\ntype=openai\nmodel=old-secret\n", encoding="utf-8-sig")
        loaded = cli.read_config(self.source)
        self.assertEqual(loaded["api_key"], "old-secret")
        self.assertEqual(loaded["model_name"], "gpt-4")

    def test_duplicate_keys_unknown_fields_and_missing_token_fail_without_write(self):
        invalid = [dict(self.config, tyep="openai"), {k: v for k, v in self.config.items() if k != "api_key"},
                   dict(self.config, env_key="MY_TOKEN"), dict(self.config, base_url="file:///tmp/test"),
                   dict(self.config, base_url="https://api.exam ple.test/v1")]
        for config in invalid:
            with self.subTest(config=config):
                source = self.write_source(config)
                code, error, _ = self.run_cli("add", source)
                self.assertEqual(code, 2)
                self.assertIn("error", error)
                self.assertFalse(self.store.exists())
        self.source.write_text("name: first\nname: second\n", encoding="utf-8")
        self.assertEqual(self.run_cli("add", self.source)[0], 2)

    def test_numeric_list_order_and_duplicate_id_fail(self):
        self.store.mkdir()
        for identity in (2, 10, 1):
            (self.store / f"{identity}_config.yaml").write_bytes(self.source.read_bytes())
        self.assertEqual([item["id"] for item in self.run_cli("list")[1]], [1, 2, 10])
        (self.store / "2_other.yaml").write_bytes(self.source.read_bytes())
        self.assertEqual(self.run_cli("list")[0], 2)

    def test_codex_preserves_settings_comments_and_unique_backups(self):
        self.target.mkdir()
        existing = '# retained comment\nmodel="old"\napproval_policy="never"\n[projects."/work"]\ntrust_level="trusted"\n[model_providers.other]\nname="other"\n[model_providers.cc_simple]\nrequest_max_retries=7\n'
        path = self.target / "config.toml"
        path.write_text(existing, encoding="utf-8")
        code, first, _ = self.run_cli("use", self.source, "--target-dir", self.target)
        self.assertEqual(code, 0)
        content = path.read_text(encoding="utf-8")
        loaded = tomlkit.parse(content)
        self.assertIn("# retained comment", content)
        self.assertEqual(loaded["approval_policy"], "never")
        self.assertEqual(loaded["projects"]["/work"]["trust_level"], "trusted")
        self.assertEqual(loaded["model_providers"]["other"]["name"], "other")
        provider = loaded["model_providers"]["cc_simple"]
        self.assertEqual(provider["request_max_retries"], 7)
        self.assertEqual(provider["wire_api"], "responses")
        self.assertEqual(provider["experimental_bearer_token"], self.config["api_key"])
        self.assertEqual(Path(first["backup"]).read_text(encoding="utf-8"), existing)
        code, second, _ = self.run_cli("use", self.source, "--target-dir", self.target)
        self.assertNotEqual(first["backup"], second["backup"])

    def test_environment_auth_is_reference_for_codex(self):
        config = dict(self.config, env_key="TEST_MODEL_TOKEN")
        del config["api_key"]
        source = self.write_source(config)
        with patch.dict(os.environ, {}, clear=True):
            code, _, _ = self.run_cli("use", source, "--target-dir", self.target)
        self.assertEqual(code, 0)
        provider = tomlkit.parse((self.target / "config.toml").read_text(encoding="utf-8"))["model_providers"]["cc_simple"]
        self.assertEqual(provider["env_key"], "TEST_MODEL_TOKEN")
        self.assertNotIn("experimental_bearer_token", provider)

    def test_new_host_does_not_inherit_old_endpoint_credentials(self):
        self.target.mkdir()
        path = self.target / "config.toml"
        path.write_text('[model_providers.cc_simple]\nbase_url="https://old.test/v1"\nrequest_max_retries=7\nmodel_catalog_url="https://old.test/models"\nenv_key_instructions="old auth"\n[model_providers.cc_simple.aws]\nregion="us-east-1"\n[model_providers.cc_simple.gateway_oauth]\naudience="old"\n[model_providers.cc_simple.http_headers]\nx-api-key="OLD_SECRET"\n[model_providers.cc_simple.env_http_headers]\nx-key="OLD_SECRET_ENV"\n[model_providers.cc_simple.query_params]\napi-key="OLD_SECRET"\n', encoding="utf-8")
        self.assertEqual(self.run_cli("use", self.source, "--target-dir", self.target)[0], 0)
        provider = tomlkit.parse(path.read_text(encoding="utf-8"))["model_providers"]["cc_simple"]
        for key in ("http_headers", "env_http_headers", "query_params", "aws", "gateway_oauth", "model_catalog_url", "env_key_instructions"):
            self.assertNotIn(key, provider)
        self.assertEqual(provider["request_max_retries"], 7)

    def test_claude_preserves_settings_and_removes_conflicting_auth(self):
        self.target.mkdir()
        path = self.target / "settings.json"
        path.write_text(json.dumps({"permissions": {"allow": ["Read"]}, "env": {"OTHER": "retained", "ANTHROPIC_API_KEY": "old-key", "ANTHROPIC_AUTH_TOKEN": "old-bearer", "ANTHROPIC_MODEL": "old-model"}}), encoding="utf-8")
        source = self.write_source(dict(self.config, type="anthropic", base_url="https://example.test"))
        code, result, _ = self.run_cli("use", source, "--target-dir", self.target)
        self.assertEqual(code, 0)
        self.assertEqual(result["target"], "claude")
        loaded = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(loaded["permissions"], {"allow": ["Read"]})
        self.assertEqual(loaded["env"]["OTHER"], "retained")
        self.assertNotIn("ANTHROPIC_AUTH_TOKEN", loaded["env"])
        self.assertNotIn("ANTHROPIC_MODEL", loaded["env"])
        self.assertEqual(loaded["env"]["ANTHROPIC_API_KEY"], self.config["api_key"])
        self.assertEqual(loaded["model"], "test-model")

    def test_claude_environment_is_resolved_and_missing_value_fails(self):
        config = dict(self.config, type="anthropic", env_key="TEST_MODEL_TOKEN")
        del config["api_key"]
        source = self.write_source(config)
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(self.run_cli("use", source, "--target-dir", self.target)[0], 2)
        self.assertFalse(self.target.exists())
        with patch.dict(os.environ, {"TEST_MODEL_TOKEN": "environment-secret"}):
            self.assertEqual(self.run_cli("use", source, "--target-dir", self.target)[0], 0)
        loaded = json.loads((self.target / "settings.json").read_text(encoding="utf-8"))
        self.assertEqual(loaded["env"]["ANTHROPIC_API_KEY"], "environment-secret")

    def test_anthropic_codex_requires_explicit_responses_proxy(self):
        source = self.write_source(dict(self.config, type="anthropic"))
        self.assertEqual(self.run_cli("use", source, "--target", "codex", "--target-dir", self.target)[0], 2)
        source = self.write_source(dict(self.config, type="anthropic", api_format="responses"))
        self.assertEqual(self.run_cli("use", source, "--target", "codex", "--target-dir", self.target)[0], 0)

    def test_dry_run_does_not_write_or_leak_credentials(self):
        code, output, _ = self.run_cli("use", self.source, "--target-dir", self.target, "--dry-run")
        self.assertEqual(code, 0)
        self.assertTrue(output["dry_run"])
        self.assertFalse(self.target.exists())
        self.assertNotIn(self.config["api_key"], json.dumps(output))

    def test_invalid_existing_file_is_preserved_and_error_hides_source(self):
        self.target.mkdir()
        path = self.target / "config.toml"
        original = 'token = "test-only-SECRET\n'
        path.write_text(original, encoding="utf-8")
        code, output, _ = self.run_cli("use", self.source, "--target-dir", self.target)
        self.assertEqual(code, 2)
        self.assertEqual(path.read_text(encoding="utf-8"), original)
        self.assertNotIn(self.config["api_key"], json.dumps(output))
        self.assertEqual(len(list(self.target.glob("*.bak.*"))), 0)

    def test_failed_atomic_replace_keeps_original_and_cleans_temp(self):
        self.target.mkdir()
        path = self.target / "config.toml"
        path.write_text("original", encoding="utf-8")
        with patch("cc_simple.os.replace", side_effect=OSError("write failed")):
            with self.assertRaises(OSError):
                cli.atomic_write(path, "replacement")
        self.assertEqual(path.read_text(encoding="utf-8"), "original")
        self.assertEqual(list(self.target.iterdir()), [path])

    def test_concurrent_add_never_overwrites_a_stored_configuration(self):
        script = Path(cli.__file__).with_name("cc-simple.py")
        environment = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
        processes = [subprocess.Popen([sys.executable, str(script), "--json", "--config-dir", str(self.store), "add", str(self.source)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8", env=environment) for _ in range(4)]
        results = [process.communicate(timeout=15) for process in processes]
        self.assertTrue(all(process.returncode == 0 for process in processes), results)
        identities = [json.loads(output)["id"] for output, _ in results]
        self.assertEqual(sorted(identities), [1, 2, 3, 4])
        self.assertEqual(len(list(self.store.glob("*.yaml"))), 4)

    def test_check_calls_library_protocol_and_does_not_write(self):
        calls = []
        fake = types.ModuleType("modeltrace")
        fake.check_api = lambda **arguments: calls.append(arguments) or {"results": [], "families": [], "api_test": {"ok": True}, "used_outputs": 3}
        with patch.dict(sys.modules, {"modeltrace": fake}):
            code, output, _ = self.run_cli("check", self.source)
            self.assertEqual(code, 0)
            self.assertEqual(calls[-1]["api_format"], "responses")
            self.assertEqual(calls[-1]["api_key"], self.config["api_key"])
            code, output, _ = self.run_cli("check", self.source, "--format", "openai", "--timeout", "7", "--temperature", "0")
            self.assertEqual(calls[-1]["api_format"], "openai")
            self.assertEqual(calls[-1]["timeout"], 7)
            self.assertEqual(calls[-1]["temperature"], 0)
        self.assertFalse(self.store.exists())
        self.assertFalse(self.target.exists())
        self.assertNotIn(self.config["api_key"], json.dumps(output))

    def test_runtime_api_errors_are_nonzero_and_redacted(self):
        fake = types.ModuleType("modeltrace")
        def fail(**arguments):
            raise RuntimeError("upstream rejected " + arguments["api_key"])
        fake.check_api = fail
        with patch.dict(sys.modules, {"modeltrace": fake}):
            code, output, _ = self.run_cli("check", self.source)
        self.assertEqual(code, 1)
        self.assertIn("<redacted>", output["error"])
        self.assertNotIn(self.config["api_key"], output["error"])

    def test_bad_temperature_and_library_value_error_are_clean(self):
        self.assertEqual(self.run_cli("check", self.source, "--temperature", "-1")[0], 2)
        fake = types.ModuleType("modeltrace")
        def fail(**arguments):
            raise ValueError("a secret value")
        fake.check_api = fail
        with patch.dict(sys.modules, {"modeltrace": fake}):
            code, output, _ = self.run_cli("check", self.source)
        self.assertEqual(code, 2)
        self.assertNotIn("secret", output["error"])

    def test_yaml_syntax_error_does_not_leak_credential_snippet(self):
        self.source.write_text('api_key: [test-only-SECRET\n', encoding="utf-8")
        code, output, _ = self.run_cli("add", self.source)
        self.assertEqual(code, 2)
        self.assertNotIn("test-only-SECRET", output["error"])

    def test_real_entrypoint_returns_failure_exit_code(self):
        script = Path(cli.__file__).with_name("cc-simple.py")
        environment = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
        process = subprocess.run([sys.executable, str(script), "--json", "--config-dir", str(self.store), "use", "999"], capture_output=True, encoding="utf-8", env=environment, timeout=15)
        self.assertEqual(process.returncode, 2)
        self.assertEqual(json.loads(process.stdout)["exit_code"], 2)


if __name__ == "__main__":
    unittest.main()
