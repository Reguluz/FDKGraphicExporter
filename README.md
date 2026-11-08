# FDKGraphicExporter

用于 Falcom FDK 游戏资源的 **Windows 图形导出工具与 Blender 预览插件**。从用户自己的游戏安装目录读取模型和贴图，导出带骨架、蒙皮的 FBX、PNG 贴图与材质记录，并在 Blender 中恢复材质连接和预览场景。

当前已适配并实测《黎之轨迹 II》和《空之轨迹 the 2nd》的特定 PC 安装版本。不同游戏通过各自的输入适配器共用转换流程；使用 FDK 的其他游戏、其他发行版本仍需单独验证。

**本仓库只发布工具源码、配置模板和文档。** 不包含游戏程序、游戏素材、解包数据、导出模型或贴图、预览工程、测试输出、资源清单，也不提交 EXE、插件 ZIP 或 Python 运行环境。EXE 和 ZIP 均由使用者在本地构建。

## 支持范围

| 游戏 | 已验证输入 | 默认本地输出 |
|---|---|---|
| 黎之轨迹 II | 散文件、CLE 包装、MDL v2、文本材质配置 | `kuro2_export/` |
| 空之轨迹 the 2nd | FPAC 归档、LZ4 图片、MDL v4/v5、二进制材质配置 | `sky2_export/` |

《空之轨迹 the 2nd》指重制版，不是原版《空之轨迹 SC》。已验证内容、格式差异和剩余限制见 [兼容性说明](docs/COMPATIBILITY.md)。

模型保留骨架与蒙皮；当前流程不导出动画轨道，不重建游戏 IK、动态骨骼或完整 FDK 着色器。Blender 材质用于预览，不保证与游戏画面完全一致。

## 准备环境

- Windows 64 位、PowerShell 和 Git。
- Python **3.11 64 位**，带 `venv` 和 `pip`。
- Blender **4.5 LTS**。导出器会检查 4.5 版本；已验证环境为 4.5.3。
- Windows 的 .NET Framework 4.x C# 编译器；构建脚本使用系统的 `Microsoft.NET/Framework64/v4.0.30319/csc.exe`。
- 用户自行安装的受支持游戏，以及用于保存导出文件的磁盘空间。

Python 依赖包含 NumPy、Pillow、Zstandard、Blowfish、xxhash、pyquaternion 和 LZ4，由安装脚本按 [requirements.txt](requirements.txt) 安装。首次安装需要网络连接以获取子模块和依赖。

## 获取与构建

在 PowerShell 中执行：

```powershell
git clone --recurse-submodules https://github.com/Reguluz/FDKGraphicExporter.git
cd FDKGraphicExporter
powershell -ExecutionPolicy Bypass -File .\setup.ps1 -Python 'python'
```

上面的 `python` 应指向 Python 3.11；也可以把 `-Python` 改为该解释器的完整路径。若克隆时没有下载子模块，在仓库根目录执行：

```powershell
git submodule update --init --recursive
```

`setup.ps1` 准备本地 `tools/python` 虚拟环境、安装依赖，并构建图形入口和 Blender 插件包。本机配置不存在时，脚本自动将 `profiles.example.json` 复制为 `profiles.json`，已有配置则保留。构建完成后得到：

- `fdk_toolkit/FDK_Asset_Tool.exe`
- `fdk_toolkit/FDK_Blender_Addon.zip`

已经安装依赖后，可用以下命令复用环境并重新构建：

```powershell
powershell -ExecutionPolicy Bypass -File .\setup.ps1 -SkipInstall
```

只准备环境而暂不构建时，可使用 `-SkipBuild`。EXE 需要同一仓库中的 `fdk_toolkit/tool`、`tools/python` 和 `tools/kuro_mdl_tool`，不能单独复制 EXE 后运行。Blender 需另行安装。

## 配置与第一次导出

1. 编辑安装脚本生成的 `fdk_toolkit/profiles.json`。该文件是本机配置，已被 Git 忽略；也可手动从 `profiles.example.json` 复制创建。
2. 填写 `blender` 的 `blender.exe` 路径，以及各游戏的 `game` 安装根目录。JSON 路径可使用 `/`，使用 `\` 时需要写成 `\\`。保留模板中的游戏 ID、适配器和格式信息。
3. 双击本地生成的 `fdk_toolkit/FDK_Asset_Tool.exe`。选择游戏，确认路径，点击「检查环境」。也可通过窗口中的「浏览…」和「保存设置」配置路径。
4. 点击「扫描模型目录」建立索引。扫描不会自动导出模型。
5. 搜索并勾选模型，选择「仅导出勾选的模型」，点击「开始导出」。首次使用先导出少量样本，检查成功后再扩大范围。
6. 在 Blender 安装本地生成的 `FDK_Blender_Addon.zip`，按 [插件说明](fdk_toolkit/BLENDER_ADDON.md) 选择配置文件或导出资源库。

配置中的相对路径以 `fdk_toolkit/` 为基准。默认输出 `../kuro2_export` 和 `../sky2_export` 位于仓库根目录下，互相独立且被 Git 忽略。输出必须是仓库内的独立子目录，并与游戏安装目录分开。只需配置自己持有的游戏，未使用的游戏配置不会被自动扫描。

## 目录与文档

```text
FDKGraphicExporter/
├─ setup.ps1                       # 本地环境准备与构建
├─ requirements.txt                # Python 依赖
├─ docs/COMPATIBILITY.md            # 兼容性与验证范围
├─ fdk_toolkit/
│  ├─ tool/                        # Windows 界面与导出后端源码
│  ├─ blender_addon/               # Blender 插件源码与打包脚本
│  ├─ tests/                       # 测试源码，集成测试位于 integration/
│  └─ profiles.example.json        # 可提交的配置模板
└─ tools/kuro_mdl_tool/             # 固定版本的上游 Git 子模块
```

- [导出工具使用说明](fdk_toolkit/README.md)
- [Blender 插件安装与使用](fdk_toolkit/BLENDER_ADDON.md)
- [兼容性分析与历史验证范围](docs/COMPATIBILITY.md)
- [第三方来源与许可证](fdk_toolkit/THIRD_PARTY.md)

## 许可证与内容归属

本项目使用 [GPL-3.0 许可证](LICENSE)，第三方依赖保留各自的版权与许可证。游戏目录在本工具的导出流程中保持只读。游戏内容的权利仍归各权利人所有；工具源码的发布不授予游戏素材的再分发权利。

提交代码或反馈问题时，不要附带游戏素材、导出文件、完整资源索引或含本机路径的配置与日志。
