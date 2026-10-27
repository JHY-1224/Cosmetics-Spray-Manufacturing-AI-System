# 云端测试包与当前验收状态（更新至2026-10-27）

## 2026-10-27源码修复与运行问题的边界

用户再次明确：训练/诊断脚本、输入合同、可分发训练数据或应用逻辑的缺陷，应修正本地源项目并同步GitHub与云端；纯云端依赖、精度、资源和训练产物问题，在云端独立环境或新run处理。不能用Mac上的阿里云5001冒充云端微调版部署。

本轮检查发现3-test-adapter.py及三个视觉HF入口仍未显式合并generation EOS与tokenizer聊天EOS；新增day1/generation.py统一校验并显式传给generate，probe_app_text.py复用同一实现，保留legacy仅供历史对照。旧冻结evaluation归档及原始报告未改，历史结果不追写为新配置结果。训练监督已有通过证据，本轮没有据停止符问题启动重训。

公开副本的四项资料路径失败已在源项目修复：测试从仓库自带、SHA256固定的evaluation归档解压至临时目录，复用公开图片子集和明确合成的模型配置夹具，不再依赖本机reports/local_training/weights。本机助手与day1联合回归200通过、3项因未安装datasets跳过；仍须在新公开副本及云端各自验证。

已读取云端最新40次回放并逐项对照20条参考，审阅记录在本机reports/source-correctness-20261027-144337/cloud-replay/content-review.json（助手审阅，不是独立专家验收）。最新Adapter仍有投料先错后改、表格行列误读、无整改前基线却称不良率下降、公式与后续结果不一致等；基座也会臆造零不良验收标准。暂不公布整体正确率，不批准部署，不盲目重新训练。同步/发布结果以随后实际校验为准。

## 2026-10-27最新增量：新run已完成，训练回放与完整同步

用户授权将当前本地项目更新到GitHub，再完整同步云端，要求源码、训练/测试资料和配置模板逐文件一致；环境、凭据、业务数据、历史报告、基座和Adapter另行保留。当前原项目根仍无.git；使用既有公开发布副本制作可审查快照，不能git reset/clean覆盖云端修改。

balanced-v3新增数据456行（72来源视图保留原答案、统一候选策略，加384条多参数教学题），预先96条参数组留出，不参与优化。新run20261027-134240-499467真实完成57步/1epoch，r8/alpha8、lr1e-5；458答案/EOS和padding通过、最长2620，loss1.824803；Adapter SHA2569f8cac5c116acec133c5c1773ebf8ac0407609b55bd8e19c3801e14f1314b59c。原数据/旧权重全保留，不因loss通过部署。

`text-balanced-http-control-20261027-v3`完成16次HTTP生成（8题×base/fine、greedy、BF16、原官方tokenizer、8192上下文）；16次stop、0length，旧失败的S02和密度题已正确，但内容与用词仍需逐项核对。16是运行次数，不是独立题准确率。其服务PID23015已正常清理、退出0。

根据用户最新要求，已启动`evaluation/text-balanced-training-replay-20261027-v1`，计划在day1/4-validation/plans/text-balanced-replay-v3.json固定：8条原来源训练样本（含4条原计算）+6条新教学训练样本+6条未参与训练的同源预留题，各base/fine共40次。输入使用实际训练消息，最后参考答案只保存references.jsonl，不入请求。先核对该轮是否完成及原回答，判断训练拟合与参数泛化；不要重复改提示词或无依据重训。

Web独立环境cosmetics-assistant-20261027已创建，但尚未安装包；`evaluation/web-runtime-20261027-v2`已经按本机测试依赖版本解析，32个包约17.1MiB，清华镜像源且逐轮与官方PyPI哈希核对，requirements.lock已生成。v1仅预解析到更新的传递依赖、未安装；v2用本机测试版本约束，保留两轮证据。独立安装后再核对pip check和云端程序测试。

公开数据副本在data/text-training/（app-v1与balanced-v3）、开发请求在data/evaluation-inputs/，候选策略在assistant_app/configs/policies/；原local_training、reports及冻结evaluation包均不覆盖。发布脚本现包含这些可分发资料与evaluation/，生成清单后需安全审计。GitHub连接器连续网络失败；本机Git通过已有系统代理可读取仓库，既有发布副本remote-check已ff到远端main9540ea1，无本地改动。尚未提交/推送当前新工作，云端尚未git更新。

## 最新接续：停止符根因已复现，8B服务回归与9B教学补充新run

**本轮执行增量（2026-10-27 13:30，优先于下方锁屏记录）**：Mac已解锁，SSH可用。`text-bf16-base-control-20261027-v1`已完成8条同输入/同策略/同精度关闭Adapter对照；S01/S02/密度题正确，但未知比例基准题自行补出的条件计算仍错，不能宣称基座全通过。新`probe_text_http.py`及针对性测试20项通过，随后实际运行私有vLLM BF16（原服务锁定环境、Triton GDN、PyTorch sampler、8192上下文、预算0.55）。

`text-http-control-20261027-v1`只在模型前预检停止：Adapter tokenizer.json多7个音频token。读取实际AutoTokenizer后确认两边词表248077、特殊ID、模板以及18条应用输入编码完全相同；官方tokenizer会在加载时注册这些token，不能误判为训练词表改变。保留失败预检。`text-http-control-20261027-v2`完成8题×base/finetuned共16次HTTP生成（全部stop、0length）。基座S02和密度题正确；最新Adapter仍输出S02前值0.72/0.36但后算式2.4/1.2，密度题将3.3+1.8算4.5、44.88+24.48错误声称等于61.2，另S11把缺执行依据误说成设备未调整。未验收。模型服务PID20506已由自己的supervisor正常停止，退出0。

