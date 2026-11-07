# 上游来源与裁剪溯源

## 官方基线

- 项目：Home Assistant Core，官方源码仓库 https://github.com/home-assistant/core 。
- 基线：**2026.10.4**，明确固定版本，不声称是所有环境下最新版本。
- 来源元数据：https://pypi.org/pypi/homeassistant/2026.10.4/json 。
- 官方源码包：https://files.pythonhosted.org/packages/f2/b1/d77d6ecc16eaf90f7913516293cdb00f354e305a126ab185da0f0dbf916a/homeassistant-2026.10.4.tar.gz 。
- 原包大小：38675928 B。
- 原包SHA256：`8410fecb434ab94b3f7724cd80584050f0ceeb856c16ee70202c24037d62238a`，本轮已对照官方元数据并在隔离环境重新计算。
- 原始运行要求：Python `>=3.14.2`；没有降低版本声明，也没有把Python3.8当成可用环境。

本目录从官方sdist提取裁剪；不是官方Git仓的完整clone，不冒称保留完整上游Git历史。完整源archive与官方元数据在`S:/work/WCIS/workenv/ha-small/`保留。新的本地Git若建立，仅记录本地裁剪结果。

## 许可与署名

官方LICENSE.md（Apache-2.0）、README.rst、保留文件的版权/第三方许可信息原样保留。新增README.md、ha-cut.md、UPSTREAM.md、PATCHES.md与cut校验资源属于本地裁剪说明/验证工具，不替换上游署名。本项目不是Home Assistant官方精简发行版。

## 删除及字节一致性

- 上游集成1523，保留119，删除1404。
- 上游文件保留5909、删除49275；原样保留字节38487103B。
- 修改上游文件数：**0**。依赖分析与实验配置新增文件另算，不混入“零补丁”的统计。
- `cut/source-audit.json`逐个记录保留上游文件的相对路径、字节数与SHA256。
- `cut/cut-report.json`记录完整保留/删除域列表、依赖原因、源包摘要与裁剪口径。
- 完整删除文件路径全表位于`workenv/ha-small/removed-upstream-files-final.json`；不将长列表重复塞入README。

被删除的不止厂商集成，还包括对应未保留测试和sdist生成metadata；保留部分的上游pyproject、核心、注册表、认证和helpers没有改写。旧PKG-INFO/egg-info由构建过程重新生成，不是运行时源码补丁。

## 依赖闭包策略与修正记录

1. 从主要功能种子开始，递归manifest的dependencies/after_dependencies。
2. 分析核心与保留集成中的静态导入，包括可选导入；保守保留，避免为瘦身改核心分派。
3. 首次漏了相对导入，启动报缺default_config；随后补相对导入闭包，恢复依赖而不是修改bootstrap。
4. 根据BASE_PLATFORMS恢复air_quality/geo_location/radio_frequency三个基础域，避免前端翻译预载找不到域。
5. 当前显式配置不启用default_config，保留可选源码不等于加载云或硬件发现服务。

pyproject中48项直接依赖原样保留。一些云/蓝牙依赖被HA helpers直接导入，不能假定删除components/cloud目录就能一起移除依赖；进一步优化必须另行验证并登记必要补丁。

## 验证范围

本机Linux x86_64容器、Python3.14.10：30功能检查＋4正常重启检查通过。媒体系统库与两个语音原生依赖在该隔离容器构建/安装。没有运行HA4FPAI/npuserv、没有部署开发板，没有配置远端仓库或Actions。

当前源码39.1MB与源码压缩包6.25MB不含外部前端包/解释器/全套依赖。完整测试venv约657MiB、HA RSS一次快照226.1MiB；这些读数不能推算板端可用性。
