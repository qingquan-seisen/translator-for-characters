"""
predict_character.py - 角色风格预测程序

用途：仅通过一段中文角色描述，推测该角色的日文台词风格。
输入：TXT 文件，格式：
    【角色名】
    角色描述（可多行）

    【角色名2】
    角色描述（可多行）

输出：predicted_characters.json（人工审核后可合入 characters.json）

用法：
  python predict_character.py --file descriptions.txt
  python predict_character.py --file descriptions.txt --save
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from app_core import chat, extract_json, log_info


BASE_DIR = Path(__file__).resolve().parent
PREDICTED_JSON = BASE_DIR / "predicted_characters.json"
DEFAULT_INPUT = BASE_DIR / "character_descriptions.txt"


PREDICT_SYSTEM = """你是日本游戏本地化专家，精通角色语气设计。

【任务】
根据用户提供的中文角色描述，预测该角色在日语台词中的风格特征。

【输出要求】
严格输出 JSON，包含以下字段：
{
  "speaking_style": "一句话描述角色的日语说话风格（中文）",
  "first_person": "日语第一人称（私/僕/俺/あたし/ワタシ/わたくし/我輩/わし/自分/うち 等）",
  "sentence_end": "日语候选句尾，用/或、分隔（如：です/ます/かしら/わ 等）",
  "tone": "日语语气关键词（中文，如：冷静、优雅、傲慢、天真 等）",
  "catchphrases": ["推荐的口头禅或常用表达（日语，最多5个）"]
}

【预测原则】
1. 第一人称要与角色的性别、年龄、身份、性格匹配
   - 成熟女性贵族 → ワタシ/わたくし
   - 温和女性 → 私
   - 活泼少女 → あたし
   - 男性粗犷 → 俺
   - 男性文雅 → 僕
   - 老成男性 → わし/我輩
2. 句尾要体现语气特征
   - 优雅贵妇 → わ/のよ/かしら/ですわ
   - 温和礼貌 → です/ます/かしら
   - 活泼少女 → だよ/なの/だね
   - 男性粗犷 → だ/ぜ/ぞ/な
   - 老成男性 → じゃ/のう/わい
3. 口头禅要符合角色身份和语气特征
4. 所有字段必须基于描述，不要编造描述中不存在的特征

【重要】
- 只输出 JSON，不要输出其他内容
- 字段值必须具体、可执行
- 如果描述信息不足，选择最保守的推断（如用"私"和"です/ます"）"""


def parse_descriptions(text: str) -> list[tuple[str, str]]:
    """解析 TXT 文件，返回 [(角色名, 描述), ...]
    格式：
        【角色名】
        描述（可多行）
    """
    pattern = re.compile(r'^\s*【([^】]+)】\s*(.*)$')
    results = []
    current_name = None
    current_lines = []

    for line in text.splitlines():
        m = pattern.match(line)
        if m:
            # 遇到新角色，保存上一个
            if current_name is not None:
                desc = "\n".join(current_lines).strip()
                if desc:
                    results.append((current_name, desc))
            current_name = m.group(1).strip()
            current_lines = []
            # 【角色名】后面可能紧跟描述
            rest = m.group(2).strip()
            if rest:
                current_lines.append(rest)
        else:
            if current_name is not None:
                current_lines.append(line)

    # 保存最后一个
    if current_name is not None:
        desc = "\n".join(current_lines).strip()
        if desc:
            results.append((current_name, desc))

    return results


def build_user_prompt(name: str, desc: str) -> str:
    return f"""【角色名】
{name}

【角色描述】
{desc}