下一步已启动`evaluation/text-http-sampling-control-20261027-v1`：同8题×两版本×固定种子3407/3408/3409，明确使用官方模型卡Best Practices的非思考推理采样参数（temperature1/top_p1/top_k40/presence2），上限1024。它仅是对照，未改应用配置，不能挑选某一好种子视为通过；结束后保留48次全部原始HTTP，按内容决定后续。启动脚本在云端项目reports/text-http-sampling-launch-20261027-v1/，恢复先读status.json，勿重复启动。无新训练、无封存、无5001正式部署。

**最新接续点（2026-10-27；以下旧状态以本段为准）**：用户要求一点一点验收，确保最终“Qwen轻量服务”选择及服务启动稳定。所有原产物保留，未正式部署或运行封存测试。

- 8B第二版告警策略 `vision-policy-candidate-v2.txt`（SHA256 `cc0620bdde81e5bd106f0466508c106a9dff069a817f14d22ecd2980cee0f4ff`）先完成6道注入题，6/6 schema、18/18字段、0关键错误；助手审阅真实字段与warnings后，才写入continue-full.json放行完整开发回归。云端 `vision-policy-v2-http-20261027-v1` 已完成48题，原始142/144字段、2关键错误，全部来自E010-Q1的`° C`与`°C`排版差异；无数值错误、漏注入告警、循环或截断。原始分数完整保留，不以字段准确率98.6%掩盖关键错误。
- 新增本地 `assistant_app/backend/visual_units.py`：严格VisionEvidence校验之后，仅规范摄氏度排版（包括`° C`/℃），不改数值、状态、raw_text或其他单位，逐条保存修改前后单位到调用元数据。orchestration.py在再次校验后传递规范结果。**尚未同步到云端实际应用**。`audit_unit_typography.py`用未修改的冻结评分器分别重放原输出及候选管线：原始142/144、关键错误2；管线144/144、关键错误0。旧v1负对照的漏注入告警仍判失败，证明没有放宽该门槛。报告 `reports/vision-unit-pipeline-audit-20261027-v2/`、`reports/vision-unit-negative-control-20261027-v1/`；云端真实应用管线与独立人工验收仍待完成。
- 新9B教学run `20261027-111328-084607`（120行/122答案EOS、30步、loss2.084787）的18应用题与12教学留出题已完成；存在合计遗漏、密度换算/身份误读，未验收。12题随后用于提示词开发，不能再宣称是独立于提示词选择的保留集；它们始终未参与SFT优化，原冻结6道文本封存题未运行。
- Thinking小对照 `text-reasoning-control-20261027-v1` 在2048输出预算内未产出最终答案，已核验PID14024身份后SIGINT停止剩余生成；保留run.json、原token IDs与operator-stop.json，不展示内部推理，不把加大预算当成已解决方案。
- 文本策略小对照v1（6题×原/候选）能修复部分计算，却仍有样本/负号问题；v2小对照又出现错误结案措辞。v3改为保留权限与证据边界的967字符精简策略（SHA256 `f888c4c87fc5ecf4116f45faf07c70d6638a4c417c9dc5b2401faad35dffb291`），8题4bit仍有密度题错误。均未部署，不继续堆提示词或盲目重训。
- 相同新Adapter/精简策略/8个输入的BF16对照 `text-bf16-control-20261027-v1` 也已完成；实际BF16结构已检查（无4bit/8bit量化参数），S02仍有前后矛盾、密度题仍错，不能仅归因4bit。**下一步是关闭Adapter的同精度同输入基座对照，尚未启动**。本地命令已准备在 `reports/text-bf16-base-transfer-command-20261027-v1.sh`，会按现存哈希备份probe_text_policy.py并增加variant选项，再用新报告目录运行8题base；该次Cursor操作在第一步getAXState即因Mac锁屏被阻断，没有传入命令。解锁后先检查现存状态与GPU，禁止把未启动说成已完成，也不要重复旧训练/评测。
- 当前Mac锁屏，已请用户手动解锁并保持Cursor SSH；没有用户要求暂停，也未撤销授权。报告私有HTTP进程PID2894/127.0.0.1:8765仍用于读取已完成证据，后续结束时核验身份再清理自己的进程/转发。所有GPU测试监督器已完成，最近BF16子进程16806结束；再次加载前仍须实查占用。
- 本地稳定性修复：模型入口补EOS并集与venv可执行PATH；双模型入口先文本身份就绪再启动视觉，拒绝错端点/占用端口，使用SO_REUSEADDR避免热重启TIME_WAIT误判，只清理自己的子进程；目录选择要求文本和视觉两端均就绪。**以上和单位排版补丁均未同步到云端或完成真实双模型重启/长上下文/并发验证**。模型质量仍未通过，前端尚未接入。

**最新已完成结果**：8B `vision-http-control-20261027-v4` 全部96生成结束，基座48/48 schema、144/144字段、0关键错误；旧Adapter同为48/48 schema、144/144字段，但E024-Q1漏报注入warnings，关键错误1，未通过当前发布门槛；两版均正常stop、0截断/循环。评分及原始回答未改，不能只报字段100%而隐去失败。实际4bit模型加载6.66GiB，LoRA safetensors 504个tensor keys、无视觉塔训练权重（以runtime-fingerprint.json实际值为准）；本次私有5112服务由监督器正常清理。新9B教学run `20261027-111328-084607` 已完成30步，真实120行/122答案EOS及collator/padding通过，loss2.084787、权重SHA256 `9b5f63b967cda95a9dc32801cecf34ef1d0d442321f32aee90617598a7d9eaaa`。应用18题和教学留出12题均已完成，但S01漏算已知合计、留出密度换算/已给总量/物料身份出现内容错误，不部署，不继续盲目加训。新小对照 `text-reasoning-control-20261027-v1` 已启动：只选S01/S02及两道失败教学留出，共4题×基座/Adapter，原输入、4bit，显式原生thinking模式、8192上下文、2048输出、EOS并集；仅把原生`</think>`后的最终答案解码写入answer，不输出内部推理。先读现存run/response再继续，不重复启动。下方“排队/训练中/HTTP进行中”均为历史。

