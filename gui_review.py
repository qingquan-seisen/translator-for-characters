import argparse
import json
import sys
from pathlib import Path

from app_core import review_text


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--original")
    parser.add_argument("--translation")
    parser.add_argument("--original-file")
    parser.add_argument("--translation-file")
    parser.add_argument("--reference")
    args = parser.parse_args()
    try:
        original = args.original
        if not original and args.original_file:
            original = Path(args.original_file).read_text(encoding="utf-8")
        translation = args.translation
        if not translation and args.translation_file:
            translation = Path(args.translation_file).read_text(encoding="utf-8")
        if not original or not translation:
            raise ValueError("--original/--translation or --original-file/--translation-file")
        report, path, elapsed = review_text(original, translation, args.reference)
        print(json.dumps({"success": True, "report": report, "filename": str(path), "time": elapsed}, ensure_ascii=False))
    except Exception as e:
        print(json.dumps({"success": False, "error": str(e)}, ensure_ascii=False))
        sys.exit(1)


if __name__ == "__main__":
    main()