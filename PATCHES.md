# 必要补丁与新增文件登记

## 上游源码补丁

**当前无补丁，修改上游文件数为0。** 原有Python代码、manifest、pyproject.toml、setup.cfg及许可文件均按保留清单保持原字节。

此前启动发现漏依赖时，采用恢复必要上游目录的方式修正删除集；没有修改import、跳过初始化或屏蔽错误。

## 新增资源

新增资源不属于上游源码修改，但应明确登记：

- README.md：本地项目入口、功能/体积/环境/运行边界。
- ha-cut.md：主要功能复选清单。
- UPSTREAM.md、PATCHES.md：来源、许可、删除策略和例外。
- .gitignore：排除依赖环境、编译缓存、运行时数据库、认证与临时构建产物。
- examples/configuration.yaml：显式最小实验配置，不启用default_config。
- examples/ha4fpai-bridge.yaml、examples/secrets.example.yaml：可选外部业务接口配置模板，不含真实凭据；尚未与生产HA4FPAI联调。
- cut/cut-report.json：保留与删除域的配置/溯源信息。
- cut/source-audit.json、cut/verify_source.py：保留上游文件哈希及只读一致性校验。
- cut/functional_probe.py、cut/persistence_probe.py：只对带专用标记的隔离HA执行功能/正常重启验证，包含测试账户与测试实体操作。
- cut/requirements-linux-x86_64.lock：实际Linux x86_64测试环境依赖锁定，不声称是ARM64锁文件。
- cut/REPRODUCE.md：本轮可复现操作。

运行日志、测试结果JSON、原始下载和一次性裁剪脚本仍在workenv，不夹入源码分发目录。

## 将来不得已修改时

每个补丁必须新增一条记录，至少包括：上游版本与路径、原/新SHA256、实际失败现象、为何恢复依赖或使用配置不能解决、最小修改、受影响功能测试、上游升级如何重放或删除补丁。不要为了让数量好看而删除权限、版本门禁、错误检查或必要能力。
