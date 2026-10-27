# 云端阿里云备用配置与重启记录

2026-10-27 18:19（Asia/Shanghai）已按用户要求启用阿里云备用并正常重启云端5001 Web。默认仍是Qwen轻量级服务；9B与8B GPU模型保持运行，Mac本地项目保持停止。

## 当前可选项

| 选项 | 文本 | 视觉 | 实际状态 |
| --- | --- | --- | --- |
| Qwen轻量级服务（默认） | qwen-light-text | qwen-light-vision | ready，可选择 |
| 阿里云百炼（备用） | deepseek-v4.1-flash-1017 | qwen3-vl-plus | ready，可选择 |

上述云端模型名称来自本机已有获准的阿里云百炼连接，并已从云端实际核对可用性及完成调用。本轮没有从供应商目录随意更换模型。

访问[http://127.0.0.1:5001](http://127.0.0.1:5001)，保持当前Cursor SSH连接。已有页面刷新后，在“选择模型”的“阿里云模型”组里选择“阿里云百炼（备用）”。默认服务仍为轻量Qwen。这里提供**手动备选**；没有实现静默切换，也不会因本地模型出错就自动把原请求发往阿里云。

## 原因与处理

界面“暂未配置云端模型”的实际原因：本机私有`.env`已有获准连接，而本次云端轻量部署使用独立`service.env`和`catalog-extra`，未带入阿里云凭据或目录条目。它是部署配置缺项，不是微调权重或共享提示词损坏。

复用现有ModelCatalog机制，只补云端私有连接和`aliyun-backup`条目，并把已有受控的dashscope主机加入允许列表。API Key只存在云端0600私有env，不写入前端、profile JSON、日志、公开源码或本地报告。凭据经现有SSH隧道的一次性回环接收导入，接收服务完成后已关闭；没有通过GitHub传密钥。

现有云端profile继承服务端的文本/视觉凭据；只加JSON、没有对应凭据和允许主机仍不可用。env在Web进程启动时加载，因此本次确实需要重启Web，不能只刷新前端或模型列表。

私有部署目录：`/root/autodl-tmp/models/services/qwen-light/deploy/lightweight-pilot-20261027-v2/`。更新前env保存在其中`aliyun-backup-20261027-v1/service-before.env`，不在HTTP报告目录内。回退时核对当前连接及版本，用该私有备份恢复env、停用本次`catalog-extra/aliyun-backup.json`并正常重启Web；不操作模型、Adapter、业务库和旧报告。

## 实际验证

- Web旧PID38613正常退出，新PID40711启动。模型5101监听PID38195、5102监听PID38363重启前后相同。
- 首页/健康就绪及模型目录可用，`default_id=configured`仍指向微调轻量服务。目录`unreadable_entries=0`；前端菜单已实际看到阿里云备用与Qwen轻量级两项均“可用”。
- 合成文字任务完成：包材缩孔排查方向，未给生产参数或确认唯一根因，实际调用deepseek-v4.1-flash-1017，正常stop。
- 合成单图任务完成：工序喷涂、设备LINE-DEMO、烘箱空气温度60°C读取正确，未认定合格或放行；实际qwen3-vl-plus读图后交文本模型答复，两次均stop。
- 两个任务元数据均为`industrial-assistant-v3`，文本SHA256 `b255504801deaee32d6b6e273043ac1e64136550bffd25cf7ca1e8591699dc23`，视觉SHA256 `b08b40ceb10ad470381b5589b5f54f91e3eaf646930decd6e6585eadc9f7c9d5`，与轻量服务相同；没有按模型分叉提示词。
- 未重新训练、未改可复用源码、未跑新一轮96题。此处2个合成任务仅验证备用链路，不能称为完整准确率或普遍防注入保证。此前83/96（86.46%）评审保持不变。

本地证据：`reports/aliyun-backup-20261027-v1/cloud-restart-result.json`、`model-listing.json`、`text-result.json`、`image-result.json`及对应intent；只含合成测试与非秘密连接元数据。云端报告在`/root/autodl-tmp/models/services/qwen-light/evaluation/aliyun-backup-20261027-v1/`；秘密env及其备份不置于该目录。

## 持续经验记录

以后每轮改善必须保存原问题/输出、事实来源、根因证据与假设、改法、版本/哈希、同口径前后结果、限制和回退位置；问题原件留在本地私有评估目录，方法写入`docs/FINE_TUNING_LESSONS.md`，入口规则写入`AGENTS.md`。训练/开发/验证/封存用途保持可追溯，助手批注不自动成为训练金标。更大模型与更高配置升级时复用这些方法和经批准的数据，不抹掉失败历史。

本次备用启用验证后继续休息，云端两种服务选项保持可用；不自动恢复旧测试、训练或知识库计划。
