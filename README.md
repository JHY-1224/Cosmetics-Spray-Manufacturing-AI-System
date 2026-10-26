# 化妆品喷涂制造工艺AI智能系统

**涂镀智溯**：面向化妆品包材喷涂与玻璃真空蒸镀的工艺问答、图文资料理解、品质追溯与人工整改闭环系统。

包含PC对话助手、可选飞书机器人、工业业务工作台、Unsloth训练入口及vLLM部署脚本。
“Qwen轻量级服务”在前端是一个选项，内部由Qwen3.5-9B文本模型和Qwen3-VL-8B-Instruct视觉模型协作，与阿里云接口并列。

**生产目标是Linux NVIDIA GPU云服务器。Mac仅用于开发、数据整理、示范与离线检查。**
仓库提供源码、开发训练样本和部署工具，未发布训练权重；本次版本还需在目标GPU完成训练与推理验收。

## 从哪里开始

1. [AGENTS.md](AGENTS.md)：完整项目规则、业务逻辑、源码导航与第37节云端接续说明。
2. [云端操作手册](docs/CLOUD_QUICKSTART.md)：基座校验、训练、产物登记、vLLM及网页启动。
3. [多模态数据说明](docs/MULTIMODAL_TRAINING.md)：图片、JSONL、标签范围及参考医疗项目的对应关系。
4. [独立助手说明](assistant_app/README.md)：对话、图片、模型选择、网关及飞书接入。
5. [完整业务手册](docs/PROJECT_MANUAL.md)和[源码索引](docs/SOURCE_INDEX.md)：原平台的权限、追溯、诊断、审批、采集及部署逻辑。
6. [本次发布状态](docs/RELEASE_STATUS.md)：已执行验证和未执行的云端验收。

## 目录

|目录|职责|
|---|---|
|`assistant_app/`|独立FastAPI/React工艺助手，默认5001；图文问答、模型选择、可选飞书|
|`quality/`、`frontend/`|工业业务平台：台账、采集、质量追溯、Agent证据诊断、审批与整改闭环|
|`migrations/`、`schemas/`|数据库迁移与API合同|
|`deploy/`、`scripts/`|企业部署模板、运维、恢复和公开打包|
|`day1/2-prepare-data/`|36条文本开发SFT样本，保留来源署名|
|`day1/3-train/`|9B文本Unsloth训练|
|`day1/multimodal/`|8B图文数据整理、校验、训练和CUDA视觉冒烟|
|`data/vision-public/`|20张CC BY 4.0图像及20条上下文开发问答|
|`models/`|基座校验清单；无权重|
|`tests/`、各模块`tests/`|隔离测试|

## 克隆与基座位置

```bash
cd /root/autodl-tmp/code
git clone https://github.com/JHY-1224/Cosmetics-Spray-Manufacturing-AI-System.git
cd Cosmetics-Spray-Manufacturing-AI-System

# 只读并校验已下载的模型，不重新下载
python day1/0-download-model/1-download.py --verify-only --model-root /root/autodl-tmp/models
```

云端基座为 `/root/autodl-tmp/models/Qwen3.5-9B` 和 `/root/autodl-tmp/models/Qwen3-VL-8B-Instruct`。
训练产物统一放 `/root/autodl-tmp/models/services/qwen-light/`，操作步骤见云端手册。
使用已配置好的Unsloth/vLLM环境，不因git clone重装CUDA或升级框架。

## 数据与实际边界

本地完整图像训练开发包为74张图、82条问答，包括工艺照片、配料表、文字、参数页与简图。
其中74条是主题/资料边界标签，8条是细项读取；不是全量逐字OCR，也未经过领域专家审核。
公开仓库只附有明确CC BY 4.0来源的20张图；其余完整包保留本地，需另行传到自己的云服务器。
文本36条和图文样本都是有限开发数据，validation/test暂为空，不支持宣称生产准确率。

模型建议不会操作PLC、自动加料、审批、放行或确认唯一根因。提示词约束与权限、隔离、限流、结果校验共同工作，不能保证所有提示词注入都被阻止。
工业平台和独立对话助手是两个应用；对话助手不隐式访问生产业务数据库。

## 许可证

代码使用[MIT](LICENSE)。数据、图像、基座和依赖各自遵循原许可，详见[数据许可](DATA_LICENSE.md)。
基座来自官方[Qwen3.5-9B](https://modelscope.cn/models/Qwen/Qwen3.5-9B)与[Qwen3-VL-8B-Instruct](https://modelscope.cn/models/Qwen/Qwen3-VL-8B-Instruct)。
模型权重、API密钥、业务数据库、客户配置、会议记录、私有训练包及历史运行报告不在Git中。
公开文件与SHA256见 `PUBLIC_RELEASE_MANIFEST.json`。
