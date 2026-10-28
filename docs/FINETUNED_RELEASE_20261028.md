# 2026-10-28 微调后交付

> 最新客户入口是Gitee仓库首页与[GITEE_RESULT_README.md](GITEE_RESULT_README.md)：新结果版直接随Git/源码ZIP带8个40MB以内LoRA分片、已构建网页、统一启动入口。客户不用找下文v1附件或执行训练；下文保留首次附件发布及其原始哈希。必要谱系元数据和路径迁移由启动程序处理，原权重字节不变。

本次交付将已训练LoRA参数与可公开源码配套，基座通过官方源取得。它不是独立基础模型，也不包含业务运行数据或服务器密码。

## 源码与实际服务状态

GitHub开发入口：https://github.com/JHY-1224/Cosmetics-Spray-Manufacturing-AI-System

Gitee发布入口：https://gitee.com/jhy1224520/Cosmetics-Spray-Manufacturing-AI-System-finetuned

当前源码选定共享策略 `industrial-assistant-v6`，文字策略SHA256 `564ea9c74eb89b93989eb3514eb76aab8d948a5f7f7d7eb8bed8a1c53f81c553`，视觉仍v3、SHA256 `b08b40ceb10ad470381b5589b5f54f91e3eaf646930decd6e6585eadc9f7c9d5`。2026-10-28云端只读清单确认正式源码HEAD仍 `1ca3b09ca9d0e8c6dd9a50e092e1d34908c5b033`、无已跟踪修改；正式5001保留v3，v6在隔离5005验收。本次公开发布不自动重启正式服务，不将候选验收描述为已完成正式切换。

固定96题v6通过89题（92.71%），达到至少87题的轻量试用目标，仍有7题失败。历史及v5均83/96，原失败保留。评审为助手逐条参考复审，题集与开发选择相关，不是独立专家或工厂现场准确率。图文5个合成任务完成，4通过、1多图观察partial：出现原图没有的引文。不能写“所有功能彻底无误”。详见 [优化证据](LIGHTWEIGHT_OPTIMIZATION_20261028.md)。

## 官方基座，只提供下载链接

|职责|官方模型|官方下载|
|---|---|---|
|文字|Qwen/Qwen3.5-9B|https://modelscope.cn/models/Qwen/Qwen3.5-9B 、 https://huggingface.co/Qwen/Qwen3.5-9B|
|视觉|Qwen/Qwen3-VL-8B-Instruct|https://modelscope.cn/models/Qwen/Qwen3-VL-8B-Instruct 、 https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct|

两基座官方模型卡标注Apache-2.0；代码MIT不替代模型许可。下载时保留官方许可/NOTICE，按 `models/Qwen3.5-9B.manifest.json` 与 `models/Qwen3-VL-8B-Instruct.manifest.json` 核对冻结文件。两基座约36.9GB，已下载者先离线核验；不执行第三方远程安装脚本，不关闭TLS，不擅自升级版本。

## 必须交付的微调参数

|组件|真实run_id|adapter_model.safetensors字节|SHA256|
|---|---|---:|---|
|文字|20261027-134240-499467|86630872|`9f8cac5c116acec133c5c1773ebf8ac0407609b55bd8e19c3801e14f1314b59c`|
|视觉|20261026-194847-896921|174663096|`ec46d1b611aab96b7bd89821284dfe9c5f005477aaa7830ca4846a4b78edb8b6`|

参数附件保留原始adapter_config、safetensors、tokenizer、chat_template及视觉processor；不上传优化器、GPU缓存、密钥、客户资料或业务库。原始权重不改写。附件名、分片大小、校验和与环境清单在 `models/releases/qwen-light-20261028.json`。

这些是项目训练出的LoRA增量，需配合上表准确基座使用，不是约37GB基座的替代。发布增量按Apache-2.0提供，保留Qwen基座署名与许可；第三方数据权利不因参数发布而改变。文本开发资料含CC BY提炼及合成教学题，保留数据署名；视觉开发实际用了74图/82问答，54张图再分发许可仍未核实，因此公开源码仅附20张明确许可图像，**不能据此承诺完整视觉训练数据已开源或从公开子集可逐字节重训同一权重**。参数发布不等于其训练素材获得新的许可。

从Gitee发行版下载所有参数附件和该版本清单到同一新目录，再恢复到空的输出目录：

```bash
python scripts/restore_finetuned_release.py \
  --manifest models/releases/qwen-light-20261028.json \
  --archive-dir /absolute/downloads/finetuned-20261028 \
  --output /root/autodl-tmp/models/services/qwen-light/imported-20261028
```

脚本逐片校验、合并、校验归档及每个文件，只解压清单列出的普通文件，不覆盖已有目录、不执行模型、不启动GPU。成功输出 `text/` 与 `vision/`；按 `configure_qwen_light.py` 指定这两个adapter目录，并在新私有service目录生成配置。若更换官方基座路径，登记器会拒绝原adapter_config中不匹配的路径，须明确核对原基座身份后另建迁移副本，保留原件及哈希，不直接修改发布原参数。

```bash
python assistant_app/scripts/configure_qwen_light.py \
  --text-adapter /root/autodl-tmp/models/services/qwen-light/imported-20261028/text \
  --vision-adapter /root/autodl-tmp/models/services/qwen-light/imported-20261028/vision \
  --service-dir /root/autodl-tmp/models/services/qwen-light/restored-deploy-20261028 \
  --profiles-dir /absolute/new/private-profiles
```

之后使用云端操作手册做运行时adapter命中验证、生成新的核验回执，再启动5101/5102与Web。不能复用旧机器的“已验证”标记冒充新环境验证。当前实际GPU为RTX4090、49140MiB；9B BF16+8B bitsandbytes4bit曾共同驻留约44GB，上下文8192、max_num_seqs=1。新机器显存不足时须重新评估，不在现有GPU服务运行中盲目开第二套。

源码保留v3/v5/v6策略及历史记录，模型原run目录保留。恢复与发布不重训、不覆盖原权重、不调用云端付费模型。完整工作方式见 [SOURCE_AND_RELEASE_WORKFLOW.md](SOURCE_AND_RELEASE_WORKFLOW.md)。
