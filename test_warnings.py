"""离线回归测试：批次对不齐时能否自动修复、失败的批次/错位的行是否足够"显眼"。全程用 stub，不调用 API。

运行：python test_warnings.py
"""
import re
import shutil
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJ))

import app_core

TEXT = "【阿尔玛】你好。\n【艾琳】早上好。"
REPLY_OK = "【アルマ】こんにちは。\n【エリン】おはよう。"
REPLY_SHORT = "【アルマ】こんにちは。"
ANALYSIS = '{"scene_summary":"s","lines":[],"must_keep_terms":[]}'

passed = 0


def check(name, cond, extra=""):
    global passed
    if not cond:
        raise SystemExit("FAIL: %s %s" % (name, extra))
    passed += 1
    print("PASS %s" % name)


def make_stub(replies):
    seq = list(replies)

    def stub(messages, temperature=0.2):
        if "分析师" in messages[0]["content"]:
            return ANALYSIS
        return seq.pop(0) if seq else replies[-1]

    return stub


def source_of(messages):
    """取出 user 消息里【原文】标记之后的内容。"""
    for m in messages:
        if m["role"] != "user":
            continue
        lines = m["content"].splitlines()
        for i, line in enumerate(lines):
            if line.startswith("【原文】"):
                return "\n".join(lines[i + 1:]).strip()
    return ""


def snapshot(tmp):
    return {p.name for p in tmp.iterdir()}


