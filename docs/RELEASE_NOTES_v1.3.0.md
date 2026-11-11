# v1.3.0

同一套工具合集和 Blender 插件现在支持 **Blender 4.5 LTS 与 5.2 LTS**。在导出窗口选择其中任一版本的 `blender.exe`，在对应 Blender 中安装同一个插件 ZIP 即可，不需要同时安装两个版本。

## 本版变化

- 环境检查读取 Blender 实际版本、内置 Python 和所需导入导出接口，明确支持 4.5、5.2 两条分支。
- 两个 Blender 导出脚本从同一份源码生成 Python 3.11、3.13 字节码，运行时自动选择；主工具运行环境继续共用，不增加整套解释器。
- 预览场景适配两版 EEVEE 标识；资源目录设置兼容旧工程和 Blender 5.x 的属性存储方式。
- 保留现有 FBX、贴图、材质记录和独立游戏资源库结构。

## 下载与使用

| 文件 | 用途 |
|---|---|
| `FDKGraphicExporter-v1.3.0-windows-x64.zip` | 完整 Windows x64 工具合集，首次使用选此项 |
| `FDK_Blender_Addon-v1.3.0.zip` | 单独安装或升级 Blender 插件 |
| `SHA256SUMS.txt` | 两个压缩包的校验值 |

完整解压合集，运行 `fdk_toolkit/FDK_Asset_Tool.exe`，选择游戏和 Blender 4.5 或 5.2 路径，保存设置并检查环境，再扫描、选择模型和导出。插件 ZIP 无需解压，在 Blender 中从磁盘安装并启用。

升级前保存 Blender 工程，禁用旧插件后安装新版并重启。建议解压到新目录，保留已有导出库；插件可直接选择旧资源库。切换导出所用的 Blender 版本后，重新导出和校验所需模型。

## 验证与范围

具体实测补丁版本、样本和校验范围见 [兼容性说明](https://github.com/Reguluz/FDKGraphicExporter/blob/v1.3.0/docs/COMPATIBILITY.md)。其他 Blender 分支没有列为支持，不能据此认为所有 4.x、5.x 均兼容。

游戏支持范围仍为已验证的《黎之轨迹 II》和《空之轨迹 the 2nd》PC 安装版本。模型保留骨架与蒙皮；不导出动画轨道，不重建游戏 IK、动态骨骼或完整 FDK 着色器。

工具合集不含游戏素材、导出结果、预览、运行日志或个人配置。对应源码与第三方许可信息仍随版本提供。
