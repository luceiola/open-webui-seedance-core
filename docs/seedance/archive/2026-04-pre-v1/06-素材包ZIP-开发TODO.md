# 素材包 ZIP 功能开发 TODO

## 目标

交付“ZIP 素材包 + prompt 文件名引用 + Seedance 工具调用”的最小可用版本（无预览）。

## 当前进度（2026-04-21）

- 后端 PoC 已打通（安装包环境内）：
  - 已有素材包接口：上传、列表、详情、引用解析、触发生成。
  - 已串 File API（文件上传）与 Responses API（生成请求）。
- 前端入口未接入：
  - 聊天框下方 `+` 菜单还没有 ZIP 上传入口。
- 工程化未完成：
  - 测试、监控、清理任务、重试策略仍待补齐。

## P0 需求冻结

- [ ] 锁定 ZIP 大小限制（上传前/解压后）。
- [ ] 锁定允许文件类型白名单（图片/视频/音频）。
- [ ] 锁定引用语法（建议 `@文件名`）。
- [ ] 锁定素材包生命周期（保存天数、清理策略）。

## P1 前端入口

- [ ] 在聊天框下方 `+` 增加 `上传素材包(zip)` 入口。
- [ ] 上传控件仅允许 `.zip`。
- [ ] 上传完成后展示：
  - [ ] 素材包 ID
  - [ ] 可引用文件名列表（纯文本）
- [ ] 上传失败时展示后端错误原文。

## P2 后端 ZIP 接收与安全处理

- [x] 新增 `POST /api/v1/material-packages`（PoC）。
- [x] 存储上传 ZIP 到临时目录（PoC）。
- [x] 解压安全校验（PoC）：
  - [x] 防 Zip Slip（路径穿越）
  - [ ] 防超深目录（待增强）
  - [x] 防超体积解压（PoC）
- [x] 过滤系统隐藏文件（`__MACOSX/`、`._*`、`.DS_Store`、`Thumbs.db` 等）。
- [ ] 基于扩展名 + MIME 做双重校验（当前以扩展名为主，待补 MIME）。
- [ ] 生成 `asset_package_id` 与 DB 记录（当前为 manifest 文件，待入库）。

## P3 Ark File API 封装

- [x] 新增 File API client：`upload/retrieve/wait_for_active`（PoC）。
- [x] 每个素材上传后持久化 `file_id`（PoC manifest）。
- [x] 处理态轮询到 `active` 或 `failed`（PoC）。
- [ ] 写失败重试策略（网络抖动/限流）。

## P4 素材清单与查询

- [x] 新增 `GET /api/v1/material-packages/{id}`（PoC）。
- [x] 返回结构：`filename, media_type, file_id, status`（PoC）。
- [ ] 返回可直接复制的引用提示文本（可选优化）。

## P5 Tool 输入编译器

- [x] 在工具层实现 prompt 引用解析器（PoC）：
  - [x] 提取 `@文件名`
  - [x] 去重
  - [x] 校验存在性
- [x] 将引用素材映射为 Responses 输入块（PoC）。
- [x] 按 `media_type` 分配为 `input_image/input_video/input_audio`（PoC）。
- [x] 未命中引用时返回（PoC）：
  - [x] 未命中文件名
  - [x] 可用文件名列表

## P6 Responses API 调用链

- [x] 构建标准 `responses.create` 请求（PoC）。
- [x] 支持基础参数：`model/prompt`（PoC）。
- [ ] 可选参数：`duration/resolution/style`（后续可扩）。
- [x] 返回统一结果结构：`task_id/status/result_url/error`（PoC）。

## P7 基础 LLM 配置（工具调用）

- [ ] 选择并配置一个支持 tool calling 的对话模型。
- [x] Tool 模板已提供（`templates/seedance_material_package_tool.py`）。
- [ ] Tool schema 注册到 OpenWebUI。
- [ ] 验证“自然语言 -> 调工具 -> 返回结果”主路径。

## P8 测试

- [ ] 单元测试：
  - [ ] ZIP 校验
  - [ ] 文件名解析
  - [ ] 引用映射
- [ ] 集成测试：
  - [ ] ZIP 上传 -> file_id 生成
  - [ ] prompt 引用 -> Responses 请求
- [ ] 异常测试：
  - [ ] 空 ZIP
  - [ ] 非法文件
  - [ ] 重名文件
  - [ ] 引用不存在

## P9 可观测与运维

- [ ] 日志字段统一：`request_id/chat_id/asset_package_id/file_id/task_id`。
- [ ] 增加失败告警点：上传失败率、处理超时率。
- [ ] 增加定时清理任务：过期 ZIP 与临时文件。

## 里程碑建议

- M1：前后端上传与素材清单可用。
- M2：prompt 引用解析 + Tool 调用可用。
- M3：稳定性与清理机制完成，进入灰度。

## 交付定义（DoD）

- [ ] 用户可上传 ZIP 并拿到可引用文件名。
- [ ] 用户可在 prompt 中引用文件名并成功发起生成。
- [ ] 主链路错误都有明确提示。
- [ ] 无预览设计下可以完整跑通一次生产素材生成。

## 下一阶段优先级（建议）

1. 接前端 `+` ZIP 上传入口（形成用户可见闭环）。
2. 固定工具调用主模型与 Tool schema（保证稳定触发）。
3. 用真实 Ark 凭据跑一次 E2E（上传 ZIP -> prompt 引用 -> 生成）。
4. 补最小测试集（解析器 + ZIP 安全 + 接口回归）。
