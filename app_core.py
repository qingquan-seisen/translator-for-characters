from __future__ import annotations

import os
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"

import csv
import difflib
import io
import json
import re
import statistics
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

import prompts as P

# ===================== 强制 UTF-8 编码 =====================
def _force_utf8(stream):
    """把标准流切换成 UTF-8。先 flush 旧缓冲，避免丢掉导入前已打印的内容。"""
    if stream is None or not hasattr(stream, "buffer"):
        return stream
    try:
        stream.flush()
    except Exception:
        pass
    try:
        return io.TextIOWrapper(stream.buffer, encoding="utf-8", errors="replace")
    except Exception:
        return stream


sys.stdout = _force_utf8(sys.stdout)
sys.stderr = _force_utf8(sys.stderr)

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "outputs"
OUTPUT_DIR.mkdir(exist_ok=True)
load_dotenv(BASE_DIR / ".env")

MODEL = os.getenv("OPENAI_MODEL_NAME", "deepseek-v4-flash")
API_BASE = os.getenv("OPENAI_API_BASE", "https://api.deepseek.com/v1")
API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
EMBED_MODEL = os.getenv("EMBED_MODEL", "paraphrase-multilingual-MiniLM-L12-v2")
MAX_TERMS_IN_PROMPT = int(os.getenv("MAX_TERMS_IN_PROMPT", "80"))
BATCH_SIZE = int(os.getenv("TRANSLATE_BATCH_SIZE", "50"))
# 批次行数对不齐时，最多二分重译几次（再对不齐才按行号强行对齐并告警）
MAX_SPLIT_DEPTH = int(os.getenv("TRANSLATE_SPLIT_DEPTH", "5"))
ANALYSIS_TEMPERATURE = 0.1
TRANSLATE_TEMPERATURE = 0.25
TRANSLATE_RETRY_TEMPERATURE = 0.1
REVIEW_TEMPERATURE = 0.0

KNOWLEDGE_JSON = BASE_DIR / "knowledge_base" / "knowledge_latest.json"
CHARACTERS_JSON = BASE_DIR / "characters.json"


def log_info(message: str) -> None:
    print(f"[LOG]{message}", flush=True)


def format_time(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 60}分{seconds % 60}秒" if seconds >= 60 else f"{seconds}秒"


def require_api_key() -> None:
    if not API_KEY:
        raise RuntimeError("未找到 OPENAI_API_KEY，请检查 .env")


def read_text(path: str | Path) -> str:
    p = Path(path)
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return p.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    raise UnicodeDecodeError("unknown", b"", 0, 1, f"无法读取文件: {p}")


def write_text(path: str | Path, text: str) -> None:
    Path(path).write_text(text, encoding="utf-8")


def timestamped(prefix: str, suffix: str = ".txt") -> Path:
    return OUTPUT_DIR / f"{prefix}_{datetime.now():%Y%m%d_%H%M%S}{suffix}"


# ===================== 进度计时器 =====================
class ProgressTicker:
    """进度计时器（后台线程，用 \\r 覆盖同一行）"""

    def __init__(self, prefix: str = "⌛ 翻译中"):
        self.prefix = prefix
        self._stop_event = threading.Event()
        self._thread = None
        self._start_time = 0.0

    def _run(self):
        while not self._stop_event.is_set():
            elapsed = int(time.time() - self._start_time)
            if elapsed < 60:
                time_str = f"{elapsed}秒"
            else:
                m = elapsed // 60
                s = elapsed % 60
                time_str = f"{m}分{s}秒"
            text = f"{self.prefix}（{time_str}）"
            print(f"\r[LOG]{text}", end="", flush=True)
            self._stop_event.wait(1)

    def start(self):
        self._start_time = time.time()
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=1.5)
        print("", flush=True)


# ===================== 编码自动回退 =====================
def _read_text_with_fallback(path: Path, is_chinese: bool = True) -> str:
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


CSV_SPEAKER_HEADERS = ("角色名", "说话人", "話者", "speaker", "角色", "name")
CSV_TEXT_HEADERS = ("台词", "文本", "テキスト", "text", "原文", "译文", "翻译", "翻訳")


def is_csv_header_row(row: list[str]) -> bool:
    """判断 CSV 第一行是不是表头（说话人,文本 / 話者,テキスト / 角色名,台词）。"""
    if not row:
        return False
    first_cell = row[0].strip().lower()
    second_cell = row[1].strip().lower() if len(row) > 1 else ""
    return first_cell in CSV_SPEAKER_HEADERS or second_cell in CSV_TEXT_HEADERS


def read_japanese_text(path: str | Path) -> str:
    """读日文文本（译文 / 参考译文）。日文语料多为 shift-jis，优先按日文编码解。"""
    return _read_text_with_fallback(Path(path), is_chinese=False)



# ===================== 知识库加载 =====================
def load_knowledge() -> dict:
    if not KNOWLEDGE_JSON.exists():
        return {}
    try:
        return json.loads(KNOWLEDGE_JSON.read_text(encoding="utf-8"))
    except Exception as e:
        log_info(f"⚠️ 加载知识库失败: {e}")
        return {}


