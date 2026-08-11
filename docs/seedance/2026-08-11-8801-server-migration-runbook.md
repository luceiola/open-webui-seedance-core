# 8801 内网服务器并行迁移执行手册（已完成归档）

日期：2026-08-11  
时区：Asia/Shanghai

> 当前状态：迁移已完成，正式生产入口为 `10.104.14.205:8801`。本机旧 prod 服务已停用，只保留历史 Git 与数据快照；日常操作以 `deploy/server-operations.md` 为准。

## 1. 目标与边界

- 开发源：`/Users/lucas/Documents/open-webui-seedance-core`
- 当前本机生产副本：`/Users/lucas/srv/open-webui-seedance-prod`
- 本机服务：`http://10.104.18.64:8801`
- 目标服务：`http://10.104.14.205:8801`
- SSH：`baize@10.104.14.205`
- 目标数据根目录：`/data/openwebui-seedance-prod`
- 目标代码发布库：`/home/baize/git/open-webui-seedance-prod.git`
- 目标运行目录：`/home/baize/workspace/openwebui-seedance-prod`
- 目标 Conda 环境：`/home/baize/miniconda3/envs/openwebui-seedance-prod`
- 目标 systemd 服务：`openwebui-seedance-prod.service`

迁移采用并行部署。本机 8801 在目标服务稳定前继续运行。首次部署只复制一个一致性数据快照，之后本地开发数据与服务器生产数据不做双向同步。

## 2. 已核实信息

- 目标机为 Ubuntu 22.04，24 核、125 GiB 内存。
- `/data` 为 3.6 TiB ext4，当前约 1.3 TiB 可用。
- 目标机已经安装 Git、rsync、ffmpeg、systemd 和 Conda Python 3.11 环境。
- 目标机原有 `openwebui-seedance.service` 占用 8801，该实例已决定废弃。
- 当前本机应用数据约 1.4 GiB。
- SMB uploads 约 3 GiB，generated-artifacts 约 22 GiB。
- 当前数据库 `integrity_check=ok`，journal mode 为 WAL。
- 预检查时 `file` 表有 723 条旧 SMB uploads 绝对路径；正式快照迁移时必须重新计数。
- 任务缓存 JSON 未发现旧 SMB 绝对路径；任务归档使用相对路径。
- 迁移前运行 prod 提交为 `a16ce7a27`，首次服务器版本以它为基线。
- 迁移时 core 有未提交的 v1.1.7 模板和测试，首次部署未包含这些 WIP。

## 3. 网络和私有 GitHub

目标机访问 GitHub 时使用本机内网代理：

```bash
export http_proxy=http://10.104.18.64:7890
export https_proxy=http://10.104.18.64:7890
export all_proxy=socks5h://10.104.18.64:7890
export HTTP_PROXY="$http_proxy"
export HTTPS_PROXY="$https_proxy"
export ALL_PROXY="$all_proxy"
```

目标生产运行时不注入这些代理变量。Ark 和 Tapque 已验证可由目标机直接访问，避免生产服务依赖本机代理。

正式发布不要求目标机直接访问私有 GitHub：

1. core 在本机完成提交、测试和构建。
2. 本机使用现有 GitHub 认证推送 `origin`。
3. 本机通过 SSH 将精确 commit/tag 推送到目标机 bare Git 仓库。
4. 目标运行目录只从目标机 bare Git 仓库部署。

若以后必须由目标机直接拉取 GitHub，只使用仓库级只读 Deploy Key。私钥留在目标机，GitHub Deploy Key 不启用写权限；禁止使用带 token 的 URL。

## 4. 阶段 A：媒体预同步（本机服务保持运行）

### 4.1 本机检查

```bash
cd /Users/lucas/srv/open-webui-seedance-prod
test -d /Volumes/市场素材/AI_Output/webui_prod/uploads
test -d /Volumes/市场素材/AI_Output/webui_prod/generated-artifacts
ssh -o BatchMode=yes baize@10.104.14.205 'df -h /data'
```

### 4.2 建立目标目录

