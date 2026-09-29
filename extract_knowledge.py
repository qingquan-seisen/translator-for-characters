"""
extract_knowledge.py - 知识提取流水线（中译日版）
支持三档提取等级：
  1级：只提取世界观（最快）
  2级：提取世界观 + 术语（推荐）
  3级：提取世界观 + 术语 + 角色语气（最完整）

输入策略：
- 若 chinese_csv/ 和 japanese_csv/ 都有内容 → 双语对照提取
- 若只有其中一个有内容 → 单语提取
- 两者都为空 → 报错退出
- 目录可用 .env 的 CORPUS_SUFFIX 切换：留空 = chinese_csv / japanese_csv；
  填 _full = chinese_csv_full / japanese_csv_full（完整备用库）

文件配对：双重验证（前缀关键词 + 结尾序号）
- dialog_cn_01.csv ↔ dialog_jp_01.csv ✅
- dialog_cn_01.csv ↔ story_jp_01.csv ❌（前缀不同）
- dialog_cn_01.csv ↔ dialog_jp_02.csv ❌（序号不同）

中日双语模式要求：每个配对文件行数一致且一一对应，否则报错终止。

编码：自动探测（支持 UTF-8 BOM / UTF-8 / GBK / Shift-JIS）
输出：knowledge_base/knowledge_xxx.json
"""

import argparse
import csv
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime

os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
from dotenv import load_dotenv

from app_core import (
    API_BASE,
    API_KEY,
    MODEL,
    TERM_BRACKETS,
    chat,
    drop_bare_duplicate_terms,
    extract_json,
    load_terms,
    make_client,
    require_api_key,
    unwrap_term,
)

import prompts as P

# ==================== 路径配置 ====================
BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "knowledge_base"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

load_dotenv(BASE_DIR / ".env")

# 人工否决过的术语候选（每行一个中文词，# 开头为注释）
TERM_BLACKLIST_FILE = BASE_DIR / "terms_blacklist.txt"


def load_term_blacklist() -> set:
    """读取人工否决的日常词；文件不存在就返回空集合。"""
    if not TERM_BLACKLIST_FILE.exists():
        return set()
    try:
        content = _read_text_with_fallback(TERM_BLACKLIST_FILE, is_chinese=True)
    except Exception as e:
        log(f"  ⚠️ 读取 terms_blacklist.txt 失败（本次不做黑名单过滤）: {e}")
        return set()
    words = set()
    for line in content.splitlines():
        word = line.strip()
        if word and not word.startswith("#"):
            words.add(word)
    return words


def load_known_terms() -> Dict[str, str]:
    """已有术语表（terms.csv + 知识库），用来判断裸词是不是已有带引号版本。"""
    try:
        return load_terms()
    except Exception as e:
        log(f"  ⚠️ 读取已有术语表失败（本次只按本次结果去重）: {e}")
        return {}


# ==================== 术语候选清洗 ====================
# 模型分不清"普通词"（希望）和"术语"（死亡→死），所以用几条机械规则兜底：
#   1. 引用形态：语料里写成「白鸦」，条目就存「白鸦」→「白鴉」，保持原符号，
#      免得裸词误命中「白鸦飞过天空」这类普通用法
#   2. 语料校验：中文句里出现过、对照的日文句里却从没用过对应写法 → 模型自己编的
#      （如"命运→ケツマツ"，人工参照写的是"サダメ"），丢掉
#   3. 中日同形（"希望→希望"）要有语料支撑才保留：语料里确实这么写（磁石→磁石、
#      水晶→水晶）就留，找不到支撑就丢；"死亡→死"这种换了写法的术语不受这条影响
RUBY_PATTERN = re.compile(r"\{RUBY\[[^\]]*\]")


def strip_ruby(text: str) -> str:
    """去掉 RUBY 标注：灰{RUBY[D]暗夜之眼}眸 → 灰暗夜之眼眸。"""
    return RUBY_PATTERN.sub("", text).replace("}", "")


def quoted_form_in(word: str, corpus: str) -> str:
    """语料里若有带引号的写法就返回它（「白鸦」），没有就返回空串。"""
    if not word:
        return ""
    for lq, rq in TERM_BRACKETS:
        candidate = f"{lq}{word}{rq}"
        if candidate in corpus:
            return candidate
    return ""


