# Open-WebUI Seedance 服务器运维手册

本文档用于维护部署在内网服务器 `10.104.14.205:8801` 的 Open-WebUI Seedance 生产服务。Mac 上的 core 仓库是唯一代码开发源；服务器保存生产数据库、任务记录、上传文件和生成产物，并只运行已经提交和标记的 Git 版本。

> 服务器已进入生产状态。禁止再用本机 `/Users/lucas/srv/open-webui-seedance-prod/.data-prod` 覆盖服务器 `app-data`，也禁止把本机旧媒体目录整体回灌到服务器。代码从 core 发布，生产数据只在服务器维护和备份。

## 环境文件隔离

core 仓库使用不同环境文件，禁止交叉使用：

| 场景 | 文件 | 用途 |
| --- | --- | --- |
| 205 生产 | `config/ark.205.env` | 服务器 Linux 路径、生产密钥和生产数据配置 |
| 本机测试 | `config/ark.local.env` | macOS `ai-utility` 路径和本机测试配置 |

`config/ark.205.env` 只作为发布输入，不能提交密钥；205 服务器上的实际文件为 `/data/openwebui-seedance-prod/config/ark.205.env`，权限应为 `0600`。本机启动必须显式指定 `ENV_FILE=config/ark.local.env`，不要让脚本回退到通用的 `config/ark.env`。

其中 `AU_BIN` 和 `AU_WORKDIR` 必须与运行主机匹配：

```dotenv
# 205
AU_BIN=/home/baize/services/ai-utility/current/.venv/bin/au
AU_WORKDIR=/home/baize/services/ai-utility/current

# local
AU_BIN=/Users/lucas/Documents/ai-utility/.venv/bin/au
AU_WORKDIR=/Users/lucas/Documents/ai-utility
```

## 快速入口

### 服务信息

| 项目 | 当前值 |
| --- | --- |
| 生产地址 | `http://10.104.14.205:8801` |
| SSH | `baize@10.104.14.205` |
| systemd | `openwebui-seedance-prod.service` |
| 当前生产基线 | `dbf59c023` |
| Python 环境 | `/home/baize/miniconda3/envs/openwebui-seedance-prod` |
| 目标工作区 | `/home/baize/workspace/openwebui-seedance-prod` |
| 内网 bare Git | `/home/baize/git/open-webui-seedance-prod.git` |

本机旧 prod 的 `8801` 已停用，只保留历史 Git 与数据快照，不作为业务回退入口，也不得重新写回服务器生产数据。

### 关键路径

| 用途 | 服务器路径 |
| --- | --- |
| 应用数据与主数据库 | `/data/openwebui-seedance-prod/app-data` |
| 主数据库 | `/data/openwebui-seedance-prod/app-data/webui.db` |
| 任务目录与任务缓存 | `/data/openwebui-seedance-prod/app-data/cache` |
| uploads | `/data/openwebui-seedance-prod/uploads` |
| generated-artifacts | `/data/openwebui-seedance-prod/generated-artifacts` |
| 运行配置 | `/data/openwebui-seedance-prod/config/ark.205.env` |
| Key routing | `/data/openwebui-seedance-prod/config/key_routing.json` |
| systemd 源文件 | `/data/openwebui-seedance-prod/config/openwebui-seedance-prod.service` |
| 数据库迁移备份 | `/data/openwebui-seedance-prod/app-data/webui.db.before-path-migration-20260811-1836` |
| 迁移日志 | `/data/openwebui-seedance-prod/migration-logs` |
| release build 归档 | `/data/openwebui-seedance-prod/release-builds` |

## 日常状态检查

### 一键检查

```bash
ssh baize@10.104.14.205 'systemctl is-active openwebui-seedance-prod.service; systemctl is-enabled openwebui-seedance-prod.service; systemctl show openwebui-seedance-prod.service -p MainPID -p NRestarts -p ActiveEnterTimestamp --no-pager; curl --noproxy "*" -fsS http://127.0.0.1:8801/health; echo; git -C /home/baize/workspace/openwebui-seedance-prod log -1 --oneline; df -h /data'
```

