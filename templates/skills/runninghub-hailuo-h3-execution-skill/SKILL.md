---
name: runninghub-hailuo-h3-execution-skill
description: RunningHub Hailuo H3 专用视频执行规范，支持文生、显式首尾帧图生和多模态参考生成。
version: v1.0.0
routing_registry: config/seedance_routing_registry.yaml
version_registry: templates/versions/registry.json
---

# RunningHub Hailuo H3 Execution Skill

## 唯一工具范围

- 提交生成：`generate_video_with_runninghub_hailuo_h3`
- 查询列表：`list_generation_tasks`
- 查询详情：`get_generation_task_status`
- 等待终态：`wait_generation_task`

禁止调用 Seedance2、HappyHorse 或其他视频生成工具。

## 确定性模式路由

1. 仅当用户明确使用“首帧、尾帧、首尾帧、以 A 为首帧、以 B 为尾帧”等语义时：
   - 映射到 `first_frame/last_frame`。
   - 不同时传 `images/videos/audios`。
2. 用户未提供任何媒体参考时：
   - 不传任何帧或媒体参数，由 Tool 路由为 t2v。
3. 用户提供普通参考图、参考视频或参考音频，但没有首尾帧语义时：
   - 分别映射到 `images/videos/audios`，由 Tool 路由为 multimodal。
4. 普通“参考这张图生成”不是首帧语义，禁止映射到 `first_frame`。
5. 首尾帧和普通多模态参考混用时，先要求用户选择，禁止自行删除或改写输入。

## 参数规则

- 分辨率固定 `2K`。
- 时长为 5-15 秒，默认 5 秒。
- t2v/multimodal 比例默认 `adaptive`；可选 `21:9/16:9/4:3/1:1/3:4/9:16`。
- i2v 不设置比例。
- multimodal 最多 9 张图片、3 个视频、3 个音频。
- 本地路径、http(s) 和 WebUI `%引用名` 均由 Tool 统一解析。

## `%引用` 一致性

调用生成工具前，从用户本轮输入提取全部 `%token`，并确保提交的 `prompt` 逐字保留这些 token。媒体参数可以同时引用相同 token，但禁止从 Prompt 中删除或改写。

## 提交与查询

- 默认只提交任务，不等待终态。
- 提交阶段 `video_url` 始终显示 `暂无`。
- 查询非终态时明确仍在处理，且 `video_url=暂无`。
- 仅查询或等待返回成功终态时展示真实视频地址。
- `task_id` 只能来自工具真实返回；缺失时按失败处理并说明“暂未创建任务”。

## 错误反馈

失败时原样输出：

- `status_code`
- `error_code`
- `error_message`
- `request_id`

路由错误应建议管理员检查当前用户组、`config/key_routing.json` alias 和对应环境变量，不得自行猜测凭据问题。

## 固定提醒

提交成功或失败均包含：

1. 无参考素材为文生视频；普通参考素材为多模态参考生成。
2. 只有明确提出首帧或尾帧时才使用首尾帧图生视频。
3. Hailuo H3 固定 2K，时长支持 5-15 秒，默认 5 秒。

## 禁止事项

- 禁止让模型自由填写或猜测 mode。
- 禁止把普通参考图当作首帧。
- 禁止把 `%引用` 直接传给 `au vendor`。
- 禁止从提交响应中的任意 URL 推断成品视频。
- 禁止编造任务号、状态、链接、错误码或请求号。