# ===================== 数据加载 =====================
def load_world_setting() -> str:
    """加载世界观：
    1. 优先读 world_setting.txt（用户手动编辑）
    2. 读不到则读 JSON 的 world_setting
    3. 都没有则返回空
    """
    p = BASE_DIR / "world_setting.txt"
    if p.exists():
        try:
            content = _read_text_with_fallback(p, is_chinese=True).strip()
            if content:
                return content
        except Exception as e:
            log_info(f"⚠️ 读取 world_setting.txt 失败: {e}")

    kb = load_knowledge()
    if kb and kb.get("world_setting"):
        return kb["world_setting"]

    return ""


# ===================== 术语表清洗 =====================

# 术语里带引号写法的外层符号（「白鸦」「面具」）
TERM_BRACKETS = (("「", "」"), ("『", "』"), ("《", "》"))


def unwrap_term(text: str) -> str:
    """去掉术语最外层的引号：「白鸦」→ 白鸦。"""
    text = text.strip()
    for lq, rq in TERM_BRACKETS:
        if text.startswith(lq) and text.endswith(rq) and len(text) > 2:
            return text[1:-1].strip()
    return text


def drop_bare_duplicate_terms(terms: dict[str, str], reference: dict[str, str] | None = None) -> dict[str, str]:
    """丢弃与带引号版本重复的裸词条目。

    语料里的称号一律写成「白鸦」这种带引号的形式，提取时却经常
    同时产出裸词条目（白鸦 → 白鴉）。裸词会误命中普通用法（「白鸦飞过天空」里的
    白鸦只是普通名词），所以只要带引号版本还在，就丢掉裸词。
    reference 传已有术语表（terms.csv + 知识库）时，本次新提取的裸词也会被已有的
    带引号版本压掉，不必等它写进 JSON 再在加载时清洗。
    角色名替换会先查带引号的原始写法，不受影响（见 resolve_speaker_name）。
    """
    pool: dict[str, str] = {}
    if reference:
        pool.update(reference)
    pool.update(terms)

    result = dict(terms)
    for ch, jp in pool.items():
        bare = unwrap_term(ch)
        if bare == ch:
            continue
        counterpart = pool.get(bare)
        if counterpart is not None and unwrap_term(counterpart) == unwrap_term(jp):
            result.pop(bare, None)
    return result


def load_terms(path: str | Path | None = None) -> dict[str, str]:
    """加载术语表：JSON + terms.csv（CSV 优先）"""
    result: dict[str, str] = {}

    kb = load_knowledge()
    if kb and kb.get("terms"):
        result.update(kb["terms"])

    p = Path(path) if path else (BASE_DIR / "terms.csv")
    if p.exists():
        try:
            content = _read_text_with_fallback(p, is_chinese=True)
            reader = csv.DictReader(content.splitlines())
            for row in reader:
                ch = (row.get("chinese") or row.get("zh") or row.get("中文") or "").strip()
                jp = (row.get("japanese") or row.get("ja") or row.get("日文") or "").strip()
                if ch and jp:
                    result[ch] = jp
        except Exception as e:
            log_info(f"⚠️ 加载 terms.csv 失败: {e}")

    return drop_bare_duplicate_terms(result)


# ===================== 角色风格加载 =====================
def _read_characters_json() -> dict[str, dict]:
    """读取 characters.json（用户手动维护，优先于知识库）。"""
    if not CHARACTERS_JSON.exists():
        return {}
    try:
        raw = CHARACTERS_JSON.read_text(encoding="utf-8").strip()
        if not raw:
            return {}
        data = json.loads(raw)
        if not isinstance(data, dict):
            return {}
        result = {}
        for name, info in data.items():
            if not isinstance(info, dict):
                continue
            catchphrases = info.get("catchphrases", [])
            if not isinstance(catchphrases, list):
                catchphrases = []
            result[name] = {
                "speaking_style": str(info.get("speaking_style", "")).strip(),
                "first_person": str(info.get("first_person", "")).strip(),
                "sentence_end": str(info.get("sentence_end", "")).strip(),
                "tone": str(info.get("tone", "")).strip(),
                "catchphrases": catchphrases,
            }
        return result
    except Exception as e:
        log_info(f"⚠️ 解析 characters.json 失败: {e}")
        return {}


_CHARACTERS_CACHE: dict[str, dict] | None = None


def load_characters() -> dict[str, dict]:
    """加载角色风格（带缓存，只加载一次）。
    优先级：characters.json（手动） > knowledge_latest.json 的 characters（自动）
    """
    global _CHARACTERS_CACHE
    if _CHARACTERS_CACHE is not None:
        return _CHARACTERS_CACHE

    result: dict[str, dict] = {}

    kb = load_knowledge()
    if kb and kb.get("characters"):
        result.update(kb["characters"])

    manual = _read_characters_json()
    if manual:
        result.update(manual)

    _CHARACTERS_CACHE = result
    return result


# ===================== 语义相似度（用于质检） =====================
_EMBED_MODEL = None