前端选择目录现已在本地补齐所有图文组合的两端可用性检查：视觉基座离线时不因文本在线而显示可选。`assistant_app/tests`最新82项通过，仍只是本机验证，未同步云端或完成真实双模型联调。当前无正式5001部署、封存测试或独立现场验收。

用户随后强调逐项测试，优先确保前端“Qwen轻量服务”及多模态服务启动稳定。新增验收记录见[Qwen轻量服务稳定性验收记录](QWEN_LIGHT_STABILITY_ACCEPTANCE.md)。本地launch_qwen_light.py改为一个引擎身份就绪后再启动另一个，拒绝占用/相同端口与错误端点，启动失败仅清理自己的子进程；launch_model.py补venv可执行PATH。`assistant_app/tests`81通过；这些启动器改动尚未同步到云端或通过真实双模型重启测试。

2026-10-27最新增量优先于下面的历史快照：9B旧诊断推理漏配聊天结束符。官方9B text_config EOS为248044（`<|endoftext|>`），tokenizer及训练监督EOS为248046（`<|im_end|>`）。旧诊断只使用前者，因此不能把其虚构后续轮次直接判成训练mask失败或Adapter退化。已修正probe_app_text.py默认显式使用两者并集，并保留legacy对照与完整生成token IDs；三道同输入旧Adapter对照S01/S02/T15，legacy均1024截断，分别在第89/122/48位置已输出248046，修正后在90/123/49 tokens结束，直到首个聊天EOS的全部tokens逐个相同。证据见本地/云端 `text-eos-control-20261027-v1/`（本地eos-proof.json）。原冻结测试包不改。

- 修正停止符后：旧9B、第一版新9B、关闭Adapter的基座各18题均正常结束、无截断/循环，但均有内容错误，不能部署。旧版S02前后数值矛盾、T17把最低300件误当实测；新run `20261027-101356-428375` 的S02/T14仍有算术矛盾；基座也有误读样本、虚构批号/版本及无依据的标准/条件。完整原回答保留，本地content-review.json为助手审阅，未冒充独立人工验收。此前“9B已明确需重训”的结论说得过早，须以本次停止符对照和内容证据修正。
- 第一版新训练已真实完成：独立smoke为 `20261027-101048-161304`（1步），正式新run `20261027-101356-428375`（18步/2epoch，loss2.24174）；72行/74个答案EOS与真实collator/padding检查通过，正式Adapter SHA256为 `5269e9d78c4b3a2ee7ed6341605e6a00488e4f71da06751839784972dd9883ee`。它是未验收候选，旧产物全部保留。
- 原36条文本源标签复核：4道算术标签计算正确，资料主要覆盖两篇乳液/3D打印容器研究，不能等同覆盖喷涂/蒸镀助手的全部任务。审校记录在 `reports/text-source-label-audit-20261027-v1/`；没有据此宣称原文事实全经独立专家验证。
- 新教学补充集 `local_training/qwen-text-teaching-20261027-v2/`：原72条两视图逐条不改，追加48条明确虚构、代码精确校验的配比/密度/未知基准/符号身份/样本充分性教学题，共120条（仍只有36个原始研究样本）；另12条参数组不重合的新留出题，不参与优化，也不是独立现场验收。train SHA256 `e73dee6dba473dab9ccce222a258e8d317ff6544d83ca92c147bb97d9b2c1a10`，validation SHA256 `f9958a39238cf408c6ca88058a685539198835088f5e9ba5feb608c5c0cbe27c`；不读测试题/参考答案，不虚构expert_reviewed。120行/122个答案EOS预检通过，最长3083。新配置lr2e-5、2epoch、warmup3，其余保持原训练栈。云端顺序任务 `evaluation/text-teaching-retrain-20261027-v2/status.json` 已排队，等待8B HTTP释放GPU，随后云端预检、全新run训练、18题应用回归、12题留出验证；恢复必须先读状态，不重复启动。
- 8B独立vLLM环境首次启动发现Transformers5保存的extra_special_tokens列表与锁定服务Transformers4不兼容，未修改Adapter或依赖。已核对官方与Adapter词表、151387条按原顺序规范化的merges、added-token IDs及模板正文一致，改用官方tokenizer；两种merges是字符串/数组保存形式差异。v1/v2失败证据保留。后续加载已成功（4bit模型6.66GiB），FlashInfer采样预热因进程PATH缺少ninja失败；现用已核实的内置PyTorch sampler（VLLM_USE_FLASHINFER_SAMPLER=0）、正确venv PATH，不新装CUDA编译器。
- 当前8B完整48题×两版本HTTP回归已在 `evaluation/vision-http-control-20261027-v4/` 实际运行，私有5112、官方tokenizer、原Adapter、候选缺项规则；GET models、base及Adapter真实POST和LoRA kernel均已执行。未完成前不计算完整通过率；结束后supervisor只清理本次已核验身份的服务进程。v1/v2/v3均是运行兼容性/预检失败，不能算质量失败。
- 本地正式模型入口launch_model.py已补从模型配置+tokenizer推导明确EOS并集，尚未复制到云端或正式启动。当前新增相关本机测试16通过；无正式5001部署、无封存测试、无独立现场验收、无Git提交推送。之后先审阅上述完整新结果，再冻结方案。