def main():
    tmp = PROJ / "outputs" / "_warn_test"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    app_core.OUTPUT_DIR = tmp

    # 1) 完全对齐：不应有任何告警，也不应生成报告
    app_core.chat = make_stub([REPLY_OK])
    text, path, elapsed, stats = app_core.translate_text(TEXT, save_txt=False)
    check("aligned:warnings-empty", stats["warnings"] == [], stats)
    check("aligned:counters", (stats["batches"], stats["retried"], stats["misaligned"], stats["failed_lines"]) == (1, 0, 0, 0), stats)
    check("aligned:segments", stats["segments"] == 2, stats)
    check("aligned:no-files", snapshot(tmp) == set(), snapshot(tmp))

    # 2) 模型漏写【角色名】（连续同角色只写一次）：应按原文行结构重新对齐，结果完整
    app_core.MAX_SPLIT_DEPTH = 5

    def drop_marker_stub(messages, temperature=0.2):
        if "分析师" in messages[0]["content"]:
            return ANALYSIS
        out, last = [], None
        for line in source_of(messages).splitlines():
            m = re.match(r"^【([^】]+)】(.*)$", line.strip())
            if not m:
                if line.strip():
                    out.append(line.strip())
                continue
            speaker, body = m.group(1), m.group(2)
            out.append(f"【{speaker}】{body}" if speaker != last else f"JP:{body}")
            last = speaker
        return "\n".join(out)

    app_core.chat = drop_marker_stub
    text, path, elapsed, stats = app_core.translate_text(
        "【阿尔玛】一。\n【阿尔玛】二。\n【阿尔玛】三。\n【艾琳】四。", save_txt=False
    )
    check("dropped-marker:repaired", stats["repaired"] == 1, stats)
    check("dropped-marker:no-warnings", stats["warnings"] == [], stats["warnings"])
    check("dropped-marker:no-failed-lines", stats["failed_lines"] == 0, stats)
    check("dropped-marker:segments", stats["segments"] == 4, stats)
    check("dropped-marker:keeps-lines", text.count("\n") == 3, repr(text))
    check("dropped-marker:no-files", snapshot(tmp) == set(), snapshot(tmp))

    # 3) 模型真的漏翻几行：应拆小批次重译，不能留下 [翻译失败]
    def missing_lines_stub(messages, temperature=0.2):
        if "分析师" in messages[0]["content"]:
            return ANALYSIS
        lines = [l for l in source_of(messages).splitlines() if l.strip()]
        return "\n".join(lines[:2])

    app_core.chat = missing_lines_stub
    long_text = "\n".join(f"【角色{i}】第{i}句。" for i in range(1, 9))
    text, path, elapsed, stats = app_core.translate_text(long_text, save_txt=False)
    check("missing-lines:split-used", stats["split"] > 0, stats)
    check("missing-lines:no-fail-marker", "[翻译失败]" not in text, repr(text))
    check("missing-lines:segments", stats["segments"] == 8, stats)
    check("missing-lines:no-warnings", stats["warnings"] == [], stats["warnings"])
    check("missing-lines:no-files", snapshot(tmp) == set(), snapshot(tmp))

    # 4) 彻底对不齐（禁用拆分重译）：保留兜底告警 + 告警报告
    app_core.MAX_SPLIT_DEPTH = 0
    app_core.chat = make_stub([REPLY_SHORT, REPLY_SHORT])
    text, path, elapsed, stats = app_core.translate_text(TEXT, save_txt=True)
    check("misaligned:retried", stats["retried"] == 1, stats)
    check("misaligned:misaligned", stats["misaligned"] == 1, stats)
    check("misaligned:failed-lines", stats["failed_lines"] == 1, stats)
    check("misaligned:two-warnings", len(stats["warnings"]) == 2, stats["warnings"])
    check("misaligned:failed-marker", "[翻译失败]" in text, text)
    check("misaligned:path-returned", path is not None and path.exists(), path)
    rep = Path(str(path).replace(".txt", ".warnings.txt"))
    check("misaligned:report-written", rep.exists(), rep)
    body = rep.read_text(encoding="utf-8")
    check("misaligned:report-details", "可能错位的行" in body and "批次1" in body, body[:300])
    check("misaligned:two-files", len(snapshot(tmp)) == 2, snapshot(tmp))

    # 5) 多批次：只有第 2 批对不齐（同样禁用拆分重译）
    app_core.BATCH_SIZE = 2
    calls = {"n": 0}

    def stub4(messages, temperature=0.2):
        if "分析师" in messages[0]["content"]:
            return ANALYSIS
        calls["n"] += 1
        return REPLY_OK if calls["n"] == 1 else REPLY_SHORT

    app_core.chat = stub4
    text, path, elapsed, stats = app_core.translate_text("【阿尔玛】一。\n【艾琳】二。\n【阿尔玛】三。\n【艾琳】四。", save_txt=False)
    check("multibatch:batches", stats["batches"] == 2, stats)
    check("multibatch:only-second", (stats["retried"], stats["misaligned"]) == (1, 1), stats)
    check("multibatch:failed-lines", stats["failed_lines"] == 1, stats)
    check("multibatch:warns", any("仍不匹配" in w for w in stats["warnings"]), stats["warnings"])

    shutil.rmtree(tmp, ignore_errors=True)
    # 6) 角色名括号保留：术语是恒等映射、或术语自带括号时，都不能丢「」
    app_core.BATCH_SIZE = 50
    app_core.MAX_SPLIT_DEPTH = 5
    real_load_terms = app_core.load_terms
    app_core.load_terms = lambda *a, **k: {"休眠": "休眠", "首领": "「首領」"}
    app_core.chat = make_stub(["【休眠】いち。\n【首領】に。"])
    text, path, elapsed, stats = app_core.translate_text("【「休眠」】一。\n【首领】二。", save_txt=False)
    out_lines = text.splitlines()
    check("speaker-wrapper:identity-keeps-quotes", out_lines[0].startswith("【「休眠」】"), repr(text))
    check("speaker-wrapper:term-with-quotes", out_lines[1].startswith("【「首領」】"), repr(text))

    # 7) 无【角色名】输入：模型自己猜的角色名不能写进输出
    app_core.load_terms = real_load_terms
    app_core.chat = make_stub(["【诺亚】こんにちは。"])
    text, path, elapsed, stats = app_core.translate_text("こんにちは。", save_txt=False)
    check("no-speaker:no-brackets", "【" not in text and "】" not in text, repr(text))
    check("no-speaker:segments", stats["segments"] == 1, stats)

    # 8) 无【角色名】多行输入：同样不允许出现【】，且译文照常输出
    app_core.chat = make_stub(["おはよう。\nいい天気だね。"])
    text, path, elapsed, stats = app_core.translate_text("早上好。\n天气不错。", save_txt=False)
    check("no-speaker-multiline:no-brackets", "【" not in text and "】" not in text, repr(text))
    check("no-speaker-multiline:has-text", "おはよう" in text, repr(text))



    print("ALL WARNING TESTS PASSED (%d checks)" % passed)


if __name__ == "__main__":
    main()
