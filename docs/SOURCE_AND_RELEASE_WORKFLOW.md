# Mac、GitHub、NVIDIA云端与Gitee工作流程

2026-10-28，按项目所有者要求建立。GitHub是持续开发源码，Gitee是经过验收的“微调后”发布快照；两者都不会因为克隆而自动启动、下载模型或开始训练。

|位置|保存内容|更新时机|
|---|---|---|
|Mac完整工作区|开发源码、数据处理、测试、私有原件、失败账本|持续开发，保护原件|
|GitHub `JHY-1224/Cosmetics-Spray-Manufacturing-AI-System`|与Mac对应的可公开源码、许可明确的数据、脚本、锁文件、脱敏经验|每次授权且验证通过的修改；不等待项目最后一天才同步|
|NVIDIA云端|冻结源码提交、独立训练/推理/Web环境、官方基座、逐run训练产物、私有运行配置|按准确提交克隆/更新；候选隔离验证，再办理正式切换|
|Gitee `jhy1224520/Cosmetics-Spray-Manufacturing-AI-System-finetuned`|客户使用的训练结果：源码、仓库内LoRA分片、网页成品、必要环境说明及统一启动入口|每次选定发布版本；保留旧版本和失败记录|

“本地与GitHub一致”指公开源码清单及文件SHA256一致。共享业务代码继续同步到Gitee；Gitee另带客户结果资源和精简README，因此不再要求两仓库整个Git树相同。Mac上的密钥、客户资料、业务库、未获再分发许可的原件、缓存和虚拟环境不属于公开源码；不能整盘镜像。云端运行进程、CUDA驱动和显存状态也不能装进Git。

## 从Mac到云端

1. 在Mac收集并核对数据来源与许可，明确训练、开发、验证及封存划分。先检查再做最小修改，保存问题原文、方法、版本、测试、未解决项及回退位置。评估答案不自动回流训练。
2. 使用独立新目录生成公开快照并审计；原工作区不移动、不覆盖。大型依赖/模型先核对官方源和可信国内镜像，保留版本及哈希。

```bash
python scripts/prepare_public_release.py --licensed-data-only --output /absolute/new/public-snapshot
python scripts/audit_public_release.py /absolute/new/public-snapshot
```

3. 对照GitHub当前HEAD审查差异；只提交已审核的公开文件，正常快进推送，不强推、不重写历史。上传后按 `PUBLIC_RELEASE_MANIFEST.json` 再核对。日后提交/推送仍遵守当轮授权和项目规则。
4. 新服务器克隆后记录完整commit SHA；先在现有NVIDIA环境核对驱动、Python和锁定依赖，不假定Mac环境可直接充当CUDA环境。按 `CLOUD_QUICKSTART.md` 分别管理训练、推理和Web环境。已有服务更新先建隔离checkout，不直接覆盖正式服务。

```bash
git clone https://github.com/JHY-1224/Cosmetics-Spray-Manufacturing-AI-System.git
cd Cosmetics-Spray-Manufacturing-AI-System
git rev-parse HEAD
# 在确认的环境中校验；此命令不训练、不下载。
python day1/0-download-model/1-download.py --verify-only --model-root /root/autodl-tmp/models
```

5. 云端如有代码修复，先导出限定源码差异并核对私密内容，回到Mac同步、测试后同步GitHub；不要仅在云端形成无人记录的唯一修复。训练参数、数据清单、run_id、adapter SHA256与实际依赖版本另记，模型产物不能靠Git提交替代备份。
6. 冻结模型、数据、提示词和评分口径后执行验收。达到本轮约定门槛才能选作发布候选；小对照、loss、单元测试与HTTP200不能替代内容验收。正式服务切换、独立备份和恢复验证分别记录，不由仓库发布推定已经部署。

## 发布到Gitee

源码与发布版本标识保持可追溯。基座提供官方链接与冻结清单，不重复上传约37GB权重；**项目训练出的LoRA参数直接作为仓库内40MB分片交付**，普通克隆/源码ZIP即包含，不能只给云端路径或要求客户另找发行版附件。历史v1附件保留。

开发源码用 `prepare_public_release.py --licensed-data-only` 导出；Gitee结果在通过源码审计后用 `prepare_customer_result.py` 附加明确选定的原参数分片与网页成品，再用 `audit_public_release.py --result` 逐项核对。Gitee根README取自 `docs/GITEE_RESULT_README.md`；训练过程不作为客户启动步骤。客户入口 `run_finetuned_service.py` 复用既有模型启动器，先核对真实LoRA身份与生成，再创建核验回执和启动Web，不静默回退基座。

Gitee个人版官方配额：Git单仓库500MB、单文件50MB，附件单文件100MB、仓库附件总量1GB。大参数按原始字节分片并记录每片与合并文件SHA256；恢复时先验片、合并、验包、再安全解压，不改权重内容。

- 配额依据：https://help.gitee.com/questions/Gitee产品配额说明
- 发行版依据：https://help.gitee.com/repository/release/create
- 当前产物与恢复：[FINETUNED_RELEASE_20261028.md](FINETUNED_RELEASE_20261028.md)

每个发布保留源码commit、提示词版本、数据哈希、模型run_id/哈希、环境版本、内容评审分子分母、已知问题和回退点。Gitee发布快照保留发布状态，GitHub继续下一轮开发；不要把正在开发的main无条件覆盖已验收版本。自动定时同步、自动训练、自动部署均未配置。
