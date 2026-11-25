# FastAPI 网关部署

客户端通过 SSH 隧道访问 FastAPI 网关 `127.0.0.1:9000`，网关将请求转发到 vLLM `127.0.0.1:8000`。每位使用者持有一把独立的 Bearer API Key。当前示例使用 RTX 3090 上的 `Qwen2.5-3B-Instruct`，原 Qwen3-8B 部署也使用相同网关。客户端从 `/v1/models` 读取模型 ID，并在请求中指定该 ID。

## 已部署网关升级密钥热加载

在已通过 Git 同步的服务器上，切到项目的 `main` 分支后拉取更新：

```bash
cd /root/llm-inference-optimization
git pull --ff-only origin main
```

也可以把 `gateway-live-keys.zip` 上传到项目目录，备份同名文件后执行 `unzip -o gateway-live-keys.zip`。

已有 `.env` 可继续使用。同步后重启一次 FastAPI 网关以载入新版。使用矩池云 HTTP 9000 端口转发时，沿用公网部署的监听方式：

```bash
cd /root/llm-inference-optimization
conda activate llm-gateway
mkdir -p logs
python -m uvicorn gateway.main:app --host 0.0.0.0 --port 9000 \
  2>&1 | tee -a logs/gateway.log
```

以后可在另一个终端运行 `python scripts/manage_keys.py add student1 student2 student3` 一次发放多把密钥。命令完成后，同学的下一次 API 请求就可使用新密钥。撤销也在下一次鉴权时生效。

## 1. 同步代码

在服务器的 `/root/llm-inference-optimization/` 同步仓库代码。使用离线包同步时，上传与目标版本对应的 `llm-inference-gateway.zip`，并在解压前备份服务器上的同名本地改动。

```bash
cd /root/llm-inference-optimization
unzip -l llm-inference-gateway.zip
unzip -o llm-inference-gateway.zip
```

项目现有的 `.gitignore` 已忽略 `.env`、日志、模型权重和 ZIP。若服务器上的 Git 忽略规则来自旧版本，请确认 `.env`、`.env.*`、`/.tmp_*` 和 `logs/` 已被忽略。密钥通过私密渠道分发。

## 2. 安装依赖和生成密钥

网关要求 Python 3.10 或以上。在服务器上激活网关环境。已有 `llm-gateway` Conda 环境时可以继续使用。首次部署可以单独创建 `.venv-gateway`，避免改动推理引擎的 Python 依赖。

```bash
python3 -m venv .venv-gateway
source .venv-gateway/bin/activate
```

```bash
python -m pip install -r requirements-gateway.txt
python -c 'import fastapi, uvicorn, httpx, dotenv; print("OK")'
python scripts/manage_keys.py init alice bob charlie
chmod 600 .env
```

`init` 会在终端显示每个人的密钥一次，只将 SHA-256 摘要写入项目根目录的 `.env`。为每个人使用独立名称，支持 1–64 个 ASCII 字母、数字、短横线或下划线。也可以先启动网关，再执行 `init`。初始化之前，推理接口返回 `401`。增发或撤销后，下一次 API 请求会使用更新后的密钥配置。

```bash
python scripts/manage_keys.py add student4 student5
python scripts/manage_keys.py revoke bob
```

默认上游为 `http://127.0.0.1:8000`，可直接连接当前 3B 实例。若 vLLM 监听其他地址或端口，在 `.env` 中设置 `VLLM_BASE_URL` 为上游源地址，例如 `http://127.0.0.1:8001`。客户端 Base URL 则包含 `/v1`。如果上游也有自己的鉴权，在 `.env` 设置独立的 `VLLM_API_KEY`。改动上游连接配置后重启网关即可生效。

## 3. 启动网关

先在服务器运行 `curl -fsS http://127.0.0.1:8000/v1/models`，确认列表里有 `Qwen2.5-3B-Instruct`。若 vLLM 还未启动，按 [模型部署说明](README.md#启动-qwen25-3b) 启动。原 Qwen3-8B 部署可以沿用 `bash scripts/start_qwen3_8b.sh`。

在另一个服务器终端运行：

```bash
cd /root/llm-inference-optimization
source .venv-gateway/bin/activate
bash scripts/start_gateway.sh
```

如果使用 Conda 环境，改用 `conda activate llm-gateway`。启动脚本读取 shell 环境中的 `GATEWAY_HOST` 和 `GATEWAY_PORT`，默认值为 `127.0.0.1` 和 `9000`。修改监听端口后，验收命令和 SSH 隧道的远端端口也应同步调整。

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
  -d '{"model":"Qwen2.5-3B-Instruct","messages":[{"role":"user","content":"你好，请介绍一下自己"}],"max_tokens":64}'
curl -fsSN http://127.0.0.1:9000/v1/chat/completions \
  -H "Authorization: Bearer $GW_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"model":"Qwen2.5-3B-Instruct","messages":[{"role":"user","content":"你好，请介绍一下自己"}],"max_tokens":64,"stream":true}'
unset GW_API_KEY
```

预期 `/ready` 返回 `{"status":"ready"}`，模型列表里有 `Qwen2.5-3B-Instruct`，非流式响应里有 `choices`，流式响应以 `data: [DONE]` 结束。在 `logs/gateway.log` 查找本次请求的 `total_tokens`。调试 Qwen3-8B 时可将 `model` 改为其 `/v1/models` 中的 ID，并按需传入 `"chat_template_kwargs":{"enable_thinking":false}`。

## 5. Windows 接入

在 PowerShell 运行以下命令，把 `gpu-server` 替换为服务器的 SSH 别名，保持隧道终端开启。

```powershell
ssh -N -o ExitOnForwardFailure=yes -L 9000:127.0.0.1:9000 gpu-server
```

OpenAI 兼容客户端的 Base URL 填 `http://127.0.0.1:9000/v1`，模型填 `Qwen2.5-3B-Instruct`，API Key 填各自收到的密钥。使用 SSH 隧道时，服务器上的两个服务都可以绑定到回环地址。每个人使用自己的 SSH 访问权限建立隧道。

## 本地测试

Windows PowerShell 可用项目内虚拟环境运行模拟测试。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-gateway.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```
