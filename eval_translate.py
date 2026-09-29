"""提示词/译文评测脚本（辅助工具，不参与正式翻译流程）。

把「输入 CSV」整篇翻译一遍，再和「参照 CSV」逐行比对，输出相似度报告。

用法（一律用项目自带虚拟环境）：

    venv\\Scripts\\python.exe eval_translate.py -i dialog_input.csv -r dialog_reference.csv
    venv\\Scripts\\python.exe eval_translate.py -i chinese_csv/dialog_cn_06.csv -r japanese_csv/dialog_jp_06.csv --worst 30 --quiet

参数：
    -i/--input      待翻译的 CSV（角色名,台词；UTF-8 / Shift-JIS 自动识别）
    -r/--ref        参照译文 CSV（角色名,译文），只用第 2 列，按行号对齐
    -n/--trials     跑几轮取平均（默认 1；长文本建议 1 轮）
    --worst N       报告里列出相似度最低的 N 行（默认 20）
    --verbose       逐行打印全部对比
    --show-context  翻译前打印命中的角色风格 / 术语
    --quiet         关闭进度计时（便于把输出重定向到文件）
    --tag NAME      输出文件名里的标签（默认取输入文件名）

输出：
    outputs/eval/translation_<tag>_<时间戳>.csv   译文
    outputs/eval/report_<tag>_<时间戳>.txt        报告（平均相似度 + 逐行对比）

注意：相似度只是参考指标。参照文件常是人工参考译文，会有改写（加词、换说法），
所以别追求满分；改完提示词跑一遍，看平均分有没有退步、低分行有没有变多就够了。
"""

import argparse
import csv
import statistics
import sys
from datetime import datetime
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
    """读 CSV（角色名,台词），自动跳过表头，编码自动回退。"""
    text = app_core._read_text_with_fallback(BASE_DIR / path, is_chinese=is_chinese)
    rows = []
    for row in csv.reader(text.splitlines()):
        if len(row) < 2:
            continue
        speaker, content = row[0].strip(), row[1].strip()
        if speaker and content:
            rows.append((speaker, content))
    if rows and app_core.is_csv_header_row([rows[0][0], rows[0][1]]):
        rows = rows[1:]
    return rows


def build_bracket_text(rows: list[tuple[str, str]]) -> str:
    return "\n".join(f"【{speaker}】{content}" for speaker, content in rows)


def save_translation(result_text: str, path: Path) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        for seg in app_core.parse_text_with_speakers(result_text):
            writer.writerow([seg.get("speaker", ""), seg.get("text", "")])