def _ensure_embed_model():
    global _EMBED_MODEL
    if _EMBED_MODEL is None:
        from sentence_transformers import SentenceTransformer
        import warnings
        warnings.filterwarnings("ignore", category=UserWarning, module="huggingface_hub")
        os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
        os.environ["TOKENIZERS_PARALLELISM"] = "false"
        os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
        os.environ["HF_TOKEN"] = ""
        log_info(f"📦 加载语义模型: {EMBED_MODEL}")
        start = time.time()
        _EMBED_MODEL = SentenceTransformer(EMBED_MODEL)
        log_info(f"✅ 语义模型加载完成（{format_time(time.time() - start)}）")
    return _EMBED_MODEL


def cosine_similarity(text1: str, text2: str) -> float:
    """计算两段文本的余弦相似度（用于质检锚点）"""
    if not text1 or not text2:
        return 0.0
    model = _ensure_embed_model()
    import numpy as np
    emb = model.encode([text1, text2], convert_to_numpy=True, normalize_embeddings=True, show_progress_bar=False)
    return float(np.dot(emb[0], emb[1]))


# ===================== JSON 解析 =====================
def extract_json(text: str) -> dict:
    text = text.strip()
    candidates = [text]
    fence = re.findall(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.I | re.S)
    candidates.extend(fence)
    decoder = json.JSONDecoder()
    for candidate in candidates:
        try:
            value, _ = decoder.raw_decode(candidate.lstrip())
            if isinstance(value, (dict, list)):
                return value
        except json.JSONDecodeError:
            pass
        for match in re.finditer(r"\{", candidate):
            try:
                value, _ = decoder.raw_decode(candidate[match.start():])
                if isinstance(value, (dict, list)):
                    return value
            except json.JSONDecodeError:
                continue
    raise ValueError("AI 返回内容无法解析为 JSON")


def normalize_translation(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:text|plaintext)?\s*", "", text, flags=re.I)
        text = re.sub(r"\s*```$", "", text)
    text = re.sub(r"^(?:【译文】|译文[:：])\s*", "", text)
    return text.strip()


# ===================== 说话人解析 =====================
def _clean_speaker(name: str) -> str:
    """清洗角色名：去掉外层引号、括号内容（用于查术语、匹配知识库）"""
    name = name.strip()
    pairs = [("「", "」"), ("『", "』"), ("《", "》"), ("（", "）"), ("(", ")"), ('"', '"'), ("'", "'")]
    for lq, rq in pairs:
        if name.startswith(lq) and name.endswith(rq) and len(name) > 2:
            name = name[1:-1].strip()
    name = re.sub(r'[（(][^）)]*[）)]', '', name).strip()
    return name


def _extract_speaker_wrapper(raw_speaker: str) -> tuple[str, str]:
    """从原始角色名中提取外层包装符号。"""
    pairs = [("「", "」"), ("『", "』"), ("《", "》"), ('"', '"'), ("'", "'")]
    for lq, rq in pairs:
        if raw_speaker.startswith(lq) and raw_speaker.endswith(rq) and len(raw_speaker) > 2:
            return (lq, rq)
    return ("", "")


def resolve_speaker_name(speaker_raw: str, speaker: str, terms: dict[str, str]) -> str:
    """角色名查术语表，并保留原文自带的引号。

    查表顺序：带引号的原始写法（「面具」）→ 裸词（面具）→ 给裸词补上引号再查一次。
    第三档是为了让裸词条目被 drop_bare_duplicate_terms 清掉之后，
    CSV 里写成不带引号的角色名（面具）依然能替换成「仮面」。
    """
    translated = terms.get(speaker_raw) or terms.get(speaker)
    if not translated:
        for lq, rq in TERM_BRACKETS:
            translated = terms.get(f"{lq}{speaker}{rq}")
            if translated:
                break
    if not translated:
        return speaker

    lq, rq = _extract_speaker_wrapper(speaker_raw)
    if lq and translated.startswith(lq) and translated.endswith(rq):
        return translated
    if lq or rq:
        return f"{lq}{translated}{rq}"
    return translated


def parse_text_with_speakers(text: str) -> list[dict]:
    """解析带【角色名】标记的文本。"""
    pattern = re.compile(r'^\s*【([^】]+)】\s*(.*)$')
    results = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        m = pattern.match(line)
        if m:
            raw_speaker = m.group(1).strip()
            speaker = _clean_speaker(raw_speaker)
            results.append({
                "speaker": speaker,
                "speaker_raw": raw_speaker,
                "text": m.group(2).strip(),
            })
        else:
            if results:
                results[-1]["text"] += "\n" + line
            else:
                results.append({"speaker": "", "speaker_raw": "", "text": line})
    return results


