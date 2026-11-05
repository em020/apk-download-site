# APK 下载站

接收已签名的 Android APK，按应用登记身份验签，生成静态下载页和版本索引。应用工程负责构建和签名，交付同一套发布包即可接入。

| 位置 | 职责 |
| --- | --- |
| `scripts/publish-apk.py` | 登记应用、查询发布状态、检查 APK、导入发布包和刷新页面 |
| `scripts/publish-ssh.py` | 通过按应用授权的 SSH key 导入发布包 |
| `scripts/publishing.py` | 验证发布包并原子更新站点快照 |
| `distribution/` | 页面模板和网关配置示例 |
| `scripts/download_service.py` | 管理发布机上的 Nginx LaunchAgent |

发布工具使用 Python 3 标准库，以及 Android SDK Build Tools 35.0.0 的 `aapt` 和 `apksigner`。验签需要 Java。下载服务由 Nginx 提供，发布机用 macOS LaunchAgent 管理。

## 从空状态开始

准备签名后的 `App.apk`、`icon.svg` 和 `manifest.json`，置于同一目录。清单字段、证书指纹和远程提交格式见 [APK 发布协议 v1](docs/publish-protocol.md)。

下面用独立状态目录演示，替换示例应用的 ID、包名、签名指纹和发布包路径。

```sh
site_state="$HOME/apk-download-site-state"
python3 scripts/publish-apk.py --state-dir "$site_state" \
  --register example-app --package app.example.android --certificate '64 位签名证书 SHA-256 指纹'
python3 scripts/publish-apk.py --state-dir "$site_state" --bundle /path/to/release
```

首版导入成功后，可在发布机用 `python3 scripts/download_service.py install --state-dir "$site_state"` 安装下载服务。该命令管理本机唯一的下载站 LaunchAgent。现有实例的目录和日常维护见[运维手册](docs/operations.md)。

## 验证

```sh
python3 -m unittest discover -s scripts/tests -v
python3 scripts/verify-downloads.py http://127.0.0.1:8098/apps/example-app/
```

前一条命令使用临时状态验证发布协议、版本目录与 SSH 入口。后一条命令检查已运行实例的页面、APK 校验值、Range 下载和访问控制。
