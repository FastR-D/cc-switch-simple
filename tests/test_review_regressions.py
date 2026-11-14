"""Regressions for the independent review's URL, integer and YAML findings."""

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import tomlkit
import yaml

import cc_simple as cli
import test_library_integration as integration


class ConfigRegressionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.config = {"name": "regression", "base_url": "https://example.test/v1",
                       "type": "openai", "api_key": "fake-only", "model_name": "mock"}

    def test_codex_base_shapes_write_the_same_api_prefix(self):
        cases = {"": "/v1", "/": "/v1", "/v1": "/v1", "/v1/responses/": "/v1",
                 "/gateway/v1": "/gateway/v1", "/gateway/v1/responses": "/gateway/v1",
                 "/gateway": "/gateway", "/gateway/responses": "/gateway",
                 "/v1/chat/completions": "/v1"}
        for suffix, expected in cases.items():
            with self.subTest(suffix=suffix):
                config = dict(self.config, base_url="https://example.test" + suffix)
                target = self.root / str(len(list(self.root.iterdir())))
                result = cli.switch(config, target_dir=target)
                provider = tomlkit.parse((target / "config.toml").read_text(encoding="utf-8"))["model_providers"]["cc_simple"]
                self.assertEqual(provider["base_url"], "https://example.test" + expected)
                self.assertEqual(result["base_url"], provider["base_url"])
                self.assertEqual(config["base_url"], "https://example.test" + suffix)

    def test_claude_base_shapes_preserve_gateway_prefix(self):
        cases = {"": "", "/": "", "/v1": "", "/v1/messages/": "",
                 "/gateway": "/gateway", "/gateway/v1": "/gateway",
                 "/gateway/v1/messages": "/gateway", "/tenant/v1/edge": "/tenant/v1/edge"}
        for suffix, expected in cases.items():
            with self.subTest(suffix=suffix):
                config = dict(self.config, type="anthropic", base_url="https://example.test" + suffix)
                target = self.root / str(len(list(self.root.iterdir())))
                result = cli.switch(config, target_dir=target)
                settings = json.loads((target / "settings.json").read_text(encoding="utf-8"))
                self.assertEqual(settings["env"]["ANTHROPIC_BASE_URL"], "https://example.test" + expected)
                self.assertEqual(result["base_url"], settings["env"]["ANTHROPIC_BASE_URL"])

    def test_ambiguous_message_endpoints_fail_before_writing(self):
        for suffix in ("/messages", "/gateway/messages", "/v1/responses"):
            with self.subTest(suffix=suffix):
                config = dict(self.config, type="anthropic", base_url="https://example.test" + suffix)
                with self.assertRaises(cli.ConfigError):
                    cli.switch(config, target_dir=self.root / "target")
                self.assertFalse((self.root / "target").exists())

    def test_codex_unversioned_full_endpoints_are_explicitly_rejected(self):
        for suffix in ("/responses", "/chat/completions"):
            with self.subTest(suffix=suffix):
                with self.assertRaises(cli.ConfigError):
                    cli.switch(dict(self.config, base_url="https://example.test" + suffix),
                               target_dir=self.root / "target")
                self.assertFalse((self.root / "target").exists())

    def test_huge_temperature_returns_json_error_without_touching_files(self):
        source = self.root / "huge.yaml"
        source.write_text(yaml.safe_dump(dict(self.config, temperature=10 ** 400)), encoding="utf-8")
        target = self.root / "target"
        target.mkdir()
        active = target / "config.toml"
        original = b'# kept\r\nmodel="existing"\r\n'
        active.write_bytes(original)
        script = Path(cli.__file__).with_name("cc-simple.py")
        for command, extra in (("add", []), ("check", []), ("use", ["--target-dir", str(target)])):
            with self.subTest(command=command):
                process = subprocess.run([sys.executable, str(script), "--json", "--config-dir",
                    str(self.root / "store"), command, str(source), *extra], cwd=self.root,
                    env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
                    capture_output=True, encoding="utf-8", timeout=15)
                self.assertEqual(process.returncode, 2, process.stderr)
                self.assertEqual(json.loads(process.stdout)["exit_code"], 2)
                self.assertNotIn("Traceback", process.stderr)
                self.assertEqual(active.read_bytes(), original)
                self.assertEqual(list(target.iterdir()), [active])
                self.assertFalse((self.root / "store").exists())

    def test_yaml_merge_defaults_and_explicit_overrides(self):
        documents = [
            '<<: &defaults {name: merged, base_url: "https://example.test/v1", type: openai, api_key: fake-only}\nmodel_name: mock\n',
            '<<: [{name: first, base_url: "https://example.test/v1", type: openai, api_key: fake-only}, {name: second, model_name: mock}]\nname: override\n',
            '<<: {<<: {name: inner, type: openai, api_key: fake-only}, name: middle, base_url: "https://example.test/v1"}\nname: outer\n',
        ]
        for document in documents:
            with self.subTest(document=document):
                source = self.root / "merge.yaml"
                source.write_text(document, encoding="utf-8")
                expected = yaml.safe_load(document)
                loaded = cli.read_config(source)
                self.assertEqual(loaded["name"], expected["name"])
                self.assertEqual(loaded["api_key"], "fake-only")
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    code = cli.main(["--json", "--config-dir", str(self.root / "store"), "add", str(source)])
                self.assertEqual(code, 0, output.getvalue())
                stored = yaml.safe_load(Path(json.loads(output.getvalue())["path"]).read_text(encoding="utf-8"))
                self.assertEqual(stored, loaded)

    def test_merge_alias_reuse_and_duplicate_explicit_keys(self):
        document = 'first: &base {name: old, <<: {name: default}}\nsecond: {<<: *base, name: new}\nthird: {<<: *base}\n'
        self.assertEqual(yaml.load(document, Loader=cli.UniqueLoader), yaml.safe_load(document))
        for document in ('name: a\nname: b\n', '<<: {name: a, name: b}\n',
                         '<<: {name: a}\nname: b\nname: c\n',
                         '<<: {name: a}\n<<: {name: b}\n', '<<: {1: bad}\n'):
            with self.subTest(document=document):
                with self.assertRaises(cli.ConfigError):
                    yaml.load(document, Loader=cli.UniqueLoader)