def main() -> None:
    parser = argparse.ArgumentParser(description="把输入 CSV 翻一遍，和参照 CSV 逐行比对相似度")
    parser.add_argument("-i", "--input", required=True, help="待翻译的 CSV")
    parser.add_argument("-r", "--ref", required=True, help="参照译文 CSV")
    parser.add_argument("-n", "--trials", type=int, default=1, help="跑几轮（默认 1）")
    parser.add_argument("--worst", type=int, default=20, help="报告里列出的最低分行数（默认 20）")
    parser.add_argument("--verbose", action="store_true", help="逐行打印全部对比")
    parser.add_argument("--show-context", action="store_true", help="翻译前打印角色风格 / 术语")
    parser.add_argument("--quiet", action="store_true", help="关闭进度计时")
    parser.add_argument("--tag", default=None, help="输出文件名标签（默认取输入文件名）")
    args = parser.parse_args()

    if args.quiet:
        app_core.ProgressTicker = QuietTicker

    tag = args.tag or Path(args.input).stem
    in_rows = load_rows(args.input, is_chinese=True)
    ref_rows = load_rows(args.ref, is_chinese=False)
    ref_texts = [content for _, content in ref_rows]
    if not in_rows:
        raise SystemExit(f"❌ 输入文件没有有效数据: {args.input}")

    bracket_text = build_bracket_text(in_rows)

    print(f"📂 输入: {args.input}（{len(in_rows)} 行）")
    print(f"📂 参照: {args.ref}（{len(ref_rows)} 行）")
    if len(in_rows) != len(ref_rows):
        print(f"⚠️ 行数不一致（输入 {len(in_rows)} / 参照 {len(ref_rows)}），只按行号对齐，结果仅供参考")

    if args.show_context:
        context = app_core.build_context(bracket_text, speakers=[sp for sp, _ in in_rows])
        print("---- 上下文（角色风格 / 命中术语）----")
        keep = False
        for line in context.splitlines():
            if line.startswith("【角色风格") or line.startswith("【命中术语"):
                keep = True
            if keep:
                print(line)
        print("----")

    EVAL_DIR.mkdir(parents=True, exist_ok=True)
    stamp = f"{datetime.now():%Y%m%d_%H%M%S}"

    run_avgs: list[float] = []
    per_line: list[list[float]] = [[] for _ in ref_texts]
    all_lines: list[list[tuple[str, str]]] = []

    for trial in range(args.trials):
        try:
            result_text, _, elapsed, stats = app_core.translate_text(bracket_text, save_txt=False)
        except Exception as e:
            print(f"⚠️ 第 {trial + 1} 轮失败，已跳过：{type(e).__name__}: {e}")
            continue
        out_path = EVAL_DIR / f"translation_{tag}_{stamp}_t{trial + 1}.csv"
        save_translation(result_text, out_path)
        out_texts = [seg["text"] for seg in app_core.parse_text_with_speakers(result_text)]

        if len(out_texts) != len(ref_texts):
            print(f"⚠️ 第 {trial + 1} 轮行数不匹配（译文 {len(out_texts)} / 参照 {len(ref_texts)}）")

        ratios = []
        for i in range(len(ref_texts)):
            got = out_texts[i] if i < len(out_texts) else ""
            ratio = app_core.line_similarity(got, ref_texts[i])
            ratios.append(ratio)
            per_line[i].append(ratio)
        run_avgs.append(statistics.mean(ratios))
        all_lines.append(list(zip(out_texts, ref_texts)))
        warnings = stats.get("warnings") or []
        print(f"第 {trial + 1} 轮: {statistics.mean(ratios) * 100:.1f}%（{elapsed:.0f} 秒）"
              f"{'  告警: ' + '; '.join(warnings) if warnings else ''}")

    if not run_avgs:
        raise SystemExit("❌ 所有轮次都失败了（网络或 API 问题），没有生成报告")
    line_means = [statistics.mean(v) for v in per_line if v]
    report = []
    report += ["=" * 72, "📊 评测报告", f"生成: {datetime.now():%Y-%m-%d %H:%M:%S}", "=" * 72, ""]
    report += [f"输入: {args.input}", f"参照: {args.ref}", f"轮数: {args.trials}", ""]
    report += [f"每轮平均相似度: " + " / ".join(f"{a * 100:.1f}%" for a in run_avgs)]
    report += [f"总平均相似度:   {statistics.mean(run_avgs) * 100:.1f}%", ""]

    buckets = [(90, "≥90%"), (70, "70-90%"), (50, "50-70%"), (0, "<50%")]
    report += ["【分布】（按行平均）"]
    for low, label in buckets:
        if label == "≥90%":
            count = sum(1 for m in line_means if m * 100 >= low)
        elif label == "70-90%":
            count = sum(1 for m in line_means if 70 <= m * 100 < 90)
        elif label == "50-70%":
            count = sum(1 for m in line_means if 50 <= m * 100 < 70)
        else:
            count = sum(1 for m in line_means if m * 100 < 50)
        report.append(f"  {label:>7}: {count} 行")
    report.append("")

    order = sorted(range(len(line_means)), key=lambda i: line_means[i])
    report += [f"【相似度最低的 {min(args.worst, len(order))} 行】"]
    for i in order[: args.worst]:
        report.append(f"[{i + 1:>4}] {line_means[i] * 100:5.1f}%")
        report.append(f"   译: {all_lines[0][i][0] if all_lines[0][i][0] else '（缺行）'}")
        report.append(f"   参: {ref_texts[i]}")
    report.append("")

    if args.verbose:
        report += ["【逐行对比】"]
        for i in range(len(ref_texts)):
            report.append(f"[{i + 1:>4}] {line_means[i] * 100:5.1f}%")
            report.append(f"   译: {all_lines[0][i][0] if i < len(all_lines[0]) else ''}")
            report.append(f"   参: {ref_texts[i]}")
        report.append("")

    report += ["=" * 72]
    report_path = EVAL_DIR / f"report_{tag}_{stamp}.txt"
    app_core.write_text(report_path, "\n".join(report))

    print("")
    print(f"✅ 总平均相似度: {statistics.mean(run_avgs) * 100:.1f}%  "
          f"(最低行 {min(line_means) * 100:.1f}%)")
    print(f"💾 报告: {report_path.relative_to(BASE_DIR)}")
    print(f"💾 译文: {EVAL_DIR.relative_to(BASE_DIR)}/translation_{tag}_{stamp}_t*.csv")


if __name__ == "__main__":
    main()
