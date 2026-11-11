# 开发与源码构建

普通使用请从 [Release](https://github.com/Reguluz/FDKGraphicExporter/releases/latest) 下载工具合集，按 [首页操作说明](../README.md) 使用。本页面向需要修改代码、运行测试或自行构建的开发者。

本页的命令和源码路径都针对下方克隆得到的源码仓库。Release 运行包不包含 `setup.ps1`、构建脚本或测试源码，不能在运行包目录中执行这些开发步骤。v1.2.1 对应源码见 [固定版本仓库](https://github.com/Reguluz/FDKGraphicExporter/tree/v1.2.1)。

## 准备开发环境

- Windows 64 位、PowerShell 和 Git。
- Python **3.11 64 位**，带 `venv` 和 `pip`。
- Blender **4.5 LTS**；导出器检查 4.5 版本，原有模型流程已验证环境为 4.5.3。
- Windows 的 .NET Framework 4.x C# 编译器。构建脚本使用 `Microsoft.NET/Framework64/v4.0.30319/csc.exe`。

测试导出功能时需自行准备受支持的游戏安装。源码仓库、Release 和测试源码均不提供游戏素材或导出结果。

## 获取源码

在 PowerShell 中执行：

```powershell
git clone --recurse-submodules https://github.com/Reguluz/FDKGraphicExporter.git
cd FDKGraphicExporter
powershell -ExecutionPolicy Bypass -File .\setup.ps1 -Python 'python'
```

`python` 应指向 Python 3.11，也可通过 `-Python` 传入解释器完整路径。若克隆时未下载子模块，先执行：

```powershell
git submodule update --init --recursive
```

首次安装需要联网获取固定版本的上游子模块与 [Python 依赖](https://github.com/Reguluz/FDKGraphicExporter/blob/v1.2.1/requirements.txt)。`setup.ps1` 创建本地 `tools/python` 虚拟环境，安装依赖，再生成：

- `fdk_toolkit/FDK_Asset_Tool.exe`
- `fdk_toolkit/FDK_Blender_Addon.zip`

配置文件不存在时，脚本将 `fdk_toolkit/profiles.example.json` 复制为 `fdk_toolkit/profiles.json`；已有配置保留。可从 GUI 设置游戏与 Blender 路径，也可编辑 JSON，路径建议使用 `/`。

## 再次构建

依赖已安装时，复用环境重新构建：

```powershell
powershell -ExecutionPolicy Bypass -File .\setup.ps1 -SkipInstall
```

只准备环境而暂不构建时使用 `-SkipBuild`。仅重新打包插件：

```powershell
.\tools\python\Scripts\python.exe .\fdk_toolkit\blender_addon\package_addon.py
```

默认插件包不读取本机路径配置，保留空资源库预设。`--local-paths` 仅供制作个人本机安装包，会把本机路径写进 ZIP，不应用于公开分发。

开发环境中的 `tools/python` 是虚拟环境，可能依赖创建它的本机 Python 安装。**不能把开发虚拟环境直接复制成便携发行包。** 公开发行包使用独立运行环境，打包流程与开发环境分开。

## 构建 Release 工具合集

准备好上述开发环境后，在仓库根目录执行：

```powershell
.\tools\python\Scripts\python.exe .\build_release.py --version 1.2.1
```

脚本获取官方 CPython 3.11.9 Windows x64 嵌入式运行包和固定版本依赖，编译图形界面、工具及解析器，打包 Blender 插件，并生成发行包。内部 Python 工具以编译后的字节码提供；Blender 插件使用 Blender 正常安装的 ZIP 结构。打包不读取开发者的游戏配置或复制已有导出目录。

默认产物位于 `dist/`，可用 `--output` 指定另一个生成目录。用于发布的附件为工具合集 ZIP、单独插件 ZIP 和 `SHA256SUMS.txt`。合集解压根目录为 `FDKGraphicExporter-v1.2.1`，包含 `fdk_toolkit/`、`tools/`、说明及许可文件。

发行包中的 Python 运行时许可为 `licenses/Python-LICENSE.txt`，上游解析器许可为 `licenses/kuro_mdl_tool-LICENSE.txt`，Python 包许可及元数据位于 `licenses/python_packages/`。`SOURCE.txt` 指向对应源码版本和依赖来源。

## 代码布局

| 位置 | 内容 |
|---|---|
| `fdk_toolkit/tool/` | Windows 图形界面、资源适配、缓存、导出与验证 |
| `fdk_toolkit/blender_addon/` | Blender 插件源码及打包脚本 |
| `fdk_toolkit/tests/` | 不要求游戏素材的回归测试 |
| `fdk_toolkit/tests/integration/` | Blender 及真实资源集成测试脚本 |
| `tools/kuro_mdl_tool/` | 固定提交的上游解析器子模块 |
| `fdk_toolkit/profiles.example.json` | 可公开提交的配置模板 |

相对输出路径以 `fdk_toolkit/` 为基准。输出必须是仓库内独立子目录，与游戏目录分开；默认 `../kuro2_export` 和 `../sky2_export` 已被 Git 忽略。请保持 `fdk_toolkit` 和 `tools` 的相对位置。

## 运行测试

在仓库根目录执行：

```powershell
.\tools\python\Scripts\python.exe -m unittest discover -s fdk_toolkit/tests -p "test_*.py"
```

需要真实游戏的环境检查默认跳过。准备好本机配置、游戏和 Blender 后，可设置 `FDK_RUN_GAME_TESTS=1` 启用对应检查。

Blender 集成脚本在 `fdk_toolkit/tests/integration/`。`test_portable_addon.py` 不需要游戏素材，可使用自己的 Blender 4.5 路径运行：

```powershell
& 'C:\Program Files\Blender Foundation\Blender 4.5\blender.exe' --background --factory-startup --python-exit-code 1 --python .\fdk_toolkit\tests\integration\test_portable_addon.py
```

其他集成测试的前置资源和运行方式见脚本，不应将生成的报告、预览、模型或贴图提交到 Git。

## 源码与发行包

Git 保存源代码、测试、模板和文档；Release 附件提供工具合集、单独插件包和校验值。下载包内的 `SOURCE.txt` 标识对应版本源码与上游固定提交，`LICENSE` 和 `licenses/` 保留许可信息。修改或再分发时应一并保留相关声明。

构建输出、Python 环境、个人配置、日志、游戏素材及导出结果不进入源码提交。发布前应检查压缩包内容，并在干净的解压目录验证启动、环境检查和导出流程。
