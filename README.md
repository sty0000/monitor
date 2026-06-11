# GPU Training Monitor

轻量 GPU 状态面板 + 异常提醒工具，面向训练机和 LLM 推理节点。默认目标是“单机可视化、低成本常驻、关键异常提醒”；systemd、Prometheus/Grafana、Hub 和发布流程都属于可选生产集成。

## 导航

- 先看：[项目定位](#项目定位)、[轻量模式 5 分钟上手](#轻量模式-5-分钟上手)、[功能特性](#功能特性)、[生产安全提醒](#生产安全提醒)
- 运行：[Dashboard 运行](#dashboard-运行)、[Dashboard API](#dashboard-api)、[命令行运行（无 UI）](#命令行运行无-ui)、[可选生产集成](#可选生产集成)
- DGX Spark：[DGX Spark 系统依赖](#dgx-spark-系统依赖)、[DGX Spark 配置](#dgx-spark-配置)、[DGX Spark 说明](#dgx-spark-说明)
- 参考：[指标](#指标)、[迁移说明](#迁移说明)、[故障排查](#故障排查)


## 项目定位

这个项目的主线不是完整 GPU 运维平台，而是训练/推理节点上的 lightweight GPU monitor + notifier。

- 默认场景：单台训练机、实验服务器、LLM 推理节点，快速查看 GPU 利用率、显存、功耗、温度和进程。
- 默认交付：本地 Dashboard、基础 history、关键告警、可选通知；保留 HTTP/API 接口，支持通过 VS Code Remote / SSH 端口转发访问目标 Linux 机器。
- 可选集成：systemd 常驻、Prometheus/Grafana、轻量 Hub、配置迁移、release checklist。
- 明确不做：多用户权限平台、远程执行/批量改配置、自动安装 NVIDIA driver/DCGM、在本地复制完整 TSDB。

## 轻量模式 5 分钟上手

适合先在训练机或 LLM 推理节点上跑起来，确认 GPU 状态和提醒逻辑。

```bash
# 在 monitor 项目目录内执行
conda create -n monitor python=3.10 -y
conda activate monitor
pip install -r requirements.txt
cp config.minimal.example.yaml config.yaml
nano config.yaml  # 至少修改 dashboard.auth.token
python -m monitor.dashboard --config config.yaml
```

然后打开：

```text
http://127.0.0.1:8090
```

最小配置只需要先关注三件事：

- `dashboard.auth.token`：改成自己的 Bearer Token。
- `threshold.*`：按训练/推理 workload 调整低利用率和高温阈值。
- `notify.control.enabled`：需要外部提醒时再改为 `true` 并启用一个通知通道。

如果只是本机临时查看，不需要 systemd、Hub、Prometheus/Grafana 或 release 流程。

## 生产安全提醒

- 不要把真实 `config.yaml`、`gpu-monitor.env*`、`hub.nodes.yaml`、`prometheus.scrape.yml` 提交到 Git。
- `config.yaml` 已在 `.gitignore` 中忽略；仓库只应保留 `config.example.yaml` 和 `deploy/*.example*`。
- 不要把真实 Bearer Token、Webhook URL、Telegram Bot Token、SMTP 密码写入 README、issue、commit message 或公开日志。
- 生产环境不要直接暴露无鉴权 Dashboard：不要使用 `host: "0.0.0.0"` 且 `dashboard.auth.enabled: false`。
- 推荐保持 `host: "127.0.0.1"`，通过 VS Code Remote 自动端口转发或 SSH tunnel 从本机浏览器/API 客户端访问远端 Linux 机器。
- 如果必须监听 `0.0.0.0`，请至少启用 Bearer Token，并放在内网、防火墙、VPN、SSH tunnel 或 Nginx/Caddy 鉴权后面。
- 如果怀疑 token/webhook 曾进入 Git 历史，应立即轮换对应密钥；历史清理不能替代密钥轮换。

## 功能特性

- `nvidia-smi` 一致性快照采样：`utilization.gpu` / `memory.used` / `power.draw` / `temperature.gpu`
- 采集局部降级：DCGM、进程列表、系统内存失败时只显示 `N/A` / 局部错误，不拖垮主循环
- 单写者 runtime：CLI 与 Dashboard 共用同一套状态机、通知与重载逻辑
- 状态机防误报：`WARMUP` -> `WAITING_ACTIVE` / `ARMING` -> `ACTIVE` -> `LOW_USAGE_ALERT` / `NO_PROCESS_ALERT`
- 支持高温告警：GPU 温度持续超过阈值时触发 `HIGH_TEMPERATURE_ALERT`
- 低利用率策略可配置：`any` / `all` / `majority` / `selected_primary`
- 多渠道通知：企业微信、飞书、钉钉、Telegram、Webhook、SMTP（支持 failover、模板和升级策略）
- 结构化日志、`/api/health`、`/metrics`、事件持久化
- Dashboard Bearer Token 鉴权；默认仅监听 `127.0.0.1`
- Dashboard 提供白名单配置编辑 UI/API，可 preview YAML diff、apply 前备份并在 reload 失败时回滚
- 多实例 Hub：可聚合多台 monitor 的 `/api/health` 与 `/api/status`
- Hub 是轻量只读聚合视图，不做远程执行或批量改配置；Dashboard 面向 Linux 机器上的本地或内网浏览器使用

## 安装

### Python 环境准备

```bash
git clone https://github.com/sty0000/monitor
cd monitor
conda create -n monitor python=3.10 -y
conda activate monitor
pip install -U pip
pip install -r requirements.txt
```


### DGX Spark 系统依赖

Monitor 适配 DGX Spark 不需要额外 Python 包；DGX 相关采集依赖系统工具：

- `nvidia-smi`：随 NVIDIA 驱动 / DGX OS 软件栈提供
- `dcgmi`：由 NVIDIA DCGM 提供，可选但推荐安装
- `ps`：由 `procps` 提供，用于 Top Processes 进程列表
- `/proc/meminfo`：Linux 内核接口，用于 System / Unified Memory 内存概览

快速检查：

```bash
which nvidia-smi
nvidia-smi -L
which dcgmi || echo "dcgmi not found; monitor will fall back to nvidia-smi"
which ps
cat /proc/meminfo | head
```

如果 `dcgmi health -c` 提示 watches 未启用，可运行：

```bash
sudo systemctl enable --now nvidia-dcgm || true
sudo dcgmi health -s a
dcgmi health -c
```

### 方式 1：自动初始化安装（推荐）

`python -m monitor.init_config` 是统一入口，会自动检测当前 Python 路径、项目目录、用户/组、GPU 平台、`nvidia-smi`、`dcgmi` 和推荐端口。

#### 模式 A：一键安装 systemd

```bash
conda activate monitor
cd path-to-monitor
python -m monitor.init_config --check-python-env
python -m monitor.init_config --install-systemd
```

该模式会自动生成 `config.yaml`、`gpu-monitor.env.new`、`gpu-monitor-dashboard.service.new`，并在确认后写入 `/etc/default/gpu-monitor`、`/etc/systemd/system/gpu-monitor-dashboard.service`，随后启动服务。安装完成后会自动检查 `/api/health` 和 `/metrics`。

如果还想在安装后立刻发送一次测试通知：

```bash
python -m monitor.init_config --install-systemd --post-install-test-notify
```

#### 模式 B：生成配置后检查安装

```bash
conda activate monitor
cd path-to-monitor
python -m monitor.init_config
```

检查配置：

```bash
nano config.yaml
nano gpu-monitor.env.new
nano gpu-monitor-dashboard.service.new
```

安装 systemd 并启动：

```bash
sudo cp gpu-monitor.env.new /etc/default/gpu-monitor
sudo cp gpu-monitor-dashboard.service.new /etc/systemd/system/gpu-monitor-dashboard.service
sudo systemctl daemon-reload
sudo systemctl enable --now gpu-monitor-dashboard
```

手动运行部署自诊断：

```bash
python -m monitor.doctor
python -m monitor.doctor --test-notify
python -m monitor.doctor --service-file /etc/systemd/system/gpu-monitor-dashboard.service
python -m monitor.doctor --skip-systemd-checks --skip-network-checks
```

遇到问题看这里：

- `conda: command not found`：先安装/初始化 Conda
- `No module named monitor`：确认已 `cd path-to-monitor`，并已执行 `pip install -r requirements.txt`
- `permission denied` 或 sudo 失败：确认当前用户有 sudo 权限
- `status=203/EXEC`：检查自动检测到的 Python 路径、项目路径、`User`、`Group`
- `dcgmi not found`：不阻断安装，monitor 会回退到 `nvidia-smi`；DGX Spark 可参考 [DGX Spark 说明](#dgx-spark-说明)
- `nvidia-dcgm.service inactive`：DCGM 服务未运行，可执行 `sudo systemctl enable --now nvidia-dcgm` 后再运行 doctor
- history 目录不可写：检查 `history.path` 的目录权限，确保 systemd 中的 `User=` 可以写入

非交互安装：完整参数示例：

```bash
python -m monitor.init_config \
  --non-interactive \
  --install-systemd \
  --post-install-test-notify \
  --force \
  --instance-name gx10-b07b \
  --host 127.0.0.1 \
  --port 8093 \
  --notify-enabled true \
  --low-usage-notify-enabled false \
  --token monitor-your-secret-token \
  --example config.example.yaml \
  --env-example deploy/gpu-monitor.env.example \
  --output-config config.yaml \
  --output-env gpu-monitor.env.new \
  --output-service gpu-monitor-dashboard.service.new \
  --system-env-path /etc/default/gpu-monitor \
  --system-service-path /etc/systemd/system/gpu-monitor-dashboard.service
```

参数说明：

| 参数 | 说明 |
| --- | --- |
| `--non-interactive` | 不进入交互问答，直接使用检测值和传入参数 |
| `--install-systemd` | 复制 env/service 到 systemd 并启动服务 |
| `--uninstall-systemd` | 停止并禁用 systemd 服务，备份系统 env/service |
| `--remove-system-files` | 配合 `--uninstall-systemd`，备份后删除系统 env/service 文件 |
| `--post-install-test-notify` | 安装后额外调用 `/api/test-notify`，验证通知通道 |
| `--print-conda-setup` | 只打印 Conda 初始化建议命令，不写文件 |
| `--check-python-env` | 检查当前 Python 是否能导入 Flask、PyYAML、prometheus-client |
| `--dry-run` | 只预览，不写文件、不安装 |
| `--force` | 允许覆盖当前目录输出文件；系统文件仍会先备份 |
| `--instance-name` | 设置 `monitor.instance_name`，用于页面和通知里的设备名 |
| `--host` | 设置 Dashboard 监听地址，默认 `127.0.0.1` |
| `--port` | 设置 Dashboard 端口；DGX Spark 推荐 `8093` |
| `--notify-enabled` | 总通知开关：`true` / `false` |
| `--low-usage-notify-enabled` | 低利用率通知开关：`true` / `false` |
| `--token` | Bearer Token；不传则自动生成 |
| `--example` | 新版 `config.example.yaml` 路径 |
| `--env-example` | 新版环境文件模板路径 |
| `--output-config` | 生成的 `config.yaml` 路径 |
| `--output-env` | 生成的 env 文件路径 |
| `--output-service` | 生成的 service 文件路径 |
| `--system-env-path` | 安装到 systemd 使用的环境文件路径 |
| `--system-service-path` | 安装到 systemd 使用的 service 文件路径 |

### 方式 2：手动配置安装

1. 创建并编辑 `config.yaml`：

```bash
cp config.example.yaml config.yaml
nano config.yaml
```

2. 安装并编辑 service 文件：

```bash
sudo cp deploy/gpu-monitor-dashboard.service /etc/systemd/system/
sudo nano /etc/systemd/system/gpu-monitor-dashboard.service
```

常见需要修改的项：

- `WorkingDirectory`
- `ExecStart`
- `User`
- `Group`

例如，若你的项目位于 `/path-to-proj/monitor`，Conda 环境名为 `monitor`，可改为：

```ini
[Service]
WorkingDirectory=/path-to-proj/monitor
ExecStart=/home/username/miniconda3/envs/monitor/bin/python -m monitor.dashboard --config /path-to-proj/monitor/config.yaml
ProtectHome=false
User=username
Group=username
```

3. 创建并编辑环境文件：

```bash
sudo cp deploy/gpu-monitor.env.example /etc/default/gpu-monitor
sudo nano /etc/default/gpu-monitor
```

最小内容示例：

```bash
GPU_MONITOR_NOTIFY_ENABLED=true
GPU_MONITOR_LOW_USAGE_NOTIFY_ENABLED=true
GPU_MONITOR_DASHBOARD_AUTH_TOKEN=CHANGE_ME_BEARER_TOKEN
```

4. 启动并开机自启：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now gpu-monitor-dashboard
```

5. 查看状态、日志与重启：

```bash
sudo systemctl status gpu-monitor-dashboard --no-pager
journalctl -u gpu-monitor-dashboard -n 100 --no-pager
sudo systemctl restart gpu-monitor-dashboard
```

说明：

- 默认模板假设项目部署在 `/opt/monitor`
- 若 `ExecStart` 或 `WorkingDirectory` 指向不存在的路径，服务会报 `status=203/EXEC`
- 若项目或 Python 位于 `/home/...` 下，而 service 中保留 `ProtectHome=true`，也可能导致 `203/EXEC`
- DGX Spark/systemd 部署时，建议添加 `Environment=PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin`，确保服务能找到 `nvidia-smi`、`dcgmi` 和 `ps`


### 卸载 / 回滚 systemd

只停止并禁用服务，备份 `/etc/default/gpu-monitor` 和 systemd service，不删除项目目录、`config.yaml`、日志或 Conda 环境：

```bash
python -m monitor.init_config --uninstall-systemd
```

如果只是预览将执行的动作：

```bash
python -m monitor.init_config --uninstall-systemd --dry-run
```

如果确认要在备份后删除系统 env/service 文件：

```bash
python -m monitor.init_config --uninstall-systemd --remove-system-files
```

## 训练 / LLM 推理场景模板

`scenario_profile` 记录当前场景来源。通过 profile preview/apply 切换模板时，模板会写入真实 `threshold.*` 和 `scenario_messages.*`；运行时只读取最终 `config.yaml`，不会维护第二套隐藏配置。模板切换不会覆盖 token/webhook，不会自动启用通知通道，也不会改变 Dashboard 监听地址。

### 训练机模板

适合单机训练、多卡训练或长时间实验。重点关注训练进程是否消失、GPU 是否长时间低利用率、高温和 GPU error。

```yaml
scenario_profile: "training"
threshold:
  usage_percent: 20
  idle_minutes: 10
  no_process_minutes: 5
  low_usage_mode: "any"
  high_temperature_c: 85
  high_temperature_minutes: 3
```

建议含义：

- `usage_percent: 20`：训练中 GPU 长时间低于该值通常值得检查。
- `idle_minutes: 10`：给数据加载、checkpoint、验证阶段留出缓冲，避免短暂波动误报。
- `no_process_minutes: 5`：训练进程消失持续 5 分钟后再提醒。
- `low_usage_mode: "any"`：任意 GPU 长时间低利用率就提醒，适合多卡训练卡住排查。

### LLM 推理机模板

适合 vLLM、TGI、llama.cpp server 或其他长驻推理服务。显存常驻但利用率随请求量波动，低利用率不一定是故障。

```yaml
scenario_profile: "inference"
threshold:
  usage_percent: 10
  idle_minutes: 30
  no_process_minutes: 10
  low_usage_mode: "all"
  high_temperature_c: 85
  high_temperature_minutes: 3
```

建议含义：

- `usage_percent: 10`：推理低流量时利用率可能很低，阈值应比训练更保守。
- `idle_minutes: 30`：长时间低利用率才提醒，避免把无请求时的服务空闲当成训练失败。
- `no_process_minutes: 10`：服务进程消失通常更重要，但仍保留启动/重启缓冲。
- `low_usage_mode: "all"`：全部 GPU 都长时间低利用率才提醒，降低误报。

### 自定义模板

```yaml
scenario_profile: "custom"
scenario_messages:
  low_usage_hint: ""
  no_process_hint: ""
```

`custom` 不隐式改写阈值，只使用你在 `threshold.*` 中显式配置的值。若填写 `scenario_messages.*`，通知正文优先使用这些真实文案；缺失时回退到通用文案。

### 用户自建 profile 模板

如果需要自己的场景，可以直接在 `config.yaml` 中添加 `profile_templates`，再通过 profile preview/apply 写入真实配置：

```yaml
scenario_profile: "llm_serving"

profile_templates:
  llm_serving:
    description: "LLM serving profile"
    threshold:
      usage_percent: 10
      idle_minutes: 30
      no_process_minutes: 10
      low_usage_mode: "all"
    scenario_messages:
      low_usage_hint: "低利用率可能是请求量较低或服务空闲，请先检查流量和请求队列。"
      no_process_hint: "未发现推理进程，请检查推理服务是否仍在运行。"
```

自建模板只是生成真实 config 的来源。apply 后请以最终 `threshold.*` 和 `scenario_messages.*` 为准；运行时不会动态读取第二套 `profile_templates.*` 阈值。

## 配置

### 常规配置

重要说明：

- 仓库只保留 `config.example.yaml`，真实配置 `config.yaml` 已被 `.gitignore` 忽略
- 可通过环境变量覆盖部分关键项：
  - `GPU_MONITOR_NOTIFY_ENABLED`
  - `GPU_MONITOR_LOW_USAGE_NOTIFY_ENABLED`
  - `GPU_MONITOR_DASHBOARD_AUTH_TOKEN`
  - `GPU_MONITOR_DASHBOARD_HOST`
  - `GPU_MONITOR_DASHBOARD_PORT`

关键配置块：

- `monitor.interval_seconds`: 采样周期
- `monitor.instance_name`: 当前监控实例名称，会显示在通知标题和正文中
- `monitor.command_timeout_seconds`: `nvidia-smi` 超时秒数
- `platform.profile`: `auto` / `generic_nvidia` / `dgx_spark`
- `platform.telemetry_order`: 采集源优先级，默认 `["dcgm", "nvidia_smi"]`；如安装可选 `nvidia-ml-py`，可设为 `["nvml", "dcgm", "nvidia_smi"]`
- `threshold.low_usage_mode`: `any` / `all` / `majority` / `selected_primary`
- `notify.control.low_usage_enabled`: 低利用率告警通知开关；关闭后仍保留其他告警通知
- `threshold.armed_stable_minutes`: 首次识别 compute 进程后的稳定窗口
- `threshold.high_temperature_c`: 高温告警阈值，默认 `85`
- `threshold.high_temperature_minutes`: 高温持续时长，超过后触发告警
- `alert.runtime_error.*`: 运行时错误通知策略，默认连续失败 `3` 次后提醒
- `alert.gpu_error.*`: GPU 设备错误通知策略，可按 GPU ID 静音
- `alert.recovery.*`: 恢复通知策略
- `dashboard.auth.*`: Bearer Token 鉴权
- `logging.event_log_path`: 事件 JSONL 持久化路径

### DGX Spark 配置

DGX Spark 建议使用自动平台探测，优先尝试 DCGM，失败后回退到 `nvidia-smi`：

```yaml
platform:
  profile: "auto"
  telemetry_order: ["dcgm", "nvidia_smi"]

dashboard:
  host: "127.0.0.1"
  port: 8093
```

## Dashboard 运行

推荐仅在本机监听：

```bash
python -m monitor.dashboard --config config.yaml
```

默认地址：

- `http://127.0.0.1:8090/`

推荐部署方式：

- 优先只监听 `127.0.0.1`，通过 SSH tunnel、VPN 或内网反向代理访问
- 生产环境不要直接暴露 `0.0.0.0` 无鉴权 Dashboard
- 如果必须绑定 `0.0.0.0`，必须启用 `dashboard.auth.enabled: true`，并建议设置 `dashboard.auth.require_auth_for_read: true`
- 通过 Nginx/Caddy 对外暴露时，建议再加一层 HTTPS 和访问控制

## 多实例 Hub

多台机器各自运行 monitor 后，可以用轻量 Hub 做汇总页面：

```bash
cp deploy/hub.nodes.example.yaml hub.nodes.yaml
nano hub.nodes.yaml
python -m monitor.hub --nodes hub.nodes.yaml --host 127.0.0.1 --port 8099
```

Hub 会轮询每个节点的 `/api/health` 和 `/api/status`，并提供：

- `GET /`：汇总页面
- `GET /api/summary`：JSON 汇总

Prometheus/Grafana：主服务的 `/metrics` 已包含 runtime、通知、错误计数，以及 per-GPU 的 utilization、memory、power、temperature、device_error 指标。


## Prometheus / Grafana

Monitor 的 `/metrics` 已暴露 runtime、通知、采集错误、history、告警确认/静音、notifier health 和 per-GPU 指标。最小 Prometheus scrape 示例：

```bash
cp deploy/prometheus.scrape.example.yml prometheus.scrape.yml
nano prometheus.scrape.yml
```

示例中默认抓取 `127.0.0.1:8093/metrics`。如果你的 `/metrics` 需要 Bearer Token，请参考文件中的 `authorization` 注释，或保持 Dashboard 仅监听 `127.0.0.1` 并通过 SSH tunnel 访问。

生产监控示例文件：

- `deploy/prometheus.scrape.example.yml`: 单机、Hub 和多实例 scrape targets。
- `deploy/prometheus.alerts.example.yml`: runtime down、长时间无采样、GPU error、高温和 notifier unhealthy 告警规则。
- `deploy/prometheus.recording.example.yml`: utilization、temperature、collector error 和 notifier health recording rules。
- `deploy/grafana-provisioning-datasource.example.yml`: Grafana Prometheus datasource provisioning 示例。
- `deploy/grafana-dashboard.example.json`: 可直接导入的 Dashboard 示例。

推荐拓扑：单机优先只监听 `127.0.0.1`；远程调试用 SSH tunnel；生产多机由内网 Prometheus 抓取各节点 `/metrics`；跨机总览可额外部署 Hub，但 Hub 不负责远程执行命令。

生产部署检查清单：

- Dashboard 优先绑定 `127.0.0.1`，通过 SSH tunnel 或内网反向代理访问
- Prometheus 使用固定 Bearer Token，避免把 token 写入公开仓库
- Grafana 导入 `deploy/grafana-dashboard.example.json` 后，确认数据源名称和 job label
- 多机器部署时每台 monitor 使用不同 `monitor.instance_name`
- 定期运行 `python -m monitor.doctor --skip-network-checks` 检查 systemd、DCGM 和 history

Grafana 可导入示例面板：

```bash
python -m json.tool deploy/grafana-dashboard.example.json >/dev/null
```

核心指标包括：

- `gpu_monitor_up`
- `gpu_monitor_state_code`
- `gpu_monitor_consecutive_failures`
- `gpu_monitor_gpu_utilization_percent`
- `gpu_monitor_gpu_memory_used_mb`
- `gpu_monitor_gpu_power_draw_watts`
- `gpu_monitor_gpu_temperature_celsius`
- `gpu_monitor_gpu_device_error`
- `gpu_monitor_notify_total`
- `gpu_monitor_alert_total`
- `gpu_monitor_collection_errors_total`

## Dashboard API

- `GET /api/status`: 当前状态、配置摘要、GPU 快照、事件摘要
- `GET /api/health`: 进程存活、最近采样、连续失败数、最近错误
- `GET /metrics`: Prometheus 指标
- `POST /api/notify` body: `{"enabled": true|false}`
- `POST /api/low-usage-notify` body: `{"enabled": true|false}`
- `POST /api/test-notify` body: `{"channel": "wecom"}`（可选）
- `POST /api/gpu-error-mute` body: `{"gpu_id": 0, "muted": true|false}`
- `POST /api/alert-silence` body: `{"alert_key":"GPU_ERROR_ALERT","mode":"1h|today|permanent|clear"}`
- `POST /api/reload-config`: 重载 `config.yaml`

### 鉴权

当 `dashboard.auth.enabled: true` 时，所有写接口必须携带：

```http
Authorization: Bearer <token>
```

如果 `dashboard.auth.require_auth_for_read: true`，则读接口也需要同样的 Header。

## 命令行运行（无 UI）

```bash
python -m monitor.agent --config config.yaml
python -m monitor.agent --config config.yaml --once
```

## 可选生产集成

轻量模式跑通后，再按需要开启这些能力：

- **systemd**：机器重启后自动恢复，适合长期训练/推理节点。
- **Prometheus/Grafana**：接入统一监控，适合多节点和长期趋势。
- **Hub**：轻量只读聚合多个 monitor 实例，只看状态，不远程执行命令。
- **config migration / release checklist**：用于跨版本升级和发布验证。

这些能力默认不是上手必需项；如果只是看单机 GPU 状态和提醒，保持 `config.minimal.example.yaml` 即可。

## systemd

推荐先确认你使用的是哪种安装方式：

- 如果项目目录和 Python 环境都放在 `/opt/monitor` 下，可直接使用仓库自带的 service 模板
- 如果项目放在 `~/.../monitor`，且 Python 来自 Conda 环境，需要先修改 service 文件中的路径

1. 安装 service 文件：

```bash
sudo cp deploy/gpu-monitor-dashboard.service /etc/systemd/system/
```

2. 如使用 Conda 或自定义目录，先修改 service 文件：

```bash
sudo nano /etc/systemd/system/gpu-monitor-dashboard.service
```

常见需要修改的项：

- `WorkingDirectory`
- `ExecStart`
- `User`
- `Group`

例如，若你的项目位于 `~/path-to-proj/monitor`，Conda 环境名为 `monitor`，可改为：

```ini
[Service]
WorkingDirectory=/path-to-proj/monitor
ExecStart=/home/username/miniconda3/envs/monitor/bin/python -m monitor.dashboard --config /path-to-proj/monitor/config.yaml
ProtectHome=false
User=username
Group=username
```

说明：

- 默认模板假设项目部署在 `/opt/monitor`
- 若 `ExecStart` 或 `WorkingDirectory` 指向不存在的路径，服务会报 `status=203/EXEC`
- 若项目或 Python 位于 `/home/...` 下，而 service 中保留 `ProtectHome=true`，也可能导致 `203/EXEC`
- DGX Spark/systemd 部署时，建议添加 `Environment=PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin`，确保服务能找到 `nvidia-smi`、`dcgmi` 和 `ps`

3. 创建环境文件:

```bash
sudo cp deploy/gpu-monitor.env.example /etc/default/gpu-monitor
sudo nano /etc/default/gpu-monitor
```

最小内容示例：

```bash
GPU_MONITOR_NOTIFY_ENABLED=true
GPU_MONITOR_LOW_USAGE_NOTIFY_ENABLED=true
GPU_MONITOR_DASHBOARD_AUTH_TOKEN=CHANGE_ME_BEARER_TOKEN
```

4. 启动并开机自启：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now gpu-monitor-dashboard
```

5. 查看状态与日志：

```bash
journalctl -u gpu-monitor-dashboard -n 100 --no-pager
sudo systemctl status gpu-monitor-dashboard
journalctl -u gpu-monitor-dashboard -f
```

6. 重启服务：

```bash
sudo systemctl restart gpu-monitor-dashboard
```

## DGX Spark 说明

DGX Spark 适配行为：

- `/api/status` 会返回 `platform_summary`，包含 profile、架构、OS、驱动版本、DCGM 可用性和当前采集源。
- DGX Spark profile 下，GPU 内存列会显示为 `GPU/Unified Mem MB`。
- Dashboard 会根据 `/proc/meminfo` 显示 `System / Unified Memory` 卡片。
- `dcgmi` 采集是 best-effort；失败时会自动回退到 `nvidia-smi`，不会中断整轮采样。
- 为避免与 NVIDIA DGX Dashboard 冲突，建议将本 Dashboard 绑定到自定义本地端口，例如 `8093`。

VS Code Remote 通常会自动提示转发 Dashboard 端口；也可以手动使用 SSH tunnel：

```bash
ssh -L 8093:127.0.0.1:8093 ubuntu@DGX_SPARK_HOST
```

## 指标

默认暴露 Prometheus 指标，例如：

- `gpu_monitor_up`
- `gpu_monitor_notify_enabled`
- `gpu_monitor_last_sample_timestamp_seconds`
- `gpu_monitor_consecutive_failures`
- `gpu_monitor_alert_total`
- `gpu_monitor_notify_total`
- `gpu_monitor_collection_errors_total`

## 迁移说明

旧版配置迁移要点：

- `notify.control.enabled` 仍保留
- `notify.control.low_usage_enabled` 可单独关闭 `LOW_USAGE_ALERT` 通知
- 建议新增 `monitor.instance_name` 区分多台机器共用同一 webhook 的消息来源
- 新增 `dashboard` / `logging` / `metrics` 配置块
- 旧版默认公网监听已改为默认本地监听
- Dashboard 现默认启用 Bearer Token 鉴权
- `config.yaml` 不再建议入库

### 自动合并配置与环境文件

升级旧版本时，建议不要直接覆盖 `config.yaml` 和 `/etc/default/gpu-monitor`，而是先生成新文件、检查后再替换。

1. 停止服务并更新代码：

```bash
cd ~/Desktop/monitor
sudo systemctl stop gpu-monitor-dashboard
git pull
conda activate monitor
pip install -r requirements.txt
```

2. 根据旧 `config.yaml` 和新版 `config.example.yaml` 先预览 schema diff：

```bash
python -m monitor.init_config \
  --upgrade-config \
  --dry-run \
  --output-config config.yaml \
  --example config.example.yaml
```

预览会脱敏 token、webhook、password、secret 等字段。

3. 生成升级后的配置文件，检查无误后再替换：

```bash
python -m monitor.config_migrate \
  --old config.yaml \
  --example config.example.yaml \
  --output config.new.yaml
```

也可以确认后直接备份并替换当前 `config.yaml`：

```bash
python -m monitor.init_config \
  --upgrade-config \
  --upgrade-apply \
  --output-config config.yaml \
  --example config.example.yaml
```

4. 如需同时合并旧 systemd 环境文件：

```bash
python -m monitor.config_migrate \
  --old config.yaml \
  --example config.example.yaml \
  --output config.new.yaml \
  --old-env /etc/default/gpu-monitor \
  --env-example deploy/gpu-monitor.env.example \
  --env-output gpu-monitor.env.new
```

5. 检查生成结果：

```bash
nano config.new.yaml
nano gpu-monitor.env.new
```

6. 确认无误后备份并替换环境文件：

```bash
sudo cp /etc/default/gpu-monitor /etc/default/gpu-monitor.bak.$(date +%Y%m%d-%H%M%S)
sudo cp gpu-monitor.env.new /etc/default/gpu-monitor
```

5. 重启并验证：

```bash
sudo systemctl daemon-reload
sudo systemctl start gpu-monitor-dashboard
sudo systemctl status gpu-monitor-dashboard --no-pager
journalctl -u gpu-monitor-dashboard -n 100 --no-pager
```

说明：

- 旧配置中仍存在于新版 example 的键会保留旧值
- 新版新增键会使用 `config.example.yaml` 的默认值补齐
- 旧版已删除或无法识别的键不会写入新文件
- 环境文件合并同样只保留新版 `deploy/gpu-monitor.env.example` 中列出的变量
- 如需覆盖已有输出文件，可加 `--force`

## 故障排查

- `nvidia-smi not found`: 检查 NVIDIA 驱动与 PATH
- `dashboard.auth.token is required`: 在 `config.yaml` 或环境变量中设置 Token
- 无法收到告警：检查 `notify.control.enabled`、`notify.control.low_usage_enabled`、`notify.strategy.order`、各渠道配置和服务器出网
- 日志过多：可调高 `logging.level` 或拉长采样周期
- 查看 systemd stdout 日志：`journalctl -u gpu-monitor-dashboard -n 100 --no-pager`
- 查看事件 JSONL：`tail -n 100 logs/events.jsonl`，每行是一个事件 JSON，可用于排查状态跳转、通知发送/失败、ack/silence 和 reload
- `Could not open requirements file`: 先进入仓库根目录再执行 `pip install -r requirements.txt`
- `status=203/EXEC`: 重点检查 `/etc/systemd/system/gpu-monitor-dashboard.service` 中的 `WorkingDirectory`、`ExecStart` 是否真实存在且可执行
- `status=203/EXEC` 且项目位于 `/home/...`: 检查是否仍启用了 `ProtectHome=true`；若使用 home 目录部署，请改为 `ProtectHome=false`
- 想加高温提醒：在 `config.yaml` 的 `threshold` 下设置 `high_temperature_c` 和 `high_temperature_minutes`

## 发布检查清单

发布前请参考 `docs/release-checklist.md`，其中包含测试、doctor、部署验证和回滚命令。兼容性边界见 `docs/compatibility-matrix.md`。`install.sh` 仅打印安全安装建议，不会自动安装系统依赖。