```bash
ssh baize@10.104.14.205 '
  set -eu
  mkdir -p \
    /data/openwebui-seedance-prod/uploads \
    /data/openwebui-seedance-prod/generated-artifacts \
    /data/openwebui-seedance-prod/app-data \
    /data/openwebui-seedance-prod/config \
    /data/openwebui-seedance-prod/migration-logs \
    /data/openwebui-seedance-prod/release-builds \
    /home/baize/git
'
```

### 4.3 首轮 rsync

本机为 openrsync 2.6.9，使用兼容参数，不使用 `--info=progress2`，也不使用 `--delete`。

```bash
mkdir -p /Users/lucas/srv/open-webui-seedance-prod/.logs/server-migration-20260811

rsync -a --partial --progress --stats \
  /Volumes/市场素材/AI_Output/webui_prod/uploads/ \
  baize@10.104.14.205:/data/openwebui-seedance-prod/uploads/ \
  2>&1 | tee /Users/lucas/srv/open-webui-seedance-prod/.logs/server-migration-20260811/uploads-presync.log

rsync -a --partial --progress --stats \
  /Volumes/市场素材/AI_Output/webui_prod/generated-artifacts/ \
  baize@10.104.14.205:/data/openwebui-seedance-prod/generated-artifacts/ \
  2>&1 | tee /Users/lucas/srv/open-webui-seedance-prod/.logs/server-migration-20260811/artifacts-presync.log
```

首轮同步允许源文件继续变化。任何 `.part` 文件在最终同步阶段重新核对。

### 4.4 预同步核验

```bash
du -sh \
  /Volumes/市场素材/AI_Output/webui_prod/uploads \
  /Volumes/市场素材/AI_Output/webui_prod/generated-artifacts

ssh baize@10.104.14.205 '
  du -sh \
    /data/openwebui-seedance-prod/uploads \
    /data/openwebui-seedance-prod/generated-artifacts
'

rsync -an --itemize-changes \
  /Volumes/市场素材/AI_Output/webui_prod/uploads/ \
  baize@10.104.14.205:/data/openwebui-seedance-prod/uploads/

rsync -an --itemize-changes \
  /Volumes/市场素材/AI_Output/webui_prod/generated-artifacts/ \
  baize@10.104.14.205:/data/openwebui-seedance-prod/generated-artifacts/
```

预同步 dry-run 可以存在新增或变化项；这里只记录差异，不要求归零。

## 5. 阶段 B：指定时间后生成一致快照

只有收到明确执行时间后才进行本节。

### 5.1 停止本机写入

1. 在任务页面确认没有必须继续等待的活跃任务。
2. 使用 `lsof -nP -iTCP:8801 -sTCP:LISTEN` 检查实际监听进程。
3. 向确认后的 PID 发送 `SIGINT`，不要使用未核实的宽泛 kill 命令。
4. 确认 8801 已停止监听。

### 5.2 SQLite checkpoint 和备份

```bash
cd /Users/lucas/srv/open-webui-seedance-prod
MIGRATION_TS=$(date +%Y%m%d-%H%M%S)

sqlite3 .data-prod/webui.db 'PRAGMA wal_checkpoint(TRUNCATE);'
sqlite3 -readonly .data-prod/webui.db 'PRAGMA integrity_check;'
sqlite3 .data-prod/webui.db ".backup '.data-prod/maintenance-backups/webui-pre-server-migration-${MIGRATION_TS}.db'"
```

必须看到 `ok` 后才能继续。

### 5.3 最终媒体和应用数据

重复阶段 A 的两个 rsync 命令。完成后连续执行两次 dry-run，均不应再出现文件差异。

```bash
rsync -a --partial --progress --stats \
  /Users/lucas/srv/open-webui-seedance-prod/.data-prod/ \
  baize@10.104.14.205:/data/openwebui-seedance-prod/app-data/
```

复制完成后立即恢复本机 8801。本机与目标机随后并行运行。

## 6. 首次代码发布

### 6.1 初始化目标 bare 仓库

```bash
ssh baize@10.104.14.205 '
  set -eu
  if [ ! -d /home/baize/git/open-webui-seedance-prod.git ]; then
    git init --bare /home/baize/git/open-webui-seedance-prod.git
  fi
'
```

