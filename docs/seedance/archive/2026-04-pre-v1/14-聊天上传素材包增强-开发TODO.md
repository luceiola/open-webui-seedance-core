# 聊天上传素材包增强开发 TODO

## 目标

把聊天 `+` 上传入口纳入素材包主链路，实现：
- ZIP/单文件统一入包
- 可识别的素材包名称
- 包内素材地址查询

## 当前现状

- 已有：ZIP 上传到素材包（后端接口可用）。
- 已有：素材包列表/详情/引用解析/生成。
- 缺失：聊天上传上下文到素材包工具的直接闭环。
- 缺失：单文件自动成包。
- 缺失：包内素材地址专用查询接口/工具。

## P0 需求冻结

- [x] 确认字段语义：`package_display_name`（展示名）与 `asset_package_id`（系统 ID）严格区分。
- [ ] 确认聊天上传上下文字段（upload_id/file_path/mime）。
- [x] 确认单消息多单文件策略：采用 **方案 B（同消息合并为一个包）**。
- [x] 确认同消息合并包命名规则：`合并上传-n个素材-yyMMdd-HHmm`（示例：`合并上传-3个素材-260422-1530`）。
- [ ] 确认素材地址返回策略（是否默认返回 temp_url）。

## P1 后端接口增强

- [x] 新增 `POST /api/v1/material-packages/from-upload`。
- [x] 支持 ZIP 与单媒体文件分支处理。
- [x] 为素材包补字段：`package_display_name/source_filename/source_kind`。
- [x] 新增 `GET /api/v1/material-packages/{package_id}/assets`（支持 `include_temp_urls`）。
- [x] 增强 `GET /api/v1/material-packages` 返回 `package_display_name`。
- [x] 确保所有列表/详情接口同时返回：`asset_package_id` + `package_display_name`。

## P2 单文件成包能力

- [x] 实现单图片/单视频/单音频直接入包。
- [x] 实现“同消息多单文件合并入同一素材包”（方案 B）。
- [x] 实现合并包命名器：`合并上传-n个素材-yyMMdd-HHmm`。
- [x] 复用现有安全校验、类型校验、TOS 上传链路。
- [x] 保证单文件包可直接被 `resolve/generate` 使用。

## P3 Tool 扩展

- [x] 新增 `create_material_package_from_chat_upload(...)`。
- [x] 增强 `list_material_packages` 返回 `package_display_name/source_filename`。
- [x] 新增 `get_material_package_assets(asset_package_id, include_temp_urls=false)`。
- [x] 保持错误结构统一：`status_code/error_code/error_message/request_id`。

## P4 Skill / Prompt 更新

- [x] 更新 `seedance-execution-skill`：
  - [x] 检测会话新上传文件后优先触发入包工具。
  - [x] 入包成功后再走 `resolve -> generate`。
- [x] 增加“历史素材包复用”规则（先 list 再选 package_id）。

## P5 前端接入（聊天 + 上传）

- [ ] 在 `+ -> 传文件` 完成后，把上传元信息传给工具可消费层。
- [ ] 上传完成提示里展示 `asset_package_id + package_display_name + references`。
- [ ] 上传失败提示透传后端错误。

## P6 文档与命令

- [x] 更新 [07-素材包Tool-接入与联调.md](/Users/lucas/Documents/openwebui-seedance/docs/07-素材包Tool-接入与联调.md)（新增聊天上传入包流程）。
- [x] 更新 [08-运行命令手册.md](/Users/lucas/Documents/openwebui-seedance/docs/08-运行命令手册.md)（新增 from-upload 与 assets 查询示例）。

## P7 测试与验收

- [ ] 用 ZIP 上传验证：能入包、可引用、可生成。
- [ ] 用单图片上传验证：自动单文件包、可生成。
- [ ] 用单视频上传验证：自动单文件包、可生成。
- [ ] 用单音频上传验证：自动单文件包、可生成。
- [ ] 验证列表展示名称可识别（不是只有 pkg_id）。
- [ ] 验证素材地址查询返回稳定字段（tos_key/tos_status）。

## DoD

- [ ] 聊天上传 ZIP/单文件均可自动入包。
- [ ] 列表可读：用户能凭 `package_display_name` 选择复用包。
- [ ] 包内素材地址可查，字段稳定。
- [ ] Tool + Skill + 文档一致且可联调。
