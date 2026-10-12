# Tool Server

Tool Server 是一个独立部署的分布式工具 Hub，用来连接工具提供方（Provider）与调用方。它借鉴了 MCP 的工具发现和调用思路，但采用自己的 HTTP / WebSocket 协议与任务模型，**不是原生 MCP Server，也不直接兼容 MCP 客户端**。AAgent 是一种调用方，但服务不依赖 AAgent：任何能使用这些接口的应用都可以接入。

Provider 可部署在不同进程或机器上，通过 WebSocket 动态注册工具并接收调用；调用方通过 HTTP 发现工具、提交任务、等待结果或查询状态。多个 Provider 可以按工具种类分别部署和扩缩容，但同名工具目前只能由一个在线 Provider 注册，不提供同名工具实例的自动负载均衡。Hub 内的在线 Provider 注册表保存在进程内，**不能仅靠增加 Hub 实例实现共享注册或高可用**。Redis 保存带过期时间的任务状态和回调队列，MongoDB 保存任务日志；根路径提供管理页面。

## 快速启动

在本目录运行（需要 Docker Compose）：

```powershell
docker compose up -d --build
docker compose ps
curl.exe http://127.0.0.1:8083/api/health
```

健康检查返回 `{"status":"ok"}` 表示服务和 Redis、MongoDB 可用。打开 `http://127.0.0.1:8083/` 可查看管理页面。默认宿主机端口为 `8083`；端口冲突时，在本目录的 `.env` 中设置 `TOOL_SERVER_PORT=8084`，并用新端口访问。此 Compose 仅启动 Tool Server、Redis 和 MongoDB，**不会启动 Provider**；未连接 Provider 时工具列表为空，调用工具会返回 `503`。

```powershell
docker compose logs -f tool_server
docker compose down
```

`docker compose down` 保留数据卷；`docker compose down -v` **会删除 Redis 和 MongoDB 数据卷**。不要将含密码的 `.env` 提交到仓库。

## 接入 Provider

Provider 连接 `ws://<tool-server-host>:8083/ws/providers`，先发送包含 `provider_id`、递增的 `revision` 及工具 Schema 的 `register` 消息，注册成功后才会出现在 `/api/tools`。可使用配套的 [Tool Provider](../tool_provider/README.md) 实现工具及启动 Provider。若 Provider 运行在另一个容器内，地址应使用容器可达的主机名，而不是该容器自身的 `localhost`。

> 单独发布 `tool_server/` 子树时，上面的相对链接不包含 Tool Provider 仓库；Provider 需要单独部署。

## 调用工具

下列示例以 `http://127.0.0.1:8083` 为服务地址；`web.search`、`keyword` 只是演示，请换成实际注册的工具和输入参数。curl 命令采用 Bash 语法；在 PowerShell 中使用 `curl.exe` 并按 PowerShell 规则转义 JSON。Python 示例需要先安装 `requests`：`pip install requests`。每个小节都可以独立使用。

### 1. 检查服务健康状态

同时检查服务与 Redis、MongoDB；正常返回 `{"status":"ok"}`，依赖不可用时返回 HTTP `503`。

```bash
curl http://127.0.0.1:8083/api/health
```

```python
import requests

response = requests.get("http://127.0.0.1:8083/api/health", timeout=10)
response.raise_for_status()
print(response.json())
```

### 2. 列出在线工具

查看当前 Provider 注册的工具；未连接 Provider 时 `tools` 为空数组。

```bash
curl http://127.0.0.1:8083/api/tools
```

```python
import requests

response = requests.get("http://127.0.0.1:8083/api/tools", timeout=10)
response.raise_for_status()
print(response.json()["tools"])
```

### 3. 查看工具 Schema

在提交调用前，查看工具的 `inputSchema`，据此构造 `arguments`。工具不存在时返回 HTTP `404`。

```bash
curl http://127.0.0.1:8083/api/tools/web.search
```

```python
import requests

response = requests.get("http://127.0.0.1:8083/api/tools/web.search", timeout=10)
response.raise_for_status()
print(response.json()["inputSchema"])
```

### 4. 同步调用工具

`mode` 默认为 `wait`。任务在等待时间内进入 `completed` 或 `failed` 时返回 HTTP `200`；尚未完成返回 `202`，可继续按 `task_id` 查询。任务对象包含 `task_id`、`status`、`result`、`error` 等字段。

```bash
curl -X POST http://127.0.0.1:8083/api/tools/web.search/calls \
	-H 'Content-Type: application/json' \
	-d '{"arguments":{"keyword":"example"},"mode":"wait","timeout_ms":30000}'
```

```python
import requests

response = requests.post(
		"http://127.0.0.1:8083/api/tools/web.search/calls",
		json={"arguments": {"keyword": "example"}, "mode": "wait", "timeout_ms": 30000},
		timeout=35,
)
response.raise_for_status()
print(response.json())
```

`timeout_ms` 是发给 Provider 的任务期限，同步等待时间还受 `TOOL_MAX_WAIT_MS` 限制。参数不符合输入 Schema 时返回 HTTP `422`；工具未在线时返回 `503`。

### 5. 异步提交任务

`mode` 设为 `async` 时立即返回 HTTP `202`。记下响应中的 `task_id`，后续用于查询。

