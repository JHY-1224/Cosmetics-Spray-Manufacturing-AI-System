> 2026-10-28最新发布状态：[微调后交付](FINETUNED_RELEASE_20261028.md)、[Mac/GitHub/云端/Gitee工作流程](SOURCE_AND_RELEASE_WORKFLOW.md)。v6相关开发回归89/96，正式5001仍v3；下文保留历史阶段。新公开快照不含许可未核实的旧74图包，训练示例涉及该包时须另有合法本地数据。

# 云端开发与历史训练操作手册

> 客户部署已训练结果请直接读[Gitee结果版使用说明](GITEE_RESULT_README.md)。本手册以下训练、微调及旧阶段命令是开发记录，客户运行结果版不需要执行。

## 固定位置

```text
/root/autodl-tmp/code/Cosmetics-Spray-Manufacturing-AI-System/  项目代码
/root/autodl-tmp/models/Qwen3.5-9B/                          原始文本基座
/root/autodl-tmp/models/Qwen3-VL-8B-Instruct/                 原始视觉基座
/root/autodl-tmp/models/services/qwen-light/
  text/<run_id>/adapter/                                    9B训练产物
  vision/<run_id>/adapter/                                  8B训练产物
  service.env                                              两组件运行配置
  text-verification.json                                   文本产物核验
  vision-verification.json                                 视觉产物核验
```

代码和模型分开保存。Git不包含基座、训练权重、密钥或客户私有资料。完整74图开发包随仓库根目录的归档分发。
前端只登记一个“Qwen轻量级服务”，与阿里云并列。内部先由8B读图，再将结构化证据交给9B；纯文本直接走9B。
这与参考医疗项目的VL→LLM调用顺序相同，不是将两个权重文件合并成一个神经网络。

## 1. 进入代码和已有环境

```bash
cd /root/autodl-tmp/code/Cosmetics-Spray-Manufacturing-AI-System
# 本次已验证的训练环境；不要用 base Python 代替
source /root/autodl-tmp/envs/cosmetics-unsloth/bin/activate
```

使用你已经配置的Unsloth环境执行训练；vLLM环境执行推理；Web使用独立轻量环境。
先检查 `python -V`、`python -m pip show unsloth transformers trl torch` 和 `nvidia-smi`。
这里不要求重新下载模型或安装另一套CUDA/PyTorch。若补装依赖，先核对锁文件、官方源与国内镜像；pip镜像不会自动改变uv锁文件或模型下载源。

对已有基座离线校验，不下载：

```bash
python day1/0-download-model/1-download.py --verify-only --model-root /root/autodl-tmp/models
```

校验需要顺序读取约36.9GB文件。若与仓库冻结的官方清单不一致，停止并核对版本，保留原文件；不清空缓存或重新覆盖下载。

## 2. 数据与训练

文本36条在 `day1/2-prepare-data/train.messages.jsonl`。视觉公开开发集在 `data/vision-public/metadata.jsonl`。
完整74张图片和82条问答已包含在仓库根目录 `qwen-vl-full-20261026.tar.gz`，git clone会一并下载，无需再手工上传。先按下方“完整训练包解压”校验并解压到 `local_training/qwen-vl-20261026/`。其中54图保留许可未核实标记，不适用代码MIT许可证。

先检查，不启动GPU训练：

```bash
python day1/3-train/2-train-9b.py --model-dir /root/autodl-tmp/models/Qwen3.5-9B --dry-run
python day1/multimodal/train_vl.py --data local_training/qwen-vl-20261026/metadata.jsonl --allow-unreviewed --dry-run
```

在同一张4090上先后训练，上一条完成后再执行下一条；不要同时运行模型服务。

```bash
python day1/3-train/2-train-9b.py --model-dir /root/autodl-tmp/models/Qwen3.5-9B --output-root /root/autodl-tmp/models/services/qwen-light/text

# 第一次先跑1步CUDA冒烟；成功后去掉 --max-steps 1 生成新的正式开发run
python day1/multimodal/train_vl.py --data local_training/qwen-vl-20261026/metadata.jsonl --allow-unreviewed --output-root /root/autodl-tmp/models/services/qwen-light/vision --max-steps 1

# 冒烟成功后，运行完整1 epoch开发微调，输出到全新run
python day1/multimodal/train_vl.py --data local_training/qwen-vl-20261026/metadata.jsonl --allow-unreviewed --output-root /root/autodl-tmp/models/services/qwen-light/vision
```

