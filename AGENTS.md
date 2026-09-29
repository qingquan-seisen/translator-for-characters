# AGENTS.md

本仓库的开发备忘：给参与维护的 AI 助手与人类协作者。
面向使用者的安装与操作说明在 `使用前请阅读我readme.txt`，两者冲突时以那份为准。

## 这个项目是什么

角色台词翻译器（Translator for Characters）：中文 → 日文的游戏台词本地化工具。
带角色风格 / 术语表 / 世界观知识库，提供翻译、AI 质检、从语料提取知识，以及命令行批量翻译。界面用 PySide6。

## 文件分工

| 文件 | 作用 |
| --- | --- |
| `app_core.py` | 翻译与质检核心：读知识库、分批、调模型、存盘、告警 |
| `prompts.py` | **所有 AI 提示词**。改提示词只动这里 |
| `prompts_lite.py` | 精简版提示词（约四成体积，接口与 `prompts.py` 完全相同）。要用就覆盖 `prompts.py`（先备份） |
| `extract_knowledge.py` | 知识提取流水线 |
| `gui_main.py` | 主界面 |
| `gui_translate.py` / `gui_review.py` / `gui_extract.py` | 由界面以子进程方式调用的入口 |
| `batch_translate.py` | 命令行批量翻译（`inputs/` → `outputs/batch/`） |
| `eval_translate.py` | 提示词评测：把输入 CSV 翻一遍，和参照 CSV 逐行比相似度 |
| `eval_stability.py` | 稳定性评测：同一输入重复翻 N 轮，量「轮间自一致性」+「对参照相似度」 |
| `predict_character.py` | 角色风格预测：给一段中文角色描述，猜日文第一人称/句尾/语气，结果待人工审核 |
| `terms_blacklist.txt` | 人工否决的术语候选，提取知识时跳过 |
| `test_core.py` / `test_warnings.py` | 离线测试 |
| `使用前请阅读我readme.txt` | **面向使用者**的说明（安装、配置、操作流程） |

## 改提示词

全部集中在 `prompts.py`：

- 不带参数的 → 大写常量，如 `ANALYSIS_SYSTEM`
- 带参数的 → 小写函数，函数参数就是原 f-string 的占位名，如 `scene_terms_jp(sample)`

业务代码里只出现 `P.XXX` 引用。**不要把提示词原文写回 `app_core.py` / `extract_knowledge.py`。**

### 改译段

- `TRANSLATE_SYSTEM` 里的【大胆改译】和紧跟的“但大胆只针对说法…”是一套，别只留前半：
  只写“大胆”会跑偏成古风腔（ご機嫌麗しゅうございます）、省略自我介绍的第一人称、
  造出「〜ましたの」这种接续错误，实测掉约 5 分
- **【高频口语与拟声词对照】优先于改译**：对照表里有的就照表写，别让模型另创说法（删掉会退步）
- **别动高频口语对照**：删掉「吓我一跳→びっくりした」「爱面子→プライドが高い」
  「你也是？→そっちも？」「哈欠→ふわぁ…」这类条目，对应行会直接掉到 15~22 分

### 温度

四个温度是 `app_core.py` 顶部常量：`ANALYSIS_TEMPERATURE=0.1`、`TRANSLATE_TEMPERATURE=0.25`、
`TRANSLATE_RETRY_TEMPERATURE=0.1`、`REVIEW_TEMPERATURE=0.0`。

- **结论：一个都不改**。曾想用温度换输出稳定性，实测换不到：降翻译温度分数掉、稳定性也没涨
  （自一致性反而随温度升高）；分析温度降到 0.0 同样无收益
- 自一致性 = 同一行不同轮译文的两两 difflib 相似度平均（跨行再平均），是“稳定性”的唯一量化口径，
  但 n=3~4 太小，只有大差值才可信
- 波动主要来自“同一句换说法”，采样温度压不住；要压得从提示词 / 术语表 / 角色风格下手
- 复现用 `eval_stability.py`：

```powershell
venv\Scripts\python.exe -X utf8 eval_stability.py -i <原文文件> -r <参照文件> --temps 0.1,0.25 -n 4
```

## 基线怎么读（评测口径）

- **“基线”有两种意思**：维护者说“目标基线 65%”“至少 60% 基线”时指的是**及格线**；
  开发汇报里的“基线”指**改动前的对照组**（同一模型 + 同一批大小 + 同一评测集下的分数）
- 换模型、换批大小、换评测集，基线都要重量，**分数不能跨条件比**
- 小改动用仓库自带的 `dialog_input.csv` + 目标模型跑 3 轮取平均；**1 分以内的差算噪声**
  （单轮波动约 ±3 分），有戏再上更大样本确认，**结论以大样本为准**
- 大样本语料不随仓库发布，需要时自备同格式的中日对照 CSV
- 「让译文更活」这类改动（如改译段）收益往往集中在某一类模型上；
  **小样本涨分不等于大样本涨分**

## 术语表与过滤

