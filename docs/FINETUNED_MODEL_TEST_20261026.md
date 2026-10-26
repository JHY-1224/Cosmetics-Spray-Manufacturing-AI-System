# 微调模型 10月26日测试准备

2026-10-26本地默认基座已改为Qwen3.5-9B，并准备VL 8B。本文仍用于既有云端27B adapter，以下命令显式指定原27B基座；不能将该adapter配给9B或8B。模型切换不会改变历史云端训练事实。

明天先确认云端 adapter 能正确加载，再比较基座与微调模型的回答，最后决定是否进入业务协议联调。测试只写新的报告目录；保持现有业务服务、数据库和模型配置。

## 已有训练结果

根据用户提供的云端日志，运行 `20261025-224923-259597` 已完成36条样本、3个 epoch、9个 step，`train_loss=1.605`。产物位于：

```text
models/finetuned/Qwen3.8-27B-cosmetics/20261025-224923-259597/adapter
```

权重文件约891MB，整个 adapter 目录约910MB；日志明确 `Merged model: not requested`。最后的 Ctrl+C 出现在保存和任务 Done 之后，日志没有显示训练被中断。仍需明天在云端核对实际文件、训练清单与加载结果。

36条训练样本主要是提供文献证据的专业问答，并非完整喷涂／蒸镀诊断数据。validation/test 均为0，训练 loss 不能作为独立效果分数。新增12题是人工编写的合成冒烟题，不是正式独立测试集；其中喷涂、蒸镀和业务边界属于迁移能力探测。不要把测试题或答案重新混入本轮训练。

## 云端先保留产物

保留本次 adapter、同级 `run-manifest.json`、`day1/3-train/outputs/20261025-224923-259597/` 和 `train-logs/sft.log`。在释放云实例或清理数据盘前，将约910MB的整个 adapter 目录、训练清单和日志另外保存到自己的持久存储，核对两端 SHA256；同盘多一份不能防止整块实例数据盘丢失。不要只保存单个权重文件，也不要为了导出再运行训练。

本轮不新增依赖或下载权重。复用已成功训练的 `day1/3-train/.venv`。如果环境缺失，先核对原锁文件和实际下载源，再按项目的镜像规范处理。

## 明天执行顺序

### 1 上传准备文件并检查 GPU

把本次新增的 `day1/4-validation/3-test-adapter.py`、`smoke-cases.json` 传到云端对应目录。其余训练和权重目录不用重传。以下命令均在云端项目根目录执行：

```bash
cd /root/autodl-tmp/code/Cosmetics-Coating-AI-Assistant
nvidia-smi
export ADAPTER=models/finetuned/Qwen3.8-27B-cosmetics/20261025-224923-259597/adapter
ls -lh "$ADAPTER"
```

检查没有遗留训练或推理进程占用GPU。如果占用来自其他任务，先确认所属任务，不盲目结束进程。

### 2 只检查文件

```bash
day1/3-train/.venv/bin/python day1/4-validation/3-test-adapter.py \
  --adapter "$ADAPTER" \
  --base-dir models/base/Qwen3.8-27B \
  --report-dir reports/adapter-files-20261026
```

默认不导入 Unsloth/CUDA，不执行推理。检查必要文件非空、基座路径、LoRA参数与训练清单一致，并逐块计算 adapter 文件 SHA256。报告中的 `files_checked_no_inference` 仅表示文件检查完成；新生成的哈希是后续备份比较依据，不证明文件与训练时原件已独立比对。

基座完整权重的哈希复验沿用原脚本，不重新下载：

```bash
day1/3-train/.venv/bin/python day1/0-download-model/1-download.py --model Qwen3.8-27B --verify-only
```

若缺少 `run-manifest.json`，先找回本次清单；若 adapter 记录的基座路径与实际路径不同，先核对云端原目录。不要通过删除检查或改写原 adapter 来强行通过。

### 3 两题推理试跑

```bash
day1/3-train/.venv/bin/python -u day1/4-validation/3-test-adapter.py \
  --adapter "$ADAPTER" --run --limit 2 \
  --base-dir models/base/Qwen3.8-27B \
  --report-dir reports/adapter-smoke2-20261026
```

