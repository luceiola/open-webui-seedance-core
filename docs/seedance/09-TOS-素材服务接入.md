# TOS 素材服务接入说明

本文档说明当前工作区中，素材包如何通过 TOS 为 Seedance 提供可访问的媒体 URL。

## 1. 背景

Seedance 生成接口要求媒体输入为 `image_url / video_url / audio_url`。当前实现已收敛为 **TOS-only**：所有素材 URL 都由 TOS 预签名生成。

## 2. 配置项

在 `config/ark.env` 中配置：

```bash
MATERIAL_PACK_TOS_ENABLED=true
TOS_ACCESS_KEY=***
TOS_SECRET_KEY=***
TOS_ENDPOINT=tos-cn-beijing.volces.com
TOS_REGION=cn-beijing
TOS_BUCKET=your-bucket
TOS_PREFIX=material-packages
TOS_PRESIGN_EXPIRES_SECONDS=3600
```

并安装 SDK：

```bash
source .venv/bin/activate
pip install tos
```

## 3. 上传阶段行为

`POST /api/v1/material-packages/` 上传 ZIP 后：

- 每个支持的素材直接上传到 TOS（TOS-only）。
- manifest 中会记录 `tos_key/tos_status/tos_error`。

## 4. 生成阶段行为

`POST /api/v1/material-packages/{package_id}/generate`：

- 解析 prompt 中 `@引用名`。
- 对每个引用生成 TOS 预签名 URL（必要时自动补传对象）。
- 若仍无法解析，返回 `unresolved_references` 明细用于排障。

## 5. 关键文件

- `.venv/lib/python3.11/site-packages/open_webui/routers/material_packages.py`
- `scripts/run_openwebui.sh`
- `docs/08-运行命令手册.md`