# ===================== 构建上下文 =====================
def build_context(original: str, speakers: list[str] = None, verbose: bool = False) -> str:
    """构建上下文：世界观 + 角色风格 + 命中术语"""
    if speakers is None:
        speakers = []

    kb = load_knowledge()
    lines = []

    # 1. 世界观
    world = load_world_setting()
    if world:
        lines += ["【世界观设定】", world]

    # 2. 角色风格
    characters = load_characters()
    char_key_map = {}
    for key in characters:
        key_clean = key.strip("「」『』")
        char_key_map[key_clean] = key

    hit_speakers = []
    seen_keys = set()
    for sp in speakers:
        sp_clean = sp.strip("「」『』")
        matched_key = None
        if sp_clean in char_key_map:
            matched_key = char_key_map[sp_clean]
        elif sp in characters:
            matched_key = sp
        if matched_key and matched_key not in seen_keys:
            hit_speakers.append(matched_key)
            seen_keys.add(matched_key)

    if hit_speakers:
        lines += [
            "【角色风格（翻译时必须严格遵守）】",
            "说明：以下每个角色都有各自的说话风格。翻译时请根据每句话的【角色名】，找到对应的风格，并严格遵守该角色的第一人称、句尾和语气。"
        ]
        for sp in hit_speakers:
            c = characters[sp]
            parts = []
            if c.get("first_person"):
                parts.append(f"第一人称={c['first_person']}")
            if c.get("sentence_end"):
                parts.append(f"句尾={c['sentence_end']}")
            if c.get("tone"):
                parts.append(f"语气={c['tone']}")
            if c.get("speaking_style"):
                parts.append(f"风格={c['speaking_style']}")
            if c.get("catchphrases"):
                phrases = c["catchphrases"][:5]
                if isinstance(phrases, list):
                    phrases_str = "、".join(str(p) for p in phrases if p)
                    if phrases_str:
                        parts.append(f"口头禅={phrases_str}")
            if parts:
                display_name = sp.strip("「」『』")
                lines.append(f"- {display_name}: {', '.join(parts)}")

    # 3. 命中术语
    terms = load_terms()
    hit_terms = [(ch, jp) for ch, jp in terms.items() if ch and ch in original]
    hit_terms.sort(key=lambda x: len(x[0]), reverse=True)
    if hit_terms:
        lines += ["【命中术语（必须优先采用）】"]
        lines += [f"- {ch} → {jp}" for ch, jp in hit_terms[:MAX_TERMS_IN_PROMPT]]

    if verbose:
        if hit_speakers:
            log_info(f"📋 识别角色 {len(hit_speakers)} 个")
        if hit_terms:
            log_info(f"📌 命中术语 {len(hit_terms)} 条")
        else:
            log_info("📌 未命中任何术语")

    return "\n".join(lines)


# ===================== LLM 调用 =====================
CHAT_TIMEOUT = 300  # 单次请求超时（秒）。长批次 + 慢模型（如 qwen）用 180 秒不够，会 APITimeoutError


def make_client():
    require_api_key()
    from openai import OpenAI
    return OpenAI(api_key=API_KEY, base_url=API_BASE, timeout=CHAT_TIMEOUT, max_retries=3)


CHAT_RETRIES = 3  # 空回复 / 瞬时网络错误的重试次数


def _is_timeout_error(e: Exception) -> bool:
    """超时类异常（openai/httpx 会包装成不同名字）。"""
    if isinstance(e, TimeoutError):
        return True
    if "Timeout" in type(e).__name__:
        return True
    return "timed out" in str(e).lower()


def chat(messages: list[dict], temperature: float = 0.2) -> str:
    """调模型取回复。空回复和网络抖动重试几次再放弃。"""
    client = make_client()
    last_error = "AI 返回为空"
    for attempt in range(CHAT_RETRIES):
        try:
            response = client.chat.completions.create(
                model=MODEL,
                messages=messages,
                temperature=temperature,
            )
            content = (response.choices[0].message.content or "").strip()
            if content:
                return content
            last_error = "AI 返回为空"
        except Exception as e:
            status = getattr(e, "status_code", None)
            if status is not None and 400 <= status < 500 and status != 429:
                raise  # 参数或鉴权问题，重试没意义
            if _is_timeout_error(e) and attempt >= 1:
                raise  # 超时不在这里干等，交给 _translate_batch 拆小批次重译
            last_error = f"{type(e).__name__}: {e}"
        if attempt < CHAT_RETRIES - 1:
            log_info(f"⚠️ 调用失败（{last_error}），{attempt + 1} 秒后重试（第 {attempt + 2}/{CHAT_RETRIES} 次）")
            time.sleep(attempt + 1)
    raise RuntimeError(f"AI 调用失败（已重试 {CHAT_RETRIES} 次）：{last_error}")


def _merge_same_speaker(segments: list[dict]) -> list[dict]:
    """把相邻且同一说话人的段落合并成一段。"""
    merged: list[dict] = []
    for seg in segments:
        sp = seg.get("speaker", "")
        if merged and sp and merged[-1].get("speaker", "") == sp:
            merged[-1]["text"] += "\n" + seg["text"]
        else:
            merged.append(dict(seg))
    return merged


def _display_speaker(seg: dict) -> str:
    """段落在提示词里显示的角色名（术语表替换后的名字优先）。"""
    return seg.get("speaker_translated") or seg.get("speaker", "")


def _segment_lines(text: str) -> list[str]:
    """把一个段落的文本摊成物理行（去掉空行）。"""
    return [line.strip() for line in str(text).split("\n") if line.strip()]


