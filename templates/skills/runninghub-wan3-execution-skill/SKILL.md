---
name: runninghub-wan3-execution-skill
description: RunningHub WAN 3.0 视频执行规范，支持纯文生、普通参考和首尾帧图生。
version: v1.0.0
routing_registry: config/seedance_routing_registry.yaml
version_registry: templates/versions/registry.json
---

# RunningHub WAN 3.0 Execution Skill

## 工具范围

- 生成：`generate_video_with_runninghub_wan3`
- 列表：`list_generation_tasks`
- 详情：`get_generation_task_status`
- 等待：`wait_generation_task`

## 模式路由

1. 没有首帧或尾帧时，固定使用 `reference-to-video`。没有任何参考素材时，这是纯文生视频。
2. 普通参考图片、视频、音频、文件 URL 和网页 URL 也使用 `reference-to-video`。
3. 只有明确的首帧或尾帧语义才使用 `image-to-video`。
4. `image-to-video` 要求首帧，可选尾帧，且不能混用其他参考输入。

## 参数规则

- 默认分辨率为 `720P`，所有模式一致。
- 默认比例为 `adaptive`，默认时长为 5 秒，支持 `auto` 或 2-30 秒。
- 默认生成音频。
- 最多 10 张图片、5 段视频、5 段音频。
- 本地路径、HTTP(S) URL 和 `%引用名` 由 Tool 统一解析。
- Prompt 中的 `%引用` 必须逐字保留。

## 提交和结果

- 提交阶段只返回真实 `task_id`、状态和任务信息，`video_url` 固定为 `暂无`。
- 只有查询或等待到成功终态后才能展示视频地址。
- 缺少真实任务号时不得创建任务，也不得编造任务号。
- 失败时保留 `status_code`、`error_code`、`error_message`、`request_id` 和原始响应。

## 禁止事项

- 禁止把纯文生误路由到 `image-to-video`。
- 禁止把普通参考图解释为首帧。
- 禁止把 `%引用名` 直接传给 `au vendor`。
- 禁止从提交响应中的任意 URL 推断成品视频。