正常结果应满足：

- 服务为 `active` 和 `enabled`。
- `/health` 返回 `{"status":true}`。
- `NRestarts` 没有持续增加。
- Git commit 是预期 release。
- `/data` 有足够剩余空间。

### 从 Mac 检查入口

```bash
curl --noproxy '*' -fsS http://10.104.14.205:8801/health
curl --noproxy '*' -fsS http://10.104.14.205:8801/api/version
curl --noproxy '*' -sS -o /dev/null -w '%{http_code}\n' http://10.104.14.205:8801/
```

依次应返回健康 JSON、版本 JSON 和 HTTP `200`。

## 日常发布

### 发布原则

- `/Users/lucas/Documents/open-webui-seedance-core` 是唯一开发源。
- 目标机不直接访问 private GitHub，也不保存 GitHub 凭据。
- 本机通过 SSH 将 commit 和 tag 推送到目标 bare Git。
- 每个生产版本使用不可变 tag，前端 `build/` 必须与同一 commit 配套。
- 目标工作区禁止直接开发或提交。
- 发布前确认没有需要继续运行的付费生成任务。

### 在 core 准备 release

先处理 core 工作区中已有的未提交修改，不要把无关 WIP 混入 release。

```bash
cd /Users/lucas/Documents/open-webui-seedance-core
git status --short
PYTHONPATH=backend pytest -q <相关测试路径>
npm run build
git add <本次文件>
git commit -m '<发布说明>'
RELEASE_TAG=server-prod-$(date +%Y%m%d-%H%M)-<简短说明>
git tag -a "$RELEASE_TAG" HEAD -m "$RELEASE_TAG"
git fetch server-prod main:refs/remotes/server-prod/main
git merge-base --is-ancestor server-prod/main HEAD
git push origin main
git push origin "$RELEASE_TAG"
git push --atomic server-prod HEAD:refs/heads/main "$RELEASE_TAG"
```

祖先关系检查失败时立即停止发布，先把服务器历史对账回 core；禁止用普通 `--force` 覆盖生产 `main`。只有已核实远端旧 SHA 并另行批准时，才允许使用带显式保护值的 `--force-with-lease=<ref>:<old-sha>`。

如果 core 尚未配置内网 remote：

```bash
git remote add server-prod baize@10.104.14.205:/home/baize/git/open-webui-seedance-prod.git
```

### 上传配套 build

先归档到以 tag 命名的目标目录，再复制到工作区。只对明确的 build 目录使用 `--delete`。

```bash
rsync -a --delete build/ "baize@10.104.14.205:/data/openwebui-seedance-prod/release-builds/${RELEASE_TAG}/build/"
```

### 目标机准备代码

```bash
ssh baize@10.104.14.205 "cd /home/baize/workspace/openwebui-seedance-prod && test -z \"\$(git status --porcelain)\" && git fetch origin --prune --tags && git rev-parse --verify '${RELEASE_TAG}^{commit}' && git checkout --detach '${RELEASE_TAG}' && rsync -a --delete '/data/openwebui-seedance-prod/release-builds/${RELEASE_TAG}/build/' '/home/baize/workspace/openwebui-seedance-prod/build/'"
```

```bash
ssh baize@10.104.14.205 'source /home/baize/miniconda3/etc/profile.d/conda.sh; conda activate openwebui-seedance-prod; cd /home/baize/workspace/openwebui-seedance-prod; ENV_FILE=/data/openwebui-seedance-prod/config/ark.205.env DATA_DIR=/data/openwebui-seedance-prod/app-data bash scripts/seedance/preflight.sh --auto-fix'
```

看到 preflight 成功后再进入重启窗口。目标机首次或冷启动通常需要约两分钟。

### 激活 release

```bash
ssh -t baize@10.104.14.205 'sudo systemctl restart openwebui-seedance-prod.service; for attempt in $(seq 1 180); do if curl --noproxy "*" -fsS --max-time 2 http://127.0.0.1:8801/health; then echo; exit 0; fi; sleep 1; done; journalctl -u openwebui-seedance-prod.service -n 160 --no-pager; exit 1'
```

