"""A small YAML configuration switcher for Codex and Claude Code."""
import argparse
import json
import math
import os
import re
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import tomlkit
import yaml


class ConfigError(ValueError):
    pass


class UniqueLoader(yaml.SafeLoader):
    """Allow merge defaults while rejecting duplicate explicit keys."""
    def flatten_mapping(self, node):
        if not getattr(node, "cc_checked", False):
            keys = set()
            for key_node, _ in node.value:
                key = "<<" if key_node.tag == "tag:yaml.org,2002:merge" else self.construct_object(key_node)
                if not isinstance(key, str) or key in keys:
                    raise ConfigError("YAML keys must be unique strings")
                keys.add(key)
            node.cc_checked = True
        super().flatten_mapping(node)


def read_config(path):
    text = Path(path).read_text(encoding="utf-8-sig")
    lines = [line.strip() for line in text.splitlines()
             if line.strip() and not line.lstrip().startswith("#")]
    if lines and all(re.match(r"^[A-Za-z_]+\s*=", line) for line in lines):
        data = {}
        for line in lines:
            key, value = line.split("=", 1)
            if key.strip() in data:
                raise ConfigError("duplicate configuration field")
            data[key.strip()] = value.strip()
    else:
        data = yaml.load(text, Loader=UniqueLoader)
    if not isinstance(data, dict):
        raise ConfigError("configuration must be a YAML mapping or legacy key=value file")
    allowed = {"name", "base_url", "type", "api_key", "env_key", "model_name",
               "model", "target", "api_format", "temperature"}
    if set(data) - allowed:
        raise ConfigError("unknown configuration fields: " + ", ".join(sorted(set(data) - allowed)))
    if "model" in data:
        if "api_key" in data or "env_key" in data:
            raise ConfigError("legacy model is a credential alias; use model_name for the API model")
        data["api_key"] = data.pop("model")
    for key in ("name", "base_url", "type"):
        if not isinstance(data.get(key), str) or not data[key].strip():
            raise ConfigError(f"missing or invalid {key}")
        data[key] = data[key].strip()
    if data["type"] not in ("openai", "anthropic"):
        raise ConfigError("type must be openai or anthropic")
    try:
        url = urlsplit(data["base_url"])
        url.port
    except ValueError:
        raise ConfigError("invalid base_url") from None
    if (any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in data["base_url"]) or
            url.scheme not in ("http", "https") or not url.hostname or
            url.username or url.password or url.query or url.fragment):
        raise ConfigError("base_url must be an HTTP(S) URL without credentials, query or fragment")
    data["base_url"] = data["base_url"].rstrip("/")
    data.setdefault("model_name", "gpt-4" if data["type"] == "openai" else "claude-opus-5-5")
    if not isinstance(data["model_name"], str) or not data["model_name"].strip():
        raise ConfigError("model_name must be a non-empty string")
    data["model_name"] = data["model_name"].strip()
    if bool(data.get("api_key")) == bool(data.get("env_key")):
        raise ConfigError("provide exactly one of api_key or env_key")
    for key in ("api_key", "env_key"):
        if key in data and (not isinstance(data[key], str) or not data[key].strip()):
            raise ConfigError(f"{key} must be a non-empty string")
    if "env_key" in data and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", data["env_key"]):
        raise ConfigError("env_key must be an environment variable name")
    if "api_key" in data and any(ord(char) < 32 or ord(char) == 127 for char in data["api_key"]):
        raise ConfigError("api_key contains a forbidden control character")
    if data.get("target") not in (None, "codex", "claude"):
        raise ConfigError("target must be codex or claude")
    if data.get("api_format") not in (None, "auto", "openai", "anthropic", "responses"):
        raise ConfigError("invalid api_format")
    if "temperature" in data:
        value = data["temperature"]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 2 or not math.isfinite(value):
            raise ConfigError("temperature must be a finite number between 0 and 2")
    return data


def resolve_target(config, target=None):
    target = target or config.get("target") or ("claude" if config["type"] == "anthropic" else "codex")
    if target == "claude" and config["type"] != "anthropic":
        raise ConfigError("Claude Code requires an Anthropic Messages endpoint")
    if target == "codex" and config["type"] == "anthropic" and config.get("api_format") != "responses":
        raise ConfigError("Anthropic-to-Codex requires an actual Responses proxy and api_format: responses")
    return target