## 最新进度：9B新run训练与两模型验收

**2026-10-27重连后的最新执行增量（覆盖下方断连快照）**：用户已完成SSH重连，并明确授权按证据整改、必要时新run重训，随后确认“完全去做”。

- 8B完整旧配置开发回归48题×两版本=96次已完成：基座schema38/48、字段112/144（77.8%），旧Adapter schema35/48、字段104/144（72.2%）；两版均0截断、0循环，未达门槛。逐条评分失败均涉及缺项状态/null表示，原始结果保留在 `development-regression-20261027-v1/vision/`。仅明确缺项规则的候选提示词SHA256 `3e5e31dee4fb4adfd6bbec82dffb50e50e85aaa5f5c712fdd5fca6ab52219e57`，在9个代表开发题×两版本=18次小对照全部通过，正常EOS且无循环；仍需完整48题及HTTP/vLLM回归，尚未修改应用提示词或重训8B。
- 9B独立18题×两版本=36次原始单轮评测完成：基座12截断/18，旧Adapter0截断/18，但T15擅自改单位，另有覆盖缺口。真实应用流程捕获的SYSTEM＋用户JSON输入进一步评测旧Adapter18题，9题截断、6题被循环检测命中，包含虚构后续user/assistant轮次。其后同一processor/4bit/应用输入关闭Adapter对照S01、S02、T15三题，3/3正常EOS、0截断/0循环，支持旧Adapter存在应用输入下退化；不能据此宣称基座全领域准确率已验收。报告：`text-app-probe-20261027-v1/`、`text-app-base-control-20261027-v1/`。
- 因此仅对9B建立新训练：原36条批准资料各保留canonical与真实助手协议视图，共72条、仍只有36个独立原始样本，assistant参考答案完全不改，不读测试题/参考答案，不虚构专家审核。原数据SHA256 `02a7d13f9a19753e8843182f364d4269e6ec5a4b870e118b7d3d4bd411e43bd9`，新版 `local_training/qwen-text-app-20261027-v1/train.messages.jsonl` SHA256 `08c200856db129bd21215b06ab5ef027d48200d6dcc8b99e8945fec80b34dd81`。本地及云端72行/74个答案与EOS逐个检查通过，最长3083 tokens；原36行37答案另按原生完整模板复核，未漏正文/EOS，额外监督仅原生分隔换行。
- pipeline.py支持显式prepared-dir并新增完整原生正文/EOS监督核验；1-train.py支持新数据目录、独立max-steps冒烟、真实collator与padding核验，保存新run的input_ids/labels审计，不改变旧数据默认路径。云端原三份脚本快照保存在 `evaluation/before-text-retrain-20261027/`，更新前逐哈希与原件比较；原产物不覆盖。新增 `prepare_app_training.py`；本地相关检查50通过、3项因本地未装datasets跳过，真实训练环境的collator将在训练前再检查。
- 新9B正式配置 `train_config.qwen35-9b.app-v1.json`：4bit、LoRA r32/alpha32、lr5e-5（旧5e-4）、2epoch、batch1/accum8、warmup2、max_length4096、seed3407。另一步smoke使用独立配置warmup0/max_steps1和独立text-smoke目录，绝不当正式服务产物。
- 已启动顺序supervisor，状态在 `/root/autodl-tmp/models/services/qwen-light/evaluation/text-retrain-20261027-v1/status.json`：smoke通过与产物/监督/指标核验后，才从官方基座启动正式新run，再自动跑新Adapter的18题真实应用输入回归；任何阶段异常保留日志并停止，不自动重试或部署。本记录时状态为smoke_running、子进程8283，已进入Trainer编译/0/1步，尚不能宣称新训练完成。控制进程身份记录在云端项目 `reports/text-retrain-launch-20261027-v1/supervisor-launch.json`；恢复先读状态，不能重复启动。
- vLLM已在独立 `/root/autodl-tmp/envs/cosmetics-vllm-20261027` 按原锁定版本与哈希安装，使用已核验清华镜像：vLLM0.22.1、Torch2.11.0+cu130、Transformers4.57.6、xgrammar0.2.5.post1，补充官方哈希固定bitsandbytes0.49.2；主环境194包依赖检查通过，补充包CPU导入通过，GPU量化/LoRA尚未验证。原Unsloth训练环境保持Torch2.8.0+cu128/Transformers5.17.0。安装过程保留失败CLI参数日志和成功记录，无模型重新下载；当前空闲磁盘约37GiB。`evaluate_http.py`已准备，只准显式loopback开发题，原始HTTP/请求指纹逐次保存，不重发未知结果，也不把HTTP stop冒充真实EOS token证据。

当前下一步：核对supervisor的smoke、正式训练、新9B真实应用回归 → 人工审阅结果 → 在隔离vLLM验证实际4bit/LoRA与8B候选提示词完整开发回归 → 方案冻结后才封存测试、独立现场验收和部署。旧9B已确认不能接入当前助手；8B小对照通过不等于全量或现场通过。本轮未启动正式模型服务或5001助手，不提交或推送这些新增改动。

用户已明确继续任务，并确认本地 `化妆品工艺图像采集包_20261026/` 中的图片已审核、可用于训练，且表示云端为同一目录。74张便携训练图与本地该采集包原图哈希逐张一致；这份用户授权不改写历史 `expert_reviewed=false`、权利来源标记或真实现场记录属性。当前云端代码目录下未找到原采集包清单，但既有便携训练包及74张图片均可用，无需重新下载模型或clone项目。

