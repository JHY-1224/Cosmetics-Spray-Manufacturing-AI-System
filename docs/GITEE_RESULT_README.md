# 涂镀智溯 · 微调后可运行交付版

这是已经完成微调的化妆品包材工艺文字与读图助手。**本仓库直接包含文字/视觉LoRA参数分片、已构建网页和统一启动程序；不需要再训练、微调或另找参数附件。**

## 1. 下载项目与两个官方基座

```bash
git clone https://gitee.com/jhy1224520/Cosmetics-Spray-Manufacturing-AI-System-finetuned.git
cd Cosmetics-Spray-Manufacturing-AI-System-finetuned
```

网页“克隆/下载 → 下载ZIP”同样包含 `pretrained/` 的8个参数分片，启动程序会自动合并、校验、解包，不用手动拼文件。

两个大型基座不随仓库重复分发。国内服务器优先使用官方ModelScope；两者合计约37GB。先准备Python3.12与ModelScope SDK（`python3.12 -m pip install modelscope`），再运行项目的固定版本下载器：

```bash
python3.12 day1/0-download-model/1-download.py --model-root /data/qwen-models
```

官方链接：[Qwen3.5-9B](https://modelscope.cn/models/Qwen/Qwen3.5-9B)、[Qwen3-VL-8B-Instruct](https://modelscope.cn/models/Qwen/Qwen3-VL-8B-Instruct)。备用官方源：[9B Hugging Face](https://huggingface.co/Qwen/Qwen3.5-9B)、[8B Hugging Face](https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct)。脚本按冻结文件版本和SHA256下载/校验，不会自动换成新版。

下载后的目录为 `/data/qwen-models/Qwen3.5-9B/` 和 `/data/qwen-models/Qwen3-VL-8B-Instruct/`；已有基座可以保留原目录，启动时把 `--model-root` 改为它们共同的父目录。

## 2. 首次准备必要环境

目标为Linux x86_64 NVIDIA服务器，Python3.12。驱动和CUDA需与GPU、PyTorch/vLLM匹配，先用 `nvidia-smi` 确认驱动正常；项目不会安装或改动系统驱动。已有正确环境可直接通过下一节的两个Python路径复用。

此锁文件使用CUDA13系列运行库，驱动须支持CUDA13（NVIDIA兼容表最低R580；已实测云端595.71.05）。PyTorch/锁定依赖会提供CUDA运行库，通常无需再手工编译安装一套CUDA Toolkit；新GPU仍需匹配的系统驱动。见[NVIDIA兼容说明](https://docs.nvidia.com/deploy/cuda-compatibility/610/minor-version-compatibility.html)。

推理环境固定vLLM0.22.1、PyTorch2.11.0、Transformers4.57.6、bitsandbytes0.49.2；锁文件在 `day2/5-service/uv.lock`。Web是独立轻量环境，避免覆盖CUDA依赖。已从[uv官方安装说明](https://docs.astral.sh/uv/getting-started/installation/)准备uv后：

```bash
uv sync --project day2/5-service --frozen --python 3.12
uv pip install --python day2/5-service/.venv/bin/python bitsandbytes==0.49.2
uv venv --python 3.12 assistant_app/.venv
uv pip install --python assistant_app/.venv/bin/python -r assistant_app/requirements.txt
```

以上会下载必要Python依赖和较大的GPU运行库。可使用已核对且包含锁定版本的可信国内PyPI镜像；uv的索引设置、PyTorch的CUDA轮子来源和ModelScope模型源各自独立，不因pip换源自动同步。保留锁文件与哈希，不关闭TLS、不升级到任意最新版。

网页成品已经包含，无需安装Node或重新构建。无需安装Unsloth或任何训练环境。

## 3. 启动服务

```bash
python3.12 scripts/run_finetuned_service.py --model-root /data/qwen-models
```

如果复用现成的推理/Web环境：

```bash
python3.12 scripts/run_finetuned_service.py --model-root /data/qwen-models \
  --inference-python /absolute/inference-env/bin/python \
  --web-python /absolute/web-env/bin/python
```

程序会校验基座、解包已训练参数、在独立运行目录生成本机路径配置，启动文字5101、视觉5102，并用真实LoRA身份和生成检查验证加载，随后启动网页5001。默认运行数据与日志位于 `.runtime-finetuned/`；原参数不覆盖。再次运行同一命令即可复用已准备文件。端口占用或模型检查失败时停止本次启动，不杀其他服务、不回退未微调基座。

服务器本机访问 **http://127.0.0.1:5001**。远程浏览可从自己电脑执行 `ssh -L 5001:127.0.0.1:5001 用户名@服务器`；公司正式网络入口按现有认证网关/HTTPS接入，配置见 `assistant_app/README.md`。Ctrl+C正常停止本次启动的服务。

已验证的云端配置为48GiB显存、9B BF16 + 8B4bit、上下文8192；默认显存比例文字0.55、视觉0.35。同系列卡不同显存不能仅按型号判断，其他GPU或显存配置需启动检查和实际验证；可通过 `--text-memory`、`--vision-memory`、`--context` 调整，比例合计不超过0.9。

本交付运行当前轻量AI助手。阿里云备用、飞书与工业业务工作台属于可选集成，凭据/企业数据库不随公开项目分发。文字相关开发回归89/96，仍有7道失败与一个多图观察问题，详见 `docs/LIGHTWEIGHT_OPTIMIZATION_20261028.md`。代码MIT、模型/参数Apache-2.0，第三方数据权利另行保留。
