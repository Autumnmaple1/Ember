# 真实模型模式运行与恢复

本模式运行 `source/server.py`，不是 `demo/launch.py`。需要外部模型及数据库。建议使用提交包中的独立解压目录和专用数据库，**不要覆盖正在使用的 Ember 工作目录或沿用业务库名称**。

## 准备

Python 3.11+、Node.js 22+、Docker Engine 或 Docker Desktop。以下命令在 `source` 中执行。模型需要兼容 OpenAI 的聊天和 Embedding 接口；1536 维向量必须与当前表结构一致，提供商需要支持项目传入的维度。费用与可用性取决于配置的服务，未包含在提交包。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

编辑 `.env`，填入大模型、小模型、Embedding 的模型名、URL 和密钥。示例服务地址仅作配置参考，不保证评审现场可用。设置 `ENABLE_NEO4J=False`；先验证向量记忆，不启用图谱。

## 独立数据库

先检查 55434 未被占用。使用新的容器和命名卷，绝不挂载业务卷或 5433 端口。将下例密码替换为自己设置的本地测试密码，再同步填写 `.env`。

```powershell
docker run --name ember-midterm-pg -e POSTGRES_USER=ember_demo -e POSTGRES_PASSWORD=CHANGE_ME -e POSTGRES_DB=ember_midterm -p 127.0.0.1:55434:5432 -v ember_midterm_pgdata:/var/lib/postgresql/data -d pgvector/pgvector:pg16
```

`.env` 中使用 `PG_HOST=127.0.0.1`、`PG_PORT=55434`、`PG_USER=ember_demo`、`PG_DB=ember_midterm`，密码与容器实际用户一致。端口或容器名冲突时先停下检查，不删除已有资源。不要只改 POSTGRES_PASSWORD 环境变量来修改一个已初始化的数据库密码；新卷初始化后密码由数据库内部认证决定。

## 启动

```powershell
.\.venv\Scripts\python.exe server.py
```

在另一终端进入 `source/frontend`，执行 `npm ci`、`npm run dev -- --host 127.0.0.1`，访问终端显示的地址。开发前端默认访问 localhost:8000。本包预构建页面供展示适配使用，真实模式请使用源码开发入口或按自己的地址重新构建。

后端是本地开发服务，当前缺账号与鉴权，不应直接暴露到公网。首次启动检查 `/config` 的 database、long_term_memory、scene 是否 ready；HTTP 503、配置警告或待确认状态是故障/降级，不能当作“没有记录”。

## 小量真实抽查

只输入合成资料。例如说“测试人物不喝咖啡，喜欢桂花茶，请记住”；完成数轮并等待编码状态；在独立数据库核对 episodic_memory 与向量，查看编码待办和错误；再询问偏好，核对回答是否有来源。不要把预设演示台词当成此步骤结果。

再检查双方分开但联系方式未知、跨日启动、时间加速和读档，核对当前事实与历史/推演是否被混用。打开 TTS，确认只朗读对白，关闭声音及手动重播有效。当前任务未对真实模型、真实语音重跑全套测试；以现场结果为准。

## 停止与回退

Ctrl+C 停止前后端。可 `docker stop ember-midterm-pg` 停止专用数据库；保留命名卷。提交包在独立目录运行，不需要更改原项目；回到原目录和原配置即可继续原版本。不要删除任何数据库卷。

展示快照只支持 `.demo.json`，不应导入真实模式。生产 `.ember` 存档加载会改变该运行目录对应的数据库和状态，必须确认连接的是上述独立库，先保存当前进度再操作。无需为评审导入私人存档。
