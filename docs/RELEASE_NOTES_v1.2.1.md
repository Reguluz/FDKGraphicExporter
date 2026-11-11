# v1.2.1

本版提供 Windows x64 可直接使用的工具合集，以及可单独安装的 Blender 预览插件。下载合集后完整解压，打开 `fdk_toolkit/FDK_Asset_Tool.exe`，在窗口选择游戏与 Blender 4.5 路径即可开始。

## 本版变化

- 提供已编译的图形导出器和独立运行依赖，使用者无需安装 Python、Git 或编译器。
- 工具合集同时提供 Blender 插件、使用说明和许可证；另附独立插件 ZIP 与 SHA-256 校验值。
- 默认配置与插件预设不携带开发者的个人目录、游戏资源库或运行记录。
- 首页 README 改为下载、解压、图形导出及 Blender 预览步骤；源码构建说明移至 `docs/BUILDING.md`。

## 下载内容

| 文件 | 用途 |
|---|---|
| `FDKGraphicExporter-v1.2.1-windows-x64.zip` | 完整工具合集，首次使用选此项 |
| `FDK_Blender_Addon-v1.2.1.zip` | 独立 Blender 插件 |
| `SHA256SUMS.txt` | 附件校验值 |

GitHub 自动提供的 Source code 压缩包用于开发。普通使用请选择工具合集，保持包内目录完整；Blender 4.5 LTS 仍需另行安装。

## 兼容性

支持已验证 PC 安装版本的《黎之轨迹 II》和《空之轨迹 the 2nd》（重制版）。角色模型保留骨架与蒙皮，Blender 插件可按真实材质引用组装预览材质。当前不导出动画轨道，不重建游戏 IK、动态骨骼或完整 FDK 着色器。

本版以运行包交付和使用流程整理为主，不扩大到其他 FDK 游戏或未经验证的安装版本。更详细的验证范围见 [兼容性说明](https://github.com/Reguluz/FDKGraphicExporter/blob/v1.2.1/docs/COMPATIBILITY.md)。

## 升级与内容范围

建议将新版完整解压到新目录，避免覆盖已有导出结果。已有资源库可在 Blender 插件中直接选择其根目录；在导出工具中继续使用既有输出时，仍须满足当前工具目录下的输出隔离规则。

升级 Blender 插件前保存工程、禁用旧版本；安装新 ZIP 后启用并重启 Blender。内部模块名仍为 `kuro2_preview`，不要同时启用两份。旧工程需要应用当前材质处理时，点击「重建当前模型材质」。

附件不含游戏程序、模型、贴图、解包数据、导出结果、预览、运行日志或个人配置。源码与对应依赖版本可从发行包中的 `SOURCE.txt` 查阅，许可证随包保留。
