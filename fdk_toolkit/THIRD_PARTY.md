# 第三方来源与许可证

本项目使用 [GPL-3.0 许可证](../LICENSE)。第三方依赖保留各自的版权声明和许可证，游戏内容不受本项目源码许可证授权。

## MDL 解析与 glTF 转换

本项目复用 [eArmada8/kuro_mdl_tool](https://github.com/eArmada8/kuro_mdl_tool)，通过仓库中的 `tools/kuro_mdl_tool` Git 子模块引入，固定提交为：

```text
e2e6dbe47d6a917fafa7ba3e0eb2d57cdbcb8090
```

该固定版本包含 GNU GPL v3 许可证文本，见 [上游 LICENSE](https://github.com/eArmada8/kuro_mdl_tool/blob/e2e6dbe47d6a917fafa7ba3e0eb2d57cdbcb8090/LICENSE)。保留上游版权声明、源文件和 [README 致谢](https://github.com/eArmada8/kuro_mdl_tool/blob/e2e6dbe47d6a917fafa7ba3e0eb2d57cdbcb8090/README.md#credits)，不将其解析工作归为本项目独立实现。子模块源码未在本项目中改写。发行包按运行需要提供解析器，并在 `SOURCE.txt` 标明对应上游提交。

上游致谢记录了 Julian Uy、KuroTools/TwnKey、weskeryiu、lakovic、Kyuuhachi、DarkStarSword 等贡献者的格式研究和工具工作。具体来源与贡献说明以上游文档为准。

Falcom 二进制 JSON 布局参考同一子模块的 `misc/kuro_decode_bin_json.py`。本项目 `tool/asset_config.py` 接受字节输入，增加边界、循环和未知类型检查，并与实际配置及上游解码结果做过交叉检查。

## FPAC 格式参考

`tool/archive_pac.py` 的只读 FPAC 适配器依据观察到的包结构与以下公开资料实现：

- [FPACker 的 FPAC 格式说明](https://github.com/coinkillerl/FPACker#the-fpac-file-format)
- [eArmada8/kuro_dlc_tool 的 sky_extract_pac.py](https://github.com/eArmada8/kuro_dlc_tool/blob/main/sky_extract_pac.py)

适配器提供归档索引、边界校验、源包变化检测和按需读取；条目内的压缩或包装由后续输入适配层处理。上述项目作为格式参考，不随本仓库复制发布。

## 运行依赖

工具自身运行于 Python 3.11；导出和预览支持 Blender 4.5 LTS、5.2 LTS，具体实测版本见兼容性说明。Python 依赖包含 NumPy、Pillow、Zstandard、Blowfish、xxhash、pyquaternion 和 LZ4。源码构建的安装版本以 [requirements.txt](https://github.com/Reguluz/FDKGraphicExporter/blob/v1.3.0/requirements.txt) 为准；各依赖的版权声明和许可证继续适用，以对应版本自带声明为准。

Windows 工具合集随包提供独立 Python 运行环境及所需依赖，供用户解压后运行；相关许可保存在 `licenses/` 及依赖自身的许可文件中。Blender 由使用者另行安装，不包含在工具合集中。源码开发环境则由 `setup.ps1` 在开发者本地创建。

v1.3.0 工具合集采用官方 CPython **3.11.9 Windows x64 嵌入式发行包**。Python 运行时许可为 `licenses/Python-LICENSE.txt`，解析器许可为 `licenses/kuro_mdl_tool-LICENSE.txt`，依赖包的许可与元数据位于 `licenses/python_packages/`。

发行包根目录的 `SOURCE.txt` 提供本工具版本源码、固定上游提交与运行依赖来源，`LICENSE` 提供项目许可证。Git 仓库保存源码与构建说明；编译后的工具合集、单独插件包和校验值通过 GitHub Release 分发。

## 游戏内容

游戏名称仅用于标明兼容性。游戏程序、模型、纹理、动画、着色器及其他游戏内容的权利归各权利人所有。本仓库不提供这些内容，也不提供解包或导出结果。工具或上游源码的许可证不授予游戏素材的再分发权利。
