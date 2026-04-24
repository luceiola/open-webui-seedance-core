# 素材包 ZIP 功能需求文档

## 1. 背景与目标

当前希望在 OpenWebUI 对话场景中支持“批量素材输入 + 文本提示词引用”的视频生成流程，降低多素材任务的操作成本。

本期目标：
- 在对话框下方 `+` 增加 ZIP 素材包入口。
- 用户上传一个 ZIP 后，在 prompt 中直接引用压缩包内素材文件名。
- 系统不提供素材预览、拖拽排序、可视化编排等强交互。
- 通过基础 LLM 进行工具调用编排，驱动 Seedance 视频生成工具。

## 2. 范围

### 2.1 In Scope

- 前端：`+` 菜单新增“上传素材包(zip)”入口。
- 后端：ZIP 接收、解压、校验、上传到 Ark File API、生成素材清单。
- Prompt 侧：支持按文件名引用素材（建议 `@文件名` 约定）。
- 工具层：解析 prompt 引用并组装 Responses API 请求。
- 结果侧：返回任务状态与视频结果链接。

### 2.2 Out of Scope

- 前端素材预览。
- 前端时间轴/镜头编排 UI。
- 自动提示词优化器。
- 多 ZIP 跨包引用（本期仅单包）。

## 3. 用户流程

1. 用户点击聊天框下方 `+`。
2. 选择“上传素材包(zip)”并上传。
3. 系统返回上传成功信息：
   - 素材包 ID
   - 可引用文件名列表
4. 用户在 prompt 中自行组织需求并引用文件名。
5. 用户发送消息，基础 LLM 决定调用视频生成工具。
6. 工具解析素材引用，生成请求并调用 Seedance。
7. 返回任务状态/结果地址。

## 4. 功能需求

## FR-1 前端入口

- 在现有 `+` 菜单中新增一个上传入口：`上传素材包(zip)`。
- 只接受 `.zip` 文件。

## FR-2 ZIP 处理

- 后端接收 ZIP 并解压到临时目录。
- 校验项：
  - 文件扩展名白名单（图片/视频/音频）。
  - 压缩包大小限制（可配置）。
  - 解压后总大小限制（可配置）。
  - 目录穿越防护（禁止 `../`）。
  - 系统隐藏文件过滤（如 `__MACOSX/`、`._*`、`.DS_Store`、`Thumbs.db`）。
- 对不合规包返回明确错误。

## FR-3 素材登记与清单

- 为每个 ZIP 生成唯一 `asset_package_id`。
- 为包内每个可用素材生成记录：
  - `filename`
  - `media_type` (`image` / `video` / `audio`)
  - `file_id`（Ark File API 返回）
- 返回清单给前端用于提示用户可引用名。

## FR-4 Prompt 引用规则

- 用户在 prompt 中引用素材文件名。
- 建议约定：`@文件名`。
- 工具在执行前解析 prompt：
  - 提取被引用素材。
  - 校验是否存在于该 `asset_package_id`。
  - 未命中则报错并返回可用文件名列表。

## FR-5 视频生成工具调用

- 工具读取素材记录，将被引用素材映射到请求输入块。
- 文本部分由用户 prompt 原文提供，不做重写魔改（仅清理标记符）。
- 生成请求后调用 Responses API。

## FR-6 基础 LLM 要求

- 对话主模型必须具备 tool/function calling 能力。
- 责任：
  - 识别用户要发起视频生成。
  - 组织工具参数（prompt、asset_package_id、可选生成参数）。

## FR-7 反馈与错误处理

- 上传阶段错误：格式不支持、超限、空包、解压失败。
- 引用阶段错误：文件名不存在、文件处理未完成。
- 生成阶段错误：模型参数错误、API 调用失败、超时。
- 所有错误需可读、可定位，不返回泛化异常。

## 5. 数据模型（建议）

### AssetPackage

- `id`
- `user_id`
- `chat_id`
- `zip_filename`
- `status` (`processing` / `ready` / `failed`)
- `created_at`
- `expires_at`