### 6.2 从当前 prod 推送首次基线

首次版本必须来自当前运行 prod，而不是 core 的未提交工作区。

```bash
cd /Users/lucas/srv/open-webui-seedance-prod
git status --short
git rev-parse HEAD

git remote get-url server-prod >/dev/null 2>&1 || \
  git remote add server-prod baize@10.104.14.205:/home/baize/git/open-webui-seedance-prod.git

git push server-prod a16ce7a27:refs/heads/main
git tag -a server-prod-initial-20260811 a16ce7a27 -m 'Initial server production mirror'
git push server-prod refs/tags/server-prod-initial-20260811
```

如果 tag 已存在且指向不同提交，停止执行，不覆盖 tag。

### 6.3 建立目标工作区并复制前端构建

```bash
ssh baize@10.104.14.205 '
  set -eu
  test ! -e /home/baize/workspace/openwebui-seedance-prod
  mkdir -p /home/baize/workspace
  git clone /home/baize/git/open-webui-seedance-prod.git \
    /home/baize/workspace/openwebui-seedance-prod
  cd /home/baize/workspace/openwebui-seedance-prod
  git checkout server-prod-initial-20260811
'

rsync -a --partial --progress \
  /Users/lucas/srv/open-webui-seedance-prod/build/ \
  baize@10.104.14.205:/home/baize/workspace/openwebui-seedance-prod/build/
```

构建产物必须与 `a16ce7a27` 配套，不使用 core 当前 WIP 重新构建。

### 6.4 复制外部配置

```bash
scp /Users/lucas/srv/open-webui-seedance-prod/config/ark.env \
  baize@10.104.14.205:/data/openwebui-seedance-prod/config/ark.env

scp /Users/lucas/srv/open-webui-seedance-prod/config/key_routing.json \
  baize@10.104.14.205:/data/openwebui-seedance-prod/config/key_routing.json
```

在目标机设置权限，并将 `ark.env` 中的存储配置改为：

```dotenv
DATA_DIR=/data/openwebui-seedance-prod/app-data
UPLOAD_DIR=/data/openwebui-seedance-prod/uploads
TASK_ARTIFACTS_ROOT=/data/openwebui-seedance-prod/generated-artifacts
KEY_ROUTING_CONFIG_FILE=/data/openwebui-seedance-prod/config/key_routing.json
```

`ark.env` 权限设为 `600`，不得输出或提交其内容。

## 7. 目标数据库路径迁移

只在目标数据库副本执行：

```bash
TARGET_DB=/data/openwebui-seedance-prod/app-data/webui.db
DB_BACKUP=/data/openwebui-seedance-prod/app-data/webui.db.before-path-migration-20260811
EXPECTED_OLD_PATHS=$(sqlite3 -readonly "$TARGET_DB" \
  "SELECT COUNT(*) FROM file WHERE path LIKE '/Volumes/市场素材/AI_Output/webui_prod/uploads/%';")
cp -p "$TARGET_DB" "$DB_BACKUP"

sqlite3 "$TARGET_DB" <<'SQL'
BEGIN IMMEDIATE;
UPDATE file
SET path = replace(
  path,
  '/Volumes/市场素材/AI_Output/webui_prod/uploads/',
  '/data/openwebui-seedance-prod/uploads/'
)
WHERE path LIKE '/Volumes/市场素材/AI_Output/webui_prod/uploads/%';
SELECT changes();
COMMIT;
PRAGMA integrity_check;
SQL

sqlite3 -readonly "$TARGET_DB" \
  "SELECT COUNT(*) FROM file WHERE path LIKE '/Volumes/市场素材/%';"
sqlite3 -readonly "$TARGET_DB" \
  "SELECT COUNT(*) FROM file WHERE path LIKE '/data/openwebui-seedance-prod/uploads/%';"
```

期望：`changes()` 与更新前动态读取的 `EXPECTED_OLD_PATHS` 相同，旧路径计数为 0，
新路径计数与 `EXPECTED_OLD_PATHS` 相同，完整性为 `ok`。

