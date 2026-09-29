"""
batch_translate.py - 批量翻译程序（不走 GUI）

用法：
  # 用默认路径（inputs/ 和 outputs/batch/）
  python batch_translate.py

  # 指定路径（覆盖默认）
  python batch_translate.py --input my_inputs/ --output my_outputs/

输入：--input 目录下的所有 .csv 文件（角色名,台词 格式）
输出：--output 目录下对应的 .csv 文件（角色名,译文 格式）
"""

import argparse
import csv
import json
import sys
import time
from datetime import datetime
from pathlib import Path

from app_core import translate_text, log_info, format_time


BASE_DIR = Path(__file__).resolve().parent

# ==================== 默认路径配置 ====================
DEFAULT_INPUT_DIR = BASE_DIR / "inputs"
DEFAULT_OUTPUT_DIR = BASE_DIR / "outputs" / "batch"


def parse_csv_input(csv_path: Path) -> list[dict]:
    """读取 CSV（角色名,台词 格式）"""
    content = csv_path.read_text(encoding="utf-8-sig")
    reader = csv.reader(content.splitlines())
    rows = []
    first = True
    for row in reader:
        if len(row) < 2:
            continue
        if first:
            first = False
            first_cell = row[0].strip().lower()
            if first_cell in ("角色名", "speaker", "角色", "name"):
                continue
        speaker = row[0].strip()
        text = row[1].strip()
        if speaker and text:
            rows.append({"speaker": speaker, "text": text})
    return rows


def rows_to_bracket_format(rows: list[dict]) -> str:
    """转成【角色名】台词 格式"""
    lines = []
    for row in rows:
        lines.append(f"【{row['speaker']}】{row['text']}")
    return "\n".join(lines)


def parse_bracket_output(result_text: str) -> list[dict]:
    """解析译文（【角色名】译文 格式）"""
    import re
    segments = []
    pattern = re.compile(r'^\s*【([^】]+)】\s*(.*)$')
    for line in result_text.splitlines():
        line = line.strip()
        if not line:
            continue
        m = pattern.match(line)
        if m:
            segments.append({"speaker": m.group(1).strip(), "text": m.group(2).strip()})
        else:
            if segments:
                segments[-1]["text"] += "\n" + line
            else:
                segments.append({"speaker": "", "text": line})
    return segments


def save_csv_output(segments: list[dict], out_path: Path):
    """保存为 CSV（角色名,译文 格式）"""
    with out_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        for seg in segments:
            if seg["speaker"]:
                writer.writerow([seg["speaker"], seg["text"]])
            else:
                writer.writerow(["", seg["text"]])


def main():
    parser = argparse.ArgumentParser(description="批量翻译程序（不走 GUI）")
    parser.add_argument("--input", "-i", default=str(DEFAULT_INPUT_DIR),
                        help=f"输入目录（默认: {DEFAULT_INPUT_DIR}）")
    parser.add_argument("--output", "-o", default=str(DEFAULT_OUTPUT_DIR),
                        help=f"输出目录（默认: {DEFAULT_OUTPUT_DIR}）")
    parser.add_argument("--skip-existing", action="store_true", help="跳过已翻译的文件")
    args = parser.parse_args()

    input_dir = Path(args.input)
    output_dir = Path(args.output)

    # ==================== 输入目录检查 ====================
    if not input_dir.exists():
        # 如果是默认路径，自动创建并提示
        if input_dir == DEFAULT_INPUT_DIR:
            input_dir.mkdir(parents=True, exist_ok=True)
            print(f"📂 已创建默认输入目录: {input_dir}")
            print()
            print(f"👉 请把待翻译的 CSV 文件放到该目录，然后重新运行。")
            print(f"   CSV 格式：角色名,台词（无表头）")
            print(f"   示例：")
            print(f"     雷诺,喂，阿尔玛…")
            print(f"     阿尔玛,嗯？怎么了，你们几个。")
            print()
            sys.exit(0)
        else:
            print(f"❌ 输入目录不存在: {input_dir}")
            print(f"   请确认路径，或去掉 --input 参数使用默认目录:")
            print(f"   {DEFAULT_INPUT_DIR}")
            sys.exit(1)
    # ======================================================

    output_dir.mkdir(parents=True, exist_ok=True)

    # 收集所有 CSV
    csv_files = sorted(input_dir.glob("*.csv"))
    if not csv_files:
        print(f"❌ 输入目录下没有 CSV 文件: {input_dir}")
        print(f"   请把待翻译的 CSV 文件放到该目录，然后重新运行。")
        sys.exit(1)

    print(f"📂 输入目录: {input_dir}")
    print(f"📂 输出目录: {output_dir}")
    print(f"📊 共 {len(csv_files)} 个 CSV 文件\n")

    total_start = time.time()
    success_count = 0
    fail_count = 0
    skip_count = 0
    failed_files = []

    for idx, csv_path in enumerate(csv_files, 1):
        out_path = output_dir / csv_path.name

        print(f"[{idx}/{len(csv_files)}] {csv_path.name}")

        # 跳过已翻译的
        if args.skip_existing and out_path.exists():
            print(f"   ⏭️  已存在，跳过")
            skip_count += 1
            continue

        try:
            # 读取
            rows = parse_csv_input(csv_path)
            if not rows:
                print(f"   ⚠️  无有效数据，跳过")
                skip_count += 1
                continue

            print(f"   📖 加载 {len(rows)} 条")

            # 转成【角色名】台词
            bracket_text = rows_to_bracket_format(rows)

            # 翻译（save_txt=False，不保存 TXT）
            file_start = time.time()
            result_text, _, elapsed, stats = translate_text(bracket_text, save_txt=False)
            file_elapsed = time.time() - file_start
            for w in stats.get("warnings", []):
                print(f"   ⚠️  {w}")

            # 解析译文
            segments = parse_bracket_output(result_text)

            # 保存 CSV
            save_csv_output(segments, out_path)

            print(f"   ✅ 完成（{len(segments)} 条，用时 {format_time(file_elapsed)}）")
            print(f"   💾 保存: {out_path.name}")
            success_count += 1

        except Exception as e:
            print(f"   ❌ 失败: {e}")
            fail_count += 1
            failed_files.append((csv_path.name, str(e)))

    total_elapsed = time.time() - total_start

    # 汇总
    print(f"\n{'=' * 60}")
    print(f"📊 批量翻译完成")
    print(f"   总文件: {len(csv_files)}")
    print(f"   ✅ 成功: {success_count}")
    print(f"   ⏭️  跳过: {skip_count}")
    print(f"   ❌ 失败: {fail_count}")
    print(f"   总用时: {format_time(total_elapsed)}")

    if failed_files:
        print(f"\n❌ 失败文件:")
        for name, err in failed_files:
            print(f"   - {name}: {err}")

    print(f"{'=' * 60}")

    # 输出 JSON 结果（供调用方解析）
    print(json.dumps({
        "success": fail_count == 0,
        "total": len(csv_files),
        "success_count": success_count,
        "skip_count": skip_count,
        "fail_count": fail_count,
        "failed_files": [{"name": n, "error": e} for n, e in failed_files],
        "elapsed": total_elapsed,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()