```bash
curl -X POST http://127.0.0.1:8083/api/tools/web.search/calls \
	-H 'Content-Type: application/json' \
	-d '{"arguments":{"keyword":"example"},"mode":"async"}'
```

```python
import requests

response = requests.post(
		"http://127.0.0.1:8083/api/tools/web.search/calls",
		json={"arguments": {"keyword": "example"}, "mode": "async"},
		timeout=10,
)
response.raise_for_status()
print(response.json()["task_id"])
```

### 6. 查询任务状态

把 `TASK_ID` / `task_id` 换成提交调用后得到的 ID。查询可能返回 `pending`、`working`、`completed` 或 `failed`；未结束时可继续查询。Redis 中的任务状态有 TTL，过期后返回 HTTP `404`。

```bash
TASK_ID=填入实际task_id
curl "http://127.0.0.1:8083/api/tasks/$TASK_ID"
```

```python
import requests

task_id = "填入实际task_id"
response = requests.get(f"http://127.0.0.1:8083/api/tasks/{task_id}", timeout=10)
response.raise_for_status()
print(response.json())
```

### 7. 查询持久化任务日志

查询 MongoDB 中该任务的最新一条日志；不存在时返回 HTTP `404`。

```bash
TASK_ID=填入实际task_id
curl "http://127.0.0.1:8083/api/task-logs/$TASK_ID"
```

```python
import requests

task_id = "填入实际task_id"
response = requests.get(f"http://127.0.0.1:8083/api/task-logs/{task_id}", timeout=10)
response.raise_for_status()
print(response.json())
```

### 8. 查看 Hub 概览

查看在线 Provider、工具、最近任务和状态计数；`limit` 限制返回的最近任务数（范围 1 至 200）。

```bash
curl 'http://127.0.0.1:8083/api/overview?limit=20'
```

```python
import requests

response = requests.get("http://127.0.0.1:8083/api/overview", params={"limit": 20}, timeout=10)
response.raise_for_status()
print(response.json())
```

### 9. 提交带 Redis 回调的任务

在调用体中传入 `callback`：`queue` 指定 Redis 队列，`event_type` 可填写任意非空字符串（最多 128 个字符）。`on` 指定通知的任务状态；省略时通知 `working`、`completed`、`failed`。回调仅写入 Redis 队列，队列消费者需自行部署。

```bash
curl -X POST http://127.0.0.1:8083/api/tools/web.search/calls \
	-H 'Content-Type: application/json' \
	-d '{"arguments":{"keyword":"example"},"mode":"async","callback":{"type":"redis","queue":"my:tool:results","event_type":"async_result","on":["completed","failed"]}}'
```

```python
import requests

response = requests.post(
		"http://127.0.0.1:8083/api/tools/web.search/calls",
		json={
				"arguments": {"keyword": "example"},
				"mode": "async",
				"callback": {
						"type": "redis",
						"queue": "my:tool:results",
						"event_type": "async_result",
						"on": ["completed", "failed"],
				},
		},
		timeout=10,
)
response.raise_for_status()
print(response.json())
```

## 主要接口

| 接口 | 用途 |
| --- | --- |
| `GET /api/health` | 检查 Redis、MongoDB 连通性 |
| `GET /api/tools`、`GET /api/tools/{tool_name}` | 查看在线工具和 Schema |
| `POST /api/tools/{tool_name}/calls` | 提交调用，同步等待或异步返回 |
| `GET /api/tasks/{task_id}` | 查询当前任务状态 |
| `GET /api/task-logs/{task_id}` | 查询最新一条持久化任务日志 |
| `GET /api/overview` | 查看 Provider、工具及最近任务概览 |
| `WS /ws/providers` | Provider 注册及任务消息通道 |

## 配置

独立 Compose 会自动读取本目录的 `.env`。以下列出常用变量和默认值；不要把 `MONGO_USER` 或 `MONGO_PASS` 只设置一项。

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `TOOL_SERVER_PORT` | `8083` | 映射到宿主机的端口 |
| `REDIS_HOST` / `REDIS_PORT` / `REDIS_DB` | `redis` / `6379` / `0` | Redis 连接参数 |
| `REDIS_PASSWORD` | 空 | 外部 Redis 的密码；独立 Compose 的 Redis 未启用认证，保持为空 |
| `MONGO_HOST` / `MONGO_PORT` | `mongodb` / `27017` | MongoDB 连接参数 |
| `MONGO_USER` / `MONGO_PASS` | 空 | MongoDB 管理员凭据；首次初始化时同时设置 |
| `MONGO_DATABASE` | `agent` | 数据库名 |
| `MONGO_TOOL_TASK_LOG_COLLECTION` | `tool_task_logs` | 任务日志集合 |
| `TOOL_TASK_TTL_SECONDS` | `86400` | Redis 中任务状态的过期时间（秒） |
| `TOOL_MAX_WAIT_MS` | `30000` | 同步调用等待上限（毫秒） |

使用独立 Compose 时，`REDIS_HOST` 和 `MONGO_HOST` 由容器网络提供，不必改成 `localhost`。独立 Compose 未传入 `REDIS_URL`；如需使用外部 Redis，应修改部署配置。修改已初始化 MongoDB 数据卷的凭据不会重建数据库用户，请按实际部署流程处理凭据变更。
