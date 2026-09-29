# 第三方组件许可声明 / Third-Party Notices

本项目（Translator for Characters）自身源码以 **MIT License** 发布，详见 `LICENSE`。

本文件汇总本项目所依赖的第三方组件及其许可，供再分发时一并保留。本项目以源码形式分发，不打包、不内嵌上述任何第三方组件的二进制。

---

## 一、直接依赖

依赖清单以 `requirements.txt` 为准，安装时解析到的具体版本可能高于此处标注的版本下限。

| 组件 | 版本要求 | 许可 | 版权持有方 |
|---|---|---|---|
| PySide6 | >= 6.7 | LGPL-3.0（另有 GPL 与商业授权可选） | The Qt Company Ltd. |
| sentence-transformers | >= 3.0 | Apache-2.0 | UKPLab / Nils Reimers |
| openai | >= 1.40 | Apache-2.0 | OpenAI |
| python-dotenv | >= 1.0 | BSD-3-Clause | Saurabh Kumar |
| numpy | >= 1.26 | BSD-3-Clause | NumPy Developers |

## 二、关于 PySide6 / Qt（LGPL-3.0）的说明

PySide6 及其运行时依赖 `shiboken6` 以 **GNU Lesser General Public License v3.0** 授权。

本项目对该组件的使用方式满足 LGPL-3.0 的要求：

- **动态使用**：以 Python `import` 方式加载已安装的 PySide6，未做静态链接，也未修改其源码。
- **可替换性**：使用者可自行以任何兼容版本替换其环境中安装的 PySide6（例如 `pip install PySide6==<版本>`），无需改动本项目代码。
- **许可原文**：LGPL-3.0 全文随 PySide6 发行包提供，位于 `PySide6/Qt/libexec/...` 之外的发行包根目录（`LICENSE.LGPLv3`），亦可自 <https://www.gnu.org/licenses/lgpl-3.0.html> 获取；Qt 源码见 <https://code.qt.io/cgit/pyside/pyside-setup.git/>。

## 三、常见间接依赖

以下组件由上述直接依赖引入，其许可随安装环境中的实际版本而定，此处仅列出常见项与典型许可：

| 组件 | 典型许可 |
|---|---|
| torch | BSD-3-Clause |
| transformers、huggingface_hub、tokenizers、safetensors | Apache-2.0 |
| scipy、scikit-learn、joblib | BSD-3-Clause |
| Pillow | MIT-CMU (HPND) |
| requests、regex、packaging | Apache-2.0 |
| tqdm | MIT 与 MPL-2.0 双许可 |
| click、jinja2、MarkupSafe、fsspec、networkx、sympy | BSD-3-Clause |
| urllib3、charset-normalizer、PyYAML | MIT |
| idna | BSD-3-Clause |
| certifi | MPL-2.0 |
| filelock | Unlicense |
| typing_extensions | PSF-2.0 |
| shiboken6 | LGPL-3.0 |

## 四、运行时下载的资源

`sentence-transformers` 会在首次使用向量化功能时从 Hugging Face Hub 下载预训练模型权重。**这些权重不随本仓库分发**，其授权取决于所选用模型自身的许可条款，需由使用者自行确认。

## 五、其他

- 各依赖包的完整许可原文，可在安装后的 `site-packages/<包名>/` 目录或该项目的官方仓库 / PyPI 页面中查阅。
- 本文件不构成法律意见；如需据此进行商业分发，建议自行复核上游组件的当期许可条款。
