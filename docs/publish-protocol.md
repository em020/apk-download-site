# APK 发布协议 v1

开发者负责构建和签名。下载站运营者为每个 App 登记发布身份。之后所有 App 都通过同一种发布包接入，下载站根据发布记录生成应用页、图标、版本列表和下载链接。

## 开发者交付

在同一构建中准备一个目录，包含以下三个普通文件。

| 文件 | 内容 |
| --- | --- |
| `App.apk` | 已签名的 Android APK，最大 500 MiB（524,288,000 字节） |
| `icon.svg` | 与此版本对应的静态 SVG，最大 256 KiB。使用独立图形，不带脚本或外部资源 |
| `manifest.json` | UTF-8 发布清单，最大 64 KiB |

清单示例：

```json
{
  "schemaVersion": 1,
  "appId": "example-app",
  "name": "Example App",
  "description": "一段供下载页展示的应用说明。",
  "notes": "本版更新说明",
  "commit": "0123456789abcdef0123456789abcdef01234567"
}
```

`appId` 由运营者分配，使用小写字母开头，由小写字母、数字和连字符组成，最多 40 个字符。
`name`、`description` 和 `notes` 必填，最多分别为 100、2,000 和 8,000 字符。
`commit` 可省略。提供时填写 40 位小写十六进制 Git commit。
服务器从 APK 提取包名、签名、版本号、SDK 和权限，按登记信息验签。每次更新都必须使用相同包名和签名证书，并提高 `versionCode`。

## 运营者一次登记

取得 App 的 Android 包名和 APK 签名证书的 SHA-256 指纹，运行：

```sh
python3 scripts/publish-apk.py \
  --register example-app \
  --package app.example.android \
  --certificate "替换为 64 位十六进制证书指纹"
```

空站首次登记时，该 App 成为默认首页。需要限制权限时，按每项允许的权限传入一个 `--allow-permission` 参数。
证书指纹是公开摘要。发布方应自己持有签名密钥，下载站验签只需要已签名的 APK 和登记信息。

站点配置位于私有状态目录的 `registry.json`。以下是配置结构示例：

```json
{
  "schemaVersion": 1,
  "defaultApp": "example-app",
  "rootAlias": {
    "appId": "example-app",
    "apkName": "Example.apk"
  },
  "apps": {
    "example-app": {
      "applicationId": "app.example.android",
      "certificateSha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
      "allowedPermissions": ["android.permission.INTERNET"]
    }
  }
}
```

`defaultApp` 选择首页应用。`rootAlias` 是可选的旧链接映射。
`allowedPermissions` 可为 App 固定 APK 权限集合。包名、证书和发布授权由运营者管理，开发者提交的清单只包含发布内容。

## 提交和结果

有发布机访问权限时，直接运行：

```sh
python3 scripts/publish-apk.py --bundle ./release
```

在别的机器上，可以用运营者为该 App 配置的受限 SSH key。
将三个文件放在 ZIP 根目录，使用常规 ZIP 格式，无目录、注释或 ZIP64。用一个全新的输出文件创建压缩包：

```sh
zip -j release.zip release/App.apk release/icon.svg release/manifest.json
ssh -T -i ~/.ssh/example-app-publish publisher-host publish-v1 < release.zip
```

运营者将开发者的**公钥**加入发布机 `authorized_keys`，使用 `restrict` 并绑定固定命令。
使用发布机上的绝对路径。脚本、SDK 和状态目录由运营者控制。每个 App 使用自己的发布 key：

```text
restrict,command="/absolute/path/to/python3 /absolute/path/to/scripts/publish-ssh.py --app-id example-app" ssh-ed25519 <开发者公钥>
```

自定义站点目录或 SDK 时，在固定命令中加上 `--state-dir` 或 `--sdk`，在固定命令里用单引号包住包含空格的路径。开发者只发 `publish-v1` 和 ZIP 内容。强制命令按公钥绑定的 App 检查清单，SSH key 不提供交互式 shell 或文件浏览。通过可信方式核对发布机的 SSH host key，并通过内网或 VPN 连接。

SSH ZIP 的总大小上限由 APK、图标和清单各自上限加上 1 MiB ZIP 开销组成。
500 MiB APK 通过本机目录提交和 SSH 发布时使用相同的校验上限。

两种提交方式都返回一行 JSON：

```json
{
  "status": "published",
  "appId": "example-app",
  "releaseId": "v42-0123456789ab",
  "versionName": "1.0.0",
  "versionCode": 42,
  "sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
  "pagePath": "apps/example-app/",
  "downloadPath": "apps/example-app/releases/v42-0123456789ab/App.apk"
}
```

路径相对于下载站首页，支持部署在 `/android/` 等子路径。
重复提交与当前版本相同的发布包返回 `unchanged`。同一版本号的发布内容必须一致。
提交失败时进程以非零状态退出，错误写入 stderr。遇到发布锁冲突时，可以在前一份发布完成后原样重试。

## 下载和核验

公开 HTTP 入口提供只读页面和 JSON，支持 GET 和 HEAD。写入通过上面的发布入口。

- `apps.json` 列出已发布的应用。
- `apps/<appId>/` 是应用下载页，`apps/<appId>/icon.svg` 是当前图标。
- `apps/<appId>/releases.json` 是版本记录。
- `apps/<appId>/latest/App.apk` 指向最新版。
- 返回的 `downloadPath` 是固定版本地址，下载后可以对照 `sha256`。

运营者可运行 `python3 scripts/verify-downloads.py <应用下载页URL> --apk-name App.apk`，检查页面、历史链接和 APK 校验。对既有根路径的兼容下载页，`--apk-name` 使用配置中对应的 APK 文件名。