def client_base_url(base_url, target):
    """Normalize API inputs for the endpoint suffix added by each real client."""
    url = urlsplit(base_url)
    path = url.path.rstrip("/")
    if target == "codex":
        for suffix in ("/responses", "/chat/completions"):
            if path.endswith(suffix):
                path = path[:-len(suffix)]
                if not path:
                    raise ConfigError("Codex endpoints need an API prefix such as /v1/responses")
                break
        if path.endswith("/messages"):
            raise ConfigError("Codex requires a Responses API base or endpoint")
        path = path or "/v1"
    else:
        if path.endswith("/messages") and not path.endswith("/v1/messages"):
            raise ConfigError("Claude Messages endpoints must end with /v1/messages")
        for suffix in ("/v1/messages", "/v1"):
            if path.endswith(suffix):
                path = path[:-len(suffix)]
                break
        if path.endswith(("/responses", "/chat/completions")):
            raise ConfigError("Claude requires a Messages API base or endpoint")
    return urlunsplit((url.scheme, url.netloc, path, "", ""))


def credential(config):
    value = config.get("api_key") or os.environ.get(config.get("env_key", ""))
    if not value:
        raise ConfigError("configured credential environment variable is not set")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ConfigError("credential contains a forbidden control character")
    return value


def atomic_write(path, content, exclusive=False):
    path = Path(path)
    if path.is_symlink():
        raise ConfigError("refusing to replace a symbolic-link configuration file")
    if not path.parent.exists():
        path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(content.encode("utf-8") if isinstance(content, str) else content)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, 0o600)
        if exclusive:
            os.link(temporary, path)
        else:
            os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def configurations(directory):
    entries = {}
    for path in directory.glob("*.y*ml"):
        match = re.fullmatch(r"([1-9][0-9]*)_.+\.ya?ml", path.name)
        if match:
            identity = int(match[1])
            if identity in entries:
                raise ConfigError(f"duplicate stored configuration ID {identity}")
            entries[identity] = path
    return entries


def source_config(source, directory):
    if re.fullmatch(r"[1-9][0-9]*", source):
        path = configurations(directory).get(int(source))
        if path is None:
            raise ConfigError("configuration ID not found")
    else:
        path = Path(source).expanduser()
    return read_config(path)


def summary(config):
    return {key: config[key] for key in ("name", "base_url", "type", "model_name")}


def switch(config, target_dir=None, target=None, dry_run=False):
    target = resolve_target(config, target)
    config = dict(config, base_url=client_base_url(config["base_url"], target))
    directory = Path(target_dir).expanduser() if target_dir else Path.home() / (".codex" if target == "codex" else ".claude")
    path = directory / ("config.toml" if target == "codex" else "settings.json")
    if path.is_symlink():
        raise ConfigError("refusing to replace a symbolic-link configuration file")
    original = path.read_bytes() if path.exists() else None
    if target == "codex":
        document = tomlkit.parse(original.decode("utf-8-sig")) if original else tomlkit.document()
        provider_id = "cc_simple"
        providers = document.setdefault("model_providers", tomlkit.table())
        if not isinstance(providers, dict):
            raise ConfigError("model_providers must be a TOML table")
        provider = providers.get(provider_id, tomlkit.table())
        if not isinstance(provider, dict):
            raise ConfigError("cc_simple provider must be a TOML table")
        for key in ("env_key", "experimental_bearer_token", "auth", "http_headers", "env_http_headers", "query_params",
                    "gateway_oauth", "aws", "model_catalog_url", "env_key_instructions"):
            provider.pop(key, None)
        provider.update({"name": config["name"], "base_url": config["base_url"], "wire_api": "responses"})
        provider["requires_openai_auth"] = False
        if "env_key" in config:
            provider["env_key"] = config["env_key"]
        else:
            provider["experimental_bearer_token"] = config["api_key"]
        providers[provider_id] = provider
        document["model_provider"] = provider_id
        document["model"] = config["model_name"]
        content = tomlkit.dumps(document)
    else:
        document = json.loads(original.decode("utf-8-sig")) if original else {}
        if not isinstance(document, dict) or not isinstance(document.get("env", {}), dict):
            raise ConfigError("Claude settings and env must be JSON objects")
        environment = document.setdefault("env", {})
        for key in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_MODEL"):
            environment.pop(key, None)
        environment["ANTHROPIC_BASE_URL"] = config["base_url"]
        environment["ANTHROPIC_API_KEY"] = "<redacted>" if dry_run else credential(config)
        document["model"] = config["model_name"]
        content = json.dumps(document, ensure_ascii=False, indent=2) + "\n"
    result = dict(summary(config), target=target, path=str(path), dry_run=dry_run)
    if not dry_run:
        if original is not None:
            suffix = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8]
            backup = path.with_name(path.name + ".bak." + suffix)
            atomic_write(backup, original)
            result["backup"] = str(backup)
        atomic_write(path, content)
    return result