- 官方基座诊断的导入路径及SizeDict序列化问题已修复，使用已校验的冻结包内evaluate，未安装同名第三方evaluate。`official-base-probe-20261027-v3` 完成14次生成：3条原始宽泛问题均到256上限、未循环复读；同图改为简洁可见内容问题均正常EOS；8个开发冒烟任务7个通过，1个因unknown状态配字符串值而不符合严格合同。不是正式验收或全量正确率。
- 已逐图检查74张训练图片、审校82条旧标注。JSONL容器未发现错误；82条均为自然语言答案，与线上VisionEvidence JSON任务不一致，74条共享同一宽泛问题，部分答案依赖未输入的来源说明。8条具体读数另核对了原页；图像本身不因这些任务问题而废弃。完整审校在 `reports/8b-label-audit-20261027/`。
- 正在云端 `development-regression-20261027-v1` 顺序运行：8B 48题×基座/Adapter共96次生成，然后9B 18题×两版本共36次。断连前最后可读取的快照是8B 31/48题、62次生成：基座字段74/93、微调69/93，完整性均未满足；基座6条和微调8条因不确定字段非null而校验失败，基座另1条把缺项占位说明标为readable。两边当时均0循环复读、0截断。不得把快照当完整结果，9B尚无已读取的新结果。
- 本地新增 `prepare_aligned.py` 并生成独立 `local_training/qwen-vl-aligned-20261027-v1/` 候选包，82条/74图，完整system＋用户JSON＋VisionEvidence答案。原数据与图片未改；历史审核标记保持。候选仅8条针对性读数任务，仍需评估任务比例，**未推荐直接作为完整OCR训练集，未启动新训练**。dataset.py新增可选system字段，旧样本保持原两轮消息；HF真实Dataset变换测试尚待云端执行。
- 本地准备 `probe_vision_policy.py` 和 `vision-policy-candidate-v1.txt`，只明确缺项/占位说明、unknown/uncertain与JSON null的含义；冻结包与应用提示词均未修改。默认仅校验文件，必须显式--run；只准开发题，保留48题分母。先用同processor/同量化/同生成参数比较该配置修正，再决定是否重训；不替换原始输出字符串、不放宽评分。
- 云端确认Python3.12.3、Torch2.8.0+cu128、Transformers5.17.0、Unsloth2026.10.8，两个现存env均无vLLM。显卡标称RTX4090但实际49140MiB，驱动595.71.05，当前数据盘剩余约54.38GiB。项目vLLM锁为0.22.1/Torch2.11.0/Transformers4.57.6，不能直接在训练环境安装覆盖；后续须隔离环境核对两模型兼容性、实际源/镜像/体积。本轮没有安装或升级核心依赖。

当前阻塞：Cursor Remote SSH断连并弹出root密码框，已请用户在Cursor自行输入完成重连，不能从聊天索取密码。回归由独立后台supervisor启动，断连后是否继续及完成须重连核对；**不重复启动任务**。私有报告下载HTTP仅绑定云端127.0.0.1:8765，经Cursor转发回本地，同一路径只服务evaluation报告；本轮PID2894，结束后核验身份再停止本轮进程并移除本轮转发。断连期间未向云端复制新的dataset修改、候选标注或提示词诊断脚本。

恢复顺序：检查现有supervisor/两个run报告及GPU → 下载完整8B/9B原始结果并复核评分 → 运行提示词小对照 → 结果支持时再选结构约束或新run训练。封存测试、独立真实现场验收与部署均未进行；训练图回放不能同时当独立现场验收。以下暂停点是2026-10-26历史，不覆盖本段。

