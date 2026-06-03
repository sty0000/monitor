# GPU Training Monitor

独立常驻 GPU 监控进程 + 前端控制台：查看设备运行状态并进行多渠道提醒。

## Features

- `nvidia-smi` 一致性快照采样：`utilization.gpu` / `memory.used` / `power.draw` / `temperature.gpu`
- 单写者 runtime：CLI 与 Dashboard 共用同一套状态机、通知与重载逻辑
- 状态机防误报：`WARMUP` -> `WAITING_ACTIVE` / `ARMING` -> `ACTIVE` -> `LOW_USAGE_ALERT` / `NO_PROCESS_ALERT`
- 支持高温告警：GPU 温度持续超过阈值时触发 `HIGH_TEMPERATURE_ALERT`
- 低利用率策略可配置：`any` / `all` / `majority` / `selected_primary`
- 多渠道通知：企业微信、飞书、钉钉、Telegram、Webhook、SMTP（支持 failover）
- 结构化日志、`/api/health`、`/metrics`、事件持久化
- Dashboard Bearer Token 鉴权；默认仅监听 `127.0.0.1`

## Install

### Option A: Conda（推荐，适合已有 Miniconda/Anaconda 的机器）

```bash
git clone https://github.com/sty0000/monitor
conda create -n monitor python=3.10 -y
conda activate monitor
cd monitor
pip install -U pip
pip install -r requirements.txt
```

注意：

- `pip install -r requirements.txt` 需要在仓库根目录执行
- 如果你还没 `cd monitor` 就安装依赖，会报 `Could not open requirements file`

### Option B: venv（适合部署到固定目录，如 `/opt/monitor`）

```bash
cd /opt/monitor
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
```

## Configure

```bash
cp config.example.yaml config.yaml
vim config.yaml
```

重要说明：

- 仓库只保留 `config.example.yaml`，真实配置 `config.yaml` 已被 `.gitignore` 忽略
- 可通过环境变量覆盖部分关键项：
  - `GPU_MONITOR_NOTIFY_ENABLED`
  - `GPU_MONITOR_DASHBOARD_AUTH_TOKEN`
  - `GPU_MONITOR_DASHBOARD_HOST`
  - `GPU_MONITOR_DASHBOARD_PORT`

关键配置块：

- `monitor.interval_seconds`: 采样周期
- `monitor.instance_name`: 当前监控实例名称，会显示在通知标题和正文中
- `monitor.command_timeout_seconds`: `nvidia-smi` 超时秒数
- `threshold.low_usage_mode`: `any` / `all` / `majority` / `selected_primary`
- `threshold.armed_stable_minutes`: 首次识别 compute 进程后的稳定窗口
- `threshold.high_temperature_c`: 高温告警阈值，默认 `85`
- `threshold.high_temperature_minutes`: 高温持续时长，超过后触发告警
- `alert.runtime_error.*`: 运行时错误通知策略，默认连续失败 `3` 次后提醒
- `alert.gpu_error.*`: GPU 设备错误通知策略，可按 GPU ID 静音
- `alert.recovery.*`: 恢复通知策略
- `dashboard.auth.*`: Bearer Token 鉴权
- `logging.event_log_path`: 事件 JSONL 持久化路径

## Dashboard Run

推荐仅在本机监听：

```bash
python -m monitor.dashboard --config config.yaml
```

默认地址：

- `http://127.0.0.1:8090/`

推荐部署方式：

- 通过 Nginx/Caddy 反向代理对外暴露
- 不建议无鉴权绑定 `0.0.0.0`

## Dashboard API

- `GET /api/status`: 当前状态、配置摘要、GPU 快照、事件摘要
- `GET /api/health`: 进程存活、最近采样、连续失败数、最近错误
- `GET /metrics`: Prometheus 指标
- `POST /api/notify` body: `{"enabled": true|false}`
- `POST /api/test-notify` body: `{"channel": "wecom"}`（可选）
- `POST /api/reload-config`: 重载 `config.yaml`

### Authentication

当 `dashboard.auth.enabled: true` 时，所有写接口必须携带：

```http
Authorization: Bearer <token>
```

如果 `dashboard.auth.require_auth_for_read: true`，则读接口也需要同样的 Header。

## Legacy Run (No UI)

```bash
python -m monitor.agent --config config.yaml
python -m monitor.agent --config config.yaml --once
```

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
WorkingDirectory=/home/username/path-to-proj/monitor
ExecStart=/home/username/path-to-proj/monitor/venv/bin/python -m monitor.dashboard --config /home/username/path-to-proj/monitor/config.yaml
ProtectHome=false
User=username
Group=username
```

说明：

- 默认模板假设项目部署在 `/opt/monitor`
- 若 `ExecStart` 或 `WorkingDirectory` 指向不存在的路径，服务会报 `status=203/EXEC`
- 若项目或 Python 位于 `/home/...` 下，而 service 中保留 `ProtectHome=true`，也可能导致 `203/EXEC`

3. 创建环境文件:

```bash
sudo tee /etc/default/gpu-monitor >/dev/null <<'EOF'
GPU_MONITOR_NOTIFY_ENABLED=true
GPU_MONITOR_DASHBOARD_AUTH_TOKEN=CHANGE_ME_BEARER_TOKEN
EOF
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

