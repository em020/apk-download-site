# 下载站运维

## 部署

选一台运行 Nginx 的 Mac 作为下载源站，使用独立状态目录存放 APK 和站点快照。下文以默认端口 8098 和自选状态目录为例。

```sh
site_state="$HOME/apk-download-site-state"
python3 scripts/publish-apk.py --state-dir "$site_state" \
  --register example-app --package app.example.android --certificate '替换为签名证书 SHA-256 指纹'
python3 scripts/publish-apk.py --state-dir "$site_state" --bundle /path/to/release
python3 scripts/download_service.py install --state-dir "$site_state"
```

安装命令先检查候选 Nginx 配置，再启用 LaunchAgent。默认状态目录和 LaunchAgent 沿用早期版本的命名，部署其他状态目录时请给各命令传入相同的 `--state-dir`。同一 Mac 用户下的安装命令管理同一个 LaunchAgent。Mac 在线且用户已登录时，LaunchAgent 会维护源站运行。

有反向代理时，可参考 [HTTP 网关示例](../distribution/nginx-gateway.conf) 或 [HTTPS 网关示例](../distribution/nginx-proxy.example.conf)，将示例的上游地址换成源站的私网地址。示例中的尾部斜线会让网关在转发时去掉 `/android/` 前缀。应用页面和 APK 继续使用相对链接。源站允许回环和私网访问，远程发布走独立的 SSH 入口。

## 日常发布

应用在各自仓库运行发版命令，或按 [v1 协议](publish-protocol.md) 准备并提交发布包。以下命令都从下载站工程根目录运行：

```sh
python3 scripts/publish-apk.py --state-dir "$site_state" --app-info example-app
python3 scripts/publish-apk.py --state-dir "$site_state" --inspect /path/to/App.apk --app-id example-app
python3 scripts/publish-apk.py --state-dir "$site_state" --bundle /path/to/release
```

`--app-info` 查询登记的包名、证书指纹和最新版本号。`--inspect` 用登记身份检查 APK 并返回元数据。Android SDK 位于其他路径时传入 `--sdk`。

仅调整页面模板时，用 `python3 scripts/publish-apk.py --state-dir "$site_state" --refresh-site` 重生成当前站点。

## 服务管理与检查

```sh
python3 scripts/download_service.py status --state-dir "$site_state"
python3 scripts/verify-downloads.py http://127.0.0.1:8098/apps/example-app/
```

`start` 和 `stop` 可控制当前实例。若使用自定义端口，`status` 也需传入相同的 `--port`。默认首页使用登记配置中的应用。若保留了旧版根路径 APK 别名，验证该地址时传入对应的 `--apk-name`。

更新网关前先备份当前配置，执行 `nginx -t` 后再 reload，并通过公开入口验证。

## 数据布局与恢复

```text
registry.json                     # 应用登记、默认首页和兼容 URL
app-releases/<appId>/<releaseId>/  # APK、图标、发布清单、元数据和校验值
sites/<snapshot>/                 # 生成的页面、索引和文件链接
current -> sites/<snapshot>       # Nginx 公开根目录
service/                          # Nginx 配置和运行日志
```

早期版本保留的 `releases/` 和 `legacy-icons/` 也属于运行状态。发布通过文件锁串行写入，在新快照就绪后原子切换 `current`。版本目录保留历史 APK 和固定下载地址。

备份源码、完整运行目录和 LaunchAgent 配置。做一致性备份前暂停发布，保留符号链接和原有文件权限。恢复时优先放回原绝对路径，因为快照包含指向产物的绝对路径。启动服务前先核对登记信息、`current` 和固定版本文件，再验证源站和公开地址。

应用版本需要回退时，在应用工程将回退后的内容构建成更高版本号、同签名的 APK，再重新发布。