def aligned_evidence(word: str, japanese: str, pairs: List[Tuple[str, str]],
                     require_quotes: Optional[bool] = None) -> Optional[bool]:
    """在对照语料里找术语证据：只看中文侧出现该词的那些行，日文侧有没有对应写法。

    True  = 有；False = 中文侧出现过但日文侧一次都没有（模型自创的写法）；
    None  = 该词（该形态）在语料里没出现过，无从判断。
    require_quotes=True 只看带引号的中文（「白鸦」），False 只看裸词，None 都看。
    """
    if not word:
        return None
    target = unwrap_term(strip_ruby(japanese))
    seen = False
    for zh, ja in pairs:
        zh_norm = strip_ruby(zh)
        ja_norm = strip_ruby(ja)
        if not ja_norm or word not in zh_norm:
            continue
        quoted = any(f"{lq}{word}{rq}" in zh_norm for lq, rq in TERM_BRACKETS)
        if require_quotes is not None and quoted != require_quotes:
            continue
        seen = True
        if target and target in ja_norm:
            return True
    return False if seen else None


# 语料目录：默认 chinese_csv / japanese_csv（小语料）
# 想跑完整备用库，在 .env 里设 CORPUS_SUFFIX=_full
CORPUS_SUFFIX = os.getenv("CORPUS_SUFFIX", "").strip()
CHINESE_DIR = BASE_DIR / f"chinese_csv{CORPUS_SUFFIX}"
JAPANESE_DIR = BASE_DIR / f"japanese_csv{CORPUS_SUFFIX}"

API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
MODEL = os.getenv("OPENAI_MODEL_NAME", "deepseek-v4-flash")
API_BASE = os.getenv("OPENAI_API_BASE", "https://api.deepseek.com/v1")



# ==================== 异常类 ====================
class FatalExtractError(Exception):
    """致命错误，必须终止任务"""
    pass


# ==================== 编码自动回退 ====================
def _read_text_with_fallback(path: Path, is_chinese: bool = True) -> str:
    """
    自动探测编码并读取文本。
    is_chinese=True: 中文文件，GBK 优先
    is_chinese=False: 日文文件，Shift-JIS 优先
    """
    if is_chinese:
        encodings = ("utf-8-sig", "utf-8", "gb18030", "gbk", "shift-jis", "cp932")
    else:
        encodings = ("utf-8-sig", "utf-8", "shift-jis", "cp932", "gb18030", "gbk")

    for encoding in encodings:
        try:
            return path.read_text(encoding=encoding)
        except (UnicodeDecodeError, LookupError):
            continue

    raise UnicodeDecodeError("unknown", b"", 0, 1, f"无法解码文件: {path}")


# ==================== 计时器 ====================
_start_time = time.time()

def format_elapsed() -> str:
    elapsed = int(time.time() - _start_time)
    if elapsed < 60:
        return f"{elapsed}秒"
    mins = elapsed // 60
    secs = elapsed % 60
    if mins < 60:
        return f"{mins}分{secs}秒"
    hours = mins // 60
    mins = mins % 60
    return f"{hours}小时{mins}分{secs}秒"


def log(msg: str):
    print(f"[LOG]{msg} (已用时 {format_elapsed()})", flush=True)


# require_api_key / make_client / chat / extract_json 统一复用 app_core 的实现，避免两份逻辑分叉


@dataclass
class CharacterProfile:
    name: str
    speaking_style: str = ""
    first_person: str = ""
    sentence_end: str = ""
    catchphrases: List[str] = field(default_factory=list)
    tone: str = ""


