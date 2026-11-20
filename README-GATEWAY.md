# FastAPI 网关部署

适用于 A100 服务器上的 Qwen3-8B。客户端通过 SSH 隧道访问 FastAPI 网关 `127.0.0.1:9000`，网关将请求转发到 vLLM `127.0.0.1:8000`。每位使用者持有一把独立的 Bearer API Key。

## 1. 同步代码

把 `llm-inference-gateway.zip` 上传到服务器的 `/root/llm-inference-optimization/`。如服务器上的同名网关文件已有修改，先备份，再解压。也可用 Git 同步这次网关改动。

```bash
cd /root/llm-inference-optimization
unzip -l llm-inference-gateway.zip
unzip -o llm-inference-gateway.zip
```

项目现有的 `.gitignore` 已忽略 `.env`、日志、模型权重和 ZIP。若服务器上的 Git 忽略规则来自旧版本，请确认 `.env`、`.env.*`、`/.tmp_*` 和 `logs/` 已被忽略。密钥通过私密渠道分发。

## 2. 安装依赖和生成密钥

在服务器上使用已有的 `llm-gateway` Conda 环境。网关要求 Python 3.10 或以上。

```bash
conda activate llm-gateway
/root/miniconda3/envs/vllm/bin/uv pip install \
  --python "$(command -v python)" \
  -r requirements-gateway.txt \
  --index-url https://pypi.org/simple
python -c 'import fastapi, uvicorn, httpx, dotenv; print("OK")'
python scripts/manage_keys.py init alice bob charlie
chmod 600 .env
```

如果 `llm-gateway` 环境还未创建，先执行 `conda create -n llm-gateway python=3.11`。如果原来的 `uv` 路径不可用，在激活环境后执行 `python -m pip install -r requirements-gateway.txt`。

`init` 会在终端显示每个人的密钥一次，只将 SHA-256 摘要写入项目根目录的 `.env`。为每个人使用独立名称，支持 1–64 个 ASCII 字母、数字、短横线或下划线。后续增发或撤销后，重启网关使改动生效。

```bash
python scripts/manage_keys.py add student4
python scripts/manage_keys.py revoke bob
```

默认上游为 `http://127.0.0.1:8000`。如需指定其他上游，在 `.env` 中设置 `VLLM_BASE_URL`，填写协议、主机和端口。如果上游也有自己的鉴权，在 `.env` 设置独立的 `VLLM_API_KEY`。

## 3. 启动网关

先在服务器运行 `curl -fsS http://127.0.0.1:8000/v1/models`，确认列表里有 `Qwen3-8B`。若 vLLM 还未启动，在一个终端运行 `bash scripts/start_qwen3_8b.sh`，等待模型加载。

在另一个服务器终端运行：

```bash
cd /root/llm-inference-optimization
conda activate llm-gateway
bash scripts/start_gateway.sh
```

`GET /healthz` 检查网关进程，`GET /ready` 检查 vLLM 就绪状态。`logs/gateway.log` 记录已鉴权请求的使用者、路径、状态码、耗时及 vLLM 返回的输入和输出 Token 数。对于流式请求，网关默认向 vLLM 请求最终用量事件，显式设置 `stream_options.include_usage=false` 时保留该设置。上游省略用量或连接提前断开时，缺失的 Token 计数显示为 `-`。

## 4. 在服务器验收

```bash
curl -fsS http://127.0.0.1:9000/healthz
curl -fsS http://127.0.0.1:9000/ready
read -r -s -p "API Key: " GW_API_KEY
echo
curl -fsS http://127.0.0.1:9000/v1/models \
  -H "Authorization: Bearer $GW_API_KEY"
curl -fsS http://127.0.0.1:9000/v1/chat/completions \
  -H "Authorization: Bearer $GW_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"model":"Qwen3-8B","messages":[{"role":"user","content":"你好，请介绍一下自己"}],"max_tokens":64,"chat_template_kwargs":{"enable_thinking":false}}'
unset GW_API_KEY
```

预期 `/ready` 返回 `{"status":"ready"}`，模型列表里有 `Qwen3-8B`，推理响应里有 `choices`。验证流式输出时，给最后一条 `curl` 加上 `-N`，并在请求 JSON 顶层加上 `"stream":true`。终端会陆续显示 `data:` 事件，最后收到 `data: [DONE]`。在 `logs/gateway.log` 查找本次请求的 `total_tokens`。

## 5. Windows 接入

在 PowerShell 运行以下命令，把 `gpu-server` 替换为服务器的 SSH 别名，保持隧道终端开启。

```powershell
ssh -N -o ExitOnForwardFailure=yes -L 9000:127.0.0.1:9000 gpu-server
```

OpenAI 兼容客户端的 Base URL 填 `http://127.0.0.1:9000/v1`，模型填 `Qwen3-8B`，API Key 填各自收到的密钥。服务器上的两个服务均绑定到回环地址。每个人使用自己的 SSH 访问权限建立隧道。

## 本地测试

Windows PowerShell 可用项目内虚拟环境运行模拟测试。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-gateway.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```
