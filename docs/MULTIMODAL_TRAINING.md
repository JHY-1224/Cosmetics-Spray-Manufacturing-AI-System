# 图文训练数据与参考实现

本项目参照“多模态医疗AI助手”的 `day1/5-train-vl/train.py`、`metadata.jsonl`、
`day2/1-launch-vllm/` 和 `day2/3-service/v3/fastapi_v3_vllm.py` 的完整链路：
图片与标签 → 图文 messages → Unsloth SFT → 两个 vLLM 端点 → 一个 FastAPI 应用。
没有复制医疗数据、诊断标签或模型权重。

## 数据是什么

图文数据也使用 JSONL。图片单独保存，JSONL 的每条记录通过相对路径引用图片，并提供问题和参考答案。
`file_name` 保留 ImageFolder 风格；本项目额外记录哈希、来源组、训练划分、许可和标签审核状态。

```json
{"id":"sample-001","file_name":"images/sample.png","images":[{"path":"images/sample.png","sha256":"实际文件的64位SHA256"}],"question":"读取表中的原料名、比例和单位。","answer":"经过逐项核对的参考答案；不清楚的字段明确写未知。","source_group":"source-family-001","source_url":"原始来源URL","split":"train","expert_reviewed":false}
```

`dataset.py` 实际打开图片为 RGB 像素，再转换为 Unsloth 接受的内容：

```python
{"messages": [
  {"role": "user", "content": [
    {"type": "text", "text": question},
    {"type": "image", "image": pil_image}
  ]},
  {"role": "assistant", "content": [{"type": "text", "text": answer}]}
]}
```

不能只拿图片文件名、OCR自动结果或网页图片标题充当完整的正确答案。

训练入口通过 `dataset.py:training_dataset` 构建真正的 Hugging Face `datasets.Dataset`。
Arrow 只保存问题、答案和图片路径，`with_transform` 在取样时打开 RGB 图片并生成上述 messages；支持单条、批量索引和 select。
保持 `skip_prepare_dataset=True`、`remove_unused_columns=False`，由 Unsloth 视觉 collator 处理图片和回答监督。
不能把仅有 `__len__` / `__getitem__` 的普通 Python 类传给 TRL 1.15.0；它会在训练器初始化时拒绝。

## 本轮整理结果

- 本地完整开发包：`local_training/qwen-vl-20261026/`，74张图片、82条问答。
- 74条为原有图像策展与来源上下文的主题/边界标签；8条为逐页核对的配料、比例、单位、表格行列和跨页缺项读取。
- 不是74张图全部完成了逐字OCR，也不是现场缺陷根因数据集；没有真实客户批次标签或专家审核。
- 16个来源组；相关论文归为同一组。全部暂作开发训练数据，validation/test为空，不能报告独立准确率。
- 公开子集：`data/vision-public/`，20张CC BY 4.0论文图和20条上下文问答，附作者与许可；只适合开发接线和有限识别训练。
- 完整74图包以根目录 `qwen-vl-full-20261026.tar.gz` 随Git分发，克隆后按CLOUD_QUICKSTART.md校验和解压即可；不需要另外上传。其余54张图仍标记公开再分发许可未核实，保留来源URL，不授予第三方素材的额外权利。

每个包的 `review.html` 可以逐条对照原图和答案；`dataset-card.json` 保存范围、数量和SHA256。
`--allow-unreviewed` 明确选择开发标签；它不把标签变成专家审核结果或自动授予任何第三方权利。

## 与参考项目的区别及原因

参考项目使用Qwen3.5-4B、固定一句话任务和BF16合并导出。本项目使用9B文本＋8B视觉，
图文任务覆盖照片、文字、表格、简图与参数页面，所以每条样本保留独立问题与答案。
采用4bit LoRA、batch=1、默认不合并，减少4090显存与磁盘压力。
视觉训练默认冻结视觉编码器，仅更新语言相关LoRA；图像仍由原生视觉processor参与训练。
这使产物与当前语言LoRA服务路线一致，不表示视觉编码器本身被微调。

保留原生模板和图像token；监督assistant答案，屏蔽用户、图片与padding。实际训练前对每条扩展序列和标签做检查，超预算报错，不静默截断。
不随机按行拆同一来源的问答；后续评测必须使用独立来源/工单/批次/原图组。

训练与服务接线详见 [云端步骤](CLOUD_QUICKSTART.md)。本机只执行文件/数据合同和dry-run，CUDA训练、LoRA与vLLM组合仍需目标4090实际冒烟。

官方参考：[Unsloth视觉微调](https://unsloth.ai/docs/basics/vision-fine-tuning)、
[Qwen3-VL指南](https://unsloth.ai/docs/models/tutorials/qwen3-how-to-run-and-fine-tune/qwen3-vl-how-to-run-and-fine-tune)、
[TRL SFT](https://huggingface.co/docs/trl/sft_trainer)。实现使用已存在的环境，不自动升级或运行远程安装脚本。