@unittest.skipIf(integration.modeltrace is None, "installed ModelTrace library is required")
class AddressIntegrationTests(unittest.TestCase):
    """Installed CLI checks use the same prefixes as the generated client config."""

    setUp = integration.LibraryIntegrationTests.setUp
    stop_server = integration.LibraryIntegrationTests.stop_server
    command = integration.LibraryIntegrationTests.command

    def test_codex_check_endpoint_matrix(self):
        host = f"http://127.0.0.1:{self.server.server_port}"
        cases = {"": "/v1/responses", "/v1": "/v1/responses", "/v1/responses": "/v1/responses",
                 "/gateway": "/gateway/responses", "/gateway/v1": "/gateway/v1/responses",
                 "/gateway/v1/responses": "/gateway/v1/responses"}
        for suffix, expected in cases.items():
            with self.subTest(suffix=suffix):
                self.server.requests.clear()
                self.config["base_url"] = host + suffix
                self.source.write_text(yaml.safe_dump(self.config), encoding="utf-8")
                code, result = self.command()
                self.assertEqual(code, 0, result)
                self.assertEqual([path for path, _ in self.server.requests], [expected] * 3)

    def test_claude_check_endpoint_matrix(self):
        host = f"http://127.0.0.1:{self.server.server_port}"
        cases = {"": "/v1/messages", "/v1": "/v1/messages", "/v1/messages": "/v1/messages",
                 "/gateway": "/gateway/v1/messages", "/gateway/v1": "/gateway/v1/messages",
                 "/gateway/v1/messages": "/gateway/v1/messages"}
        self.config["type"] = "anthropic"
        for suffix, expected in cases.items():
            with self.subTest(suffix=suffix):
                self.server.requests.clear()
                self.config["base_url"] = host + suffix
                self.source.write_text(yaml.safe_dump(self.config), encoding="utf-8")
                code, result = self.command()
                self.assertEqual(code, 0, result)
                self.assertEqual([path for path, _ in self.server.requests], [expected] * 3)


if __name__ == "__main__":
    unittest.main()
