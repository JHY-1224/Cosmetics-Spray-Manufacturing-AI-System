# 独立模型测试包

此目录是开源项目的评测资料入口，可单独下载，无需重新获取整个项目。

## 文件与范围

- `evaluation-kit-20261026.tar.gz`：3,054,964 字节，包含 32 张合成测试图、64 道图文题、24 道文本题，以及参考答案、评分规则、开发／封存划分、提示词与字段合同快照、执行脚本和 MIT 许可证。
- `evaluation-kit-20261026.tar.gz.sha256`：原始压缩包的 SHA256 校验值。
- 压缩包 SHA256：`8b1ec491b099ae91383918a3c46ebbcd3124823e6a3d59362a0dcff0ddb6a399`。

图文题分为 48 道开发题和 16 道封存题；文本题分为 18 道开发题和 6 道封存题。图片均为自有合成资料，不包含客户原件、模型权重或凭据。封存表示使用流程上的冻结；公开题目及参考答案不是保密题库。请勿将评测资料用于训练，也不要反复利用封存结果调参后仍宣称独立验收。真实现场图片与专家验收另行完成。

## 方式一：直接下载测试包

打开本目录中的压缩包，点击 **Download raw file**，同时下载同名 `.sha256` 文件。只传输这两个文件，不下载源码、训练包或模型。将它们放到已有云端项目根目录。

在尚无同名解压目录时，先核对再解压：

```bash
sha256sum -c evaluation-kit-20261026.tar.gz.sha256
tar --keep-old-files -xzf evaluation-kit-20261026.tar.gz
```

已有文件／目录时先核对，不覆盖或删除原件。压缩包内有完整的 `docs/EVALUATION_HANDOFF.md`。

## 方式二：Git 只检出本目录

在新的独立目录运行，保留原云端项目。GitHub partial clone 会读取必要的 Git 元数据，只在检出时获取所选目录的文件内容：

```bash
git clone --depth 1 --filter=blob:none --no-checkout --single-branch --branch main https://github.com/JHY-1224/Cosmetics-Spray-Manufacturing-AI-System.git cosmetics-evaluation-only
cd cosmetics-evaluation-only
git sparse-checkout init --no-cone
git sparse-checkout set '/evaluation/'
git checkout main
cd evaluation
sha256sum -c evaluation-kit-20261026.tar.gz.sha256
```

这会取得整个测试包，包括开发和封存题；不会检出其他源码、训练资料或模型。再将压缩包和校验文件复制到已有项目根目录，遇到同名文件先比对，不覆盖。压缩包不是训练图片包的替代品。

## 运行前提与顺序

当前云端约定使用已有 `/root/autodl-tmp/envs/cosmetics-unsloth/bin/python`、两个已训练 Adapter 和项目中的 `local_training/qwen-vl-20261026/metadata.jsonl` 及其原图。路径变化时显式设置 `COAT_EVAL_PYTHON`、`COAT_EVAL_TEXT_ADAPTER`、`COAT_EVAL_VISION_ADAPTER`、`COAT_EVAL_TRAINING_DATA`；不重新安装依赖或下载模型。

按阶段执行，检查结果后再进入下一阶段：

1. `bash evaluation-kit-20261026/run_tests.sh check`：包内哈希与视觉题划分／训练重合检查，不加载 GPU。
2. `bash evaluation-kit-20261026/run_tests.sh compare`：前三条训练样本的同基座关闭／开启 Adapter 对照，共 6 次生成，用于定位复读，不计独立正确率。
3. `bash evaluation-kit-20261026/run_tests.sh regression`：视觉 48 道及文本 18 道开发题的两个版本对照；顺序加载两个模型，查看评分与原回答并逐项人工核对。
4. `bash evaluation-kit-20261026/run_tests.sh final`：仅在开发方案冻结且人工核对完成后执行视觉 16 道、文本 6 道封存题。

报告写入 `/root/autodl-tmp/models/services/qwen-light/evaluation/` 的全新时间目录。脚本不会训练、下载模型、启动服务或批准上线；命令正常结束不等于模型质量通过。本次公开发布与文件传输也不代表已执行新的云端评测。
