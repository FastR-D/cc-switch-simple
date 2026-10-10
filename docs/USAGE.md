# 使用说明

## 安装

Python 3.10+。Linux 安装命令见 [README](../README.md)；已有虚拟环境时可直接使用。`add`、`list`、`use` 只依赖 PyYAML 和 tomlkit，`check` 另需 ModelTrace。ModelTrace 安装在同一环境后，无需启动其网页服务。

Windows 在项目目录执行，将 `C:\path\to\ModelTrace` 替换为 ModelTrace 源码目录：

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install .
.\.venv\Scripts\python.exe -m pip install C:\path\to\ModelTrace
.\.venv\Scripts\cc-simple.exe --help
```

如果已拿到两个 wheel，也可在同一虚拟环境安装：

```sh
python -m pip install modeltrace-0.1.0-py3-none-any.whl cc_switch_simple-0.2.1-py3-none-any.whl
```

安装后使用该环境的 `cc-simple` 命令。原入口 `python cc-simple.py` 仍可用。

## 配置字段

| 字段 | 含义 |
| --- | --- |
| `name` | 配置名称，必填 |
| `base_url` | HTTP(S) 接口地址，必填；不能包含凭据、查询参数或片段 |
| `type` | `openai` 或 `anthropic`，必填 |
| `api_key` | 接口密钥，与 `env_key` 二选一 |
| `env_key` | 保存密钥的环境变量名称，与 `api_key` 二选一 |
| `model_name` | 向接口请求的模型 ID，建议明确填写 |
| `target` | 可选，`codex` 或 `claude`，默认按 `type` 选择 |
| `api_format` | 可选，`auto`、`openai`、`anthropic` 或 `responses` |
| `temperature` | 可选，0 至 2；省略时不发送温度参数 |

`model_name` 省略时沿用旧版默认值：`openai` 为 `gpt-4`，`anthropic` 为 `claude-opus-5-5`。接口未必提供这些 ID，应按服务商实际模型填写。

使用环境变量时，将 YAML 的 `api_key` 换为 `env_key`：

```yaml
name: my-provider
base_url: https://api.example.com/v1
type: openai
env_key: MODEL_API_TOKEN
model_name: YOUR_MODEL_ID
```

在当前终端设置密钥：

```sh
export MODEL_API_TOKEN='YOUR_API_KEY'
```

```powershell
$env:MODEL_API_TOKEN = 'YOUR_API_KEY'
```

文件使用 UTF-8，支持 Windows UTF-8 BOM、YAML 锚点和 `<<` 合并。显式字段可覆盖合并默认值；重复显式字段、重复合并键、未知字段和无效配置会报错。

旧版逐行 `key=value` 文件仍可读取。旧字段 `model=密钥` 是 `api_key` 的别名，模型名称必须用 `model_name`。

## 导入与切换

`add` 将配置保存到 `~/.cc-simple/confs` 并分配数字 ID，`list` 按 ID 排序。配置名称不会用作文件路径。用 `--config-dir` 指定其他目录；旧版 `confs/` 可通过 `--config-dir ./confs` 使用。重复 ID 会报错。

`use` 和 `check` 接受数字 ID 或 YAML 路径：

```sh
cc-simple use provider.yaml --dry-run
cc-simple use provider.yaml
cc-simple check provider.yaml --json
```

`--dry-run` 校验输入与已有配置，返回目标摘要，不写文件。默认切换位置为：

| 类型 | 配置文件 |
| --- | --- |
| `openai` | `~/.codex/config.toml` |
| `anthropic` | `~/.claude/settings.json` |

切换保留 TOML 注释、其他 provider、项目配置、Claude permissions 等无关设置。Codex 的 `cc_simple` provider 会清除旧认证、headers 和 query 参数，保留 timeout、retry 等无关字段。Claude 会清除旧 `ANTHROPIC_AUTH_TOKEN` 和 `ANTHROPIC_MODEL`，写入新接口、密钥及模型。

已有文件在切换前保存为唯一备份，例如 `config.toml.bak.20261009T120000-a1b2c3d4`；新文件写完并刷新后原子替换。现有 TOML/JSON 无效时停止写入。恢复时先退出客户端，再用所需备份替换目标文件。

`api_key` 会保存在配置与备份中。Codex 使用 `env_key` 时保留变量名；Claude 使用 `env_key` 时解析当前变量值并写入 settings。Linux 新配置目录权限为 700，配置和备份为 600；Windows 沿用父目录 ACL。列表、普通结果及 dry-run 不展示密钥。

测试时可隔离配置目录和目标目录：

```sh
cc-simple --config-dir ./demo-store add provider.yaml
cc-simple --config-dir ./demo-store use 1 --target-dir ./demo-codex --dry-run
cc-simple --config-dir ./demo-store use 1 --target-dir ./demo-codex
cc-simple --config-dir ./demo-store list --json
```

## 协议与地址

Codex 使用 Responses，认证字段为 `env_key` 或 `experimental_bearer_token`。只有 Chat Completions 的接口可用 `check --format openai` 检测，但不能直接供 Codex 运行。

Claude 使用 Anthropic Messages 的标准 `x-api-key` 认证，检测与切换使用相同认证方式。只接受 Bearer token 的中转站需要提供标准 Messages 兼容认证。

若 Anthropic 模型通过 Responses 中转，可设置 `api_format: responses`，配合 `target: codex` 或 `--target codex`。服务商必须实际支持 Responses。

`use` 与 `check` 共用地址规范化，保留自定义前缀：

| 目标 | YAML 地址路径 | 写入客户端的路径 | 默认检测路径 |
| --- | --- | --- | --- |
| Codex | 裸域名、`/v1`、`/v1/responses` | `/v1` | `/v1/responses` |
| Codex | `/gateway/v1`、`/gateway/v1/responses` | `/gateway/v1` | `/gateway/v1/responses` |
| Codex | `/gateway`、`/gateway/responses` | `/gateway` | `/gateway/responses` |
| Claude | 裸域名、`/v1`、`/v1/messages` | 裸域名 | `/v1/messages` |
| Claude | `/gateway`、`/gateway/v1`、`/gateway/v1/messages` | `/gateway` | `/gateway/v1/messages` |

Codex 裸域名按 `/v1` 处理，无前缀的完整 `/responses`、`/chat/completions` 地址会被拒绝。Claude 完整端点需以 `/v1/messages` 结尾，只有 `/messages` 的地址会被拒绝。末尾斜线会去除。结果的 `base_url` 为客户端地址，`check` 的 `api_endpoint` 为实际请求端点。

## 指纹检测

`check` 调用 `modeltrace.check_api`，不修改客户端配置。默认最多尝试 6 个数字挑战，得到 3 份有效回答后分析；网络失败、鉴权失败或有效回答不足会报错。请求会消耗接口额度。

默认协议随目标选择：Codex 为 `responses`，Claude 为 `anthropic`；`--format auto` 也遵循该规则。可通过 `--format` 覆盖协议，其中 `openai` 指 Chat Completions，`responses` 指 Responses。覆盖协议后检测成功，只能证明该协议可用。

```sh
cc-simple check 1 --format openai --timeout 60 --attempts 6 --json
cc-simple check provider.yaml --format anthropic --temperature 0
```

`--timeout` 为每个挑战的请求时间预算，大于 0 且不超过 240 秒。`--attempts` 为 3 至 20。命令行的 `--temperature` 覆盖 YAML 设置。

JSON 结果字段：

| 字段 | 含义 |
| --- | --- |
| `analysis.results` | 候选模型排序 |
| `analysis.families` | 模型家族排序 |
| `analysis.used_outputs` | 有效回答数量 |
| `analysis.api_test` | 实际协议、尝试次数等检测信息 |

指纹结果基于候选样本的统计相似性，可辅助核验模型；排名与分数不能保证真实身份。

## Python 调用与退出码

配置读取和切换可调用 `cc_simple.read_config(path)`、`cc_simple.switch(config, target_dir=...)`。`switch` 返回配置摘要与备份路径，配置错误抛出 `cc_simple.ConfigError`。

指纹检测调用 `modeltrace.check_api`，成功返回分析字典；接口失败或有效回答不足抛出 `modeltrace.APIError`，输入错误抛出 `ValueError`。

| 退出码 | 含义 |
| --- | --- |
| `0` | 成功 |
| `2` | 输入、配置或格式错误 |
| `1` | API、网络或文件操作失败 |

`--json` 可放在子命令前或后；业务错误返回 JSON 的 `error`、`exit_code`。命令行参数语法错误由 argparse 在 stderr 提示。程序不回显请求密钥。

## 测试

在项目目录执行，测试使用临时目录和模拟接口：

```sh
python -m unittest discover -s tests -v
```
