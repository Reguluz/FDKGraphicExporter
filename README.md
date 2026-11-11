# FDKGraphicExporter

**FDK 游戏模型导出工具 + Blender 材质预览插件。** 支持已验证 PC 版本的《黎之轨迹 II》和《空之轨迹 the 2nd》（重制版）。在图形窗口选择模型，导出带骨架、蒙皮的 FBX 和所需贴图，再用 Blender 插件恢复材质并预览。

## 1. 下载工具

打开 **[最新版本下载页](https://github.com/Reguluz/FDKGraphicExporter/releases/latest)**，在 **Assets** 中选择：

| 下载文件 | 用途 |
|---|---|
| `FDKGraphicExporter-v1.2.1-windows-x64.zip` | **首次使用下载这个**：已编译的图形工具、独立运行依赖、Blender 插件和使用说明 |
| `FDK_Blender_Addon-v1.2.1.zip` | 单独的 Blender 插件；已有导出资源库时使用，也可单独升级 |
| `SHA256SUMS.txt` | 下载文件的 SHA-256 校验值 |

运行工具无需安装 Python、Git 或编译器。GitHub 自动生成的 **Source code** 压缩包供开发使用，请选择上面的 Windows 工具合集。

需要 **Windows 64 位、Blender 4.5 LTS** 和自行安装的受支持游戏。Blender 需另行安装，可从 [Blender 官方下载页](https://www.blender.org/releases/4-5/) 获取。工具只读取游戏目录；下载包不含游戏素材、已导出模型、贴图或个人配置。

## 2. 解压并设置路径

1. 将工具合集**完整解压**到有写入权限、空间充足的目录。不要在压缩包中直接运行，也不要只取出 EXE。
2. 打开解压目录中的 **`fdk_toolkit/FDK_Asset_Tool.exe`**。
3. 在顶部选择游戏，点击「浏览…」选择该游戏的安装根目录。
4. 选择 Blender 4.5 的 `blender.exe`，点击「保存设置」，再点击「检查环境」。

只需设置自己要处理的游戏。游戏路径和偏好保存在解压目录内的 `fdk_toolkit/profiles.json`，不需要手工编辑 JSON。

## 3. 扫描并导出模型

1. 点击「扫描模型目录」，等待列表建立。扫描只生成索引，不会自动导出全部模型。
2. 搜索模型 ID 或筛选类别，勾选需要的模型。
3. 选择「仅导出勾选的模型」，点击「开始导出」。也可选择「全部模型」处理当前目录中所有入选模型。
4. 查看进度和日志；完成后点击「查看导出文件」。需要中断时点击「停止任务」，下次可继续处理。

默认输出位于解压目录内，按游戏分别保存：

| 游戏 | 输出文件夹 |
|---|---|
| 黎之轨迹 II | `kuro2_export/` |
| 空之轨迹 the 2nd | `sky2_export/` |

每个输出根目录包含 `fbx/`、`textures/`、`materials/`、`gltf/` 和 `reports/` 等内容。请完整保留这些目录，Blender 插件需要材质记录和贴图引用。默认复用输入未变化的已完成结果，通常无需勾选重建选项。

## 4. 在 Blender 中预览

1. 在 Blender 4.5 打开「编辑 → 偏好设置 → 插件」，从菜单选择「从磁盘安装」。
2. 选择工具合集内的 **`fdk_toolkit/FDK_Blender_Addon.zip`**，或单独下载的 **`FDK_Blender_Addon-v1.2.1.zip`**，无需解压插件 ZIP。
3. 启用 **FDK 模型与材质预览**。在插件偏好中，将「游戏配置文件」指向刚才解压目录内的 `fdk_toolkit/profiles.json`，保存偏好设置。
4. 在 3D 视图按 **N → FDK**，选择游戏资源库，点击「刷新模型列表」。
5. 选择已导出的模型，点击「导入模型并恢复材质」。插件会连接贴图并可创建预览相机、灯光。

也可不选择配置文件，直接将「自定义目录」指向 `kuro2_export` 或 `sky2_export` 的根目录。安装插件本身不会生成模型，需要先完成导出。

升级插件时，先保存工程、禁用旧版本，再安装新 ZIP，启用后重启 Blender。旧工程可点击「重建当前模型材质」应用当前材质处理。

## 常见问题

| 现象 | 处理方法 |
|---|---|
| 双击后提示缺少依赖或文件 | 重新完整解压工具合集，保留 `fdk_toolkit` 和 `tools` 的相对位置；若系统提示缺少 .NET Framework，安装或启用系统的 .NET Framework 4.x |
| 环境检查找不到游戏或 Blender | 在窗口中重新选择游戏根目录与 Blender **4.5** 的 `blender.exe`，保存后再检查 |
| 扫描有模型，Blender 列表为空 | 扫描只建立索引；先导出所选模型，再在插件中选择正确资源库并刷新 |
| 模型有缺图提示 | 查看导出日志和引用记录；工具保留真实缺失状态，不用相似图片替代 |
| 切换游戏后提示资源库不匹配 | 选择该模型原本所属游戏的资源库，保持两款游戏的输出目录独立 |

角色模型保留骨架与蒙皮；当前不导出动画轨道，也不重建游戏 IK、动态骨骼或完整 FDK 着色器。预览效果不保证与游戏完全一致。其他 FDK 游戏、其他发行版本仍需单独验证。

## 更多说明

- [导出工具详细操作](fdk_toolkit/README.md)
- [Blender 插件详细说明](fdk_toolkit/BLENDER_ADDON.md)
- [兼容性与验证范围](docs/COMPATIBILITY.md)
- [v1.2.1 更新说明](docs/RELEASE_NOTES_v1.2.1.md)
- [开发与源码构建](docs/BUILDING.md)
- [第三方来源与许可证](fdk_toolkit/THIRD_PARTY.md)

项目源码采用 [GPL-3.0 许可证](LICENSE)，第三方依赖保留各自许可证。Release 提供可运行工具，Git 仓库保留对应源码与构建说明。游戏内容的权利仍归各权利人所有，工具许可证不授予游戏素材的再分发权利。
