---
name: ai-utility-minimax-h3
description: AI Utility Public API MiniMax H3 Ref2V/FL2V 任务创建、查询、费用和成片下载规范。
version: v1.0.0
routing_registry: config/seedance_routing_registry.yaml
version_registry: templates/versions/registry.json
---

# AI Utility MiniMax H3 Execution Skill

## 工具范围

- 参考素材生成：`create_h3_ref2v_job`
- 文生或首尾帧生成：`create_h3_fl2v_job`
- 查询任务：`get_h3_job`
- 等待任务：`wait_h3_job`
- 任务列表：`list_h3_jobs`
- 任务日志：`get_h3_job_logs`
- 取消任务：`cancel_h3_job`
- 下载并附加视频：`attach_h3_video`
- 账户和用量：`get_h3_account_usage`

禁止调用其他视频工具或直接请求 RunningHub。

## 模式路由

1. 纯文本生视频：`create_h3_fl2v_job`，无首尾帧。
2. 明确首帧、尾帧或首尾帧：`create_h3_fl2v_job`，传 `first_frame/last_frame`。
3. 普通参考图片、视频或音频：`create_h3_ref2v_job`。
4. 首尾帧与额外参考素材混用：先询问用户，禁止自行丢弃。
5. 普通“参考图”不能解释成首帧。

## Context-IR

- 默认值固定为 `rh-official`。
- 仅当用户明确要求时才传 `none` 或 `doubao`。
- `none`：Context-IR 费用为 0。
- `rh-official`：通常返回人民币费用。
- `doubao`：可能只返回 token，不能生成人民币合计。

## 参数

Ref2V：

- duration：5-15 秒，默认 5 秒。
- ratio：默认 9:16。
- instance_type：默认 plus。
- 最多 9 张图片、3 个视频、3 段音频。

FL2V：

- duration：1-15 秒，默认 5 秒。
- ratio：默认 9:16。
- first_frame、last_frame 均可选；均不传是纯文生。

共同限制：

- prompt 最长 7000 字符。
- ratio 支持 `1:1/4:5/5:4/2:3/3:2/3:4/4:3/9:16/16:9/21:9`。
- 创建任务必须传 `Idempotency-Key`。

## 付费确认

提交前必须向用户确认：

1. 使用的模式。
2. 时长、比例和 instance type。
3. Context-IR 提供方。
4. 可能产生 Context-IR 和视频生成费用。

用户确认后才可传 `confirm_processing=true`。不得自动重试创建、resume、continue 或额外付费操作。

## 任务查询与成片

- 创建成功后保存 `job_id`。
- 查询时展示状态、错误和可用费用。
- 只有 `completed` 才能调用 `attach_h3_video`。
- 使用 `attach_h3_video.download_url_markdown` 提供成片链接。
- 不直接展示 Public API 原始 Bearer 视频地址。

## 费用显示

任务结果中的 `costs` 结构：

```json
{
  "currency": "CNY",
  "context_ir": {"provider": "rh-official", "total": "0.25"},
  "video_generation": {"provider": "runninghub", "total": "1.50"},
  "known_subtotal": "1.75",
  "total": "1.75"
}
```

显示顺序：

1. Context-IR（包含 provider）。
2. 视频生成。
3. 合计。

如果 `total` 为空，只显示 `known_subtotal` 和“金额未返回”。禁止把缺失费用显示为 0。

任务中心的“费用”弹窗会读取同一份 `costs`，不需要在 `generation_params` 重复写入费用。

## 错误处理

原样保留：

- `status_code`
- `error_code`
- `error_message`
- `request_id`
- `job_id`

常见情况：

- `ProcessingConfirmationRequired`：等待用户明确确认。
- `H3ModeConflict`：重新确认模式，不要擅改。
- `ReferencesRequired`：Ref2V 至少需要一个参考附件。
- `MissingUploadReference`：附件名和 `%引用名` 不一致。
- `job_not_completed`：任务未完成，不能下载。

## 禁止事项

- 禁止自行改变 `context_ir`。
- 禁止把普通参考图当首帧。
- 禁止绕过付费确认。
- 禁止编造费用、任务号、状态或链接。
- 禁止把 Public API Key、OpenWebUI Token 或服务器路径展示给用户。
