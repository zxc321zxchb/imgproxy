# SmartEdu 图片缩略图服务部署

## 镜像与版本

- 仓库：https://github.com/zxc321zxchb/imgproxy
- 构建记录：https://github.com/zxc321zxchb/imgproxy/actions/workflows/image.yml
- 镜像：`ghcr.io/zxc321zxchb/imgproxy:v4.0.16`
- 下载：https://github.com/zxc321zxchb/imgproxy/releases/tag/v4.0.16
- 架构：`linux/amd64`、`linux/arm64`，Docker 自动选择。

此版本基于云效 `agent/imgproxy` 的 `master` 提交
`0040eb67c85108bec5ce14e49d0ddeb9270ef840`，其代码版本为 4.0.16；从源码编译，未升级上游。
保留 Apache-2.0 LICENSE 和 NOTICE。原上游工作流保存在 `.github/upstream-workflows/`，
新工作流使用 GitHub 托管构建机与 `GITHUB_TOKEN`，无需配置 Docker Hub 密码。
只有两个架构均通过容器验证后，才发布正式多架构标签。

```bash
docker pull ghcr.io/zxc321zxchb/imgproxy:v4.0.16
```

生产固定版本或 digest；`latest` 随 master 构建更新。每次构建还提供 `sha-<完整Git提交>` 标签。
GHCR 镜像包需设为 Public，交付时必须验证无登录拉取。镜像公开不代表存储桶需要公开。

无法访问 GHCR 时，从上述 Release 下载对应架构的 `imgproxy-linux-amd64.tar.gz`
或 `imgproxy-linux-arm64.tar.gz` 及同名 `.sha256` 文件：

```bash
sha256sum -c imgproxy-linux-amd64.tar.gz.sha256
gzip -dc imgproxy-linux-amd64.tar.gz | docker load
```

离线包导入后具有相同的版本标签；离线启动 Compose 时用 `docker compose up -d --pull never`。
普通分支构建的离线包在 Actions Artifacts 保留 7 天，版本 Release 的附件长期保留。

## 端口、网络与存储

| 方向 | 端口 | 用途 |
| --- | --- | --- |
| 入站 | TCP 8080 | 唯一默认服务端口，媒资后端请求缩略图，`GET /health` 健康探针 |
| 出站 | 对象存储 API 实际端口 | OSS/S3 通常 TCP 443；MinIO 常用 TCP 9000 或反向代理 443 |
| 出站 | UDP/TCP 53 | 容器或集群 DNS（按环境网络策略） |
| 拉取镜像 | TCP 443 | 节点访问 GHCR 及镜像层下载地址；运行中的 imgproxy 无需访问 GitHub |

8080 仅需对媒资后端和集群探针开放。Kubernetes 使用 ClusterIP，无需 NodePort、Ingress 或公网入口。
8080 不需要对老师浏览器开放，缩略图由媒资后端调用并保存到受控对象存储。
单机映射可改为 `私网IP:18080:8080`；此时后端填写宿主机的 18080。

服务只从 S3 读取原图，并在 HTTP 响应中返回处理结果；不直接写入存储。
无需 MySQL、Redis、Nacos、GPU 或独立持久卷，横向扩容不需要共享本地目录。
缩略图的保存由 SmartEdu Media 完成。存储账号只需获准源桶的 `GetObject` 权限。

## 资源建议

| 场景 | CPU | 内存 | 并发处理数 |
| --- | --- | --- | --- |
| 小规模验证 | 1 核 | 512Mi–1Gi | `IMGPROXY_WORKERS=1`，大图建议 1Gi 以上 |
| 建议起步 | 2 核 | 2Gi | `IMGPROXY_WORKERS=2` |
| 批量生成 | 每副本 2–4 核 | 每副本 2–4Gi | 每副本 2–4；先压测再调整 |

Kubernetes 示例 request 为 `500m / 512Mi`、limit 为 `2 CPU / 2Gi`，单副本；
需要可用性时用两个副本。以上是容量规划起点，未做业务吞吐压测。
libvips 使用原生内存，增加 worker 会增加内存峰值；大图负载下先提高内存或降低并发。
无需持久化空间，节点为镜像、临时文件和日志预留至少 2Gi 可用空间，并按日志保留周期调整。

示例限制原图 50MP、20MiB、动画只取首帧，输出最大单边 2048；媒资当前固定生成
最长边 480px 的 WebP（等比、不放大、质量 92）。超限原图会被拒绝，需要更大图时按资源容量调高限制。