检查数据库引用的文件：

```bash
missing_count=0
while IFS= read -r media_path; do
  if [ ! -f "$media_path" ]; then
    printf 'MISSING %s\n' "$media_path"
    missing_count=$((missing_count + 1))
  fi
done < <(sqlite3 -noheader "$TARGET_DB" 'SELECT path FROM file ORDER BY id;')
printf 'missing_count=%s\n' "$missing_count"
```

缺失数量非零时保存清单并调查，不修改本机源数据库。

## 8. Python 环境和临时端口验收

在目标机执行：

```bash
source /home/baize/miniconda3/etc/profile.d/conda.sh

if ! conda env list | awk '{print $1}' | grep -qx openwebui-seedance-prod; then
  conda create -y -n openwebui-seedance-prod --clone openwebui-seedance
fi

conda activate openwebui-seedance-prod
cd /home/baize/workspace/openwebui-seedance-prod

ENV_FILE=/data/openwebui-seedance-prod/config/ark.env \
DATA_DIR=/data/openwebui-seedance-prod/app-data \
bash scripts/seedance/preflight.sh --auto-fix

PORT=18801 \
ENV_FILE=/data/openwebui-seedance-prod/config/ark.env \
DATA_DIR=/data/openwebui-seedance-prod/app-data \
bash scripts/seedance/run_openwebui.sh \
  >/tmp/openwebui-seedance-prod-18801.log 2>&1 &
TEST_PID=$!

sleep 15
curl -fsS http://127.0.0.1:18801/health
kill -INT "$TEST_PID"
wait "$TEST_PID" || true
```

检查临时日志，不得存在数据库迁移、权限、缺包或路径错误。

## 9. systemd 接管目标机 8801

本节需要用户在目标机 SSH 终端输入 sudo 密码。

```bash
sudo systemctl disable --now openwebui-seedance.service
```

创建 `/etc/systemd/system/openwebui-seedance-prod.service`：

```ini
[Unit]
Description=OpenWebUI Seedance Production (8801)
After=network.target

[Service]
Type=simple
User=baize
WorkingDirectory=/home/baize/workspace/openwebui-seedance-prod
EnvironmentFile=/data/openwebui-seedance-prod/config/ark.env
Environment=ENV_FILE=/data/openwebui-seedance-prod/config/ark.env
Environment=HOST=0.0.0.0
Environment=PORT=8801
Environment=PATH=/home/baize/miniconda3/envs/openwebui-seedance-prod/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin
ExecStart=/bin/bash /home/baize/workspace/openwebui-seedance-prod/scripts/seedance/run_openwebui.sh
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now openwebui-seedance-prod.service
sudo systemctl status openwebui-seedance-prod.service --no-pager -l
journalctl -u openwebui-seedance-prod.service -n 100 --no-pager
curl -fsS http://127.0.0.1:8801/health
```

## 10. 验收清单

- [ ] `http://10.104.14.205:8801/health` 返回成功。
- [ ] 用户可以登录，模型列表可以加载。
- [ ] 任务列表和关键历史任务正常。
- [ ] 历史上传、视频、缩略图、预览和下载正常。
- [ ] 新上传写入目标机 uploads。
- [ ] 新生成产物写入目标机 generated-artifacts。
- [ ] journal 没有 SMB、权限、SQLite 或重启循环错误。
- [x] 本机 8801 在迁移观察期曾恢复运行；现已停用，不再作为回滚来源。

验收不提交计费生成任务，除非另行授权。

## 11. 后续从 core 发布

core 是唯一开发源。prod 副本的通用启动修复需先分类回收到 core，生产 UUID 留在外部配置。

每次发布：

1. 在 core 完成修改、测试和前端构建。
2. 提交并推送私有 GitHub `origin`。
3. 创建不可变 release tag。
4. 将 commit/tag 推送到 `server-prod`。
5. 上传与 commit SHA 配套的 `build/`。
6. 目标机 checkout 精确 tag，运行 preflight，重启 systemd，执行 healthcheck。

目标机不得修改跟踪代码。服务器配置、数据库、媒体和密钥始终位于仓库外。

## 12. 回滚

