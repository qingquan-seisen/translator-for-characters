import argparse
import json
import sys
from pathlib import Path

from app_core import translate_text, read_text


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--text")
    parser.add_argument("--text-file")
    parser.add_argument("--file")
    parser.add_argument("--no-save-txt", action="store_true", help="不保存 TXT（用于 CSV 模式）")
    args = parser.parse_args()

    try:
        if args.text:
            text = args.text
        elif args.text_file:
            text = Path(args.text_file).read_text(encoding="utf-8")
        elif args.file:
            text = read_text(args.file)
        else:
            raise ValueError("--text / --text-file / --file")
        if not text.strip():
            raise ValueError("文本为空")

        result, path, elapsed, stats = translate_text(text.strip(), save_txt=not args.no_save_txt)
        print(json.dumps({
            "success": True,
            "result": result,
            "filename": str(path) if path else "",
            "warnings": stats.get("warnings", []),
            "time": elapsed
        }, ensure_ascii=False))
    except Exception as e:
        print(json.dumps({"success": False, "error": str(e)}, ensure_ascii=False))
        sys.exit(1)


if __name__ == "__main__":
    main()