### AssetItem

- `id`
- `asset_package_id`
- `filename`
- `media_type`
- `size_bytes`
- `mime_type`
- `file_id`（Ark）
- `status` (`processing` / `active` / `failed`)

## 6. 接口契约（建议）

## POST `/api/v1/material-packages`

- 入参：`multipart/form-data`
  - `zip_file`
  - `chat_id`
- 出参：
  - `asset_package_id`
  - `status`
  - `assets[]`（文件名、类型、file_id/状态）

## GET `/api/v1/material-packages/{id}`

- 查询素材包处理状态与可引用清单。

## GET `/api/v1/material-packages`

- 查询当前用户素材包列表。

## POST `/api/v1/material-packages/{id}/resolve`

- 输入：原始 prompt
- 输出：命中素材、清洗后 prompt、未命中错误信息

## POST `/api/v1/material-packages/{id}/generate`

- 输入：模型参数 + 原始 prompt
- 处理：内部完成引用解析与 Responses API 调用
- 输出：`task_id/status/result_url/error`

## 工具入参（LLM -> Tool）

- `asset_package_id: string`
- `prompt: string`
- `model: string`（可选，默认配置）
- `duration/resolution/...`（可选）

## 7. 非功能要求

- 安全：
  - 解压安全校验。
  - 文件类型校验。
  - 临时文件定期清理。
- 性能：
  - 上传后异步处理，避免阻塞对话主线程。
- 可观测：
  - 日志需包含 `asset_package_id`、`file_id`、`task_id`。

## 8. 验收标准（MVP）

- 用户可通过 `+` 上传 ZIP。
- 上传成功后可拿到可引用文件名列表。
- 用户在 prompt 使用文件名引用后，可成功触发工具。
- 工具能正确使用被引用素材发起视频生成。
- 引用错误时能返回精确错误信息。
- 全流程无需素材预览即可完成。

## 9. 风险与约束

- 压缩包内同名文件冲突（需定义冲突策略：拒绝或重命名）。
- 大文件上传耗时与 File API 预处理耗时较高。
- 用户 prompt 组织能力会影响效果（本期接受该约束）。

## 10. 待确认项

- ZIP 大小上限、解压后总大小上限。
- 支持的具体扩展名白名单。
- 素材包有效期与清理策略。
- 是否允许一个会话内复用历史素材包。

## 11. 火山方舟 API 对齐要求（落地约束）

为降低后续返工，本功能默认采用“File API + Responses API”双接口模式：

- 素材上传阶段：
  - ZIP 解压出的媒体文件逐个上传到 Ark File API。
  - 以 `file_id` 作为后续生成阶段唯一引用标识。
  - 对上传结果做状态跟踪，确保文件进入可用态后再生成。
- 生成阶段：
  - 由工具层把用户 prompt 中引用的文件名映射到对应 `file_id`。
  - 按媒体类型映射到 Responses API 输入块：
    - 图片 -> `input_image`
    - 视频 -> `input_video`
    - 音频 -> `input_audio`
  - 用户原始文本作为 `input_text` 一并提交。

说明：
- 本期只做“引用解析 + 请求编排”，不做自动分镜、自动重写 prompt。
- 用户需自行在 prompt 中明确每个素材作用和期望效果。

## 12. 当前实现状态（2026-04-21）

当前状态按“可跑通优先”执行：

- 已完成（后端 PoC）：
  - 已新增素材包路由，包含上传/查询/引用解析/生成调用主链路。
  - 已接入 Ark File API 上传逻辑与基本状态处理。
  - 已接入 Ark Responses API 请求组装与调用。
- 未完成（正式可用前）：
  - 前端 `+` 菜单 ZIP 上传入口仍待接入。
  - Tool schema 与基础 LLM 的生产配置仍待固定。
  - 自动化测试、清理任务、异常重试策略仍待补齐。

现阶段结论：
- 后端链路可以先用于联调验证接口；
- 前端入口完成后即可给业务侧提供最小闭环。
