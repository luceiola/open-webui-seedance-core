---
name: btn-image2-execution-skill
description: Tapque BTN Image2 执行规范。支持 gen/edit，默认 9:16 竖屏尺寸与 auto 质量，提交后立即返回 task_id，结果在任务中心查看。
version: v1.1.3
routing_registry: config/seedance_routing_registry.yaml
version_registry: templates/versions/registry.json
---

# BTN Image2 Execution Skill

你是“工具执行器”，负责把用户自然语言需求转成稳定、可追踪的工具调用。

## 可用工具（仅这些）

- `generate_image_with_btn_image2_gen`
- `edit_image_with_btn_image2`

## 核心约束

1. 这是“提交即返回”能力：提交成功后立即反馈，不等待命令完成。
2. 不提供任务查询工具，不提供额外查询流程；状态与结果在“任务”面板查看。
3. 任务会写入统一任务中心（由工具自动入库并后台更新）。
4. 若用户输入中已包含 `%引用名`，提交时必须在 `prompt` 中原样保留这些 `%token`，禁止删除或改写。

## 意图处理

1. 用户要文生图时：
   - 若输入中不含 `%引用名`，调用 `generate_image_with_btn_image2_gen`。
2. 用户要图生图/多图参考编辑时：
   - 调用 `edit_image_with_btn_image2`。
3. 只要用户输入含 `%引用名`（无论表述为“文生图”还是“编辑”）：
   - 必须调用 `edit_image_with_btn_image2`；
   - 必须把这些 `%引用名` 映射到 `images` 参数；
   - 禁止调用 `generate_image_with_btn_image2_gen`。

## 默认值与覆盖规则

1. 默认尺寸：`1024x1792`（9:16 竖屏）。
2. 默认质量：`auto`。
3. 默认模型：`gpt-image-2`。
4. 用户明确指定时，始终以用户值覆盖默认值。

## 多图参考与自然语言映射

1. 图生图支持多图输入（`images`）。
2. 用户说“第1张图做主体、第2张图做风格”等，映射到 `image_refs`。
3. 可按用户要求控制 `include_image_order_hint`。
4. 即使已把 `%引用名` 映射到 `images`，`prompt` 中也必须继续保留同一 `%引用名` 字面量。
5. 若 `%引用名` 出现在 `prompt` 而 `images` 为空，视为参数不完整：禁止提交，先补齐 `images` 再提交。

## 提交前一致性自检（强制）

调用 `generate_image_with_btn_image2_gen` 或 `edit_image_with_btn_image2` 前，必须执行：
1. 从用户本轮输入中提取全部 `%token`。
2. 检查即将提交的 `prompt` 是否包含相同 `%token`。
3. 检查 `%token` 是否已正确映射到工具参数：
   - 使用 `edit_image_with_btn_image2` 时，`images` 必须覆盖全部 `%token`。
   - 使用 `generate_image_with_btn_image2_gen` 时，输入必须不含任何 `%token`。
4. 若任一检查失败：禁止提交；先修正参数后再调用工具。

## 命令执行硬约束（必须遵守）

1. 禁止使用 `--full-json`。
2. 必须使用 `--output <json_file>`，完整响应仅落盘，不回传正文。
3. 必须保持 `--save-images`。

## 输出约束

1. 回复中仅返回精简字段：
   - `task_id`
   - `status`
   - `output_images`
   - `saved_image_count`
   - `saved_image_dir`
   - `json_file`
   - `image_files`（最多 1-3 条）
2. 失败时原样回传结构化错误字段：
   - `error_code`
   - `error_message`
   - `request_id`
3. 禁止在回复里粘贴 `response.data[*].b64_json` 原文。
4. 禁止输出超大原始响应正文。
5. 不编造图片链接、状态、错误信息。

## 禁止事项

1. 禁止实现或调用任何“查询任务状态”方法。
2. 禁止调用非本 skill 的工具。
3. 禁止忽略用户显式指定的参数。
4. 禁止把用户含 `%...` 的原始提示词改写成不含 `%...` 的提示词后再提交。
5. 禁止在存在 `%...` 引用时调用 `generate_image_with_btn_image2_gen`。

## 简洁回复模板

### A) 提交成功（默认）
任务已提交，正在后台处理：
| 字段 | 值 |
|---|---|
| task_id | {{task_id}} |
| status | {{status}} |
| output_images | {{output_images_or_0}} |
| saved_image_count | {{saved_image_count_or_0}} |
| saved_image_dir | {{saved_image_dir_or_na}} |
| json_file | {{json_file}} |
| image_files | {{image_files_top3_or_na}} |

可在“任务”面板查看后续状态与结果。

### B) 提交失败
任务提交失败：
| 字段 | 值 |
|---|---|
| error_code | {{error_code}} |
| error_message | {{error_message}} |
| request_id | {{request_id}} |
