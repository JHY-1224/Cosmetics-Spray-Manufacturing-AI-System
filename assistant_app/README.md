# 涂镀智溯 · 多模态工艺助手

> 最终云端入口见[云端操作手册](../docs/CLOUD_QUICKSTART.md)和[AGENTS第37节](../AGENTS.md)。Linux NVIDIA承担训练与推理，Mac仅开发与示范。新增Qwen轻量级服务将9B与8B两套微调产物作为一个前端选项；历史“vision_base”说明只描述原始预览模式。真实就绪要求两份核验和两个服务端点均通过。

独立的 **PC 网页助手**，默认 `127.0.0.1:5001`。手机端使用飞书应用机器人，不提供手机网页布局。页面采用参考图的浅色侧栏、居中欢迎区、问题卡片与底部输入框；整页按当前浏览器窗口自然展示，不设置整页最小/最大宽度、固定比例或浏览器强制视口；聊天内容保留适当阅读宽度，窄窗口不切成手机布局。

**模型选择增量**：选择入口已移到输入框右下方，分阿里云、本地微调、本地原始模型。后端读取统一 `models/` 目录（可由 `ASSISTANT_MODEL_DIRECTORY` 配置）及服务端模型描述；每条请求冻结选择，不改其他访客或飞书后台的默认模型。尚未部署或未核验的条目明确显示状态，页面不再展示飞书状态/配置。登记方式见 [模型选择目录说明](configs/model_profiles/README.md)。

## 当前连接和验证事实

2026-10-26 用户追加授权：使用已有阿里云大模型连接，实际测试成功后替换本助手连接。已真实测试并选用：

| 通道 | 实际模型 | 状态 |
| --- | --- | --- |
| 文字理解、回答 | 阿里云百炼 `deepseek-v4.1-flash-1017` | 真实短问答及网页生成成功 |
| 图片识读 | 阿里云百炼 `qwen3-vl-plus` | 合成图片识读、两张图片逐张提取成功 |
| 多轮 | 同一独立会话的服务端历史 | “第二张图的温度”追问成功，正确保留缺测 |
| 本地文本 | 用户已有 Qwen3.5-9B | 文件存在，未加载GPU，未发现9B Adapter |
| 本地视觉 | 用户已有 Qwen3-VL-8B-Instruct | 下载后文件存在；未加载GPU、未进行视觉微调 |
| 飞书 | 新增应用机器人通道代码 | 默认 disabled；模拟测试与配置说明，未进行真实飞书联调 |

当前运行配置在本目录的私有 `.env`（0600），不包含在公开示例中。只读取原平台连接的必要字段，原平台私有配置未修改。网页明确显示阿里云模式，文字和图片会发送至配置的阿里云端点，不能将其称为厂内离线推理或本项目微调模型。

## 本机启动、检查和停止

本机已准备 `assistant_app/.venv` 供开发使用，复用现有项目和内置运行时的已安装包，没有新下载大型依赖。该本机环境不是可搬运的部署包。

在项目根目录：

```bash
assistant_app/.venv/bin/python assistant_app/scripts/run_assistant.py
```

从其他目录调用时给出上述 Python 和脚本的绝对路径即可。也可双击本目录的 `启动工艺助手.command`。启动器默认读取本目录的 `.env`；其他配置使用 `--env-file /absolute/private/config.env`。