class KnowledgeMiningAgent:
    def __init__(self, level: int = 2):
        self.level = level  # 1=世界观, 2=世界观+术语, 3=世界观+术语+角色
        self.all_terms: Dict[str, str] = {}          # 中文 → 日文
        self.rejected_terms: Dict[str, str] = {}     # 被过滤掉的候选
        self.reject_reasons: Dict[str, str] = {}     # 候选 → 过滤原因
        self.term_blacklist = load_term_blacklist()
        self.known_terms = load_known_terms()
        self.all_characters: Dict[str, CharacterProfile] = {}
        self.scene_worldviews: Dict[str, str] = {}
        self.total_texts = 0
        self.total_scenes = 0
        self.world_setting = ""
        self.mode = ""

    # ==================== 输入模式检测 ====================
    def detect_mode(self) -> str:
        ch_has = CHINESE_DIR.exists() and any(CHINESE_DIR.glob("*.csv"))
        jp_has = JAPANESE_DIR.exists() and any(JAPANESE_DIR.glob("*.csv"))

        if ch_has and jp_has:
            return "bilingual"
        elif ch_has:
            return "ch_only"
        elif jp_has:
            return "jp_only"
        else:
            raise FileNotFoundError(
                f"chinese_csv/ 和 japanese_csv/ 都为空或不存在，无法提取知识。"
            )

    # ==================== 文件配对（双重验证） ====================
    def find_pairs(self) -> List[Tuple[str, Optional[Path], Optional[Path]]]:
        if self.mode == "bilingual":
            def parse_stem(stem: str) -> Optional[Tuple[str, str]]:
                m = re.match(r'^([a-zA-Z]+).*?_?(\d+)$', stem)
                if m:
                    return (m.group(1).lower(), m.group(2))
                return None

            ch_map: Dict[Tuple[str, str], Path] = {}
            jp_map: Dict[Tuple[str, str], Path] = {}

            for f in CHINESE_DIR.glob("*.csv"):
                key = parse_stem(f.stem)
                if key:
                    ch_map[key] = f
                else:
                    log(f"  ⚠️ 中文文件 {f.name} 无法解析（需形如 xxx_01.csv），跳过")

            for f in JAPANESE_DIR.glob("*.csv"):
                key = parse_stem(f.stem)
                if key:
                    jp_map[key] = f
                else:
                    log(f"  ⚠️ 日文文件 {f.name} 无法解析（需形如 xxx_01.csv），跳过")

            pairs = []
            for key in sorted(ch_map.keys()):
                if key in jp_map:
                    scene_name = f"{key[0]}_{key[1]}"
                    pairs.append((scene_name, ch_map[key], jp_map[key]))
                else:
                    log(f"  ⚠️ 中文文件 {ch_map[key].name} 没有对应的日文文件，跳过")

            for key in sorted(jp_map.keys()):
                if key not in ch_map:
                    log(f"  ⚠️ 日文文件 {jp_map[key].name} 没有对应的中文文件，跳过")

            log(f"📂 双语模式：共 {len(pairs)} 个配对场景")
            return pairs

        elif self.mode == "ch_only":
            ch_files = sorted(CHINESE_DIR.glob("*.csv"))
            log(f"📂 仅中文模式：共 {len(ch_files)} 个场景")
            return [(f.stem, f, None) for f in ch_files]

        else:
            jp_files = sorted(JAPANESE_DIR.glob("*.csv"))
            log(f"📂 仅日文模式：共 {len(jp_files)} 个场景")
            return [(f.stem, None, f) for f in jp_files]

    # ==================== 加载文本 ====================
    def _load_single_csv(self, csv_path: Path, is_chinese: bool = True) -> List[Dict]:
        """读取单个 CSV（自动探测编码）"""
        rows = []
        content = _read_text_with_fallback(csv_path, is_chinese=is_chinese)
        reader = csv.reader(content.splitlines())
        for row in reader:
            if len(row) < 2:
                continue
            rows.append({
                "speaker": row[0].strip(),
                "original": row[1].strip(),
            })
        return rows

    def load_scene(self, ch_file: Optional[Path], jp_file: Optional[Path]) -> List[Dict]:
        """加载场景文本（双语对照或单语）"""
        if self.mode == "bilingual":
            ch_rows = self._load_single_csv(ch_file, is_chinese=True)
            jp_rows = self._load_single_csv(jp_file, is_chinese=False)

            # 行数校验（兜底）
            if len(ch_rows) != len(jp_rows):
                raise FatalExtractError(
                    f"行数不一致：{ch_file.name}={len(ch_rows)} 行，"
                    f"{jp_file.name}={len(jp_rows)} 行。"
                    f"中日文本需要行数一致且一一对应。"
                )

            result = []
            for i in range(len(ch_rows)):
                ch = ch_rows[i]
                jp = jp_rows[i]
                speaker = ch["speaker"] if ch["speaker"] else jp["speaker"]
                result.append({
                    "speaker": speaker,
                    "chinese": ch["original"],
                    "japanese": jp["original"],
                })
            return result

        elif self.mode == "ch_only":
            ch_rows = self._load_single_csv(ch_file, is_chinese=True)
            return [{"speaker": r["speaker"], "chinese": r["original"], "japanese": ""} for r in ch_rows]

        else:  # jp_only
            jp_rows = self._load_single_csv(jp_file, is_chinese=False)
            return [{"speaker": r["speaker"], "chinese": "", "japanese": r["original"]} for r in jp_rows]

    # ==================== 世界观提取 ====================
    def extract_scene_worldview(self, texts: List[Dict], scene_name: str) -> str:
        if not texts:
            return ""

        if self.mode == "bilingual":
            sample_lines = [
                f"中: {t['chinese']}\n日: {t['japanese']}"
                for t in texts[:30] if t['chinese'] and t['japanese']
            ]
        elif self.mode == "ch_only":
            sample_lines = [t['chinese'] for t in texts[:30] if t['chinese']]
        else:
            sample_lines = [t['japanese'] for t in texts[:30] if t['japanese']]

        sample = "\n".join(sample_lines)[:4000]

        if self.mode == "jp_only":
            prompt = P.scene_worldview_jp(sample)
        else:
            prompt = P.scene_worldview_bilingual('中日对照' if self.mode == 'bilingual' else '中文', sample)
        try:
            resp = chat([
                {"role": "system", "content": "你是游戏世界观分析专家。只输出简洁描述。"},
                {"role": "user", "content": prompt},
            ], temperature=0.3)
            return resp.strip()
        except Exception as e:
            log(f"  ⚠️ 世界观提取失败: {e}")
            return ""

    # ==================== 术语提取 ====================
    def extract_scene_terms(self, texts: List[Dict]) -> Dict[str, str]:
        all_terms: Dict[str, str] = {}
        batch_size = 40

        for i in range(0, len(texts), batch_size):
            batch = texts[i:i+batch_size]

            if self.mode == "bilingual":
                sample = "\n".join([
                    f"中文: {t['chinese']}\n日文: {t['japanese']}"
                    for t in batch if t['chinese'] and t['japanese']
                ])[:10000]
                prompt = P.scene_terms_bilingual(sample, self.term_blacklist)
            elif self.mode == "ch_only":
                sample = "\n".join([t['chinese'] for t in batch if t['chinese']])[:10000]
                prompt = P.scene_terms_ch(sample, self.term_blacklist)
            else:
                sample = "\n".join([t['japanese'] for t in batch if t['japanese']])[:10000]
                prompt = P.scene_terms_jp(sample, self.term_blacklist)
            try:
                resp = chat([
                    {"role": "system", "content": "只输出JSON。"},
                    {"role": "user", "content": prompt},
                ], temperature=0.1)
                data = extract_json(resp)
                for t in data.get("terms", []):
                    if isinstance(t, dict):
                        ch = t.get("chinese", "").strip()
                        jp = t.get("japanese", "").strip()
                        if ch and jp and len(ch) >= 2 and ch not in all_terms:
                            all_terms[ch] = jp
            except Exception as e:
                log(f"    ⚠️ 术语批次失败: {e}")

        return self.filter_terms(all_terms, texts)

    def _reject(self, chinese: str, japanese: str, reason: str) -> None:
        """记下丢掉的候选和原因（结尾会打印，方便人工过目）。"""
        self.rejected_terms[chinese] = japanese
        self.reject_reasons[chinese] = reason

    def _group_rejected(self) -> Dict[str, List[str]]:
        groups: Dict[str, List[str]] = {}
        for ch in self.rejected_terms:
            groups.setdefault(self.reject_reasons.get(ch, "其他"), []).append(ch)
        return dict(sorted(groups.items(), key=lambda kv: -len(kv[1])))

    def filter_terms(self, terms: Dict[str, str], texts: Optional[List[Dict]] = None) -> Dict[str, str]:
        """过滤术语候选（规则见文件头的"术语候选清洗"）。

        texts 传本场景的对照文本，用来校验"语料里到底怎么写"；
        单语模式没有日文侧，规则 1、2 跳过；规则 3 拿不到支撑，同形条目一律丢掉。
        """
        pairs: List[Tuple[str, str]] = [
            (t.get("chinese", ""), t.get("japanese", "")) for t in (texts or [])
        ]
        zh_corpus = strip_ruby("\n".join(zh for zh, _ in pairs))
        ja_corpus = strip_ruby("\n".join(ja for _, ja in pairs))
        judge_by_corpus = bool(ja_corpus.strip())

        kept: Dict[str, str] = {}
        for ch, jp in terms.items():
            if ch in self.term_blacklist:
                self._reject(ch, jp, "人工黑名单")
                continue

            word = unwrap_term(ch)
            support = aligned_evidence(word, jp, pairs) if judge_by_corpus else None
            if judge_by_corpus:
                # 2) 日文侧完全对不上 → 模型自创的写法，丢掉
                if support is False:
                    self._reject(ch, jp, "语料里找不到对应日文写法")
                    continue
                # 1) 引号里才是术语、裸词是普通义（「白鸦」 vs 白鸦飞过天空）→ 保持原符号
                if (aligned_evidence(word, jp, pairs, require_quotes=True)
                        and aligned_evidence(word, jp, pairs, require_quotes=False) is False):
                    ch = quoted_form_in(word, zh_corpus) or ch
                    jp = quoted_form_in(unwrap_term(jp), ja_corpus) or jp

            # 3) 中日同形（希望→希望）要有语料支撑才保留
            if ch == jp and support is not True:
                self._reject(ch, jp, "中日同形，缺语料支撑")
                continue

            if ch not in kept:
                kept[ch] = jp

        # 4) 与带引号版本重复的裸词（含 terms.csv / 知识库里已有的）
        cleaned = drop_bare_duplicate_terms(kept, reference=self.known_terms)
        for ch, jp in kept.items():
            if ch not in cleaned:
                self._reject(ch, jp, "与带引号版本重复的裸词")
        return cleaned

    # ==================== 角色风格提取 ====================
    def extract_scene_characters(self, texts: List[Dict]) -> Dict[str, CharacterProfile]:
        speaker_lines: Dict[str, List[str]] = {}

        for t in texts:
            speaker = t['speaker']
            if not speaker or speaker == '？？？':
                continue
            line = t['japanese'] if t['japanese'] else t['chinese']
            if line:
                speaker_lines.setdefault(speaker, []).append(line)

        profiles = {}
        for speaker, lines in speaker_lines.items():
            if len(lines) < 3:
                continue

            # 均匀采样最多 50 条
            if len(lines) > 50:
                step = len(lines) / 50
                sample_lines = [lines[int(i * step)] for i in range(50)]
            else:
                sample_lines = lines

            sample = "\n".join(sample_lines)

            if self.mode == "ch_only":
                prompt = P.character_style_ch(speaker, sample)
            else:
                prompt = P.character_style_jp(speaker, sample)
            try:
                resp = chat([
                    {"role": "system", "content": "只输出JSON。"},
                    {"role": "user", "content": prompt},
                ], temperature=0.2)
                data = extract_json(resp)
                profile = CharacterProfile(
                    name=speaker,
                    speaking_style=data.get("style", ""),
                    first_person=data.get("first_person", ""),
                    sentence_end=data.get("sentence_end", ""),
                    catchphrases=data.get("catchphrases", []),
                    tone=data.get("tone", ""),
                )
                if data.get("jp_hint"):
                    profile.speaking_style += f" / 日文建议: {data['jp_hint']}"
                profiles[speaker] = profile
            except Exception as e:
                log(f"    ⚠️ {speaker} 分析失败: {e}")

        return profiles

    # ==================== 场景处理（按等级） ====================
    def process_scene(self, scene_name: str, ch_file: Optional[Path], jp_file: Optional[Path]) -> Tuple[str, Dict, Dict]:
        log(f"  📖 场景 {scene_name}")

        texts = self.load_scene(ch_file, jp_file)
        if not texts:
            log(f"    ⚠️ 无有效文本")
            return "", {}, {}

        self.total_texts += len(texts)
        log(f"    加载 {len(texts)} 条文本")

        # 世界观（等级 >= 1，始终执行）
        worldview = self.extract_scene_worldview(texts, scene_name)
        if worldview:
            log(f"    ✅ 世界观: {len(worldview)} 字")

        # 术语（等级 >= 2）
        terms = {}
        if self.level >= 2:
            terms = self.extract_scene_terms(texts)
            if terms:
                log(f"    ✅ 术语: {len(terms)} 个")

        # 角色（等级 >= 3）
        chars = {}
        if self.level >= 3:
            chars = self.extract_scene_characters(texts)
            if chars:
                log(f"    ✅ 角色: {len(chars)} 个")

        return worldview, terms, chars

    # ==================== 汇总世界观 ====================
    def summarize_worldview(self) -> str:
        log("🌍 汇总总世界观...")
        if not self.scene_worldviews:
            log("  ⚠️ 无场景世界观可汇总")
            return ""

        sorted_scenes = sorted(self.scene_worldviews.items())
        combined = "\n---\n".join([f"【{name}】\n{text}" for name, text in sorted_scenes])

        if len(combined) > 8000:
            combined = combined[:8000] + "\n...(后续场景略)"

        prompt = P.worldview_summary(combined)
        try:
            resp = chat([
                {"role": "system", "content": "你是游戏世界观分析专家。只输出整合后的总世界观。"},
                {"role": "user", "content": prompt},
            ], temperature=0.3)
            self.world_setting = resp.strip()
            log(f"  ✅ 总世界观: {len(self.world_setting)} 字")
            return self.world_setting
        except Exception as e:
            log(f"  ⚠️ 汇总失败: {e}")
            return ""

    def merge_characters(self, new_chars: Dict[str, CharacterProfile]):
        for name, profile in new_chars.items():
            if name not in self.all_characters:
                self.all_characters[name] = profile

    # ==================== 保存 ====================
    def save_knowledge(self, output_path: Path):
        knowledge = {
            "meta": {
                "direction": "中译日",
                "mode": self.mode,
                "level": self.level,
                "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "total_texts": self.total_texts,
                "total_scenes": self.total_scenes,
                "total_characters": len(self.all_characters),
                "total_terms": len(self.all_terms),
                "filtered_terms": len(self.rejected_terms),
                "filtered_terms_detail": self.reject_reasons,
            },
            "world_setting": self.world_setting,
            "scene_worldviews": self.scene_worldviews,
            "terms": self.all_terms,
            "characters": {
                name: {
                    "speaking_style": p.speaking_style,
                    "first_person": p.first_person,
                    "sentence_end": p.sentence_end,
                    "catchphrases": p.catchphrases,
                    "tone": p.tone,
                }
                for name, p in self.all_characters.items()
            },
        }
        output_path.write_text(
            json.dumps(knowledge, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        log(f"💾 保存: {output_path.name}")

    # ==================== 主流程 ====================
    def run(self):
        self.mode = self.detect_mode()
        mode_desc = {"bilingual": "双语对照", "ch_only": "仅中文", "jp_only": "仅日文"}[self.mode]
        level_desc = {1: "世界观", 2: "世界观+术语", 3: "世界观+术语+角色"}[self.level]

        log(f"🚀 启动知识提取（{mode_desc} / {level_desc}）")
        if self.mode == "bilingual":
            log(f"📂 中文: {CHINESE_DIR}")
            log(f"📂 日文: {JAPANESE_DIR}")
        elif self.mode == "ch_only":
            log(f"📂 中文: {CHINESE_DIR}")
        else:
            log(f"📂 日文: {JAPANESE_DIR}")

        pairs = self.find_pairs()
        if not pairs:
            log("❌ 无有效配对")
            return

        self.total_scenes = len(pairs)

        # ==================== 预校验：中日行数是否一致 ====================
        if self.mode == "bilingual":
            errors = []
            for scene_name, ch_file, jp_file in pairs:
                ch_rows = self._load_single_csv(ch_file, is_chinese=True)
                jp_rows = self._load_single_csv(jp_file, is_chinese=False)
                if len(ch_rows) != len(jp_rows):
                    errors.append(
                        f"  {scene_name}: {ch_file.name}={len(ch_rows)} 行, "
                        f"{jp_file.name}={len(jp_rows)} 行"
                    )
            if errors:
                log("❌ 中日文本需要行数一致且一一对应，以下文件不符合：")
                for e in errors:
                    log(e)
                raise FatalExtractError(f"共 {len(errors)} 个文件行数不一致，请先修复原始 CSV")
        # ================================================================

        log(f"📊 共 {self.total_scenes} 个场景")
        log("")

        log("【阶段一】场景级提取")
        log("-" * 50)

        for idx, (scene_name, ch_file, jp_file) in enumerate(pairs, 1):
            log(f"\n[{idx}/{self.total_scenes}] 场景 {scene_name}")
            try:
                worldview, terms, chars = self.process_scene(scene_name, ch_file, jp_file)

                if worldview:
                    self.scene_worldviews[scene_name] = worldview

                if terms:
                    for ch, jp in terms.items():
                        if ch not in self.all_terms:
                            self.all_terms[ch] = jp
                    for ch in terms:
                        self.rejected_terms.pop(ch, None)
                        self.reject_reasons.pop(ch, None)
                    log(f"    📌 累计术语: {len(self.all_terms)} 个")

                if chars:
                    self.merge_characters(chars)
                    log(f"    📌 累计角色: {len(self.all_characters)} 个")

            except FatalExtractError:
                raise   # 致命错误直接抛出，终止任务
            except Exception as e:
                log(f"  ⚠️ 场景 {scene_name} 处理失败（已跳过）: {e}")
                continue

        log("")
        log("-" * 50)
        log(f"✅ 阶段一完成")
        log(f"  处理场景: {len(self.scene_worldviews)}/{self.total_scenes}")
        log(f"  累计术语: {len(self.all_terms)} 个")
        log(f"  累计角色: {len(self.all_characters)} 个")
        log(f"  累计文本: {self.total_texts} 条")
        log("")

        # 阶段二：汇总总世界观
        if self.level >= 1 and self.scene_worldviews:
            log("【阶段二】全局汇总")
            log("-" * 50)
            self.summarize_worldview()
            log("-" * 50)
            log("")

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = OUTPUT_DIR / f"knowledge_{timestamp}.json"
        self.save_knowledge(output_path)

        latest_path = OUTPUT_DIR / "knowledge_latest.json"
        self.save_knowledge(latest_path)

        log("")
        log("=" * 50)
        log("📊 最终统计:")
        log(f"  等级: {level_desc}")
        log(f"  模式: {mode_desc}")
        log(f"  场景: {len(self.scene_worldviews)} 个")
        log(f"  文本: {self.total_texts} 条")
        log(f"  术语: {len(self.all_terms)} 个")
        if self.rejected_terms:
            log(f"  已过滤候选: {len(self.rejected_terms)} 个")
            for reason, words in self._group_rejected().items():
                shown = "、".join(words[:8])
                log(f"    · {reason}: {len(words)} 个 — {shown}{' …' if len(words) > 8 else ''}")
        log(f"  角色: {len(self.all_characters)} 个")
        log(f"  世界观: {len(self.world_setting)} 字")
        log("=" * 50)
        log(f"🏁 全部完成！总用时 {format_elapsed()}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--level", "-l", type=int, default=3, choices=[1, 2, 3],
                        help="提取等级: 1=世界观, 2=世界观+术语, 3=世界观+术语+角色")
    args = parser.parse_args()

    try:
        agent = KnowledgeMiningAgent(level=args.level)
        agent.run()

        print(json.dumps({
            "success": True,
            "output_dir": str(OUTPUT_DIR),
            "mode": agent.mode,
            "level": agent.level,
            "scenes": len(agent.scene_worldviews),
            "texts": agent.total_texts,
            "characters": len(agent.all_characters),
            "terms": len(agent.all_terms),
            "world_setting_len": len(agent.world_setting)
        }, ensure_ascii=False))

    except FatalExtractError as e:
        print(json.dumps({"success": False, "error": str(e)}, ensure_ascii=False))
        sys.exit(1)
    except Exception as e:
        print(json.dumps({"success": False, "error": str(e)}, ensure_ascii=False))
        sys.exit(1)


if __name__ == "__main__":
    main()