复用现有 FastModel，按训练时的4bit配置加载本地 adapter 及其基座，不要求先导出 merged。每道题在同一模型和 tokenizer 下，分别关闭和启用 adapter，使用相同的贪心生成参数；报告中的 `base` 是共享 adapter tokenizer 的4bit基座对照，不是独立加载的官方原始精度基准。人工验收要点不进入模型输入。此路径依据 [Unsloth 推理文档](https://unsloth.ai/docs/basics/inference-and-deployment/unsloth-inference) 和 [PEFT 的 disable_adapter 接口](https://huggingface.co/docs/peft/main/en/package_reference/peft_model#peft.PeftModel.disable_adapter)；本机没有云端GPU，实际模型兼容性由本步骤确认。

报告目录必须全新，重复执行请换目录后缀。查看 `run.json` 的状态、`environment.json` 的活动 adapter 与版本，以及 `responses.jsonl` 中两种结果。显存不足或加载报错时保留错误报告，先分析，不升级核心依赖、不重复训练、不改用未核验下载。

### 4 十二题完整对照与人工复核

```bash
day1/3-train/.venv/bin/python -u day1/4-validation/3-test-adapter.py \
  --adapter "$ADAPTER" --run \
  --base-dir models/base/Qwen3.8-27B \
  --report-dir reports/adapter-smoke12-20261026
```

共12题、24次生成。默认上下文4096、输出最多768 tokens；超出上下文会报错，不静默截断。`hit_output_limit`、未正常结束、空回答、重复文本均需复核，不能把截断当完整回答。如果需要重测，保留原报告，并让基座和微调使用相同的新参数。

| 检查范围 | 用例 | 人工关注点 |
|---|---|---|
| 证据提取、算量、JSON | S01—S03 | 算术、单位、字段与未知值；禁止无依据均分 |
| 相容性与冲突证据 | S04—S05 | 不外推货架期、不静默消除来源冲突 |
| 喷涂与蒸镀 | S06—S07 | 候选原因与事实分开，不编造批准参数或放行结论 |
| 质量、配方与更正 | S08—S10 | 对象覆盖、体积质量边界、有效更正及限值缺口 |
| 指令注入与稳定性 | S11—S12 | 来源不能命令系统造假；比较S07/S12的证据边界 |

每题按 `cases.json` 中的 review 要点逐项记录满足／不满足／需专家复核，摘录对应回答并给出基座与微调的胜／平／负。先隐藏 variant 标签复核，再揭示模型身份，可以减少主观偏好。

建议的冒烟门槛：全部结果非空且完整；S01—S03计算和结构字段准确；各题所有适用边界无重大失守。任何伪造证据、虚构已执行动作、无依据根因确认或整批放行，均阻断业务模型替换。胜平负与完成率只描述这12题，不宣称泛化准确率。若两者答案一致，记录事实，不强行宣称微调有提升。

### 5 通过后才进入业务协议测试

现有 `quality/agent.py` 使用受限诊断 JSON、有效证据ID和受控动作；专业问答通过并不意味着可直接替换。明天优先完成前四步。随后在独立测试库验证：诊断schema、证据引用、缺证拒判、截断／非完整结束拒绝、人工审批、回执与复测。沿用 `scripts/verify_model_workflows.py` 与 `scripts/industrial_browser_check.py --suite model`，待有真实兼容的模型服务及私有连接配置后再执行真实调用，不先改8002配置。

现有 vLLM 启动脚本要求完整模型路径，不能把本次 adapter 目录直接填进去。日志中的GPU显存为47.371GB，而项目记录BF16基座权重约55GB，因此不能假定当前单卡能直接加载BF16 merged。服务化部署路径另行评估，避免同时启动两套推理进程。

当前8002系统另有一个启用的“浏览器断连验证”连接指向 `127.0.0.1:19999/records`，服务未运行使业务就绪检查返回503。该告警与新adapter效果无关；联调前需由配置负责人决定保留、停用或补齐对应测试源，不能忽略它来宣布系统全就绪。

## 明天保留的结果

- adapter文件清单及SHA256、训练日志和本次清单的持久备份。
- GPU与依赖版本、两题试跑、12题成对原始回答及耗时／显存记录。
- 人工逐题复核、严重问题清单，以及是否允许进入隔离业务协议测试的结论。

今晚本机验证仅覆盖脚本失败门槛、题库格式和现有接口测试；真实CUDA加载、模型回答质量和性能均待云端执行。
