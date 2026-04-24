# OpenWebUI Tools 开发指南

## 1. 目标

在 OpenWebUI 中新增一个可被模型调用的工具（Tool），用于触发外部 API（后续是 Seedance 视频生成）。

## 2. Tool 文件结构（最小约定）

1. 顶部 frontmatter（三引号包裹）
2. `Tools` 类（必须）
3. `Valves`（可选，系统级配置）
4. `UserValves`（可选，用户级配置）
5. `async` 工具函数（模型实际调用的方法）

## 3. Frontmatter 建议字段

```python
"""
title: Seedance Video Tool
author: team
version: 0.1.0
required_open_webui_version: 0.8.0
requirements: httpx>=0.28.1
"""
```

`requirements` 可用于声明额外依赖。

## 4. Valves / UserValves 设计建议

- `Valves` 放系统配置：如 `ARK_API_KEY`、`BASE_URL`、默认模型。
- `UserValves` 放用户配置：如个人默认风格、输出偏好等。
- 用 `pydantic.Field` 提供默认值、范围和描述，方便在 UI 配置。

## 5. Tool 函数设计规范

- 输入参数尽量小而清晰（prompt、时长、比例等）。
- 统一返回结构化字符串或 JSON 字符串。
- 对外部 API 异常做明确错误提示（超时、鉴权失败、限流）。
- 建议默认异步提交 + 轮询，避免前端长时间阻塞。

## 6. 调试建议

1. 先本地直接调用 API client（不经过 OpenWebUI）
2. 再接 Tool
3. 最后在 OpenWebUI Chat 中实测函数调用

优先保证可观测性：
- 输入参数日志（脱敏）
- 请求 ID / task ID
- 最终 result URL
