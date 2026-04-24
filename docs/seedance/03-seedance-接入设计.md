# Seedance 接入设计草案

## 1. 设计目标

为 OpenWebUI 提供一个可复用的 Seedance 视频生成 Tool，支持：
- 文生视频提交
- 任务状态查询
- 结果链接返回

## 2. 分层建议

- `client` 层：封装 HTTP 请求、鉴权、超时、重试
- `service` 层：编排“提交 -> 轮询 -> 返回结果”
- `tool` 层：对 OpenWebUI 暴露简洁函数签名

建议目录：

```text
tools/
  seedance/
    client.py
    service.py
    schemas.py
    tool.py
```

## 3. 环境变量建议

参考 `.env.example`：
- `ARK_API_KEY`
- `ARK_BASE_URL`
- `SEEDANCE_MODEL`
- `SEEDANCE_POLL_INTERVAL`
- `SEEDANCE_MAX_WAIT_SECONDS`

## 4. 接口流程（推荐）

1. 提交视频生成任务，拿到 `task_id`
2. 轮询任务状态（间隔 3~8 秒）
3. 成功后返回 `result_url`（或等价字段）
4. 失败时返回明确错误码和错误信息

## 5. 超时与重试策略

- 单次 HTTP 超时：20~60 秒
- 轮询总超时：2~10 分钟（按模型实际耗时）
- 可重试错误：429、5xx、网络抖动
- 不重试错误：参数错误、鉴权失败

## 6. 安全建议

- API Key 仅放 `Valves` 或环境变量，不写死在代码中
- 日志默认脱敏（不打印完整密钥）
- 在 git 中忽略 `.env` 与运行态文件

## 7. MVP 范围

第一版只做：
- 文生视频
- 返回任务最终结果 URL
- 基础异常处理

第二版再加：
- 更多可控参数（镜头/运动强度/风格）
- 回调模式
- 任务取消