不要因为启动前 30 到 120 秒的 `connection refused` 连续重启。进程仍在加载模型时，反复重启只会重新计时。

## 服务操作

### 状态、日志和端口

OpenWebUI 由 `systemd` 常驻管理，服务的标准输出和错误输出统一进入
`journalctl`。查看实时控制台输出时，使用下面的 `-f` 命令；按 `Ctrl-C`
只会退出日志跟踪，不会停止服务。

```bash
# 服务状态
ssh baize@10.104.14.205 'systemctl status openwebui-seedance-prod.service --no-pager -l'

# 最近 160 行日志
ssh baize@10.104.14.205 'journalctl -u openwebui-seedance-prod.service -n 160 --no-pager'

# 实时跟踪控制台输出
ssh -t baize@10.104.14.205 'journalctl -u openwebui-seedance-prod.service -f'

# 查看今天以来的日志
ssh baize@10.104.14.205 'journalctl -u openwebui-seedance-prod.service --since today --no-pager'

# 状态、主进程信息和最近日志一起查看
ssh baize@10.104.14.205 'systemctl show openwebui-seedance-prod.service -p MainPID -p NRestarts -p ActiveState -p SubState --no-pager; journalctl -u openwebui-seedance-prod.service -n 80 --no-pager'

# 8801 监听端口
ssh baize@10.104.14.205 'ss -ltnp "sport = :8801"; lsof -nP -iTCP:8801 -sTCP:LISTEN'
```

如果远程账号没有读取 systemd 日志的权限，在对应命令前加 `sudo`，例如：

```bash
ssh -t baize@10.104.14.205 'sudo journalctl -u openwebui-seedance-prod.service -f'
```

### 重启、停止和启动

```bash
ssh -t baize@10.104.14.205 'sudo systemctl restart openwebui-seedance-prod.service'
ssh -t baize@10.104.14.205 'sudo systemctl stop openwebui-seedance-prod.service'
ssh -t baize@10.104.14.205 'sudo systemctl start openwebui-seedance-prod.service'
```

停止或重启前必须确认没有正在归档结果或执行付费生成的近期任务。不要使用 `pkill open-webui`；确需按进程发信号时，必须先核对 systemd `MainPID` 和完整命令。

## 任务与生成结果

### 查看活跃任务

任务索引位于 `app-data/cache/task_catalog.sqlite3`。历史数据中可能存在早期遗留的 `PENDING` 或 `RUNNING`，因此要同时查看创建和更新时间，不能只看汇总数量。

```bash
ssh baize@10.104.14.205 'sqlite3 -header -column /data/openwebui-seedance-prod/app-data/cache/task_catalog.sqlite3 "SELECT task_id,status,provider,datetime(created_at, '\''unixepoch'\'', '\''localtime'\'') AS created,datetime(updated_at, '\''unixepoch'\'', '\''localtime'\'') AS updated FROM task_catalog WHERE lower(status) IN ('\''pending'\'','\''running'\'','\''processing'\'','\''queued'\'') ORDER BY updated_at DESC LIMIT 100;"'
```

发布前还应在任务页面确认没有刚提交的任务。任务可能在最后一次数据库查询后立即创建，停服务前需要再检查一次。

### 卡住任务处理原则

1. 先区分网络不可达、前端没有刷新、本地任务索引过期和供应商真实运行状态。
2. 只查询供应商状态，不重新提交生成。
3. 供应商已经返回成功结果时，优先恢复结果 URL、归档文件并更新任务状态。
4. 只有确认无法取得结果且超过运行时限时，才标记失败。
5. 手工失败原因统一填写 `运行超时，手动停止`，并保留供应商最后响应。
6. 未证明幂等前，禁止用“重试”重新触发可能计费的生成请求。

### Tapque Image 历史路径

