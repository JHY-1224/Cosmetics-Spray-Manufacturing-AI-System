# 通知扩展位置

对应参考项目的 `4-feishu`。本次没有新增飞书机器人、复制其 webhook 或发送消息。
项目通知需求确定后，在这里实现实际通知适配。

2026-10-26增量：独立工艺助手的双向飞书应用机器人已另在 `assistant_app/backend/channels/feishu.py` 与 `assistant_app/scripts/run_feishu_ws.py` 实现，复用助手编排，不向本目录复制Webhook或密钥。手机端以该通道为入口；默认disabled，缺真实应用凭据和发布条件时不宣称已接通。见 [独立助手说明](../../assistant_app/README.md)。上文是此目录原有预留状态。
