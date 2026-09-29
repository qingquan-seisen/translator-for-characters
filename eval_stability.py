"""输出稳定性评测脚本（辅助工具，不参与正式翻译流程）。

把同一个输入重复翻译 N 轮，量两件事：

  - 自一致性：同一行在不同轮译文之间的相似度（越高越稳）
  - 参照相似：和参照译文的相似度（口径与 eval_translate.py 相同，别为了稳把质量赔进去）

用法（一律用项目自带虚拟环境）：

    venv\\Scripts\\python.exe eval_stability.py -i <原文.txt> -r <参照.txt> -n 4
    venv\\Scripts\\python.exe eval_stability.py -i dialog_input.csv -r dialog_reference.csv -n 3 --temps 0.1,0.25

参数：
    -i/--input      待翻译的文件：.csv 按「角色名,台词」读，其它后缀按「【角色名】台词」读
    -r/--ref        参照译文：.csv 只取第 2 列，其它后缀按「【角色名】台词」读；按行号对齐
    -n/--trials     每个实验组跑几轮（默认 3；一轮 = 一次完整翻译，会真实调用模型）
    --temps         要对比的温度，逗号分隔；不写就只跑一组（= 当前代码里的值）
    --stage         扫哪个环节的温度：translate（默认）/ analysis
    --worst N       报告里列出「自一致性最低」的 N 行（默认 10）
    --tag NAME      输出文件名里的标签（默认取输入文件名）
    --quiet         关闭进度计时

输出（每轮结束立刻落盘，中途中断也不丢已跑的数据）：
    outputs/eval/stability_<tag>_<时间戳>.txt    报告
    outputs/eval/stability_<tag>_<时间戳>.json   逐行译文 + 汇总

注意：
    - 自一致性是「稳定性」的量化口径，但轮数少时噪声很大（n=3 只有 3 对比较），
      1~2 分的差别当结论；宁可少设几组温度、把 -n 加大
    - 实测（2026-09-14）降低翻译温度并不会提高自一致性，波动来自「同一句换说法」本身，
      详见 AGENTS.md 的「温度」一节
    - 部分端点（如阿里 token-plan）接受 seed 参数但并不当真，所以本脚本不提供 --seed
"""

import argparse
import csv
import json
import statistics
import sys
import time
from datetime import datetime
from itertools import combinations
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

import app_core

EVAL_DIR = BASE_DIR / "outputs" / "eval"


class QuietTicker:
    """--quiet 时顶替 app_core.ProgressTicker，不打印读秒。"""

    def __init__(self, prefix: str = ""):
        pass

    def start(self):
        pass

    def stop(self):
        pass


def load_rows(path: str | Path, is_chinese: bool) -> list[tuple[str, str]]:
    """读输入/参照。.csv 走「角色名,台词」，其它后缀走「【角色名】台词」。"""
    full = BASE_DIR / path if not Path(path).is_absolute() else Path(path)
    text = app_core._read_text_with_fallback(full, is_chinese=is_chinese)
    if full.suffix.lower() == ".csv":
        rows = []
        for row in csv.reader(text.splitlines()):
            if len(row) >= 2 and row[0].strip() and row[1].strip():
                rows.append((row[0].strip(), row[1].strip()))
        if rows and app_core.is_csv_header_row([rows[0][0], rows[0][1]]):
            rows = rows[1:]
        return rows
    return [(seg.get("speaker", ""), seg.get("text", "")) for seg in app_core.parse_text_with_speakers(text)]


def build_source(rows: list[tuple[str, str]]) -> str:
    return "\n".join(f"【{speaker}】{content}" if speaker else content for speaker, content in rows)


def install_temperature(stage: str, value: float | None) -> None:
    """改 app_core 里对应的温度常量（None = 保持代码里的当前值）。

    调用处是 `temperature=TRANSLATE_TEMPERATURE` 这种写法，运行时从模块全局取值，
    所以改常量就够了，不用去 monkeypatch chat()。
    """
    if value is None:
        return
    if stage == "analysis":
        app_core.ANALYSIS_TEMPERATURE = value
    else:
        app_core.TRANSLATE_TEMPERATURE = value