迁移前的 Tapque Image 任务可能在记录中保留 `/Users/lucas/.../.data-prod/cache/material_packages/...`。生产版本 `dbf59c023` 会将同一用户下的这类路径安全重定位到服务器 `app-data/cache/material_packages`，并通过以下代理提供图片：

```text
/api/v1/tasks/<task_id>/images/<index>
```

若图片仍不显示，依次核对任务记录、目标文件和服务版本，不要直接批量替换任务 JSON：

```bash
ssh baize@10.104.14.205 'git -C /home/baize/workspace/openwebui-seedance-prod log -1 --oneline; find /data/openwebui-seedance-prod/app-data/cache/material_packages -path "*<task_id>/images/*" -type f -print'
```

## SQLite 与备份

### 数据所有权

- 服务器 `webui.db` 是迁移后的生产主数据库。
- 服务器任务缓存和任务 JSON 是生产任务事实源。
- 本机 `.data-prod` 只保留为迁移历史快照，不是运行中副本，也不得覆盖服务器。
- 代码回滚不等于数据库回滚。

### 主数据库完整性

服务运行时可执行只读检查：

```bash
ssh baize@10.104.14.205 'sqlite3 -readonly /data/openwebui-seedance-prod/app-data/webui.db "PRAGMA integrity_check;"'
```

期望输出 `ok`。不要在服务运行时复制 `webui.db` 文件本体来制作备份。

### 一致性备份

一致性备份需要维护窗口，并会触发约两分钟启动时间：

```bash
ssh -t baize@10.104.14.205 'set -eu; SERVICE=openwebui-seedance-prod.service; SERVICE_STOPPED=0; restore_service() { rc=$?; trap - EXIT INT TERM; if [ "$SERVICE_STOPPED" = 1 ]; then sudo systemctl start "$SERVICE" || rc=$?; fi; exit "$rc"; }; trap restore_service EXIT INT TERM; sudo systemctl stop "$SERVICE"; SERVICE_STOPPED=1; DB=/data/openwebui-seedance-prod/app-data/webui.db; TS=$(date +%Y%m%d-%H%M%S); BACKUP_DIR=/data/openwebui-seedance-prod/app-data/maintenance-backups; mkdir -p "$BACKUP_DIR"; sqlite3 "$DB" "PRAGMA wal_checkpoint(TRUNCATE);"; test "$(sqlite3 -readonly "$DB" "PRAGMA integrity_check;")" = ok; sqlite3 "$DB" ".backup '\''$BACKUP_DIR/webui-$TS.db'\''"; sqlite3 /data/openwebui-seedance-prod/app-data/cache/task_catalog.sqlite3 ".backup '\''$BACKUP_DIR/task-catalog-$TS.sqlite3'\''"; sudo systemctl start "$SERVICE"; SERVICE_STOPPED=0; trap - EXIT INT TERM'
```

退出陷阱会在备份中途失败时尝试恢复服务；命令结束后仍需检查 systemd 和 `/health`。更稳妥的做法是在独立 SSH 窗口持续观察 systemd。

### 数据库路径检查

```bash
ssh baize@10.104.14.205 'sqlite3 -readonly /data/openwebui-seedance-prod/app-data/webui.db "SELECT COUNT(*) FROM file WHERE path LIKE '\''/Volumes/%'\''; SELECT COUNT(*) FROM file WHERE path LIKE '\''/data/openwebui-seedance-prod/uploads/%'\'';"'
```

第一行应为 `0`。发现异常时先备份目标数据库，只修改目标副本，不从本机旧数据库覆盖。

## 媒体与磁盘

### 存储检查

```bash
ssh baize@10.104.14.205 'du -sh /data/openwebui-seedance-prod/{app-data,uploads,generated-artifacts,release-builds}; df -h /data'
ssh baize@10.104.14.205 'find /data/openwebui-seedance-prod/uploads -type f | wc -l; find /data/openwebui-seedance-prod/generated-artifacts -type f | wc -l'
```

不要把文件数量当作完整性证明；关键任务还应检查文件大小、媒体头或通过前端实际预览和下载。

### 迁移后的同步边界