def check(config, args):
    target = resolve_target(config, args.target)
    config = dict(config, base_url=client_base_url(config["base_url"], target))
    api_base = config["base_url"] + ("/v1" if target == "claude" else "")
    api_format = args.api_format or config.get("api_format") or ("responses" if target == "codex" else "anthropic")
    protocol = ("responses" if target == "codex" else "anthropic") if api_format == "auto" else api_format
    endpoint = api_base + {"responses": "/responses", "openai": "/chat/completions", "anthropic": "/messages"}[protocol]
    try:
        from modeltrace import check_api
    except ImportError:
        raise ConfigError("check requires the modeltrace library; see README installation") from None
    result = check_api(base_url=endpoint, api_key=credential(config),
                       api_model=config["model_name"], api_format=api_format,
                       temperature=args.temperature if args.temperature is not None else config.get("temperature"),
                       timeout=args.timeout, attempts=args.attempts)
    return dict(summary(config), target=target, api_base_url=api_base, api_endpoint=endpoint, requested_format=api_format, analysis=result)


def positive_number(value):
    number = float(value)
    if not math.isfinite(number) or not 0 < number <= 240:
        raise argparse.ArgumentTypeError("must be a finite number between 0 and 240")
    return number


def attempt_count(value):
    number = int(value)
    if not 3 <= number <= 20:
        raise argparse.ArgumentTypeError("must be between 3 and 20")
    return number


def make_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config-dir", default=argparse.SUPPRESS)
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    parser.add_argument("--config-dir", default="~/.cc-simple/confs")
    parser.add_argument("--json", action="store_true")
    commands = parser.add_subparsers(dest="command", required=True)
    add = commands.add_parser("add", parents=[common], help="import a YAML configuration")
    add.add_argument("source")
    commands.add_parser("list", parents=[common], help="list imported configurations")
    use = commands.add_parser("use", parents=[common], help="switch Codex or Claude Code configuration")
    use.add_argument("source", help="ID or YAML path")
    use.add_argument("--target", choices=("codex", "claude"))
    use.add_argument("--target-dir")
    use.add_argument("--dry-run", action="store_true")
    test = commands.add_parser("check", parents=[common], help="run ModelTrace fingerprint analysis")
    test.add_argument("source", help="ID or YAML path")
    test.add_argument("--target", choices=("codex", "claude"))
    test.add_argument("--format", dest="api_format", choices=("auto", "openai", "anthropic", "responses"))
    test.add_argument("--timeout", type=positive_number, default=60)
    test.add_argument("--attempts", type=attempt_count, default=6)
    test.add_argument("--temperature", type=float)
    return parser


def main(argv=None):
    args = make_parser().parse_args(argv)
    config = None
    try:
        directory = Path(args.config_dir).expanduser()
        if args.command == "list":
            result = [dict(summary(read_config(path)), id=identity)
                      for identity, path in sorted(configurations(directory).items())]
        else:
            config = read_config(args.source) if args.command == "add" else source_config(args.source, directory)
            if args.command == "add":
                resolve_target(config)
                identity = max(configurations(directory), default=0) + 1
                while True:
                    destination = directory / f"{identity}_config.yaml"
                    try:
                        atomic_write(destination, yaml.safe_dump(config, allow_unicode=True, sort_keys=False), exclusive=True)
                        break
                    except FileExistsError:
                        identity += 1
                result = dict(summary(config), id=identity, path=str(destination))
            elif args.command == "use":
                result = switch(config, args.target_dir, args.target, args.dry_run)
            else:
                if args.temperature is not None and (not math.isfinite(args.temperature) or not 0 <= args.temperature <= 2):
                    raise ConfigError("temperature must be a finite number between 0 and 2")
                result = check(config, args)
        if args.json:
            print(json.dumps(result, ensure_ascii=False))
        elif isinstance(result, list):
            for item in result:
                print(f"{item['id']} {item['name']} {item['type']} {item['base_url']} {item['model_name']}")
            if not result:
                print("No configurations found")
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except ConfigError as error:
        code, message = 2, str(error)
    except (yaml.YAMLError, tomlkit.exceptions.ParseError, UnicodeError, json.JSONDecodeError):
        code, message = 2, "invalid YAML, TOML, JSON or text encoding; existing files were preserved"
    except ValueError:
        code, message = 2, "invalid input value; existing files were preserved"
    except (OSError, RuntimeError) as error:
        code, message = 1, str(error)
    if config:
        secret = config.get("api_key") or os.environ.get(config.get("env_key", ""))
        if secret:
            message = message.replace(secret, "<redacted>")
    if args.json:
        print(json.dumps({"error": message, "exit_code": code}, ensure_ascii=False))
    else:
        print("Error: " + message, file=sys.stderr)
    return code