- `terms.csv` —— 人工维护的术语（`chinese,japanese`），优先级高于知识库，**不受自动过滤影响**
- `terms_blacklist.txt` —— 人工否决的日常词（如“希望”），提取知识时跳过
- 提取出的候选还要过 `extract_knowledge.filter_terms` 四条规则（对照本场景的中日文本判断）：
  1. 引用形态：语料里写「白鸦」、裸词只作普通义（白鸦飞过天空）→ 条目存「白鸦」→「白鴉」，保持原符号
  2. 语料校验：中文句里出现过、对照的日文句里从没用过对应写法 → 当作模型自创的译法，丢掉
  3. 中日同形（“希望→希望”）要有语料支撑才保留：语料里确实这么写（磁石→磁石、水晶→水晶）就留，
     找不到支撑就丢；“死亡→死”这种换了写法的术语不受这条影响
  4. 与带引号版本重复的裸词丢掉（`app_core.drop_bare_duplicate_terms`，连 terms.csv / 知识库里
     已有的引号条目一起判断）
- 想强制保留某种写法（包括中日同形的），往 `terms.csv` 加一行即可；过滤原因写在知识库 meta 的
  `filtered_terms_detail`，结尾日志也会按原因分类打印
- 角色名替换走 `app_core.resolve_speaker_name`，会先查带引号的写法，不受影响

## 语料目录与 CORPUS_SUFFIX

- 默认读 `chinese_csv/`、`japanese_csv/`
- 想换一套语料：在 `.env` 里设 `CORPUS_SUFFIX`（如 `_full`），就会改读 `chinese_csv_full/`、`japanese_csv_full/`。
  `.env` 被 git 忽略，所以切换不会进提交历史；扩展语料不随仓库发布，按同格式自备即可
- 双语提取要求中/日文件名成对（`dialog_cn_03.csv` ↔ `dialog_jp_03.csv`）且行数一致，否则报错终止

## 角色风格预测（predict_character.py）

台词语料还没有、只有中文角色设定时，先用它猜一套日文风格。

- 输入 TXT，格式是 `【角色名】` 一行 + 描述若干行（可多行）；默认读 `character_descriptions.txt`
  （文件不存在会自动生成模板，**空文件会直接报错退出**）
- 输出字段：`speaking_style` / `first_person` / `sentence_end` / `tone` / `catchphrases`，
  写进 `predicted_characters.json`（顶层键统一成 `「角色名」`，和 `characters.json` 一致）
- 结果是**预测值，不是知识库**：人工审核后挑好的合入 `characters.json`；一旦合入，
  翻译时按知识库严格执行（见上面的角色风格规则）
- 走 `.env` 里配置的模型（和翻译同一个），需要联网、会花钱，一个角色一次调用

```powershell
venv\Scripts\python.exe -X utf8 predict_character.py -f character_descriptions.txt          # 只打印
venv\Scripts\python.exe -X utf8 predict_character.py -f character_descriptions.txt --save   # 写入 JSON
```

注意：`PREDICT_SYSTEM` 内联在 `predict_character.py` 里，**有意不挪进 `prompts.py`**——
这个预测程序相对独立于翻译流水线（只借用 `app_core.chat`），提示词跟着脚本走更好维护。

## 运行与测试

用项目自带虚拟环境里的解释器：

```powershell
venv\Scripts\python.exe test_core.py
venv\Scripts\python.exe test_warnings.py
```

两个测试都用 stub 顶替模型调用，**离线、不需要 API Key、不产生费用**。改完代码先跑这两个。

改完提示词想量化对比，用评测脚本（会真实调用模型，花钱，注意轮数）：

```powershell
venv\Scripts\python.exe eval_translate.py -i dialog_input.csv -r dialog_reference.csv -n 3 --quiet
```

长文本把 `-n` 设成 1 就行。报告和译文落在 `outputs/eval/`。
参照取自人工参考译文，可信度高，看平均分有没有退步就够了。

界面 `start.bat`；批量翻译 `batch_translate.bat`。

### 评测的成本与超时

- 批大小 `TRANSLATE_BATCH_SIZE`（默认 50）直接决定输入侧 token，因为每批都要重发提示词 + 上下文。
  **想省就用大批**
- 大批的风险是单次调用超时。`chat` 对超时最多试 2 次，之后 `_translate_batch` 会**自动拆半重译**
  （层数上限 `TRANSLATE_SPLIT_DEPTH`，默认 5），大批不会再让整轮失败，只是会慢
- 不同模型的吞吐差很多，同一任务本身也有波动；要速度还是要改译质量，按自己的端点与预算选
- 评测脚本单轮失败不再中断整场（跳过继续），全部失败才退出

## 环境注意事项

- 用项目自带虚拟环境里的解释器（`venv\Scripts\python.exe`），系统 Python 通常没装依赖
- 控制台编码不是 UTF-8 时，打印中文的脚本要 `python -X utf8` 或设 `PYTHONIOENCODING=utf-8`，
  否则 `UnicodeEncodeError`
- 项目路径**不能含中文**
- PowerShell 容易吃掉引号/百分号，复杂脚本先写进文件再执行，不要用 `python -c` 硬拼
- 写文件统一 UTF-8 无 BOM

## 提交约定

- 提交消息用中文短句（如“修正 readme 说明”）
- 未经维护者同意不要提交或推送；改写已推送的历史（合并 / 改消息 / 改作者）需要 `--force-with-lease`
- `.env`、`venv/`、`outputs/` 已在 `.gitignore`，**绝不能把 API Key 提交或打包出去**
- 远程仓库：<https://github.com/qingquan-seisen/translator-for-characters>

## 分享给他人

用 `git archive` 打包含“已提交文件”的 zip，天然排除 `.env`、`venv/`、`outputs/`：

```powershell
git archive --format=zip --prefix=translator-for-characters/ -o ..\translator-for-characters.zip HEAD
```