- 不再挂载或依赖 SMB。
- 不再定期把 Mac uploads、generated-artifacts 或 `.data-prod` 推向服务器。
- 需要异地备份时，应从服务器向明确的备份目标复制，或使用 `/data` 的存储快照。
- 未建立备份保留策略前，不清理服务器历史媒体。
- 不对 `/data/openwebui-seedance-prod` 执行宽泛递归删除。

## 配置、密钥与代理

### 配置检查

只能查看变量名和文件权限，不在终端、日志、文档或截图中输出密钥值：

```bash
ssh baize@10.104.14.205 'stat -c "%a %U:%G %n" /data/openwebui-seedance-prod/config/ark.205.env /data/openwebui-seedance-prod/config/key_routing.json /home/baize/workspace/openwebui-seedance-prod/.webui_secret_key; sed -n "s/^\([A-Z0-9_]*\)=.*/\1/p" /data/openwebui-seedance-prod/config/ark.205.env | sort'
```

`ark.205.env` 和 `.webui_secret_key` 权限应为 `0600`。不要旋转 `.webui_secret_key` 作为普通排障步骤；这会影响已有登录令牌，并可能影响依赖该密钥的数据。

### systemd 配置

```bash
ssh baize@10.104.14.205 'systemctl cat openwebui-seedance-prod.service'
```

unit 应同时包含：

```ini
EnvironmentFile=/data/openwebui-seedance-prod/config/ark.205.env
Environment=ENV_FILE=/data/openwebui-seedance-prod/config/ark.205.env
```

只配置 `EnvironmentFile` 时变量仍会注入，但启动脚本会打印 env 文件路径警告。修改 unit 后执行：

```bash
ssh -t baize@10.104.14.205 'sudo cp /data/openwebui-seedance-prod/config/openwebui-seedance-prod.service /etc/systemd/system/openwebui-seedance-prod.service; sudo systemctl daemon-reload; sudo systemctl restart openwebui-seedance-prod.service'
```

### GitHub 与代理

目标机日常发布不需要 GitHub。private GitHub 认证只保留在 Mac core 仓库，本机再推送到目标 bare Git。

目标机临时访问外网时使用 Mac 的内网代理地址，不要写成服务器自己的 `127.0.0.1`：

```bash
export https_proxy=http://10.104.18.64:7890
export http_proxy=http://10.104.18.64:7890
export all_proxy=socks5h://10.104.18.64:7890
```

不要把代理注入生产 systemd。Ark 和 Tapque 的生产请求当前可以直接访问。

## 回滚

### 代码回滚

回滚前记录当前 commit，并确认目标 tag 及其 build 归档存在：

```bash
ROLLBACK_TAG=<上一已验收tag>
ssh baize@10.104.14.205 "cd /home/baize/workspace/openwebui-seedance-prod && git fetch origin --tags && git rev-parse --verify '${ROLLBACK_TAG}^{commit}' && test -f '/data/openwebui-seedance-prod/release-builds/${ROLLBACK_TAG}/build/index.html' && git checkout --detach '${ROLLBACK_TAG}' && rsync -a --delete '/data/openwebui-seedance-prod/release-builds/${ROLLBACK_TAG}/build/' '/home/baize/workspace/openwebui-seedance-prod/build/'"
ssh -t baize@10.104.14.205 'sudo systemctl restart openwebui-seedance-prod.service'
```

回滚后执行健康检查，并测试登录、任务列表、历史媒体和下载。

### 数据回滚边界

- 代码回滚不会回滚 SQLite、任务 JSON 或媒体。
- 数据恢复必须先停服务，并先备份故障现场的当前数据库。
- 恢复迁移前数据库会重新引入旧 Mac/SMB 路径，不能直接用于当前服务器。
- 目标服务不可用时优先回滚到服务器上一已验收 release；本机旧 prod 已停用，不能作为业务回退实例。

## 常见故障

### systemd active 但 8801 connection refused

冷启动会加载句向量模型和插件依赖，实测约两分钟。先检查进程是否持续存在、CPU 是否活动和 `NRestarts` 是否稳定：

