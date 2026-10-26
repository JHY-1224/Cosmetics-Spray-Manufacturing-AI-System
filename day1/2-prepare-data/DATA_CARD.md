# 数据卡

版本cosmetics_sft_dataset_20261025_1650，固定种子20261025。包材工艺、配方、计量、相容性的来源约束问答；不是现场实验、操作授权或生产配方验证。

319条活动候选，293个证据知识家族，128个来源、103个防泄漏组。默认训练36条，正式validation/test均0条；候选validation/test为15/14。另3条教学、3条不可信文档指令回归，不能计入来源评测规模。

生成与复核均为同一Codex会话不同阶段，非独立专家。当前会话声明模型gpt-6.1-sol，推理档位未核实；旧版模型身份未从程序确认。逐条证据回读不替代专家复核。当前复核受原件质量、图像分辨率与缺失实验条件限制。

领域：{"compatibility_quality":54,"contents_formulation":96,"equipment_reference":23,"packaging_coating":79,"thermal_curing":3,"uv_curing":32,"vacuum_aluminizing":32}。任务：{"T01":37,"T02":54,"T03":19,"T04":5,"T05":18,"T06":42,"T07":11,"T08":42,"T09":72,"T10":1,"T11":18}。答案字符共34917，未执行tokenizer。

默认仅两个来源，集中于乳液和PLA/PHA容器相容性，不能代表完整工艺范围。按来源和领域的偏倚统计见dataset_statistics.json。不能复制行造平衡。

content_status、use_status、expert_status、generation_status独立。source_checked仅指明确引用片段，source_checked_in_scoped_spans不是全文通过。pending_quality没有可训练消息。部分配方组成/步骤齐备也不表示安全、稳定、可生产；重量份、未知百分比基准、q.s.、商品浓度与纯活性分别保存。

初始家族分组保留；A03、X030-D001、D06-R002因直接来源重叠隔离heldout。无跨集合家族/词面近重复违规，不等于无任何语义泄漏。同一会话已经接触旧heldout，本轮不能声称独立盲测。未执行领域专家审核、嵌入语义审计或模型评测。

仍未完成：719页章节/翻译/矩阵未完整复核，另18页扫描OCR仅关键片段逐字验收；详见根目录剩余页码清单.csv。3927条旧分块未覆盖核验片段，不能当作已生成问答。未确认事项清单含来源缺项、冲突、翻译及未完成项；待用途确认与待专家审核是独立状态。