## DGX Spark

Recommended DGX Spark settings use automatic platform detection, try DCGM first, and fall back to `nvidia-smi`:

```yaml
platform:
  profile: "auto"
  telemetry_order: ["dcgm", "nvidia_smi"]

dashboard:
  host: "127.0.0.1"
  port: 8093
```

### DGX Spark dependencies

Monitor does not require extra Python packages for DGX Spark. The DGX-specific telemetry is collected from system tools:

- `nvidia-smi`: installed with the NVIDIA driver / DGX OS stack
- `dcgmi`: provided by NVIDIA DCGM; optional but recommended
- `ps`: provided by `procps`; used for Top Processes
- `/proc/meminfo`: Linux kernel interface used for System / Unified Memory

Check the tools first:

```bash
which nvidia-smi
nvidia-smi -L
which dcgmi || echo "dcgmi not found; monitor will fall back to nvidia-smi"
which ps
cat /proc/meminfo | head
```

If `dcgmi` is missing on DGX OS / Ubuntu, install or enable DCGM with your NVIDIA package source. Common Ubuntu package names are:

```bash
sudo apt update
sudo apt install -y datacenter-gpu-manager
```

Some DGX OS images already include DCGM but the service may need to be enabled:

```bash
sudo systemctl enable --now nvidia-dcgm || true
dcgmi discovery -l
dcgmi health -c
```

If `datacenter-gpu-manager` is not found, keep `platform.telemetry_order: ["dcgm", "nvidia_smi"]`; monitor will report `dcgm_available=false` and continue with `nvidia-smi`. Do not install random third-party DCGM packages. Prefer NVIDIA/DGX OS repositories or NVIDIA's official CUDA/DCGM repository for your OS version.

For systemd deployments, make sure service `PATH` can find `nvidia-smi`, `dcgmi`, and `ps`:

```ini
Environment=PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
```

Then reload and restart:

```bash
sudo systemctl daemon-reload
sudo systemctl restart gpu-monitor-dashboard
```


DGX Spark behavior:

- `/api/status` includes `platform_summary` with profile, architecture, OS, driver version, DCGM availability, and active telemetry source.
- DGX Spark profile labels GPU memory as `GPU/Unified Mem MB`.
- Dashboard shows `System / Unified Memory` from `/proc/meminfo`.
- `dcgmi` failures are best-effort and automatically fall back to `nvidia-smi`; sampling continues.
- To avoid conflicts with NVIDIA DGX Dashboard, keep this dashboard on a custom local port such as `8093`.

SSH tunnel example:

```bash
ssh -L 8093:127.0.0.1:8093 ubuntu@DGX_SPARK_HOST
```


## Metrics

默认暴露 Prometheus 指标，例如：

- `gpu_monitor_up`
- `gpu_monitor_notify_enabled`
- `gpu_monitor_last_sample_timestamp_seconds`
- `gpu_monitor_consecutive_failures`
- `gpu_monitor_alert_total`
- `gpu_monitor_notify_total`
- `gpu_monitor_collection_errors_total`

## Migration Notes

旧版配置迁移要点：

- `notify.control.enabled` 仍保留
- 建议新增 `monitor.instance_name` 区分多台机器共用同一 webhook 的消息来源
- 新增 `dashboard` / `logging` / `metrics` 配置块
- 旧版默认公网监听已改为默认本地监听
- Dashboard 现默认启用 Bearer Token 鉴权
- `config.yaml` 不再建议入库

## Troubleshooting

- `nvidia-smi not found`: 检查 NVIDIA 驱动与 PATH
- `dashboard.auth.token is required`: 在 `config.yaml` 或环境变量中设置 Token
- 无法收到告警：检查 `notify.strategy.order`、各渠道配置和服务器出网
- 日志过多：可调高 `logging.level` 或拉长采样周期
- `Could not open requirements file`: 先进入仓库根目录再执行 `pip install -r requirements.txt`
- `status=203/EXEC`: 重点检查 `/etc/systemd/system/gpu-monitor-dashboard.service` 中的 `WorkingDirectory`、`ExecStart` 是否真实存在且可执行
- `status=203/EXEC` 且项目位于 `/home/...`: 检查是否仍启用了 `ProtectHome=true`；若使用 home 目录部署，请改为 `ProtectHome=false`
- 想加高温提醒：在 `config.yaml` 的 `threshold` 下设置 `high_temperature_c` 和 `high_temperature_minutes`





