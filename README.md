# 基于 vLLM 的大模型推理部署与性能优化

本项目面向 AI Infra，使用 vLLM 提供 OpenAI 兼容的推理服务，通过 FastAPI 网关提供独立 API Key，并以可复现的基准测试分析吞吐量、延迟和显存占用。

## 当前部署基线

| 项目 | 服务器记录 |
| --- | --- |
| GPU | NVIDIA GeForce RTX 3090，24 GB（24576 MiB） |
| 驱动与 CUDA | NVIDIA Driver `580.76.05`，驱动支持 CUDA `13.0` |
| 系统与环境 | Ubuntu 22.04，Python `3.12.4`，vLLM `0.14.0`，虚拟环境 `.venv` |
| 项目目录 | `/root/llm-inference-optimization`，服务器磁盘空间约 200 GB |
| 模型 | `Qwen2.5-3B-Instruct`，权重约 5.8 GiB，来源为 [ModelScope](https://modelscope.cn/models/qwen/Qwen2.5-3B-Instruct/summary) |
| 权重目录 | `/root/llm-inference-optimization/models/Qwen2.5-3B-Instruct` |
| 推理 API | `http://127.0.0.1:8000/v1`，`half`，上下文上限 `4096`，显存利用率参数 `0.75` |

现有服务器已完成模型启动和直连推理验证。空闲时观测到 19124 MiB 显存占用、0% GPU 利用率。这是该部署的一次空闲快照，负载下的吞吐量、延迟和显存峰值待基准测试获取。`nvidia-smi` 显示的 CUDA 版本是驱动支持版本。新服务器安装时按版本化文档选择匹配的 PyTorch 和 vLLM 预编译包。 [vLLM 0.14.0 GPU 安装文档](https://docs.vllm.ai/en/v0.14.0/getting_started/installation/gpu/)

## 启动 Qwen2.5-3B

在已安装 vLLM 0.14.0 并下载完模型的 GPU 服务器上执行：

```bash
cd /root/llm-inference-optimization
.venv/bin/vllm --version
bash scripts/start_qwen2_5_3b.sh
```

启动脚本在仓库根目录查找模型和虚拟环境，日志写入 `logs/qwen2.5-3b.log`。如果旧进程仍占用 `8000`，可以直接执行下方验收，也可以在维护窗口停掉旧进程后用脚本启动。

新机器如需下载权重，激活虚拟环境并安装 `modelscope` 后，在仓库根目录执行：

```bash
modelscope download --model Qwen/Qwen2.5-3B-Instruct \
  --local_dir "$PWD/models/Qwen2.5-3B-Instruct"
```

下载后可记录 ModelScope revision，便于在新机器上复现同一版模型。 [ModelScope CLI 文档](https://github.com/modelscope/modelscope/blob/master/docs/source/command.md)

### 模型配置

以下变量可通过 shell 环境覆盖。相对路径以仓库根目录为准。

| 变量 | 默认值 |
| --- | --- |
| `VLLM_BIN` | `.venv/bin/vllm` |
| `VLLM_MODEL_PATH` | `models/Qwen2.5-3B-Instruct` |
| `VLLM_SERVED_MODEL_NAME` | `Qwen2.5-3B-Instruct` |
| `VLLM_HOST` / `VLLM_PORT` | `127.0.0.1` / `8000` |
| `VLLM_DTYPE` | `half` |
| `VLLM_MAX_MODEL_LEN` | `4096` |
| `VLLM_GPU_MEMORY_UTILIZATION` | `0.75` |

命令行参数可传给 `vllm serve`，例如显式开启 Prefix Caching。

```bash
VLLM_PORT=8001 bash scripts/start_qwen2_5_3b.sh --enable-prefix-caching
```

如需切换使用同一张 GPU 的实例，先结束原实例。仅更换端口仍会占用同一块卡的显存。

### 直连验收

在另一个服务器终端检查模型列表，然后发送聊天请求。

```bash
curl -fsS http://127.0.0.1:8000/v1/models
curl -fsS http://127.0.0.1:8000/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"Qwen2.5-3B-Instruct","messages":[{"role":"user","content":"你好，请介绍一下自己"}],"max_tokens":64}'
```

模型列表应包含 `Qwen2.5-3B-Instruct`，响应应包含 `choices`。流式验收时给第二条 `curl` 加上 `-N`，并在 JSON 顶层加入 `"stream":true`。

## 仓库导航与进度

| 模块 | 当前进度 |
| --- | --- |
| 模型部署 | Qwen2.5-3B 为当前基线。早期 A100 部署入口见 [Qwen3-8B 脚本](scripts/start_qwen3_8b.sh)。 |
| FastAPI 网关 | 支持鉴权、密钥热加载、异步转发、SSE、用量日志和健康检查。部署与 3B 端到端验收见 [网关部署](README-GATEWAY.md)。 |
| 性能基准与优化 | [实验计划](docs/benchmark-plan.md) 包含直连基准命令、并发与长度矩阵，以及缓存、调度、显存对照方案。实测结果待采集。 |
| 监控及工程化 | Prometheus、Grafana、Docker 和 GitHub Pages 待建设。 |

模型权重、虚拟环境、`.env`、密钥文件、运行日志和原始压测结果保存在 Git 忽略的本地路径。公开结果提交经过整理的汇总表和实验说明。