**后续发布与传输更新（同日）**：用户随后明确授权将完整测试包作为开源项目的一部分上传 GitHub，并传入现有云服务器。已发布到仓库 `evaluation/`，包含原始压缩包、SHA256 文件与单独下载／稀疏检出说明；压缩包提交为 `9540ea1c2a006aa67f6d59f4fc448b4305d99781`。包大小 3,054,964 字节，SHA256 为 `8b1ec491b099ae91383918a3c46ebbcd3124823e6a3d59362a0dcff0ddb6a399`。通过 Cursor 既有 Remote SSH 终端，在独立目录 partial clone＋sparse checkout 仅检出 `/evaluation/`，校验后将压缩包及校验文件放入现有云端项目根目录，两文件 SHA256 均匹配。本轮未解压、未运行评测、训练或服务。详见 [公开测试目录](https://github.com/JHY-1224/Cosmetics-Spray-Manufacturing-AI-System/tree/main/evaluation) 和本地 `reports/evaluation-publish-20261026-zUdzmZ/`。下文“不需要改GitHub／尚未上传”属于制作测试包时的历史状态；包内冻结说明与原包字节保留，不重新打包。

## 下一步：compare已完成，排查8B复读

**2026-10-26暂停时的历史接续点**：用户明确要求先暂停，助手停止继续启动测试／训练／服务。82条监督重建已通过，详见下段。新增 `day1/multimodal/verify_official_base.py` 已复制云端并尝试启动（PID101956），但在顶层 `from evaluate import ...` 时出现 `ModuleNotFoundError: No module named 'evaluate'`，发生在GPU框架与模型加载前，未生成run.json或回答；日志保存在 `/root/autodl-tmp/models/services/qwen-light/evaluation/official-base-probe-20261026-v1.log`。云端项目根的day1/multimodal尚没有该评测模块，评测工具在独立包中；恢复时先明确导入包内已校验的evaluate及其应用合同，不能安装同名第三方evaluate包来掩盖路径问题。此失败不是新模型质量结果。最后观察的nvidia-smi无计算进程。诊断脚本本机仅完成语法／--help检查，不代表云端可运行；修正后先做导入和路径预检，再分配新日志／报告目录，保留失败日志。

**同轮授权清理已完成**：用户决定将两套大模型留待96GB服务器，并再次明确授权删除当前云端32B／27B。清理前核对目录文件数与大小、无symlink、无Adapter引用、无打开／映射权重；先备份全部非权重元数据与清单，再定向删除 `/root/autodl-tmp/models/Qwen3-VL-32B-Instruct` 和 `/root/autodl-tmp/models/Qwen3.8-27B`。实际释放122,312,888,320字节（113.91GiB），当时剩余154.38GiB；8B／9B基座目录仍存在，其Adapter与数据不在删除范围。云端元数据与结果在项目 `reports/removed-large-bases-20261026/`，不保留大权重副本，恢复须重新下载并校验匹配的官方版本。第一次分片匹配和第二次报告父目录创建均在删除前保护停止；修正后完成，不能把失败重试当作已删过三轮。截图在本地 `reports/cloud-supervision-audit-20261026/cloud-cleanup-result.png`。48GB不适合作为双大模型常驻＋长上下文＋高并发的目标配置，不等于已实测任何上下文都无法运行；96GB量化/并发预算仍需压测。

**最新进展：真实collator监督重建已完成**：用户随后授权助手操作Cursor现有Remote SSH，核验训练监督、审校8B数据，必要时修正并建立新run，保留旧产物；9B单独评测，通过验证后再部署。2026-10-26助手在云端原训练环境执行新增 `day1/multimodal/audit_supervision.py`，采用原8B脚本的4bit/r16/alpha16、原82条数据、相同像素预算与真实Unsloth collator，未执行优化器步骤。82/82条原生模板尾部与实际展开input_ids精确对齐，回答正文及EOS均被监督，user/image/assistant-header/padding未误入监督，最长与最短样本的padding混批也通过；输入374—1616 tokens，没有超过4096。没有发现回答／EOS掩码错误。该结果是当前流程重建，旧训练没有保存逐批tensor，不能冒充历史tensor取证；也不证明标注正确、训练强度合适或推理效果通过。

云端报告 `/root/autodl-tmp/models/services/qwen-light/evaluation/supervision-audit-20261026-v1/supervision-audit.json`，SHA256 `1314427d33d1f66791f8d8b4219bcfe1209335abd6b415479b2d782e60a76314`。数据与原train_vl.py哈希均未变；pad=151643、EOS=151645，collator屏蔽ID不含EOS。本机审计边界测试8项通过，观察摘要和截图保存在 `reports/cloud-supervision-audit-20261026/`。Adapter嵌套image_processor配置已在云端读到，像素预算262144—1572864，与训练脚本一致。尚未修改训练数据、训练脚本、依赖或权重，未开始新训练或部署。接下来做官方基座＋官方processor诊断，继续审校任务／答案及9B独立回归。下文“尚需核验监督／未运行GPU”是此前阶段记录，不覆盖本段的新结果。

**标准库配置检查已收到结果**：用户云端输出确认两者processor_class均为Qwen3VLProcessor、EOS文本均为`<|im_end|>`、pad均为`<|endoftext|>`；Adapter基座路径正确，LORA r=16/alpha=16。Adapter目录没有generation_config.json不能单独判为停止配置损坏，compare实际已使用基座的EOS ID `[151645,151643]`，且一条能正常EOS结束。

**模板正文已证明一致**：本地官方chat_template.json文件SHA256与用户云端值 `5c72a170d2a4a1a3bc5adad2e689ae28138a9700e5b8c96c0266331e86c0acce` 相同；解码其chat_template字符串后，正文SHA256为 `3636d0f0bd6bef02654cdffdc447b79cb2cef8ab02cc75267345946291a489e4`，恰与用户Adapter的jinja文件一致。本地官方tokenizer内嵌模板也与解码正文相同。因此不能以JSON容器文件哈希与jinja哈希不同认定模板变化；这项仅证明磁盘模板正文，不代替实际运行时选用及渲染验证。

用户Adapter的image_parameters显示为空，是上一条检查只取preprocessor_config.json顶层的摘要。本机同版本Transformers5.17.0的ProcessorMixin.save_pretrained将非tokenizer组件保存到processor_config.json复合配置；因此目前不能认定视觉参数丢失，需读其中image_processor等嵌套配置。训练源码本来就显式改变像素预算，数值变化也不能直接等同错误。

**训练资料范围复核**：本地metadata SHA256仍为 `66eeb0c7de39c3963857dbff50f2d231680823c3949a5e9ba879c6066f19fd81`，82条、74张、16来源组，均train，专家审核0条；74条主题／来源上下文标签，8条具体读取，0条answer为JSON对象。JSONL容器格式没有被本轮判错；建议围绕实际读图、单位／缺项及VisionEvidence合同审校和改进任务／答案，明确可见事实与来源上下文。缺JSON训练答案不证明模型必然不能输出JSON，也不是复读唯一根因已找到。原图保留，按图核对关联与质量，不因当前日志全量重新采集；是否训练新8B run须先核验真实collator的回答／EOS监督及运行设置。当前8B Adapter尚不具备上线依据；9B另按开发回归评价，不由8B故障推定重训。本轮未改数据、图片、权重、训练脚本或云端依赖。

**配置检查命令修正**：用户直接导入 `transformers.AutoProcessor` 的检查在导入阶段失败，调用链为Transformers→torchao，最终 `ImportError: cannot import name 'ScalingType' from 'torch.nn.functional'`；同时日志提示该torchao扩展要求Torch≥2.11、现有为2.8.0+cu128。失败发生在加载processor／模型之前，不是新的8B生成结果，也没有修改权重。此前已成功的是Unsloth入口；不能据此未经核验就升级Torch、CUDA、Transformers或删除torchao。上一条直接AutoProcessor检查命令应停止沿用，先改用仅Python标准库读取两目录的tokenizer、processor、preprocessor、generation配置及聊天模板哈希。标准库命令已在本机官方processor文件＋合成Adapter元数据上验证语法和缺失文件分支，不代表云端配置比对已完成，也不代替运行时collator／EOS监督验证。

2026-10-26用户随后提供完整compare输出，云端报告为 `/root/autodl-tmp/models/services/qwen-light/evaluation/20261026-125538-358999-compare/vision-paired.json`。6次生成已完成、Adapter为default。本地保留用户原始终端文本、解析JSON及分析记录于 `reports/compare-analysis-20261026-qsv3bfu7/`，本助手没有重跑GPU。

三条base均输出256 tokens、无EOS、达到上限；回答有连续论述，未观察到微调版那种明显循环，但存在传感器、热成型、高温高压等无充分依据的推断。finetuned的S28-002与S28-004明显循环并达到256上限；S28-006输出30 tokens、带EOS正常结束，但“玻璃瓶口密封胶圈”与训练参考不一致，不能判为正确。训练参考属于来源上下文标签且未经专家审核，不能直接据此计算独立准确率。

当前证据支持“开启Adapter后出现明显循环退化”，不证明唯一根因、权重损坏或必须重训；第三条能以EOS结束也不能证明所有训练样本EOS监督都正确。base三条因长度截断，不可一概称作复读或停止符失效。本轮compare未使用应用完整VISION/JSON提示词，不能把这里的复读仅归因于VisionEvidence格式差异。

接下来先只读检查官方基座与Adapter的processor、聊天模板、图像预算、特殊token/停止配置及Adapter基座身份，再重建实际训练collator核对答案与EOS监督。当前train_vl.py的预检只证明长度不超预算和labels非空，尚不足以逐token证明声明的mask全部正确；这是一项待核验环节，不代表已经证实mask错误。必要时以官方基座＋官方processor复核共同识图问题。先保留现有产物及原始输出，不加复读抑制掩盖失败，不立即重训或进入封存测试。

### 已完成的check与compare执行记录

2026-10-26用户已贴出云端手动执行的完整输出：压缩包SHA256与包内清单检查通过，解压成功，check完整结束，状态为 `files_checked_no_inference`；development的cases与expected_cases均为48，8个类别各6题，cases_sha256为 `5f828b5682f1c8bf76fae6add5ae5ead728f9a23020e754c1212f0b08cf4a3f1`，`production_accepted=false`。报告目录为 `/root/autodl-tmp/models/services/qwen-light/evaluation/20261026-125359-172543-check`。这是用户终端输出证据，不是助手另行运行的结果；尚未执行新的GPU对照，不代表模型效果通过。

当前下一条手动命令为：

```bash
cd /root/autodl-tmp/code/Cosmetics-Spray-Manufacturing-AI-System &&
bash evaluation-kit-20261026/run_tests.sh compare
```

执行前用 `nvidia-smi` 查看当前GPU占用；如有其他训练或推理任务，先核对，不自动结束未知进程。compare会加载8B并做6次生成，使用已有训练环境与Adapter，保持本轮生成参数不变。完成后查看新报告目录中的 `vision-paired.json` 和 `terminal.log`，用户贴回完整输出后再决定分支。当前不进入regression/final，不重新训练或启动模型／业务服务。

以下为已完成的解压检查指令记录，不需再次解压：

```bash
cd /root/autodl-tmp/code/Cosmetics-Spray-Manufacturing-AI-System &&
sha256sum -c evaluation-kit-20261026.tar.gz.sha256 &&
if [ -e evaluation-kit-20261026 ] || [ -L evaluation-kit-20261026 ]; then
  echo '解压目录已存在，停止，请先核对；不要删除或覆盖。'
else
  tar --keep-old-files -xzf evaluation-kit-20261026.tar.gz &&
  bash evaluation-kit-20261026/run_tests.sh check
fi
```

check需要完整结束，看到 `Test package integrity: OK`、`status: files_checked_no_inference`、开发题数量48及报告路径；这不验证GPU、模型效果或Adapter激活。若目录已存在或check失败，先检查原目录／错误，不覆盖、不直接进入compare。入口默认调用现有训练Python，无需重新安装或下载模型。

check输出确认后，下一阶段才执行 `bash evaluation-kit-20261026/run_tests.sh compare`。默认前三条训练样本、关闭／开启Adapter，共6次生成；同Adapter目录processor、同4bit基座、同模板、seed=3407、greedy、max_new_tokens=256。查看 `vision-paired.json` 的两版本原回答、`ended_with_eos`、`hit_output_limit`，训练样本回放不计独立正确率。

判断分支：关闭正常而开启退化→核对标签、回答／EOS监督、LoRA模块及训练强度；两边都异常→检查共同模板、processor、像素输入、停止条件、精度及运行栈，并以官方基座＋官方processor另作复核，不能直接认定基座能力不行；普通回答正常但应用结构化测试失败→核对训练任务与VISION提示词／VisionEvidence合同；内容连贯但达到输出上限→区分输出预算不足与循环复读。compare不带应用完整JSON任务，结构化分支须以后续开发测试证据确认。

先保存并阅读本轮结果，再决定是否修改配置或创建新的训练run；不加复读抑制参数掩盖原问题，不动现有权重，不提前跑regression/final或启动vLLM／5001服务。

制作测试包时的历史选择：仅保存完整测试资料，使用独立测试包传服务器，不需要改GitHub或重新clone。当时没有上传新测试代码、改云端服务配置、重新训练或替换权重；后续上传事实以上方更新为准。

## 当前事实

- 8B训练完成：82条、1 epoch、21步；adapter为 `/root/autodl-tmp/models/services/qwen-light/vision/20261026-194847-896921/adapter`。
- 9B训练完成：36条、3 epochs、9步；adapter为 `/root/autodl-tmp/models/services/qwen-light/text/20261026-195051-375615/adapter`。
- 两个adapter均已在用户云端加载和生成。9B目前只核对了S01：A+B合计15g，各自未知，回答符合该题。
- 8B前三个训练样本回放出现错误描述与明显复读，当前视觉质量不通过。不能用loss下降或adapter激活代替验收。
- 本地改的是测试工具。文件不会自动同步到云端；独立包包含所需测试脚本快照，解压到新目录运行，不覆盖项目源码。
- 8B多数训练标签是主题短句，应用的VISION提示词和VisionEvidence合同要求结构化JSON，必须测试这层差异；尚不能据此确定唯一失败根因。
- 9B基座73秒、微调版2.87秒的单次耗时包含先后执行/编译/输出长度差异，不能据此宣称微调带来25倍提速。

## 保存内容

`data/vision-eval-v2/`：32张原创建模图片、64问、192个字段；48问开发回归＋16问封存测试。涵盖配料、参数设定/实测、批次、流程箭头、缺项、单位符号、证据边界和图片内注入。每图的全部问答同组同划分；检查与训练图片哈希不重合，禁止相同题目/完整参考跨划分。`review.html`可对照图片与答案。

`data/text-eval-v1/`：24道文字题，18道开发回归＋6道封存题。前12道沿用既有smoke题，补充加量提醒、计量基准、冲突、温度语义、复测充分性、身份、单位换算和注入等。每题有人工验收要点，脚本不把这些要点送给模型。

这些是合成/开发测试资料，不是真实现场检验或正式生产参数。图文封存题仍共享生成器，不能宣称跨工厂泛化。原82条已用于训练，只用于诊断回放，不能计入独立正确率。早期 `vision-eval-v1` 为本地初稿，最终包只使用完成去重检查的v2。

## 分轮顺序

1. **检查包**：哈希、图片/标注对应、划分、训练重合检查；不加载GPU。
2. **定位8B退化**：同图、同processor、同量化基座、同生成参数，关闭/开启LoRA对照。记录EOS、截断和原回答；不加复读抑制参数来掩盖失败。
3. **开发回归**：8B按应用实际VISION提示词与JSON合同测48问（两种variant共96次）；9B测18题（两种variant共36次）。逐项人工看原图和回答，保留失败与未知。Greedy/HF测试没有vLLM的JSON语法约束，HTTP接入仍须单独测。
4. **按失败原因处理**：基座正常而adapter退化，检查标签、EOS监督、训练任务和强度，必要时训练新的run；两边都失败，检查模板、图片处理、生成设置和运行栈。先有对照证据，不能直接认定只能重训。
5. **冻结方案后测封存集**：视觉16问、文本6题；不把封存题用于反复调参。如果使用其结果开发，须标为已用并另建真正未用的最终集，不能不断重测直到碰巧过关。
6. **服务验收**：完成真实未见现场图/专家复核；再核对vLLM兼容与双组件显存，测HTTP图文链路、多轮上下文、加量提醒、注入、限流/超时、前端统一选择和飞书。通过后才决定正式服务启用。

## 评分与通过边界

视觉自动核对JSON合同/图片ID、字段行列、数值、单位、缺失保留、注入警告、EOS、截断、复读。固定标签精确匹配，数值允许有限Decimal等值；同数值错单位仍失败。标签同义改写若被扣分应由人查看原回答，不允许静默放宽评分。

开发回归的暂定最低条件：完整执行本轮全部题、JSON合同全部有效、关键字段零错误、无循环复读/输出截断、字段正确率≥95%。这仅表示可进入人工核验；文本逐题核对全部review要点。模型与现场系统都不会被脚本自动批准，报告始终保留 `production_accepted=false`。实际图片真实性、领域正确性、业务适用性和现场验收另行核对。

## 单独上传、解压和运行

本地包：`reports/evaluation-kit-20261026.tar.gz`，校验文件与核验报告在同目录；不含基座、adapter、客户原件、API密钥或字体文件。
仅把tar.gz上传到 `/root/autodl-tmp/code/Cosmetics-Spray-Manufacturing-AI-System/`，不用重新clone。

```bash
cd /root/autodl-tmp/code/Cosmetics-Spray-Manufacturing-AI-System
tar --keep-old-files -xzf evaluation-kit-20261026.tar.gz
bash evaluation-kit-20261026/run_tests.sh check
bash evaluation-kit-20261026/run_tests.sh compare
```

先检查与对照，保存/查看输出再决定后续。不要把训练原图回放当作独立正确率。

```bash
# 完整开发回归：8B和9B依次执行，不同时占GPU
bash evaluation-kit-20261026/run_tests.sh regression

# 仅在开发方案冻结、人工核验完毕后执行封存测试
bash evaluation-kit-20261026/run_tests.sh final
```

入口使用已验证Python `/root/autodl-tmp/envs/cosmetics-unsloth/bin/python`，不安装依赖。报告保存在 `/root/autodl-tmp/models/services/qwen-light/evaluation/` 的全新时间目录，失败保留已有输出。脚本不下载、不训练、不启动业务服务、不覆盖项目或权重。`--keep-old-files`遇到同名目录时停止，保留原目录，不强行覆盖。

未来训练新adapter时，可通过 `COAT_EVAL_TEXT_ADAPTER` / `COAT_EVAL_VISION_ADAPTER` 指定新目录；基座路径仍显式固定在云端models。重新同步项目也只需正常git pull，原训练产物独立保存，无需重新clone。