所有run独立保存，不覆盖基座或前一次run。开发集规模小，不能把loss下降等同现场诊断有效。当前不自动导出两个BF16 merged模型；截图所示剩余37GB空间不足以宽裕保存双份权重和训练缓存。

## 3. 登记一个服务

把下面两个adapter路径替换为训练结束打印的真实路径。登记不会启动GPU，也不会把未验证模型显示为可用。

```bash
python assistant_app/scripts/configure_qwen_light.py \
  --text-adapter /root/autodl-tmp/models/services/qwen-light/text/TEXT_RUN/adapter \
  --vision-adapter /root/autodl-tmp/models/services/qwen-light/vision/VISION_RUN/adapter
```

生成 `service.env` 和 `assistant_app/configs/model_profiles/qwen-light.json`。后者只有一个前端选项，同时绑定两组件、专用服务别名和核验文件。
Aliyun私有配置继续使用原 `.env`，不要把云端密钥写进模型profile或Git。

## 4. 运行时核验

先在Unsloth环境验证训练产物确实加载了adapter：

```bash
python day1/4-validation/3-test-adapter.py --adapter /实际/TEXT_RUN/adapter --base-dir /root/autodl-tmp/models/Qwen3.5-9B --report-dir /root/autodl-tmp/models/services/qwen-light/text-runtime --limit 1 --run
python day1/multimodal/verify_vl.py --adapter /实际/VISION_RUN/adapter --data local_training/qwen-vl-20261026/metadata.jsonl --output /root/autodl-tmp/models/services/qwen-light/vision-runtime.json
```

文本使用既有 `3-test-adapter.py --run` 生成的 `environment.json`。两个产物分别登记核验：

```bash
python assistant_app/scripts/check_models.py --env-file /root/autodl-tmp/models/services/qwen-light/service.env --hash --kind text --runtime-evidence /root/autodl-tmp/models/services/qwen-light/text-runtime/environment.json --output /root/autodl-tmp/models/services/qwen-light/text-verification.json
python assistant_app/scripts/check_models.py --env-file /root/autodl-tmp/models/services/qwen-light/service.env --hash --kind vision --runtime-evidence /root/autodl-tmp/models/services/qwen-light/vision-runtime.json --output /root/autodl-tmp/models/services/qwen-light/vision-verification.json
```

文件校验、adapter运行时命中、HTTP响应和人工读图质量是不同检查，不能相互替代。

## 5. 启动vLLM与网页

参考项目是两个4B模型；这里是9B＋8B，不能照搬其显存参数后保证单4090同时运行两个BF16模型。
先分别验证当前vLLM版本对两基座、语言LoRA及选用量化方式的支持，并观察真实显存峰值。
`ASSISTANT_TEXT_QUANTIZATION` / `ASSISTANT_VISION_QUANTIZATION` 可显式指定 `bitsandbytes`、`awq` 或 `gptq`；后两项需要匹配的量化产物，不能给普通BF16权重随意加参数。

已有兼容性验证后，使用vLLM环境，显式设置 `CUDA_VISIBLE_DEVICES`、
`ASSISTANT_TEXT_GPU_MEMORY_UTILIZATION`、`ASSISTANT_VISION_GPU_MEMORY_UTILIZATION`（两项和≤0.9），
以及 `ASSISTANT_VLLM_LORA_VALIDATED=1`。这个开关是运维确认，不能代替真实验证。

```bash
python assistant_app/scripts/launch_qwen_light.py --env-file /root/autodl-tmp/models/services/qwen-light/service.env
```