访问 [PC 助手](http://127.0.0.1:5001/)。无需注册、密码或访客填写 API Key。模型不可用时保留页面，并返回真实错误，不回退模拟答案。

```bash
assistant_app/.venv/bin/python assistant_app/scripts/status_assistant.py
assistant_app/.venv/bin/python assistant_app/scripts/stop_assistant.py
```

也可在前台终端按 Ctrl+C。停止脚本只对启动回执中 PID、创建时间、脚本路径一致的助手发送 SIGTERM。不会执行模糊 pkill，也不会停止8001或模型进程。

## 干净环境部署

使用 Python 3.12 或3.13，单独创建轻量 Web 环境，按本目录 `requirements.txt` 安装直接依赖。安装源优先官方 PyPI；国内可评估清华镜像 `https://pypi.tuna.tsinghua.edu.cn/simple`，保持版本、TLS和已有锁文件不变。本目录不安装 Torch、CUDA、Unsloth 或 vLLM，也不自动安装依赖。

```bash
python3.12 -m venv assistant_app/.venv
assistant_app/.venv/bin/python -m pip install -r assistant_app/requirements.txt
cd assistant_app/frontend
pnpm install --frozen-lockfile
pnpm build
```

前端精确版本和 pnpm 锁复用原 React19、Ant Design5、Vite 环境，构建只写本目录 `frontend/dist`。本机通过独立 `node_modules` 链接复用已有安装，没有写原工作台 `frontend/dist`。

默认只监听本机。公网部署必须明确设置 `ASSISTANT_PUBLIC_ORIGIN=https://实际域名`、`ASSISTANT_SECURE_COOKIE=true`，通过受控 HTTPS 反向代理访问；监听地址仅在明确部署时配置。不修改云防火墙，不直接公开GPU推理端口。当前实现只支持**一个 API 进程、一个有界执行器**，文件锁拒绝多 Worker 共用同一数据目录。

## 模型配置与切换

完整示例见 `configs/assistant.example.env`，说明性模型清单见 `configs/models.example.json`。所有变量使用 `ASSISTANT_` 前缀；浏览器不接触模型密钥、内网端点和本地权重路径。

| 变量 | 用途 |
| --- | --- |
| `TEXT_MODEL_BASE_URL` / `VISION_MODEL_BASE_URL` | 服务端模型HTTP地址，包含 `/v1` 或 `/compatible-mode/v1` |
| `TEXT_SERVED_MODEL` / `VISION_SERVED_MODEL` | 实际服务模型名，响应身份也必须匹配 |
| `TEXT_API_KEY` / `VISION_API_KEY` | 各通道后端凭据 |
| `MODEL_HOSTS` | 显式批准的模型主机，不接受访客指定URL |
| `TEXT_MODE` | `finetuned_lora`、`finetuned_merged`、`base_preview`、`approved_cloud` |
| `VISION_MODE` | `vision_base` 或 `approved_cloud` |
| `TEXT_MODEL_PATH` / `VISION_MODEL_PATH` | 本机真实基座路径；不自动下载 |
| `TEXT_ADAPTER_PATH` / `TEXT_MERGED_MODEL_PATH` | 明确选定的9B训练产物 |
| `MODEL_VERIFICATION_FILE` | 离线产物核验报告，不能用文件名代替核验 |
| `ALLOW_BASE_PREVIEW` | 显式允许未微调文本基座接线预览 |

表中每个变量都须加 `ASSISTANT_` 前缀。

默认本地配置仍期望9B微调版，缺产物返回 `text_adapter_missing`。只有显式设置 `TEXT_MODE=base_preview` 和 `ALLOW_BASE_PREVIEW=true` 才使用9B基座预览，页面明确标注未加载本项目9B微调版本。当前私有配置则明确选用用户后来授权的 `approved_cloud`，不是自动回退。

`configure_aliyun.py --source /absolute/approved-private.json --vision-model qwen3-vl-plus` 可显式导入已有获准连接到**新的**私有配置；拒绝覆盖已有文件，不修改来源，不输出密钥。切换配置后只重启本助手。

### 两种本地模型网络方式

同在云服务器：助手调用云端 `127.0.0.1:5101/v1`、`:5102/v1`。

本机网页、云端模型：使用已有获准 SSH 通道转发到本机5101/5102，或配置明确获准的受控 HTTPS 内网端点。示例（将占位符替换为真实授权地址）：

```bash
ssh -N -L 5101:127.0.0.1:5101 -L 5102:127.0.0.1:5102 已获准的云服务器
```

本机127.0.0.1不会直接代表云服务器。浏览器始终只访问5001。

### 离线检查与推理入口

```bash
assistant_app/.venv/bin/python assistant_app/scripts/preflight.py
assistant_app/.venv/bin/python assistant_app/scripts/check_models.py --output /new/private/report.json --hash
```

preflight 只查配置、分片、tokenizer/processor/聊天模板、当前环境版本、端口和GPU信息；`--hash` 会读取全部权重，生成本地哈希清单，不下载、不加载权重。官方来源真实性另与 `models/*.manifest.json` 对照；文件存在检查不等于逐文件SHA256验证。`--probe` 是显式真实HTTP文字生成，不能仅凭模型列表证明推理成功。请在需要检查的模型配置环境运行，不把云端模式报告冒充本地文件验收。

独立GPU启动入口：`scripts/launch_text_model.sh`、`launch_vision_model.sh`。必须指定现有 `ASSISTANT_MODEL_PYTHON`、`CUDA_VISIBLE_DEVICES` 和每个模型的 `ASSISTANT_TEXT_GPU_MEMORY_UTILIZATION` / `ASSISTANT_VISION_GPU_MEMORY_UTILIZATION`，启动前打印实际GPU和包版本、检查分片和端口。两张卡可分开指定；同卡的两个常驻模型预算须共同评估，不可各占0.9。启动脚本拒绝云端模式误启动本地模型，且不下载量化模型、不清理未知GPU进程。

文本默认端口5101、视觉5102、上下文8192、各模型并发1；均可通过显式配置调整。文本采用 language-model-only。LoRA需先核对当前 vLLM/模型/量化组合，再显式设置 `ASSISTANT_VLLM_LORA_VALIDATED=1`；不因为框架通常支持LoRA就声称本组合已通过。运行时不开放访客加载/卸载Adapter API。

本地模型脚本前台运行，分别按 Ctrl+C 停止各自进程，不影响网页。实际GPU双模型共存峰值、量化兼容性和Adapter命中本轮未验证。

## 9B独立训练入口

复用原 `day1` 的批准数据、原生模板分词、全部assistant回答+EOS监督mask、Unsloth/TRL训练与独立run产物，新增专用配置和入口：

```bash
# 在已有获准GPU训练环境、项目根目录；先离线检查
python day1/3-train/2-train-9b.py --model-dir /root/autodl-tmp/models/Qwen3.5-9B --dry-run
# 用户另行决定训练时执行，不随网页启动
python day1/3-train/2-train-9b.py --model-dir /root/autodl-tmp/models/Qwen3.5-9B
```

新配置保留现有r32等训练参数，只保留1个阶段checkpoint以减少临时磁盘占用；每次仍使用新run_id，最终adapter独立保存，不自动合并。记录实际可训练模块。旧27B Adapter不能迁给9B或VL；没有图文标签时不编造视觉微调。当前36条批准训练样本、无独立验证集的边界保留，候选/test/review不并入训练。

基座/Adapter成对核验继续使用现有 `day1/4-validation/3-test-adapter.py`，默认只读，只有 `--run` 才显式加载GPU；生成新的报告目录。`check_models.py --runtime-evidence /actual/environment.json` 支持记录该证据；该训练环境证据也不能单独证明另一个vLLM实例已命中Adapter，页面不宣称运行时Adapter已验证。

## 请求、会话和图片

### 工业提示词与加量核对（2026-10-26增量）

`backend/prompts.py` 统一管理 `industrial-assistant-v2` 的文字与视觉规则，所有服务器登记的多模态服务都使用同一套规则；每次任务记录版本和两个提示词的SHA-256。修改此文件后需重新启动助手进程才会加载新规则。

规则覆盖全文字图片、配料/组成表、参数截图、跨页关系和工艺简图，要求保留表头、行列、原文、单位与不确定性。范围限于包材工艺、品质追溯和相关材料验证；没有批准SOP、实际数据和适用窗口时，不能编造可直接上机的参数、加料指令或正式放行结论。用户/图片/视觉结果/历史回答都不能授予权限或覆盖服务端规则。

`backend/quantity_review.py` 对当前输入里明确写出的“原来20克，本次80克”等可比数字做Decimal核对，达到2倍或降至一半等较大变化时显示独立提醒。2倍是软件的数据变化提醒阈值，不是生产工艺限值；不同物料、质量/体积基准不混算。基准未提供时提示补资料，不推算安全加量。提醒随任务持久保存、模型失败仍可显示，关闭页面或重新进入不会变成批准或执行记录。当前规则只比较明确输入，不自动读取MES历史或猜测其他批次；中文数字、图片内数值及复杂多物料条件由模型提示并待人工核对。

注入模式识别仅记审计信号，不把正则匹配当安全保证，也不因文档讨论“注入”就拒绝正常读表。服务端拒绝模型工具调用、不完整输出和配置密钥回显；渲染禁止原始HTML、危险协议和外部图片。`ASSISTANT_VISION_MAX_OUTPUT_TOKENS` 默认4096，读取超过30字段的表格时必须说明省略/请求局部图，不宣称已完整读取。

接收请求体增加并发上限（`ASSISTANT_BODY_CONCURRENCY`，默认8），生成仍受队列8/并发2和持久预算控制。任务出队、每次模型调用前和回答保存前重新核对会话有效性。请求日志只记请求ID、方法、路由模板、状态与耗时，不记查询参数、正文、凭据和图片内容。

本轮测试记录位于 `../reports/industrial-hardening-20261026-150900/`。真实云端冒烟仅使用合成问题与虚构文字表格，不包含客户现场资料；少量测试通过不等于普遍抵抗所有提示词注入。公网入口或非回环监听现在强制要求账号网关密钥，所有请求须同时有网关注入的身份头和密钥；Cookie会话绑定账号摘要，切换账号不能沿用旧Cookie读取对话。网关负责真实账号认证和撤权，必须覆盖客户端自带的同名头，后端端口仅允许网关访问。部署方仍需配置HTTPS、真实账号/远程授权及实际容量验收；不能靠提示词阻止服务器管理员拷贝权重。

静默签发独立 HttpOnly、SameSite Strict Cookie，所有对话、图片、任务、SSE、反馈、删除和取消均在后端检查归属；Cookie写请求检查CSRF和Origin。身份不使用IP，也不信任浏览器owner字段。清空对话会取消其任务、删除独立数据库记录及重编码图片。

文字仅调用文本模型；每条1—3张图片逐张调用视觉模型，校验 `VisionEvidence` 后将完整证据、来源、不确定性和受控历史作为用户层数据交给文本模型。图片本身不作为高优先级指令。只有图片时使用中性描述任务，不默认判NG。历史保存于后端，原图过期和上下文裁剪明确传递。

图片支持静态JPEG/PNG/WebP，单图默认6MiB、12MP，检查实际解码、帧数、总量和CPU处理时限；处理EXIF方向并重新编码为真实PNG，不盲目缩为1280。需要读小字时请裁剪局部上传。默认最多30张访客图片、总临时图像512MiB。图片与会话默认24小时TTL；清理仅作用于本助手私有目录。

默认队列8、执行并发2，每会话最多一个有效生成；每访客80次请求、全服务每日500次模型调用的保守预留预算（图像每张1次+文字1次），失败和未知调用不自动退额/重发；频率按访客和来源受控。请求全流程180秒、模型单次60秒。取消结束本地等待和HTTP请求，不声称所有远端推理引擎已释放显存。

请求键持久去重，快照绑定对话、文字及有序图片ID；重连只读任务，不重复推理。重启把未完成任务标记interrupted，不自动重发。当前返回真实阶段SSE与最终整段结果，**没有伪造逐字token流**。Markdown关闭原始HTML、危险协议和外部图片，支持安全表格；不显示隐藏推理。

## API

统一前缀 `/api/assistant`：

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| POST | `/session` | 静默建立访客身份，返回CSRF |
| GET / POST | `/conversations` | 本访客列表 / 新建 |
| GET / DELETE | `/conversations/{id}` | 恢复历史 / 清空后台关联 |
| POST | `/uploads` | multipart，conversation_id和1—3个files |
| GET / DELETE | `/attachments/{id}` | 有归属校验的PNG / 删除 |
| POST | `/conversations/{id}/messages` | text、attachment_ids、request_key |
| GET | `/requests/{id}` | 查询持久任务与答案 |
| GET | `/requests/{id}/events` | SSE，支持after/Last-Event-ID续读 |
| POST | `/requests/{id}/cancel` | 取消本访客任务 |
| POST | `/feedback` | helpful/problem，不自动训练 |
| GET | `/status` | 脱敏模型与通道状态 |
| GET | `/models` | 当前访客可见的模型选项，`refresh=true`重读服务端目录；无端点/路径/凭据 |
| POST | `/channels/feishu/events` | 独立验签的飞书收件箱 |

提交消息可携带 `model_id`，只允许上述模型目录中的标识；省略时仍使用后台配置的默认连接。请求键也绑定模型选择，不能用同一键改选模型重复推理。已排队请求使用提交时的服务端连接快照；快照只持久保存公开模型标识及版本，不保存密钥。本地微调与其他基座不会自动复用9B原始模型别名，需按真实部署登记端点/served model。

另有 `/health/live` 与 `/health/ready`。后者文本不可用或期望产物不完整时返回503，视觉离线不妨碍纯文字。SSE事件为phase、vision_result、done、error，包含request_id和递增sequence；无真实token流时不发伪delta。

## 飞书：手机入口

原 `day2/4-notification` 仅有预留README，本轮新增 `backend/channels/feishu.py` 和 `scripts/run_feishu_ws.py`，不是复用已上线机器人。文字/图片/图文与PC网页共用Jobs核心，独立身份、会话、图片和事件账本，不自动互通网页历史。

优先选择官方SDK长连接 `ASSISTANT_FEISHU_TRANSPORT=ws`，单独使用 `requirements-feishu.txt` 中固定的官方MIT SDK。来源为官方PyPI，约7.7MB主wheel，不涉及CUDA；未在本轮自动安装。设置后端 App ID/Secret、机器人open_id、企业/用户允许列表、可选群允许列表和随机桥接密钥，启动：

```bash
python assistant_app/scripts/run_feishu_ws.py --env-file /absolute/private/feishu.env
```

同一env还需用于主助手进程。SDK负责平台长连接身份；桥接使用HMAC、时间窗和随机nonce送本机可靠收件箱，入库后才ACK。一个应用只启用一套消费者；本机文件锁避免重复。前台 Ctrl+C 只停止此消费者。

也提供互斥的 `http` 模式，要求平台签名、5分钟时间窗、Encrypt Key的AES-256-CBC解密和Verification Token。应用订阅 `im.message.receive_v1`，依据官方文档配置应用发布、收消息、图片资源读取及机器人回复权限；群默认必须@本机器人，范围不匹配/机器人自发消息均忽略。

事件ID和消息ID持久去重，后台取官方消息资源接口的图片，执行同一上传校验。会话按企业、私聊/群聊、群、线程及用户隔离。回复发送前记账，结果未知不自动重发；重启不会再次调用模型。没有真实凭据、授权用户和发布条件时保持disabled，不向任何真实群发送测试消息。飞书平台会经过消息；微信/企业微信只保留ChannelAdapter扩展边界。

## 验证证据与边界

本轮证据在 `../reports/assistant-20261026/`；交付说明见 `verification.md`，视觉检查见本目录 `design-qa.md`。测试模型仅通过测试依赖注入，不是运行时失败回退。真实云端生成与模拟协议测试分开记录。

原8001平台、quality权限、数据库、配置和frontend/dist不在本助手链路，不导入quality.app、不执行工业迁移、不访问PLC/ERP/WMS。没有执行GPU训练、模型合并、生产部署、Git commit/push/merge。当前源码没有本项目整体公开许可证核验结论，不因依赖采用MIT/Apache就自动授权发布整个仓库。

官方接口参考：[百炼文字与图片协议](https://www.alibabacloud.com/help/en/model-studio/qwen-api-via-openai-chat-completions)、[飞书事件订阅](https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/event-subscription-guide/event-subscriptions)、[飞书官方SDK](https://github.com/larksuite/oapi-sdk-python)、[vLLM模型支持](https://docs.vllm.ai/en/latest/models/supported_models.html)。官方支持说明不能代替当前GPU环境实测。