def _resplit_by_source(translated_segments: list[dict], batch: list[dict]) -> list[dict] | None:
    """模型漏写【角色名】、把多行并成一段时，按原文的行结构把译文重新切分。

    只在译文行数与原文一致、且带【角色名】的译文行都能和原文对上时才采用；
    否则返回 None，交给重试 / 二分重译处理，避免默默错位。
    """
    src_lines: list[tuple[str, str]] = []
    src_counts: list[int] = []
    for seg in batch:
        lines = _segment_lines(seg["text"])
        src_counts.append(len(lines))
        src_lines += [(_display_speaker(seg), line) for line in lines]

    trans_lines: list[tuple[str, str]] = []
    for seg in translated_segments:
        for idx, line in enumerate(_segment_lines(seg.get("text", ""))):
            trans_lines.append((seg.get("speaker", "") if idx == 0 else "", line))

    if len(src_lines) != len(trans_lines):
        return None

    for (trans_sp, _), (src_sp, _) in zip(trans_lines, src_lines):
        if trans_sp and src_sp and _clean_speaker(trans_sp) != _clean_speaker(src_sp):
            return None

    rebuilt: list[dict] = []
    pos = 0
    for seg, count in zip(batch, src_counts):
        chunk = [line for _, line in trans_lines[pos:pos + count]]
        pos += count
        rebuilt.append({**seg, "text": "\n".join(chunk)})
    return rebuilt


def _write_warning_report(stats: dict, details: list[str], path: Path | None) -> Path:
    """把本次运行的告警写成独立报告，方便人工核对。"""
    target = timestamped("translation_warnings") if path is None else path.with_name(path.stem + ".warnings.txt")
    lines = [
        "=" * 72,
        "⚠️ 翻译告警报告",
        f"生成: {datetime.now():%Y-%m-%d %H:%M:%S}",
        "=" * 72,
        "",
        f"批次总数: {stats['batches']}  |  重试: {stats['retried']}  |  自动重新对齐: {stats['repaired']}  |  拆分重译: {stats['split']}  |  强行对齐: {stats['misaligned']}  |  失败行数: {stats['failed_lines']}",
        "",
    ]
    lines += [f"- {w}" for w in stats["warnings"]]
    if details:
        lines += ["", "【可能错位的行（原 / 译）】"] + details
    lines += ["", "=" * 72]
    write_text(target, "\n".join(lines))
    return target


