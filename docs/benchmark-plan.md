# vLLM 性能基线与优化实验计划

实验环境固定为 RTX 3090 24 GB、vLLM `0.14.0` 和本地 ModelScope 下载的 `Qwen2.5-3B-Instruct`。第一轮使用 `half`、`max_model_len=4096`、`gpu_memory_utilization=0.75`，并记录启动日志中实际生效的缓存和调度配置。

## 1. 建立直连基线

在 GPU 服务器本机直连 vLLM 的 `/v1/completions`，固定输入、输出和采样参数。网关透传验证使用 `/v1/chat/completions`。后续对比有无网关时，两组都用 `/v1/chat/completions` 和完全相同的请求，避免模板和协议差异混入结果。

先在仓库根目录激活环境、检查模型列表并创建本轮结果目录。

```bash
source .venv/bin/activate
curl -fsS http://127.0.0.1:8000/v1/models
vllm bench serve --help
model_dir="$PWD/models/Qwen2.5-3B-Instruct"
run_dir="$PWD/results/$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$run_dir"
```

按下文在另一终端启动 GPU 采样后，回到原终端执行单点实验。`--model` 指向本地权重和 tokenizer 所在目录，`--served-model-name` 指定发给服务的模型 ID。把 `BENCH_PROMPTS` 改为 `8` 可先做一次冒烟测试。 [vLLM 0.14.0 Benchmark 参数](https://docs.vllm.ai/en/v0.14.0/cli/bench/serve/)

```bash
vllm bench serve \
  --backend openai --base-url http://127.0.0.1:8000 --endpoint /v1/completions \
  --model "$model_dir" --served-model-name Qwen2.5-3B-Instruct \
  --dataset-name random --seed 42 --random-range-ratio 0 \
  --random-input-len "${BENCH_INPUT_LEN:-512}" \
  --random-output-len "${BENCH_OUTPUT_LEN:-128}" \
  --random-prefix-len "${BENCH_PREFIX_LEN:-0}" --ignore-eos --temperature 0 \
  --num-warmups 8 --num-prompts "${BENCH_PROMPTS:-256}" \
  --request-rate "${BENCH_REQUEST_RATE:-inf}" \
  --max-concurrency "${BENCH_CONCURRENCY:-8}" \
  --percentile-metrics ttft,tpot,itl,e2el --metric-percentiles 50,95,99 \
  --save-result --result-dir "$run_dir" --result-filename benchmark.json \
  --metadata gpu=RTX3090 vllm=0.14.0 dtype=half max_model_len=4096 gpu_memory_utilization=0.75
```

每次重跑时创建新的 `run_dir`。切换并发点时设置 `BENCH_CONCURRENCY`。`request-rate=inf` 时，客户端尽快发起请求，最多同时保留 `max-concurrency` 个在途请求。测试固定到达速率时，设置有限请求速率，并将客户端并发上限设得足够大，同时记录实际吞吐和服务端排队。 [vLLM 0.14.0 Benchmark 参数](https://docs.vllm.ai/en/v0.14.0/cli/bench/serve/)

| 顺序 | 变量与取值 | 固定条件及目的 |
| --- | --- | --- |
| 1 | 并发 `1 / 4 / 8 / 16 / 32`，资源稳定时追加 `64` | 输入 512、输出 128，寻找吞吐增长放缓和尾延迟上升的点 |
| 2 | 输入 Token `128 / 512 / 1024 / 2048 / 3072` | 并发 8、输出 128，观察 Prefill 成本与 TTFT |
| 3 | 输出 Token `64 / 128 / 256 / 512` | 并发 8、输入 512，观察 Decode 的 TPOT、ITL 与输出吞吐 |
| 4 | 固定到达速率，取约 `50% / 80% / 100% / 120%` 的已测饱和请求吞吐 | 输入 512、输出 128，观察排队、失败率和 P99 变化 |

一次仅改变表中的目标变量。固定预热方式、请求样本顺序与随机种子。每个配置至少重复 3 次，汇总各次中位数和波动范围。每点 256 个请求用于筛选，关键点增加到至少 1024 个请求后再解读 P99，并记录实际的输入与输出 Token 数。所有请求使用的总上下文应落在 `4096` 以内。

## 2. 采集指标和保存证据

- **客户端**保存 TTFT、TPOT、ITL、端到端延迟的 P50/P95/P99，以及成功数、失败率、请求吞吐（req/s）和输出 Token 吞吐（tok/s）。总 Token 吞吐也可保留，图表中标明口径。
- **GPU**在压测开始前启动采样，压测结束后停止。记录空闲显存、压测峰值显存和利用率时间序列。下方把 `run_dir` 设置成和当前压测相同的目录。

  ```bash
  nvidia-smi --query-gpu=timestamp,index,utilization.gpu,memory.used,memory.total \
    --format=csv,nounits -l 1 > "$run_dir/gpu.csv"
  ```

- **vLLM**在每轮前后保存 `curl -fsS http://127.0.0.1:8000/metrics` 的快照。压测期间定时采集运行及排队请求数、KV Cache 使用率和抢占次数。比较 Prefix Caching 时记录命中及查询计数的增量。 [vLLM 0.14.0 指标定义](https://docs.vllm.ai/en/v0.14.0/design/metrics/)
- **复现信息**与结果一起保存代码提交、模型来源和版本、PyTorch 版本、GPU 驱动、完整启动命令、采样参数和测试时段。`--metadata` 中的字段需要随实际配置更新。失败或 OOM 的配置也记入结果表。原始文件保存在 `results/`，公开报告记录汇总、运行条件和实验结论。

## 3. 开展优化对照实验

| 实验 | 对照设置 | 观察指标 |
| --- | --- | --- |
| Prefix Caching | 同一组随机请求，固定共享前缀 1024 Token 和随机后缀 256 Token，比较 `--no-enable-prefix-caching` 与 `--enable-prefix-caching`。每组用相同请求预热，再分别用无共享前缀的请求做对照。 | TTFT、输入吞吐、缓存命中率、TPOT |
| Chunked Prefill | 固定 `--max-num-batched-tokens 8192`，比较 `--no-enable-chunked-prefill` 与 `--enable-chunked-prefill`。启用后再单独比较批处理预算 `512 / 2048 / 4096`，先测试长输入，再用同一组短长混合请求复核。 | 长短请求各自的 TTFT、ITL、吞吐及排队 |
| Continuous Batching 与调度 | 固定批处理 Token 预算，在并发 16 和 32 上分别比较 `--max-num-seqs 4 / 8 / 16 / 32`。 | 运行和等待请求数、批处理吞吐、尾延迟 |
| PagedAttention 与 KV Cache | 固定模型、精度、并发与长度，比较 `VLLM_GPU_MEMORY_UTILIZATION=0.65 / 0.75 / 0.85`，结合启动日志里的 KV Cache 容量，分析同一压测负载下的内存压力。 | 可用 KV Cache、峰值显存、抢占、失败率、吞吐 |

切换服务端参数时在同一张 GPU 上顺序启动各组。缓存实验记录每一轮的冷启动或预热状态。若需测冷缓存首请求，重启后单独测量首个请求，再按一致的预热流程采集稳态数据。若某组无法启动，保存错误和完整配置。PagedAttention 通过容量及运行指标分析，避免把显存比例的差异归因到注意力算法。 [vLLM 0.14.0 启动参数](https://docs.vllm.ai/en/v0.14.0/cli/serve/) · [优化与调优](https://docs.vllm.ai/en/v0.14.0/configuration/optimization/)

基线通过后，将命令块整理成参数化压测脚本。用吞吐量与 P95/P99 延迟曲线定位饱和点，再接入 Prometheus 与 Grafana 复现同一轮实验的队列长度、KV Cache 使用率和 GPU 指标。模型规模、量化及 Docker 部署在单模型对照组建立后推进。
