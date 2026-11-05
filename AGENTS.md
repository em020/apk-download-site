# APK 下载站

- 修改发布逻辑前，读 [APK 发布协议 v1](docs/publish-protocol.md)。涉及部署、数据迁移或服务管理时，读[运维手册](docs/operations.md)。
- 下载站接收已签名的 APK。应用构建、签名和生成图标属于各应用工程。
- 维持包名、签名证书和版本号检查。SSH 发布按公钥绑定的应用授权，HTTP 只提供下载。
- 维护版本文件的不可变性、发布锁与站点快照的原子切换。保留已有的固定下载地址及兼容别名。
- 将运行状态、构建产物、密钥、配置备份和日志放在工作区与公开目录之外。
- 修改后运行 `python3 -m unittest discover -s scripts/tests -v`。测试使用临时状态；部署后用 `scripts/verify-downloads.py` 验证实际下载入口。