- 迁移当晚目标新服务失败时可临时使用本机 `10.104.18.64:8801`；该入口现已停用，日常回滚使用服务器上一 release。
- 数据库路径迁移失败：停止目标服务，恢复 `webui.db.before-path-migration-20260811`。
- 代码版本失败：checkout 上一已验收 tag，恢复对应 SHA 的 `build/`，重启并执行 healthcheck。

观察期内不得删除本机数据库、SMB 原始文件或目标机旧服务文件。

## 13. 迁移后清理

以下不在今晚关键路径：

- 将 prod 通用启动修复整理回 core。
- 将本地开发服务改为从 core 启动并使用本机独立存储。
- [x] 停用本地 prod 服务；旧代码、数据库和 SMB 数据只作历史快照保留。
- 清理目标机旧 Seedance 代码、旧 Conda 环境和旧数据库。

必须在目标生产稳定观察后单独执行，不得与首次部署同时删除。

## 14. 执行记录

### 2026-08-11 首轮预同步

- [x] 目标数据目录已创建。
- [x] bare Git 发布库已初始化：`/home/baize/git/open-webui-seedance-prod.git`。
- [x] uploads 首轮同步完成，rsync 退出码为 0。
- [x] uploads 源端和目标端均为 732 个文件。
- [x] uploads 完整 dry-run 无差异。
- [x] generated-artifacts 首轮同步完成，最终完整 rsync 退出码为 0。
- [x] generated-artifacts 源端和目标端均为 8,215 个文件。
- [x] generated-artifacts 完整 dry-run 无差异。
- [x] 目标 generated-artifacts 目录统计为 23,472,421,451 字节。
- [x] artifacts 完成日志未发现 warning/error。
- [x] 阶段 B 和目标服务部署已于 2026-08-11 完成，详见下方正式迁移记录。

本地日志：

- `.logs/server-migration-20260811/uploads-presync.log`
- `.logs/server-migration-20260811/uploads-presync-dry-run.log`
- `.logs/server-migration-20260811/artifacts-presync-tail.log`
- `.logs/server-migration-20260811/artifacts-presync-finalize.log`
- `.logs/server-migration-20260811/artifacts-presync-dry-run.log`

### 2026-08-11 正式迁移

- [x] 停机前确认最近两小时无活跃任务。
- [x] 本机 8801 使用精确 PID `20241` 接收 `SIGINT`，未使用宽泛 kill。
- [x] SQLite WAL checkpoint 完成，`integrity_check=ok`。
- [x] 本机快照备份：`.data-prod/maintenance-backups/webui-pre-server-migration-20260811-182103.db`。
- [x] uploads 和 generated-artifacts 完成最终增量同步，连续两次 dry-run 均无差异。
- [x] `.data-prod` 一致快照复制到目标 `app-data` 后，本机 8801 已恢复健康，PID `97041`。
- [x] 首次发布基线和 tag 已推送，目标工作区提交为 `a16ce7a27`。
- [x] 目标独立 Conda 环境 `openwebui-seedance-prod` 已克隆。
- [x] 目标数据库动态读取旧路径数为 778，实际更新 778 条；旧路径为 0，新路径为 778。
- [x] 目标数据库 `integrity_check=ok`，数据库引用媒体缺失数为 0。
- [x] 临时端口 18801 运行时健康检查通过，测试进程已停止。
- [x] 原 `openwebui-seedance.service` 已停止并禁用。
- [x] `openwebui-seedance-prod.service` 已启用并运行，目标 8801 健康，`NRestarts=0`。
- [x] 外部访问 `http://10.104.14.205:8801/health` 和首页均返回 200。
- [ ] 用户登录、任务页面和关键历史媒体需要人工浏览器验收。
- [ ] 未执行付费生成测试。

运行提示：首次正式启动耗时约 2 分 20 秒，主要用于加载句向量模型和插件依赖。
现有 unit 仅通过 `EnvironmentFile` 注入配置，运行正常但启动脚本会打印 env 文件路径警告；
下次更新 unit 时加入本手册中的 `Environment=ENV_FILE=...` 后可消除该警告。