注意这是 imgproxy **4.x**：并发项是 `IMGPROXY_WORKERS`，不要照搬旧版 `IMGPROXY_CONCURRENCY`。

## 必填配置

复制 `deploy/.env.example` 为 `deploy/.env`，填写以下参数：

| 环境变量 | 配置说明 |
| --- | --- |
| `IMGPROXY_KEY` | `openssl rand -hex 32` 生成的 64 位十六进制签名 key |
| `IMGPROXY_SALT` | 再执行一次上述命令生成独立的 salt |
| `IMGPROXY_S3_ENDPOINT` | 容器可访问的 S3 API 地址，含 http/https；MinIO 不要填管理控制台端口 |
| `IMGPROXY_S3_REGION` | 对应 region；MinIO 常用 `us-east-1`，OSS 用桶所在 region |
| `AWS_ACCESS_KEY_ID` | 只读源对象的存储 access key |
| `AWS_SECRET_ACCESS_KEY` | 对应 secret key |
| `IMGPROXY_S3_ALLOWED_BUCKETS` | 实际源桶名；多个桶用逗号分隔 |
| `IMGPROXY_ALLOWED_SOURCES` | 如 `s3://media/`；多个前缀用逗号分隔 |

示例已设置 `IMGPROXY_USE_S3=true` 和 `IMGPROXY_S3_ENDPOINT_USE_PATH_STYLE=true`，
适合 MinIO / OSS 的 S3 兼容入口。凭据与签名密钥只在运行时注入，不放入镜像或代码。
空 key/salt 会使上游关闭签名验证；Compose 使用必填校验防止漏配。

## Docker Compose

```bash
cd deploy
cp .env.example .env
# 编辑 .env：填写存储配置，并分别生成 KEY、SALT
docker compose up -d
docker compose ps
curl -fsS http://127.0.0.1:8080/health
```

默认只绑定宿主机回环地址；远程后端访问时，在 `.env` 中设置 `IMGPROXY_HOST_IP=宿主机私网IP`，
并将防火墙来源限制为后端。容器内 `127.0.0.1` 不代表另一台主机或另一个容器。

## Kubernetes / ACK

在目标 namespace 中创建专用 Secret，key 与 `.env.example` 中的前八项一致；
不需要 `IMGPROXY_HOST_IP` 或 `IMGPROXY_HOST_PORT`。可在控制台填写，或使用权限受控的 env 文件：

```bash
kubectl -n <目标命名空间> create secret generic imgproxy-secrets \
  --from-env-file=/受控目录/imgproxy.env
kubectl -n <目标命名空间> apply -f deploy/kubernetes.yaml
kubectl -n <目标命名空间> rollout status deployment/imgproxy
```

清单使用同命名空间的专用 Secret、ClusterIP、健康探针、非 root 用户、只读根文件系统、
内存临时目录和 CPU/内存限额。`/health` 只验证服务进程，不验证真实存储账号权限；
上线后需上传一张实际原图验证整条业务链。

## SmartEdu Media 侧配置

imgproxy 与媒资后端须使用相同的 KEY / SALT：

```dotenv
MEDIA_THUMBNAIL_ENABLED=true
MEDIA_IMGPROXY_BASE_URL=http://imgproxy:8080
MEDIA_IMGPROXY_KEY=<与IMGPROXY_KEY相同>
MEDIA_IMGPROXY_SALT=<与IMGPROXY_SALT相同>
```

`http://imgproxy:8080` 适合同一 Kubernetes namespace 或同一 Docker 网络。
跨 namespace 使用 `http://imgproxy.<namespace>.svc.cluster.local:8080`；
跨主机使用可达的私网地址和映射端口。以上配置填在 **媒资后端**，不填到前端。

## 验证与构建

Actions 在两个架构上分别从源码编译，再运行 `scripts/smoke-image.py`：
启动容器、检查 HTTP `/health` 与 CLI health、用临时签名请求模拟 S3 原图、验证输出为
480×240 WebP，并验证错误签名返回 403 且未访问源存储。
该验证使用合成图片与临时凭据，不连接真实存储，也不替代实际 OSS/MinIO 联调或容量压测。

```bash
docker build -f docker/Dockerfile -t imgproxy:local .
python3 scripts/smoke-image.py imgproxy:local
```

回滚时将部署镜像改回上一版本标签或 digest，不涉及服务数据库迁移。
