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

2026-11-11安装恢复修正：对于同一Nginx可执行文件、state路径、端口和LaunchAgent定义，
重复`install`只校验并reload配置，不bootout/bootstrap运行中的服务。确需重新登记LaunchAgent时，
仅对launchd短暂的exit5进行最多5次有界重试（总等待约3.7秒）；其他错误立即返回，持续错误明确
报告。重配置无法启动时尝试恢复之前的配置和LaunchAgent定义。对应回归测试使用临时目录与
mock launchctl，不操作生产服务；此前一次真实bootout/bootstrap间隙的exit5已由独立start恢复。

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

## 文件分发与对象存储迁移

默认不需要额外配置：APK仍从本机源站下载。服务端同时生成版本化`download.json`描述，
将文件身份与下载位置分开。新描述路由需要更新`download_service.py install`生成的Nginx配置；
先备份现有配置，再按服务管理流程安装/检查。旧APK固定路径和latest别名继续实际提供文件，
不改成旧更新器拒绝的302跳转。

运营者可在**私有state目录**创建`delivery.json`，例如：

```json
{
  "schemaVersion": 1,
  "mode": "external",
  "publicBaseUrl": "https://downloads.example.com/",
  "publishCommand": ["/absolute/path/to/upload-artifact"]
}
```

`publicBaseUrl`必须为以斜线结尾的永久HTTPS地址，不能含用户名、密码、查询签名或fragment。
发布者仍运行原来的发版脚本。APK在外部的固定objectKey为
`apps/<appId>/releases/<releaseId>/App.apk`；因此CDN或对象存储可直接承载相同目录结构。

`publishCommand`是运营者控制的JSON argv，无shell展开。脚本追加四个参数：

```text
upload-artifact <absolute-file-path> <object-key> <sha256> <bytes>
```

命令可自行调用S3兼容SDK、云厂商CLI或现有上传程序；凭据通过运营端私有环境/凭据文件提供，
不要把密钥放进argv或Git。命令必须按不可变key发布，不覆盖不同内容，成功退出0。stdout/stderr
不写入公开输出；失败或一小时超时只返回通用错误，当前站点不切换。

对象已预先上传时可以省略`publishCommand`。发布端会先检查远端：存在且内容匹配则复用，
不存在才运行上传命令；不同内容直接拒绝，不能覆盖。上传后实际流式读取永久URL，核对
完整大小和SHA-256；确认可访问后才原子激活新站点。CDN尚未传播而返回404时，本次发布失败，
待文件可访问后原样重试。成功核验结果缓存于私有`delivery-cache/`，按URL和SHA标识。

迁移已有版本时，先配置外部分发、预上传文件或提供上传命令，再运行`--refresh-site`。
所有在用版本就绪后才切站点；下载页与新更新器直接访问CDN，旧版固定路径仍由本地兼容服务提供。
删除配置或改为`{"schemaVersion":1,"mode":"local"}`后refresh可切回本地。保留本地APK用于兼容与恢复。
此模式只迁移大文件下载位置，发布机和版本元数据服务仍需正常运行。

通用适配器也可被整书ZIP发布流程调用，不要求文件是APK：

```sh
python3 scripts/artifact_delivery.py --config /private/delivery.json \
  --file /private/book.zip --object-key book-packages/book-id/package-id/book.zip \
  --sha256 '<完整SHA-256>' --bytes 12345678
```

命令成功后仅输出objectKey、bytes、sha256、downloadUrl、expiresAt JSON；local模式的URL为null，
由业务服务提供本地下载路由。临时签名URL尚未实现，后续需增加动态描述服务，不能将过期URL
固化到静态站点。Android下载信任策略和SHA校验由各App更新器负责，服务端不下发存储凭据。

## 云之家候选文件服务实测（2026-11-11，仅实验）

用户指定的上传接口为`POST https://www.yunzhijia.com/docrest/doc/file/uploadfile`，
鉴权仅在请求头`openToken`，multipart字段`file`和`bizKey=xuntong`；响应`data[0].fileId`。
下载URL为`https://www.yunzhijia.com/docrest/file/downloadfile/<fileId>`。
本次没有调用token兑换，也没有切换线上分发配置；令牌值未保存到源码或普通笔记。

- 98,361字节合成文本、262,434字节合成ZIP、731,476字节现有TV APK均上传成功。
  不带token/Cookie的完整下载均200、长度及SHA-256一致，未发生跳转。
- HEAD返回正确Content-Length；首段和中途续传Range均206，Content-Range及字节正确。
  Linux后端另对APK匿名下载与Range复测通过，单次完整GET约0.544秒（不是大文件测速结论）。
- 实测错误If-Range仍返回206，服务端没有按该条件回退200；接入时固定fileId/版本并以
  完整SHA-256为最终校验，不能把ETag当成完整性hash或条件请求已生效的证据。
- 仅验证上述小文件，未确认大文件上限、保存期限、配额或长期稳定性。
- 当前external分发按publicBaseUrl+自定义objectKey拼地址，不能直接套用云之家分配的fileId。
  后续需要发布端provider持久保存artifactId/objectKey+SHA→fileId/downloadUrl映射，
  匿名下载完整校验后再激活；App的download.json协议可保持不变，上传token仅留发布端。

脱敏证据：任务目录`output/yunzhijia-storage-20261111/results.json`。