def pairwise_consistency(lines: list[list[str]]) -> float:
    """逐行计算「不同轮之间」的两两相似度，再跨行取平均。

    lines[i] = 第 i 行在各个轮次里的译文（缺行补 None）。
    """
    scores = []
    for texts in lines:
        got = [t for t in texts if t is not None]
        pairs = list(combinations(got, 2))
        if pairs:
            scores.append(statistics.mean(app_core.line_similarity(a, b) for a, b in pairs))
    return statistics.mean(scores) if scores else float("nan")


def fmt_pct(value: float | None) -> str:
    """分数显示：没有有效值（比如只跑 1 轮）时显示 —。"""
    if value is None or value != value:
        return "—"
    return f"{value * 100:.1f}%"


def main() -> None:
    parser = argparse.ArgumentParser(description="同一输入跑 N 轮，量自一致性（稳定性）和参照相似度（质量）")
    parser.add_argument("-i", "--input", required=True, help="待翻译的文件（.csv 或【角色名】台词文本）")
    parser.add_argument("-r", "--ref", required=True, help="参照译文")
    parser.add_argument("-n", "--trials", type=int, default=3, help="每个实验组跑几轮（默认 3）")
    parser.add_argument("--temps", default=None, help="要对比的温度，逗号分隔（默认只跑当前值）")
    parser.add_argument("--stage", choices=["translate", "analysis"], default="translate",
                        help="扫哪个环节的温度（默认 translate）")
    parser.add_argument("--worst", type=int, default=10, help="报告里列出的最不稳的行数（默认 10）")
    parser.add_argument("--tag", default=None, help="输出文件名标签（默认取输入文件名）")
    parser.add_argument("--quiet", action="store_true", help="关闭进度计时")
    args = parser.parse_args()

    if args.quiet:
        app_core.ProgressTicker = QuietTicker

    if args.trials < 2:
        print("⚠️ -n 1：自一致性至少要 2 轮才有轮间比较，本轮只报参照相似度")
    temps = None
    if args.temps:
        temps = [float(t) for t in args.temps.replace("，", ",").split(",") if t.strip()]

    tag = args.tag or Path(args.input).stem
    in_rows = load_rows(args.input, is_chinese=True)
    ref_rows = load_rows(args.ref, is_chinese=False)
    ref_texts = [content for _, content in ref_rows]
    if not in_rows:
        raise SystemExit(f"❌ 输入文件没有有效数据: {args.input}")
    if len(in_rows) != len(ref_texts):
        print(f"⚠️ 行数不一致（输入 {len(in_rows)} / 参照 {len(ref_texts)}），只按行号对齐，结果仅供参考")

    source = build_source(in_rows)
    print(f"📂 输入: {args.input}（{len(in_rows)} 行）")
    print(f"📂 参照: {args.ref}（{len(ref_texts)} 行）")
    print(f"🧪 环节: {args.stage}，温度组: {temps if temps else '当前值'}，每组 {args.trials} 轮")

    EVAL_DIR.mkdir(parents=True, exist_ok=True)
    stamp = f"{datetime.now():%Y%m%d_%H%M%S}"
    json_path = EVAL_DIR / f"stability_{tag}_{stamp}.json"
    report_path = EVAL_DIR / f"stability_{tag}_{stamp}.txt"

    arms: dict[str, list[dict]] = {}
    for value in temps if temps else [None]:
        label = f"{args.stage} {value}" if value is not None else f"{args.stage}（当前值）"
        arms[label] = []
        install_temperature(args.stage, value)
        for trial in range(args.trials):
            started = time.time()
            try:
                result_text, _, elapsed, stats = app_core.translate_text(source, save_txt=False)
                texts = [seg["text"] for seg in app_core.parse_text_with_speakers(result_text)]
                sims = [app_core.line_similarity(texts[i] if i < len(texts) else "", ref_texts[i])
                        for i in range(len(ref_texts))]
                record = {
                    "ok": True, "texts": texts, "line_count": len(texts),
                    "ref_mean": statistics.mean(sims), "sims": sims,
                    "warnings": stats.get("warnings") or [], "elapsed": elapsed,
                }
                print(f"  {label} 第 {trial + 1}/{args.trials} 轮：参照 {record['ref_mean'] * 100:.1f}%"
                      f"（{elapsed:.0f} 秒，{len(texts)} 行）")
            except Exception as e:
                record = {"ok": False, "error": f"{type(e).__name__}: {e}",
                          "elapsed": time.time() - started}
                print(f"  {label} 第 {trial + 1}/{args.trials} 轮：失败 {record['error'][:120]}")
            arms[label].append(record)
            json_path.write_text(
                json.dumps({"input": args.input, "ref": args.ref, "stage": args.stage,
                            "trials": args.trials, "source": source, "ref_texts": ref_texts,
                            "arms": arms},
                           ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    summary = []
    for label, records in arms.items():
        good = [r for r in records if r["ok"]]
        if not good:
            summary.append({"label": label, "ok": 0, "consistency": None, "ref_mean": None})
            continue
        width = max(len(r["texts"]) for r in good)
        per_line = [[r["texts"][i] if i < len(r["texts"]) else None for r in good] for i in range(width)]
        consistency = pairwise_consistency(per_line)
        refs = [r["ref_mean"] for r in good]
        lengths = [sum(len(t) for t in r["texts"]) for r in good]
        line_counts = {r["line_count"] for r in good}
        summary.append({
            "label": label, "ok": len(good), "trials": len(records),
            "consistency": consistency,
            "ref_mean": statistics.mean(refs),
            "ref_sd": statistics.pstdev(refs) if len(refs) > 1 else None,
            "len_sd": statistics.pstdev(lengths) if len(lengths) > 1 else None,
            "line_counts": sorted(line_counts),
            "per_line": [pairwise_consistency([line]) for line in per_line],
            "warnings": sorted({w for r in good for w in r["warnings"]}),
        })

    report = ["=" * 72, "🔁 输出稳定性评测", f"生成: {datetime.now():%Y-%m-%d %H:%M:%S}", "=" * 72, ""]
    report += [f"输入: {args.input}（{len(in_rows)} 行）", f"参照: {args.ref}", f"环节: {args.stage}",
               f"每组轮数: {args.trials}", ""]
    report += ["【汇总】自一致性 = 同一行不同轮译文的两两相似度（逐行算完再取平均）"]
    report += [f"{'组':<22}{'成功':>6}{'自一致性':>10}{'参照相似':>10}{'参照波动':>10}{'长度波动':>10}"]
    for item in summary:
        if item["ok"] == 0:
            report += [f"{item['label']:<22}{'0/' + str(args.trials):>6}   全部失败"]
            continue
        counts = item["line_counts"]
        flag = "" if len(counts) == 1 and counts[0] == len(ref_texts) else f"  ⚠️ 行数 {counts}"
        ok_str = f"{item['ok']}/{item['trials']}"
        report += [f"{item['label']:<22}{ok_str:>6}"
                   f"{fmt_pct(item['consistency']):>9}{fmt_pct(item['ref_mean']):>9}"
                   f"{fmt_pct(item['ref_sd']):>9}{fmt_num(item['len_sd']):>10}{flag}"]
    report.append("")

    for item in summary:
        if item["ok"] == 0:
            continue
        if args.trials < 2:
            report += [f"【{item['label']}】-n 1 没有轮间比较，跳过（自一致性至少需要 2 轮）", ""]
            continue
        order = sorted(range(len(item["per_line"])), key=lambda i: item["per_line"][i])
        report += [f"【{item['label']}】自一致性最低的 {min(args.worst, len(order))} 行"]
        for i in order[: args.worst]:
            label = f"第 {i + 1} 行" if i < len(in_rows) else f"第 {i + 1} 行（参照里没有）"
            speaker = in_rows[i][0] if i < len(in_rows) else ""
            report += [f"[{label}] {item['per_line'][i] * 100:5.1f}%  {speaker}".rstrip()]
        report.append("")

    report += ["=" * 72]
    app_core.write_text(report_path, "\n".join(report))

    print("")
    print(f"{'组':<22}{'成功':>6}{'自一致性':>10}{'参照相似':>10}")
    for item in summary:
        if item["ok"] == 0:
            print(f"{item['label']:<22}{'0/' + str(args.trials):>6}   全部失败")
            continue
        ok_str = f"{item['ok']}/{item['trials']}"
        print(f"{item['label']:<22}{ok_str:>6}"
              f"{fmt_pct(item['consistency']):>9}{fmt_pct(item['ref_mean']):>9}")
    print(f"💾 报告: {report_path.relative_to(BASE_DIR)}")
    print(f"💾 明细: {json_path.relative_to(BASE_DIR)}")


if __name__ == "__main__":
    main()
def fmt_num(value: float | None) -> str:
    """数值显示：没有有效值（例如只跑 1 轮，没有波动可言）时显示 —。"""
    if value is None:
        return "—"
    return f"{value:.1f}"

