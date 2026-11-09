# 单用户常驻服务：本机容器验证与恢复

验证日期：2026-11-04。当前交付是常驻服务基础与本机隔离验证。部署设备稍后确定；没有购买主机、开放公网、迁移原聊天数据库或修改旧中期提交包。

## 当前能力

- 一个角色世界、一个写入进程；生产前端与 API 同源，手机尺寸网页可登录并接续聊天、材料和活动。
- 访问码登录、持久设备会话、设备撤销；HTTP、WebSocket、语音和存档接口均受保护。会话 Cookie 为 HttpOnly / SameSite=Strict；修改请求校验 CSRF，WebSocket 校验来源并在收发时复查授权。
- 世界文件与 PostgreSQL 分别保存在命名卷；进程重启恢复状态。进程异常退出由 Compose 重启，数据库断连返回故障，重连后可以继续读取。
- 现实时间驱动的持久备份任务，包含租约、有限重试、去重和备份回执核对。当前只注册自动备份任务，尚未接入外部提醒或消息投递。
- 按现实日期分别限制模型、记忆向量和语音请求数；记录可获得的模型 token 数。失败请求也占用预留额度，未返回回执的调用保留 pending。这里是请求预算，尚未实现货币账单和存储容量预算。

本地开发启动方式保留。常驻入口为 `python -m service.run`，不会读取运行目录外的旧 `.env`；配置必须通过环境变量传入。Docker 默认关闭可选 Neo4j，本次验证覆盖 PostgreSQL 路径。

## 首次启动真实服务

Windows 推荐直接双击项目根目录的 `start_service.bat`：检查并尝试启动 Docker Desktop，首次在容器中生成配置、构建并等待服务就绪，然后打开网页。宿主机不需要 Python 或 Node.js。先在 `.env` 填好三组模型配置；生成后实际使用 `deploy/service.env`，后续修改请改此文件。停止用 `stop_service.bat`，不删除卷；`start_service.bat -NoBrowser` 可关闭自动打开网页。

脚本不会覆盖既有配置；配置缺失但已有数据卷或访问码时会停止，要求恢复原配置，避免重新生成数据库密码。重复启动复用同一 Compose 项目并构建当前代码。启动失败显示具体步骤，不自动删除或重置数据。

2026-11-07 已用 Windows PowerShell 5 和合成测试栈实际验证：中文空格目录调用 BAT、容器内配置生成、服务健康就绪、停止后保留卷、缺少模型项报错，以及已有卷时阻止重新生成配置。原 `run_all.bat` 继续作为本地开发入口。

以下为手动命令，供非 Windows 或已有 Python 环境使用：

在项目根目录、可用的 Python 3.11+ 环境中执行。需要已安装项目 Python 依赖；镜像构建自行安装容器依赖。

```powershell
python utils/service_setup.py
docker compose --env-file deploy/service.env -f compose.service.yml up -d --build
docker compose --env-file deploy/service.env -f compose.service.yml ps
```

生成器只复制原 `.env` 中的模型配置、角色名称和时区；创建独立数据库密码、服务访问码及其 PBKDF2 验证值，不覆盖已有文件。原项目 `.env`、数据库和角色文件保持不变。