# ===================== 翻译 =====================
def translate_text(text: str, save_txt: bool = True) -> tuple[str, Path | None, float, dict]:
    """分批翻译带【角色名】的文本。"""
    start = time.time()

    segments = parse_text_with_speakers(text)
    if not segments:
        raise ValueError("文本为空")

    # 预处理：用术语表替换角色名（保留原始包装）
    terms = load_terms()
    for seg in segments:
        if seg["speaker"]:
            seg["speaker_translated"] = resolve_speaker_name(
                seg.get("speaker_raw", ""), seg["speaker"], terms
            )
        else:
            seg["speaker_translated"] = ""

    log_info(f"📖 解析出 {len(segments)} 个段落")

    # 预计算第一批的 batch_text，用于提前输出日志
    total_batches = (len(segments) + BATCH_SIZE - 1) // BATCH_SIZE
    if total_batches > 0:
        first_batch = segments[0:BATCH_SIZE]
        first_batch_lines = []
        first_batch_speakers = set()
        for seg in first_batch:
            if seg["speaker"]:
                first_batch_speakers.add(seg["speaker"])
                display_sp = seg["speaker_translated"] if seg["speaker_translated"] else seg["speaker"]
                first_batch_lines.append(f"【{display_sp}】{seg['text']}")
            else:
                first_batch_lines.append(seg["text"])
        first_batch_text = "\n".join(first_batch_lines)
        build_context(first_batch_text, speakers=list(first_batch_speakers), verbose=True)

    # 启动读秒
    ticker = ProgressTicker("⌛ 翻译中")
    ticker.start()

    try:
        results: list[dict] = []
        counters = {"retried": 0, "repaired": 0, "split": 0, "misaligned": 0, "failed_lines": 0}
        misaligned_details: list[str] = []

        def _translate_batch(batch: list[dict], tag: str, depth: int = 0) -> list[dict]:
            """翻译一个批次，返回与 batch 等长的结果；对不齐时自动修复或二分重译。"""
            batch_lines = []
            batch_speakers = set()
            for seg in batch:
                if seg["speaker"]:
                    batch_speakers.add(seg["speaker"])
                    batch_lines.append(f"【{_display_speaker(seg)}】{seg['text']}")
                else:
                    batch_lines.append(seg["text"])
            batch_text = "\n".join(batch_lines)
            has_speaker = any(seg["speaker"] for seg in batch)

            context = build_context(batch_text, speakers=list(batch_speakers), verbose=False)

            def _split_on_timeout(e: Exception):
                """调用超时就拆成两半重译：批次越小越快，避免整轮失败。"""
                if _is_timeout_error(e) and len(batch) > 1 and depth < MAX_SPLIT_DEPTH:
                    mid = len(batch) // 2
                    counters["split"] += 1
                    log_info(f"⚠️ {tag} 调用超时（{type(e).__name__}），改拆成 {mid} + {len(batch) - mid} 行分别重译")
                    return (
                        _translate_batch(batch[:mid], f"{tag}上半", depth + 1)
                        + _translate_batch(batch[mid:], f"{tag}下半", depth + 1)
                    )
                raise

            # 第一次 LLM 调用（逐句分析）
            try:
                analysis = chat([
                    {"role": "system", "content": P.ANALYSIS_SYSTEM},
                    {"role": "user", "content": f"{context}\n\n【原文】\n{batch_text}"},
                ], temperature=ANALYSIS_TEMPERATURE)
            except Exception as e:
                return _split_on_timeout(e)
            try:
                analysis_text = json.dumps(extract_json(analysis), ensure_ascii=False)
            except Exception:
                analysis_text = analysis

            # 第二次 LLM 调用（翻译）
            def _translate_once(extra_rule: str = "") -> str:
                messages = [
                    {"role": "system", "content": P.TRANSLATE_SYSTEM},
                    {"role": "user", "content": P.translate_user_prompt(context, analysis_text, batch_text, len(batch), has_speaker=has_speaker)},
                ]
                if not has_speaker:
                    messages.append({"role": "system", "content": P.NO_SPEAKER_RULE})
                if extra_rule:
                    messages.append({"role": "system", "content": extra_rule})
                temperature = TRANSLATE_TEMPERATURE if not extra_rule else TRANSLATE_RETRY_TEMPERATURE
                return chat(messages, temperature=temperature)

            def _parse(reply: str) -> list[dict]:
                return parse_text_with_speakers(normalize_translation(reply))

            def _align(translated: list[dict]) -> list[dict] | None:
                """把译文对齐成与原文等长；对不上返回 None。"""
                if len(translated) == len(batch):
                    return translated
                rebuilt = _resplit_by_source(translated, batch)
                if rebuilt is not None:
                    counters["repaired"] += 1
                    log_info(f"ℹ️ {tag} 译文漏了【角色名】，已按原文行结构重新切分对齐（角色名取原文）")
                    return rebuilt
                merged = _merge_same_speaker(translated)
                if len(merged) == len(batch):
                    counters["repaired"] += 1
                    log_info(f"ℹ️ {tag} 译文多切了行，已按原文行结构合并对齐")
                    return merged
                return None

            try:
                translated = _parse(_translate_once())
                aligned = _align(translated)
                if aligned is None:
                    log_info(f"⚠️ {tag} 段落数不匹配（原文 {len(batch)} / 译文 {len(translated)}），已重试一次")
                    counters["retried"] += 1
                    translated = _parse(_translate_once(P.RETRY_RULE))
                    aligned = _align(translated)
            except Exception as e:
                return _split_on_timeout(e)

            if aligned is not None:
                return [
                    {
                        "speaker": orig["speaker"],
                        "speaker_translated": orig["speaker_translated"],
                        "llm_speaker": trans.get("speaker", ""),
                        "original": orig["text"],
                        "translation": trans["text"],
                    }
                    for orig, trans in zip(batch, aligned)
                ]

            # 仍然对不齐：拆成两半分别重译（批次越小越不容易丢行）
            if len(batch) > 1 and depth < MAX_SPLIT_DEPTH:
                mid = len(batch) // 2
                counters["split"] += 1
                log_info(f"⚠️ {tag} 重试后仍不匹配，改拆成 {mid} + {len(batch) - mid} 行分别重译")
                return (
                    _translate_batch(batch[:mid], f"{tag}上半", depth + 1)
                    + _translate_batch(batch[mid:], f"{tag}下半", depth + 1)
                )

            # 最后兜底：按行号强行对齐
            counters["misaligned"] += 1
            n_pair = min(len(batch), len(translated))
            log_info(f"   ⚠️ {tag} 仍不匹配（原文 {len(batch)} / 译文 {len(translated)}），已按行号强行对齐，以下内容可能错位，请人工核对：")
            for i in range(max(0, n_pair - 12), n_pair):
                o = batch[i]["text"][:24]
                t = translated[i]["text"][:24]
                log_info(f"      [{i + 1}] 原: {o}")
                log_info(f"      [{i + 1}] 译: {t}")
                misaligned_details.append(f"  {tag} [{i + 1}] 原: {o}")
                misaligned_details.append(f"  {tag} [{i + 1}] 译: {t}")
            out: list[dict] = []
            for i, seg in enumerate(batch):
                if i < n_pair:
                    out.append({
                        "speaker": seg["speaker"],
                        "speaker_translated": seg["speaker_translated"],
                        "llm_speaker": translated[i].get("speaker", ""),
                        "original": seg["text"],
                        "translation": translated[i]["text"],
                    })
                else:
                    counters["failed_lines"] += 1
                    out.append({
                        "speaker": seg["speaker"],
                        "speaker_translated": seg["speaker_translated"],
                        "llm_speaker": "",
                        "original": seg["text"],
                        "translation": "[翻译失败]",
                    })
            return out

        for batch_idx in range(total_batches):
            batch = segments[batch_idx * BATCH_SIZE : (batch_idx + 1) * BATCH_SIZE]
            results += _translate_batch(batch, f"批次{batch_idx + 1}")
    finally:
        ticker.stop()

    stats = {
        "batches": total_batches,
        "retried": counters["retried"],
        "repaired": counters["repaired"],
        "split": counters["split"],
        "misaligned": counters["misaligned"],
        "failed_lines": counters["failed_lines"],
        "segments": len(results),
    }
    warnings = []
    if counters["misaligned"]:
        warnings.append(f"{counters['misaligned']} 个批次行数仍不匹配，已按行号强行对齐，可能错位")
    if counters["failed_lines"]:
        warnings.append(f"{counters['failed_lines']} 行翻译失败（结果中标记为 [翻译失败]）")
    stats["warnings"] = warnings
    if counters["retried"]:
        log_info(f"ℹ️ 有 {counters['retried']} 个批次行数不匹配，已自动重试")
    if counters["repaired"] or counters["split"]:
        log_info(
            f"ℹ️ 自动修复：重新对齐 {counters['repaired']} 个批次、"
            f"拆分重译 {counters['split']} 个批次（结果行数已与原文一致）"
        )
    if warnings:
        log_info("⚠️ 本次运行需要人工核对：")
        for w in warnings:
            log_info(f"   - {w}")
    else:
        log_info("✅ 全部批次按行对齐，无失败")

    # 拼接输出
    output_lines = []
    for r in results:
        original_sp = r.get("speaker", "")
        translated_sp = r.get("speaker_translated", "")
        llm_sp = r.get("llm_speaker", "")

        # 原文没有【角色名】时，模型自行猜测的角色名一律丢弃，只输出译文
        if not original_sp:
            output_lines.append(r["translation"])
            continue

        if translated_sp and translated_sp != original_sp:
            sp = translated_sp
        else:
            sp = llm_sp or original_sp

        if sp:
            output_lines.append(f"【{sp}】{r['translation']}")
        else:
            output_lines.append(r["translation"])
    result_text = "\n".join(output_lines)

    elapsed = time.time() - start

    if save_txt:
        path = timestamped("translation_results")
        write_text(path, result_text)
        log_info(f"💾 译文已保存: {path.name}")
        log_info(f"⏱️ 总用时: {format_time(elapsed)}")
        if warnings:
            report = _write_warning_report(stats, misaligned_details, path)
            log_info(f"⚠️ 告警报告已保存: {report.name}")
        return result_text, path, elapsed, stats
    else:
        log_info(f"⏱️ 总用时: {format_time(elapsed)}")
        if warnings:
            report = _write_warning_report(stats, misaligned_details, None)
            log_info(f"⚠️ 告警报告已保存: {report.name}")
        return result_text, None, elapsed, stats


