# 角色台词翻译器（Translator for Characters）

中文 → 日文的游戏台词本地化工具。带**角色风格 / 术语表 / 世界观**知识库，译文可以直接交给 AI 质检，还能从语料里自动提取知识库。
> **免责声明**：本项目为个人练习作品，与任何游戏公司无关，不包含任何游戏文本或数据；示例角色与术语均为虚构。


## 功能

- **翻译**：中文台词 → 日文，按角色分批处理，自动匹配术语表、角色风格与世界观
- **质检**：AI 逐维度打分（语义准确 / 日语自然度 / 角色一致性 / 术语 / 格式 / 世界观一致性），并统计术语正确率与参照译文相似度
- **提取知识**：从中文或日文语料中提取世界观、专有术语、角色说话风格，可选 1/2/3 级（仅世界观 / +术语 / +角色），写入 `knowledge_base/`
- **批量翻译**：不走界面，直接处理一整个目录的 csv
- **角色风格预测**（测试版）：按 `character_descriptions.txt` 的角色描述推测说话风格，输出 `predicted_characters.json`
- **失败可见**：行数对不齐时先按原文行结构重新对齐、再重试，最后拆小批次重译；仍不匹配才列出可能错位的行并生成告警报告

## 环境要求

- Windows
- Python 3.11（安装时勾选 Add to PATH）
- **项目路径不能含中文**（PySide6 / sentence-transformers 对非 ASCII 路径不友好）
- 一个兼容 OpenAI 接口的模型服务（DeepSeek、通义千问等）

## 安装

双击 `install.bat`：自动创建 `venv` 并从清华源安装依赖。

手动安装：

```powershell
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

## 配置

首次启动软件会自动弹出「配置 API」窗口，填入模型名与 API Key 后即可开始；也可以直接编辑 `.env`：

| 变量 | 说明 | 默认值 |
| --- | --- | --- |
| `OPENAI_API_KEY` | API Key | 空 |
| `OPENAI_API_BASE` | 接口地址 | `https://api.deepseek.com/v1` |
| `OPENAI_MODEL_NAME` | 模型名 | `deepseek-v4-flash` |
| `EMBED_MODEL` | 本地语义检索模型 | `paraphrase-multilingual-MiniLM-L12-v2` |
| `MAX_TERMS_IN_PROMPT` | 单次注入提示词的术语上限 | `80` |
| `TRANSLATE_BATCH_SIZE` | 每批翻译的台词条数 | `50` |
| `CORPUS_SUFFIX` | 语料目录后缀：留空读 `chinese_csv`/`japanese_csv`；填 `_full` 读 `chinese_csv_full`/`japanese_csv_full` | 空 |

`.env` 模板里还有下面几项，当前代码并没有读取（早期版本遗留），留空即可：

| 变量 | 说明 |
| --- | --- |
| `RAG_TOP_K` | 语义检索的召回条数，当前未使用 |
| `RAG_THRESHOLD` | 语义检索的相似度阈值，当前未使用 |
| `CHARACTER_TOP_K` | 角色风格检索条数，当前未使用 |
| `TERM_BATCH_SIZE` | 术语分批大小，当前未使用 |

再填写以下资料（前三项也可留空，改用「提取知识」自动生成）：

- `world_setting.txt` —— 世界观（中文）
- `terms.csv` —— 术语，格式 `chinese,japanese`
- `terms_blacklist.txt` —— 人工否决的术语候选，提取知识时跳过（每行一个中文词，`#` 开头是注释）
- 术语自动过滤：提取知识时会丢掉"语料里没有对应日文写法（模型自创）"和"中日同形但语料里也没有
  支撑（希望→希望）"的候选；语料里确实这么写的同形词（磁石→磁石）会留下，带引号的词保持原符号
  （「白鸦」→「白鴉」）。想强制保留某个写法就写进 `terms.csv`（人工表不过滤）
- `characters.json` —— 角色说话风格（也可以用"提取知识"自动生成；仓库自带两个示例角色，可直接替换）