- 服务配置：`deploy/service.env`。启动前检查三个模型配置组及其模型名称。
- 登录访问码：`deploy/private/owner-code.txt`。在自己的编辑器中查看，不把内容发到公共日志或提交 Git。
- 默认地址：[本机服务](http://localhost:8080)。数据库只在 Compose 内部可达，不发布宿主机端口。
- `/health/live` 表示进程存活；`/health/ready` 同时检查场景准备、存储初始化与当前数据库连接。它不调用收费模型，因此 ready 不代表模型供应商可用。
- 设置中的“连接与服务”显示设备、当天额度及备份状态。撤销当前设备会返回登录入口。

这是**一个全新的独立世界**。本次没有生成含真实密钥的默认 `deploy/service.env`，也没有用验证用台词替代真实服务模型。

### 配置与数据的位置

| 位置 | 内容 |
|---|---|
| Compose `service_pg` 卷 | `public` 中的聊天、状态、向量记忆；`ember_service` 中的身份、任务和用量 |
| Compose `service_world` 卷的 `/runtime/config` | 角色状态、短期与工作记忆、共同材料、活动、人设 |
| `/runtime/data/archives` | 普通 `.ember` 世界存档及自动世界备份 |
| `/runtime/service/backups` | 最近三份身份与任务 SQL 备份 |
| 宿主 `deploy/service.env`、`deploy/private` | 服务配置和访问码，须另外安全保管 |

首次启动才复制默认人设和初见场景；更新镜像不会覆盖卷内已存在的人设。文件锁和启动检查禁止同一世界多 worker。不要额外启动第二个后端写入这些卷，也不要设置 `WEB_CONCURRENCY>1`。

默认 `START_TIME` 为空、时区 Asia/Shanghai、时间倍率 1。正常重启跟随现实时间；游戏读档恢复存档逻辑时间。服务任务和额度仍使用现实时间。

## 手机与后续部署

当前验证只绑定回环地址，没有开放局域网或公网。若之后在受信任局域网试用手机，修改服务配置：

```dotenv
EMBER_BIND_HOST=0.0.0.0
EMBER_PUBLIC_URL=http://主机局域网IP:8080
EMBER_COOKIE_SECURE=false
```

然后重新执行上面的 `up -d --build`，手机与主机处于同一网络，用该 IP 访问。HTTP 方案只用于局域网验证；正式远程访问先配置 TLS 反向代理，示例见 `deploy/Caddyfile.example`。主机上的代理转发到回环端口 8080，配置改为：

```dotenv
EMBER_BIND_HOST=127.0.0.1
EMBER_PUBLIC_URL=https://自己的完整域名
EMBER_COOKIE_SECURE=true
```

Origin 配置不能带路径；如确需额外入口，用逗号分隔 `EMBER_ALLOWED_ORIGINS`。默认使用同一个域名访问页面与 API。不要公开数据库端口，或直接把旧开发服务的 8000 / 5173 端口映射到公网。

最终机器、真实手机、TLS、主电脑关机后 72 小时可联系，以及跨设备的实际回访效果，留待部署设备确定后验收。Docker 自动重启依赖宿主与 Docker 本身正常运行；关闭当前电脑不会让本地容器继续运行。

## 日常运行和回退

```powershell
docker compose --env-file deploy/service.env -f compose.service.yml logs --tail 80 ember
docker compose --env-file deploy/service.env -f compose.service.yml restart ember
docker compose --env-file deploy/service.env -f compose.service.yml stop
docker compose --env-file deploy/service.env -f compose.service.yml up -d
```

停止会等待当前操作并保存状态，最多留出五分钟。更新前先备份，用 `up -d --build` 重建应用。保留原配置和卷；**不要执行 `down -v` 或 prune volumes**。Docker 已初始化的数据库不会因为修改环境文件就改变实际密码，已有库改密码需要同步数据库用户认证信息。

回到旧版本时停止这个 Compose 项目，按原来的本地启动方式连接保留的本机数据库即可。本轮没有迁移业务数据，两个世界互相独立；常驻版后续新产生的记录不会自动回流到旧本地世界。

### 接入原来的世界

先在原版本创建并校验一个 `.ember` 存档，再停止原版本的写入。保留原库和文件备份，在独立服务中验证恢复后才切换日常使用。

目前没有网页上传存档入口。可用 Compose 的 `cp` 将选定存档复制到 `ember:/runtime/data/archives/原存档名.ember`，然后登录新服务，在存档界面检查有效性并读档。不要直接把宿主正在变化的配置目录覆盖到运行中的 `/runtime/config`。本轮没有替用户执行这个业务世界切换。

## 备份与灾难恢复

自动任务默认每个现实日安排一次，启动后首次安排等待约十秒；忙于对话时等待或有限重试。`EMBER_BACKUP_INTERVAL` 单位为现实秒，最低 60，测试设为 60，正常默认 86400。

每次任务分别保存世界 `.ember` 和 `control_<任务UUID>.sql`。两者都完成才记录任务成功。身份 SQL 通过 PostgreSQL 16 的 `pg_dump` 生成，临时文件完成后原子替换，只清理本目录内过期的生成文件。世界备份按已有自动备份保留策略处理。

**游戏读档**只恢复世界：不会恢复或撤销设备授权、现实任务与额度。**设备损坏后的恢复**则需要世界文件、数据库及私密配置。当前自动备份仍在同一台设备，尚未具备跨设备灾备。

建议在停应用写入后做一次完整备份。以下命令使用默认数据库名与用户；改过配置时对应调整：

```powershell
New-Item -ItemType Directory -Force output/service-backup
docker compose --env-file deploy/service.env -f compose.service.yml stop ember
docker compose --env-file deploy/service.env -f compose.service.yml exec -T postgres pg_dump -U ember_service -d ember_service --format=custom --no-owner --no-privileges --file=/tmp/ember-full.dump
docker compose --env-file deploy/service.env -f compose.service.yml cp postgres:/tmp/ember-full.dump output/service-backup/ember-full.dump
docker compose --env-file deploy/service.env -f compose.service.yml cp ember:/runtime output/service-backup/runtime
docker compose --env-file deploy/service.env -f compose.service.yml start ember
```

逐步确认命令成功，另外保管 `deploy/service.env` 与访问码，再将备份加密复制到另一设备。`output/` 被 Git 忽略。备份包含私人聊天及授权信息，不能当演示包公开。

恢复时先在**新建的空数据库和独立新卷**中演练，应用保持停止：用 PostgreSQL 16 客户端 `pg_restore --exit-on-error --no-owner --no-privileges` 导入完整 dump；恢复对应 `/runtime` 内容并确保属主为容器 uid 10001。目标已有业务数据就停止，不使用 `--clean` 覆盖。只持有 `.ember` 时可恢复世界；只持有 control SQL 时不能恢复聊天和角色文件。

若选择单独恢复 control SQL，使用 `psql -v ON_ERROR_STOP=1`，只导入不存在 `ember_service` schema 的独立数据库；其余世界数据须另行恢复。恢复后复查任务租约与已完成回执。发生凭据泄露或灾难恢复时，先撤销旧会话并轮换访问码验证值，再开放入口，避免把旧设备权限重新启用。轮换 `EMBER_ACCESS_CODE_HASH` 后重启会使旧凭据指纹的会话失效；不能通过重跑配置生成器覆盖原数据库密码。

## 本轮验证与复现

自动测试均使用临时配置、合成模型和独立数据库。没有真实模型、外部消息、真实语音供应商请求；本轮不据此评价自然对话质量。

| 检查 | 结果 |
|---|---|
| 完整后端隔离回归 | 270 项通过；仍有 FastAPI `on_event` 弃用提醒 |
| 前端单元、lint、生产构建 | 27 项通过；构建保留 Live2D 大包提醒 |
| 实际镜像构建与 Compose | PostgreSQL / 合成模型 / Ember 均健康；应用非 root、只读根文件系统 |
| 390×844 浏览器 | 登录、错误提示、刷新会话、聊天与材料、设备撤销、无横向溢出与页面异常均通过 |
| 优雅重启与杀死 PID 1 | 容器恢复，会话、消息稳定 ID、材料保留 |
| 故障与恢复 | 断连返回 503、重连、真实世界存读档及身份保留均通过；控制 SQL 在新测试库逐行比对一致（任务 60、登录窗口 1、会话 11、用量 9 行） |

登录后角色运行库此前被误拦导致页面空白，已放行必要静态资源，并为角色组件加载失败增加文字入口降级。

容器验证还发现 PostgreSQL 默认搜索路径会优先选择与用户名同名的 schema，导致世界表进入身份命名空间，旧导入器又把不识别的 SQL 当作空表。现已固定所有世界连接与导出使用 `public`；不支持的导入格式必须失败，触发原有回滚。新增回归覆盖同名 schema 和不识别的数据语句。原业务库未受这次测试影响。

独立验证项目固定为 `ember-service-verify-20261104`，绑定 127.0.0.1:8086；配置与凭据位于忽略的 `output/service-verify`。它使用合成台词，TTS 额度为零，不能作为真实 AI 服务交付。

```powershell
python utils/service_verify_setup.py
docker build --pull=false -t ember-service:dev .
docker compose --env-file output/service-verify/service.env -f compose.service.yml -f deploy/compose.verify.yml -p ember-service-verify-20261104 up -d --no-build
node utils/service_verify_browser.mjs
python utils/service_verify_recovery.py
docker compose --env-file output/service-verify/service.env -f compose.service.yml -f deploy/compose.verify.yml -p ember-service-verify-20261104 stop
```

浏览器脚本需要 Playwright 与 Chromium，可沿用 `tests/README.md` 的环境变量。恢复脚本校验容器项目标签，只在固定合成栈断开数据库，创建随机 `ember_test_service_restore_*` 新库并在核对后删除该新库。重复登录仍受十次／五分钟的限制，遇到 429 等待窗口结束。

本机报告：`output/service-verify/browser/result.json`、`browser/phone-service.png`、`recovery-result.json`。本轮结束后测试容器停止，命名卷与镜像保留。后续依次做：确定常开设备 → 配置 TLS 与真实手机 → 少量真实模型/语音抽查 → 72 小时持续运行与异机恢复 → 再接首个外部消息渠道。