# ===================== 质检 =====================
def load_reference_file(path: Optional[str]) -> Optional[str]:
    """加载参考译文。支持 TXT 和 CSV。"""
    if not path:
        return None

    p = Path(path)
    if p.suffix.lower() == ".csv":
        try:
            content = read_japanese_text(p)
            reader = csv.reader(content.splitlines())
            lines = []
            first = True
            for row in reader:
                if len(row) < 2:
                    continue
                if first:
                    first = False
                    if is_csv_header_row(row):
                        continue
                speaker = row[0].strip()
                text = row[1].strip()
                if speaker and text:
                    lines.append(f"【{speaker}】{text}")
            return "\n".join(lines)
        except Exception as e:
            log_info(f"⚠️ 解析 CSV 参考译文失败: {e}")
            return None
    else:
        text = read_text(path).strip()
        m = re.search(r"【译文】\s*(.*?)(?=\n【|$)", text, flags=re.S)
        return m.group(1).strip() if m else text


# ===================== 与参考译文的字面比对 =====================
def line_similarity(translation: str, reference: str) -> float:
    """两行的字面相似度（difflib，字符级，0~1）。评测脚本与质检共用这一份。

    同义改写、长短差异都会被扣分，所以只适合在同一批语料里做相对比较
    （哪个版本更好、哪几行偏得最远），不要当成绝对的质量分。
    """
    if not translation and not reference:
        return 1.0
    return difflib.SequenceMatcher(None, translation or "", reference or "").ratio()


def _split_for_compare(text: str) -> list[str]:
    """拆成可比对的行：带【角色名】就按段落，否则按物理行。"""
    segs = [s.get("text", "").strip() for s in parse_text_with_speakers(text or "")]
    if len(segs) <= 1:
        return [line.strip() for line in (text or "").splitlines() if line.strip()]
    return segs


def compare_with_reference(translation: str, reference: str, worst: int = 5) -> dict:
    """逐行比对译文与参考译文，返回平均字面相似度和偏离最大的几行。

    {"lines", "text_lines", "reference_lines", "mean", "worst": [(行号, 分数, 译, 参)]}
    """
    got = _split_for_compare(translation)
    ref = _split_for_compare(reference)
    count = min(len(got), len(ref))
    scores = [line_similarity(got[i], ref[i]) for i in range(count)]
    ranked = sorted(
        [(i + 1, scores[i], got[i], ref[i]) for i in range(count)],
        key=lambda item: item[1],
    )[:worst]
    return {
        "lines": count,
        "text_lines": len(got),
        "reference_lines": len(ref),
        "mean": statistics.mean(scores) if scores else 0.0,
        "worst": ranked,
    }


