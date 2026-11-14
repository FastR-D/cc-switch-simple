# cc-switch-simple

读取本地 YAML，切换 Codex 或 Claude Code 配置，用 `check` 调用 ModelTrace 检测模型指纹。

## 安装

需要 Python 3.10+。在项目目录执行：

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .
```

`check` 还需要安装 ModelTrace，将下面路径替换为其源码目录：

```sh
python -m pip install /path/to/ModelTrace
```

## 配置

保存为 `provider.yaml`：

```yaml
name: my-provider
base_url: https://api.example.com/v1
type: openai
api_key: YOUR_API_KEY
model_name: YOUR_MODEL_ID
```

`type` 为 `openai`（Codex）或 `anthropic`（Claude Code）。Codex 接口需支持 Responses。两种配置模板在 [conf_tmpl/](conf_tmpl/)。

## 使用

```sh
cc-simple add provider.yaml
cc-simple list
cc-simple use 1 --dry-run
cc-simple use 1
cc-simple check 1 --json
```

`use` 和 `check` 也可直接传 YAML 路径。切换前自动备份，保留已有无关配置。`check` 不修改客户端配置，会调用模型接口并消耗接口额度。

Windows 安装、环境变量和高级选项见 [使用说明](docs/USAGE.md)。