它以前台方式管理5101文本和5102视觉两个vLLM子进程；任一退出会停止本次启动的另一个，Ctrl+C只结束本次子进程。
新版入口按顺序加载：文本端点返回配置中的精确模型身份后，再启动视觉端点；两个身份就绪后才报告就绪。
默认每个模型冷启动等待600秒，可用 `--startup-timeout` 显式调整。占用/相同端口、非本地端点、错误身份或子进程退出均拒绝继续，不结束其他进程。
两个入口监听127.0.0.1，配置端点须用127.0.0.1或localhost。入口保留模型generation EOS并加入tokenizer的聊天EOS，避免回答结束后继续生成虚构对话；通过绝对路径调用venv Python时也补齐该venv的可执行PATH。
这些启动检查不代替真实推理、质量、双模型驻留和重启负载验证；具体通过状态见[稳定性验收记录](QWEN_LIGHT_STABILITY_ACCEPTANCE.md)。
也可分别运行 `launch_model.py text` 和 `launch_model.py vision` 排查，不关闭其他GPU进程。

在Web环境构建 `assistant_app/frontend` 后运行：

```bash
python assistant_app/scripts/run_assistant.py --env-file assistant_app/.env
```

默认网页127.0.0.1:5001。保留Aliyun默认连接时，选择器读取同目录下新增profile；两份产物和两个端点均就绪后，Qwen轻量级服务才可选。
远程浏览先用SSH端口转发；公网发布使用真实账号网关、HTTPS和独立网关密钥，详见 `assistant_app/README.md`。
飞书仍复用同一Jobs流程，由独立凭据与白名单启用，不随Git发布。

首次构建网页（Node.js/pnpm已准备好时）：

```bash
cd assistant_app/frontend
pnpm install --frozen-lockfile
pnpm build
cd ../..
```

训练/vLLM环境与Web依赖分开；Web依赖固定在`assistant_app/requirements.txt`，飞书扩展在`requirements-feishu.txt`。
若缺依赖，先核对镜像与锁定版本，再在对应环境安装；不要将Web全部依赖强行覆盖进已配置好的CUDA环境。

## 完整训练包解压与旧项目处理

仓库根目录已附`qwen-vl-full-20261026.tar.gz`，含全部74张图和82条标签，不含基座和凭据。
已克隆旧版的用户先执行 `git pull --ff-only`。在项目根目录依次执行；校验失败时停止。`--keep-old-files`保护已经解压后修改的文件，若提示目标已存在，不要强行覆盖：

```bash
sha256sum -c qwen-vl-full-20261026.tar.gz.sha256
mkdir -p local_training
tar --keep-old-files -xzf qwen-vl-full-20261026.tar.gz -C local_training
python day1/multimodal/train_vl.py --data local_training/qwen-vl-20261026/metadata.jsonl --allow-unreviewed --dry-run
```

旧项目不影响新仓库克隆。若确认旧目录里没有需要保留的代码、训练产物、数据和配置，可只删除用户指定目录；删除后未备份内容不可恢复，其他models/envs目录不受影响：

```bash
test -d /root/autodl-tmp/code/fine-tuning-project2-v4 && rm -rf -- /root/autodl-tmp/code/fine-tuning-project2-v4
```

本项目不会自动运行该删除命令。

## 当前验证范围

本机已验证数据合同、原图哈希、路径约束、来源分组和服务目录逻辑。2026-10-26云端已验证Unsloth导入、CUDA autograd及8B权重加载；首次冒烟在训练器初始化时因旧 VisionRows 类型被拒绝，尚未完成优化器步骤。修复后的HF Dataset接口已在相同云端datasets 4.8.5上检查全部82条图片/答案与批量取样。真实训练及双模型vLLM容量仍待验收。

### 旧版 VisionRows 报错

遇到 `train_dataset must be a Dataset or IterableDataset, got VisionRows`，在项目根目录执行 `git pull --ff-only`，然后重跑上面的8B单步冒烟命令。新版使用HF Dataset和按需图片转换。无需重装依赖、重新下载基座、重解压数据或修改unsloth_compiled_cache；失败run原样保留，新运行另建目录。成功须以训练结束打印 `Saved vision adapter:` 为准，权重加载到100%还不代表完成训练。