```bash
ssh baize@10.104.14.205 'systemctl show openwebui-seedance-prod.service -p MainPID -p NRestarts -p ActiveState -p SubState; pid=$(systemctl show -p MainPID --value openwebui-seedance-prod.service); ps -p "$pid" -o pid,stat,etime,%cpu,%mem,nlwp,wchan:30,command; journalctl -u openwebui-seedance-prod.service -n 120 --no-pager'
```

若日志停在模型加载且进程仍工作，继续等待。若 `NRestarts` 持续增加，再排查配置、权限、缺包和端口占用。

### 页面还是旧版本

```bash
ssh baize@10.104.14.205 'git -C /home/baize/workspace/openwebui-seedance-prod log -1 --oneline; stat /home/baize/workspace/openwebui-seedance-prod/build/index.html'
```

后端代码和 frontend build 必须来自同一 release。硬刷新浏览器不能弥补服务器 build 未更新。

### 历史图片或视频不显示

1. 在任务弹窗记录任务 ID、用户 ID、provider 和资源字段。
2. 检查目标文件是否存在且大小非零。
3. 检查数据库 `file.path` 是否指向 `/data/openwebui-seedance-prod/uploads`。
4. Tapque Image 检查服务是否至少为 `dbf59c023`。
5. 查看请求 URL 的 HTTP 状态和 journal，不要先改任务记录。

### database is locked 或 SQLite 错误

- 不要反复重启或复制 WAL 状态下的裸数据库。
- 记录故障时间和相关任务。
- 检查是否存在第二个使用同一 `DATA_DIR` 的 Open-WebUI 进程。
- 需要写入修复时先停服务、checkpoint、完整性检查和 `.backup`。

### 服务重启循环

```bash
ssh baize@10.104.14.205 'systemctl show openwebui-seedance-prod.service -p NRestarts -p ExecMainStatus; journalctl -u openwebui-seedance-prod.service -n 200 --no-pager; namei -l /data/openwebui-seedance-prod/app-data/webui.db; namei -l /data/openwebui-seedance-prod/uploads; namei -l /data/openwebui-seedance-prod/generated-artifacts'
```

先定位第一条真实错误，不要让后续重复日志掩盖根因。

## 安全与变更纪律

- core 是唯一开发源；目标工作区只 fetch 和 checkout。
- private GitHub 凭据不进入目标机。
- 密钥、代理凭据和登录密码不写入仓库、命令输出或手册。
- 发布和重启前确认近期付费任务状态。
- 供应商已有成功结果时先恢复，禁止直接重新生成。
- 服务器数据库和媒体是生产事实源，禁止从本机覆盖。
- 不使用 `git reset --hard`、宽泛 kill 或宽泛递归删除。
- 所有数据修复先备份，所有代码发布使用 commit 和不可变 tag。
- 每次变更记录目标 commit、激活时间、健康检查和异常处理。

## 发布验收清单

1. 目标工作区是预期 tag 和 commit，`git status --short` 干净。
2. 配套 `build/` 已上传并通过 preflight。
3. 发布前没有需要继续运行的近期任务。
4. systemd 为 `active` 和 `enabled`，`NRestarts` 稳定。
5. `/health`、`/api/version` 和首页均正常。
6. 用户可以登录，模型和 Agent 列表正常。
7. 任务页面、历史视频、Tapque 图片、缩略图、预览和下载正常。
8. 新上传写入服务器 uploads，新产物写入 generated-artifacts。
9. journal 没有 SQLite、权限、SMB、缺包或重启循环错误。
10. 未经明确授权，不用付费生成作为发布冒烟测试。

## 文档维护

Markdown 是唯一内容来源。修改 `deploy/server-operations.md` 后重新生成 HTML：

```bash
cd /Users/lucas/Documents/open-webui-seedance-core
python scripts/build_server_operations_manual.py
python scripts/build_server_operations_manual.py --check
```

不要直接编辑 `deploy/server-operations.html`。HTML 内含源文件 SHA-256，`--check` 用于检测生成文件是否过期。