请预测该角色的日语台词风格。"""


def predict_character(name: str, desc: str) -> dict:
    """预测单个角色的风格"""
    log_info(f"🔮 预测角色「{name}」的风格...")

    raw = chat([
        {"role": "system", "content": PREDICT_SYSTEM},
        {"role": "user", "content": build_user_prompt(name, desc)},
    ], temperature=0.3)

    data = extract_json(raw)

    result = {
        "speaking_style": str(data.get("speaking_style", "")).strip(),
        "first_person": str(data.get("first_person", "私")).strip(),
        "sentence_end": str(data.get("sentence_end", "です/ます")).strip(),
        "tone": str(data.get("tone", "")).strip(),
        "catchphrases": data.get("catchphrases", []),
    }

    if not isinstance(result["catchphrases"], list):
        result["catchphrases"] = []
    result["catchphrases"] = [str(x).strip() for x in result["catchphrases"] if x]

    return result


def merge_to_predicted_json(name: str, profile: dict):
    """把预测结果合并到 predicted_characters.json"""
    existing = {}
    if PREDICTED_JSON.exists():
        try:
            raw = PREDICTED_JSON.read_text(encoding="utf-8").strip()
            if raw:
                existing = json.loads(raw)
        except Exception as e:
            log_info(f"⚠️ 读取 predicted_characters.json 失败: {e}")
            existing = {}

    key = name if name.startswith("「") else f"「{name}」"
    existing[key] = profile

    PREDICTED_JSON.write_text(
        json.dumps(existing, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


def save_all_results(all_results: dict):
    """一次性保存所有结果"""
    existing = {}
    if PREDICTED_JSON.exists():
        try:
            raw = PREDICTED_JSON.read_text(encoding="utf-8").strip()
            if raw:
                existing = json.loads(raw)
        except Exception as e:
            log_info(f"⚠️ 读取 predicted_characters.json 失败: {e}")
            existing = {}

    for name, profile in all_results.items():
        key = name if name.startswith("「") else f"「{name}」"
        existing[key] = profile

    PREDICTED_JSON.write_text(
        json.dumps(existing, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    log_info(f"💾 已写入 predicted_characters.json（共 {len(existing)} 个角色）")


def main():
    parser = argparse.ArgumentParser(description="角色风格预测")
    parser.add_argument("--file", "-f", default=str(DEFAULT_INPUT),
                        help=f"输入 TXT 文件（默认: {DEFAULT_INPUT.name}）")
    parser.add_argument("--save", "-s", action="store_true", help="保存到 predicted_characters.json")
    args = parser.parse_args()

    input_path = Path(args.file)

    # 输入文件不存在时，自动创建模板
    if not input_path.exists():
        if input_path == DEFAULT_INPUT:
            input_path.write_text(
                "【角色名1】\n在这里填写角色的中文描述，可以写多行。\n"
                "比如：优雅冷静、略带傲慢的成熟女性，常以敬语保持距离。\n\n"
                "【角色名2】\n另一个角色的描述。\n",
                encoding="utf-8"
            )
            print(f"📂 已创建模板文件: {input_path}")
            print()
            print("👉 请填写角色描述，然后重新运行。")
            print("   格式：")
            print("      【角色名】")
            print("      角色描述（可多行）")
            sys.exit(0)
        else:
            print(f"❌ 文件不存在: {input_path}")
            sys.exit(1)

    # 读取
    text = input_path.read_text(encoding="utf-8-sig").strip()
    if not text:
        print(f"❌ 文件为空: {input_path}")
        sys.exit(1)

    # 解析
    descriptions = parse_descriptions(text)
    if not descriptions:
        print("❌ 未解析到任何角色（需要【角色名】格式）")
        sys.exit(1)

    print(f"📂 输入: {input_path}")
    print(f"📊 共 {len(descriptions)} 个角色\n")

    # 逐个预测
    all_results = {}
    fail_count = 0

    for idx, (name, desc) in enumerate(descriptions, 1):
        print(f"[{idx}/{len(descriptions)}] {name}")
        try:
            profile = predict_character(name, desc)
            all_results[name] = profile
            # 打印结果
            print(json.dumps({f"「{name}」" if not name.startswith("「") else name: profile},
                             ensure_ascii=False, indent=2))
            print()
        except Exception as e:
            log_info(f"❌ 角色「{name}」预测失败: {e}")
            fail_count += 1

    # 汇总
    print(f"{'=' * 60}")
    print(f"📊 预测完成")
    print(f"   总数: {len(descriptions)}")
    print(f"   ✅ 成功: {len(all_results)}")
    print(f"   ❌ 失败: {fail_count}")
    print(f"{'=' * 60}")

    # 保存
    if args.save and all_results:
        save_all_results(all_results)
        print(f"💾 已保存到: {PREDICTED_JSON}")
        print()
        print("ℹ️ 请人工审核后，把满意的条目复制到 characters.json")
    elif all_results:
        print()
        print("ℹ️ 未保存。加 --save 参数可写入 predicted_characters.json")


if __name__ == "__main__":
    main()