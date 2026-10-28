# 默认训练数据的许可与署名

公开仓库分发 `day1/2-prepare-data/train.messages.jsonl` 的36条默认文本训练样本，
以及 `data/vision-public/` 的20张图和20条开发图文问答。
这些样本是两篇明确采用 CC BY 4.0 的作者文章的中文提炼、结构化问答与明确标注的教学核算派生。

分发和改编这些派生内容时，请保留 `day1/2-prepare-data/ATTRIBUTION.txt`，
注明原文、作者、许可链接和中文提炼/教学计算改编事实；原始文章仍适用作者原有权利声明。

- CC BY 4.0：https://creativecommons.org/licenses/by/4.0/
- 具体来源与变更说明：`day1/2-prepare-data/ATTRIBUTION.txt`
- 数据范围、缺项和审核状态：`day1/2-prepare-data/DATA_CARD.md`

根目录 MIT LICENSE 适用于本项目代码，不替代第三方文章、模型或数据的原有许可。
原始采集资料目录、客户资料及用途待确认的其他记录不属于此次公开发布。

图像子集来自Daiki Tomita、Yue Bao的两篇CC BY 4.0论文：
Detection of Chips on the Threaded Part of Cosmetic Glass Bottles（DOI:10.3390/jimaging11030077），
Inspection of Defective Glass Bottle Mouths Using Machine Learning（DOI:10.3390/jimaging11040105）。
原图像素保留，新增中文上下文问答；并非领域专家标签，不能改变原图的研究对象或实验边界。
署名和变更说明见 `data/vision-public/ATTRIBUTION.json`。两论文存在相关素材，按同一来源组处理。
完整图像包 `qwen-vl-full-20261026.tar.gz` 按项目所有者本次明确要求随Git仓库提供。包内54份其他来源资料的再分发许可仍未核实；公开可下载不代表已取得这些权利，不适用本项目MIT许可证。包内metadata.jsonl逐项保留source_url与rights_status，原权利归原作者/权利人，不因本次上传改变。


## 2026-10-28 发布范围更新

上述74图归档描述保留旧发布历史。本次 `--licensed-data-only` 快照不再附该归档，仅分发20张已核实 CC BY 4.0图像及其署名；54张许可未核实原件留本地，不改写旧Git历史。微调参数的基座许可与训练数据来源说明见 `FINETUNED_RELEASE_20261028.md`，代码MIT不替代第三方权利。