## 使用

双击 `start.bat` 启动界面。按钮分别是：配置 API / 翻译 / 译文质检 / 提取知识 / 终止任务。

详细操作流程见 `使用前请阅读我readme.txt`。

原文可手动输入，也可点「导入原文」（txt 为 `【角色名】台词`，csv 为 `角色名,台词`）；不带角色名也能翻译，但语气准确度会下降。

译文保存到 `outputs/`：手动输入或导入 txt 时存为 txt；导入 csv 时只存 csv（不存 txt）。

**批量翻译**：把 csv（格式 `角色名,台词`）放进 `inputs/`，双击 `batch_translate.bat`，译文输出到 `outputs/batch/`；已翻译过的文件会自动跳过。

## 目录结构

```
app_core.py            翻译与质检核心：分批、知识注入、存盘
prompts.py             所有 AI 提示词集中在这里
extract_knowledge.py   知识提取
gui_main.py            主界面
gui_translate.py       翻译子进程（由界面调用）
gui_review.py          质检子进程
gui_extract.py         知识提取子进程
batch_translate.py     命令行批量翻译
predict_character.py   角色风格预测（测试版）
eval_translate.py      提示词评测（输入 CSV 对比参照 CSV，逐行相似度）
test_core.py           核心函数离线测试
test_warnings.py       失败告警的离线回归测试
terms.csv              术语表
terms_blacklist.txt    人工否决的术语候选（提取知识时跳过）
characters.json        角色风格
world_setting.txt      世界观
knowledge_base/        知识库（翻译与质检读取 knowledge_latest.json）
inputs/                批量翻译输入
outputs/               所有输出（译文、质检报告、告警报告）
chinese_csv*/          中文语料（提取知识用）
japanese_csv*/         日文语料（提取知识用）
install.bat            一键安装
start.bat              启动界面
batch_translate.bat    启动批量翻译
predict_character.bat  启动角色风格预测
predicted_characters.json  角色风格预测的输出
```

## 修改提示词

所有提示词都在 `prompts.py`，业务代码里只出现 `P.XXX` 这样的引用：

- 不带参数的提示词是**大写常量**，例如 `ANALYSIS_SYSTEM`
- 带参数的提示词是**小写函数**，函数名下的小写参数就是原 f-string 里的占位名，例如 `scene_terms_jp(sample)`

改完保存即可，不用动其它文件。

## 失败告警与人工核对

翻译过程中，模型返回的行数与原文不一致时，程序会依次自动处理：

1. 按原文的行结构重新切分（模型漏写【角色名】、把多行并成一段时用）
2. 带上更强的规则重试一次
3. 把该批次拆成两半分别重译（批次越小越不容易丢行）

以上都失败时，日志才会逐条列出警告，并在 `outputs/` 下生成 `translation_warnings_时间戳.txt`：

- 某批次仍不一致（按行号强行对齐，可能错位；报告里会列出疑似错位的原/译对照）
- 个别行翻译失败（结果中标为 `[翻译失败]`）

一切正常时日志显示 `✅ 全部批次按行对齐，无失败`；发生过自动修复时会另外显示 `ℹ️ 自动修复：...`。

## 开发

```powershell
venv\Scripts\python.exe test_core.py
venv\Scripts\python.exe test_warnings.py
```

两个测试都是离线的（用 stub 顶替模型调用），**不需要 API Key，也不产生费用**。

要量化对比提示词改动，用评测脚本（真实调用模型，会产生费用）：

```powershell
venv\Scripts\python.exe eval_translate.py -i dialog_input.csv -r dialog_reference.csv -n 3 --quiet
venv\Scripts\python.exe eval_translate.py -i chinese_csv/dialog_cn_03.csv -r japanese_csv/dialog_jp_03.csv --worst 30
```

报告和译文落在 `outputs/eval/`。参照取自人工参考译文，可信度高，看平均分有没有退步就够了。

## 作者

清泉

## 许可

MIT License，见 `LICENSE`。