class TermChecker:
    def __init__(self, terms: Optional[dict[str, str]] = None):
        self.terms = terms if terms is not None else load_terms()

    def check(self, original: str, translation: str) -> dict:
        hits = []
        for ch, jp in sorted(self.terms.items(), key=lambda x: len(x[0]), reverse=True):
            if ch in original and ch != jp:
                ok = jp in translation
                hits.append({"chinese": ch, "expected": jp, "present": ok})
        return {
            "total": len(hits),
            "correct": sum(1 for h in hits if h["present"]),
            "incorrect": sum(1 for h in hits if not h["present"]),
            "issues": [h for h in hits if not h["present"]],
        }


def ai_review(original: str, translation: str, reference: Optional[str]) -> dict:
    speakers = []
    for m in re.finditer(r'【([^】]+)】', original):
        speakers.append(_clean_speaker(m.group(1)))
    context = build_context(original, speakers=speakers)
    reference_section = ""
    if reference:
        reference_section = f"\n【人工参考译文】\n{reference}\n\n参考译文是权威依据，请以它为准评判译文；译文与参照不一致处，按参照修正。"
    prompt = P.review_user_prompt(context, original, translation, reference_section)
    raw = chat([
        {"role": "system", "content": P.REVIEW_SYSTEM},
        {"role": "user", "content": prompt},
    ], temperature=REVIEW_TEMPERATURE)
    data = extract_json(raw)
    defaults = {
        "accuracy": 0, "naturalness": 0, "character_consistency": 0,
        "terminology": 0, "format_consistency": 0, "world_consistency": 0,
        "overall_score": 0, "severity": "major", "suggestions": [],
        "final_version": translation,
    }
    defaults.update(data)
    if not isinstance(defaults["suggestions"], list):
        defaults["suggestions"] = [str(defaults["suggestions"])]

    defaults["final_version"] = normalize_translation(str(defaults.get("final_version") or translation))
    return defaults


def full_review(original: str, translation: str, reference: Optional[str]) -> tuple[str, dict]:
    def _pad_label(label: str, target_width: int = 12) -> str:
        width = 0
        for ch in label:
            width += 2 if ord(ch) > 0x7F else 1
        padding = max(0, target_width - width)
        return label + " " * padding

    term_report = TermChecker().check(original, translation)
    cosine = cosine_similarity(translation, reference) if reference else None
    line_cmp = compare_with_reference(translation, reference) if reference else None
    ai = ai_review(original, translation, reference)

    report = []
    report += ["=" * 72, "📊 综合质检报告", f"生成: {datetime.now():%Y-%m-%d %H:%M:%S}", "=" * 72, ""]

    report += ["【术语一致性】"]
    if not term_report["total"]:
        report.append("  未命中术语")
    else:
        report.append(f"  命中: {term_report['total']} | 正确: {term_report['correct']} | 问题: {term_report['incorrect']}")
        for issue in term_report["issues"]:
            report.append(f"  • 「{issue['chinese']}」建议使用「{issue['expected']}」")

    if cosine is not None:
        report += ["", "【参考译文吻合度】", f"  语义相似度（整段余弦）: {cosine * 100:.1f}%"]
        if line_cmp and line_cmp["lines"]:
            report.append(f"  字面相似度（逐行平均）: {line_cmp['mean'] * 100:.1f}%  共 {line_cmp['lines']} 行")
            if line_cmp["text_lines"] != line_cmp["reference_lines"]:
                report.append(f"  ⚠️ 行数不一致（译文 {line_cmp['text_lines']} / 参照 {line_cmp['reference_lines']}），"
                              f"只比对前 {line_cmp['lines']} 行")

    report += ["", "【AI质量评分】"]
    labels = [
        ("accuracy", "准确性"),
        ("naturalness", "自然度"),
        ("character_consistency", "角色一致性"),
        ("terminology", "术语一致性"),
        ("format_consistency", "格式一致性"),
        ("world_consistency", "世界观一致性"),
        ("overall_score", "综合评分"),
    ]
    for key, label in labels:
        report.append(f"  {_pad_label(label)} {ai.get(key, 0)}/100")

    severity_map = {
        "pass": "完美",
        "minor": "优秀",
        "major": "良好",
        "critical": "不及格"
    }
    severity_value = ai.get('severity', 'major')
    severity_display = severity_map.get(severity_value, severity_value)
    report.append(f"  {_pad_label('综合评价')} {severity_display}")

    if ai.get("suggestions"):
        report += ["", "【改进建议】"]
        report.extend([f"  • {s}" for s in ai["suggestions"]])

    if ai.get("final_version") and ai["final_version"] != translation:
        report += ["", "【建议译文】", ai["final_version"]]

    report += ["", "=" * 72]
    return "\n".join(report), {"term": term_report, "cosine": cosine, "line": line_cmp, "ai": ai}


def review_text(original: str, translation: str, reference_file: Optional[str] = None) -> tuple[str, Path, float]:
    start = time.time()
    reference = load_reference_file(reference_file)

    if reference:
        _ensure_embed_model()

    ticker = ProgressTicker("⌛ 质检中")
    ticker.start()

    try:
        report, _ = full_review(original, translation, reference)
    finally:
        ticker.stop()

    path = timestamped("review_results")
    write_text(path, report)
    elapsed = time.time() - start
    log_info(f"💾 质检报告已保存: {path.name}")
    log_info(f"⏱️ 质检用时: {format_time(elapsed)}")
    return report, path, elapsed
