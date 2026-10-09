### 最简cc-switch   
目前cc-switch-cli只能基于tui配置，操作比较麻烦。    
实现了一个简单版，一键导入配置，增加antropic和openai中转。    

## 安装

```bash
chmod +x cc-simple.py
# 可选：创建软链接到 PATH
ln -s $(pwd)/cc-simple.py /usr/local/bin/cc-simple
```

## 使用

### 1. 添加配置    
```bash
$ python3 cc-simple.py add conf.yaml

# OpenAI 兼容 API 配置示例
$ cat conf.yaml
name=xingshu
base_url=https://submodelhub.ilaien.com/v1
type=openai
api_key=sk-xxxx
model_name=gpt-4

# Anthropic API 配置示例
$ cat anthropic.yaml
name=anthropic-official
base_url=https://api.anthropic.com
type=anthropic
api_key=sk-ant-api03-xxxx
model_name=claude-opus-5-5
```

配置字段说明：
- `name` - 配置名称（必填）
- `base_url` - API 端点 URL（必填）
- `type` - 提供商类型：`openai` 或 `anthropic`（必填）
- `api_key` - API 密钥（必填）
- `model_name` - 模型名称（可选，默认：openai=gpt-4, anthropic=claude-opus-5-5）


### 2. 查看所有配置   
```bash
$ python3 cc-simple.py list
1 sbbbbbb https://fast.sbbbbbbbbb.xyz/v1
2 anthropic-proxy https://api.anthropic.com
```

### 3. 切换配置   
```bash
$ python3 cc-simple.py use 1
Backed up existing config to /root/.codex/config.toml.bak
Switched to configuration: sbbbbbb (ID: 1)
Provider type: openai
Base URL: https://fast.sbbbbbbbbb.xyz/v1
```
