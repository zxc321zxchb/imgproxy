SmartEdu 图片缩略图服务，基于云效 agent/imgproxy master 的源代码（imgproxy 4.0.16，原始提交 0040eb67c85108bec5ce14e49d0ddeb9270ef840）。

- 镜像：`ghcr.io/zxc321zxchb/imgproxy:v4.0.16`
- 平台：linux/amd64、linux/arm64，Docker 自动选择。
- 部署、环境变量、网络和资源建议：[部署说明](https://github.com/zxc321zxchb/imgproxy/blob/master/docs/SMARTEDU-DEPLOY.md)
- 唯一默认服务端口：TCP 8080，仅后端内部访问。
- 建议起步资源：2 CPU、2Gi 内存，2 workers；无需 GPU、数据库、Redis 或持久卷。
- 两个平台均通过健康探针、签名 S3 原图读取、480×240 WebP 生成与错误签名拒绝验证后发布。

在线拉取：

```bash
docker pull ghcr.io/zxc321zxchb/imgproxy:v4.0.16
```

无法访问 GHCR 时，下载对应架构的压缩镜像及 SHA256 文件：

```bash
sha256sum -c imgproxy-linux-amd64.tar.gz.sha256
gzip -dc imgproxy-linux-amd64.tar.gz | docker load
```

arm64 主机将文件名中的 amd64 替换为 arm64。签名密钥和存储凭据在部署时注入，详见部署说明。
资源建议未经业务吞吐压测；S3 fixture 验证不替代真实存储联调。
