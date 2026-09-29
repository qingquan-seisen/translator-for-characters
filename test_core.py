import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import app_core


def test_config_and_data():
    assert app_core.BASE_DIR == ROOT
    assert len(app_core.load_terms()) >= 1
    assert len(app_core.load_characters()) >= 1


def test_json_parser():
    assert app_core.extract_json('```json\n{"x": 1}\n```')["x"] == 1
    assert app_core.extract_json('[{"chinese":"测试","japanese":"テスト"}]')[0]["japanese"] == "テスト"
    assert app_core.extract_json('前言\n{"x": 2}\n后文')["x"] == 2


def test_term_checker_longest_first():
    checker = app_core.TermChecker({"符文": "ルーン", "古代符文": "エンシェントルーン"})
    report = checker.check("古代符文", "エンシェントルーン")
    assert report["correct"] == 2


def test_normalize_translation():
    assert app_core.normalize_translation("【译文】\nこんにちは。") == "こんにちは。"
    assert app_core.normalize_translation("```text\nこんにちは。\n```") == "こんにちは。"


def test_drop_bare_duplicate_terms():
    terms = {
        "「白鸦」": "「白鴉」",
        "白鸦": "白鴉",
        "面具": "仮面",
        "首领": "「首領」",
        "水晶": "水晶",
    }
    cleaned = app_core.drop_bare_duplicate_terms(terms)
    assert "白鸦" not in cleaned
    assert cleaned["「白鸦」"] == "「白鴉」"
    assert cleaned["面具"] == "仮面"
    assert cleaned["首领"] == "「首領」"
    assert cleaned["水晶"] == "水晶"


def test_resolve_speaker_name():
    terms = {"「面具」": "仮面", "アルマ": "アルマ"}
    assert app_core.resolve_speaker_name("「面具」", "面具", terms) == "「仮面」"
    assert app_core.resolve_speaker_name("面具", "面具", terms) == "仮面"
    assert app_core.resolve_speaker_name("アルマ", "アルマ", terms) == "アルマ"
    assert app_core.resolve_speaker_name("エリン", "エリン", terms) == "エリン"


def test_real_terms_have_no_bare_duplicate():
    terms = app_core.load_terms()
    assert "白鸦" not in terms, "带引号的「白鸦」还在，裸词条目应该被过滤掉"


def test_aligned_evidence():
    import extract_knowledge as ex

    pairs = [("「白鸦」大人。", "「白鴉」様。"), ("白鸦飞过天空。", "白いカラスが空を渡る。")]
    assert ex.aligned_evidence("白鸦", "白鴉", pairs) is True
    assert ex.aligned_evidence("白鸦", "白鴉", pairs, require_quotes=True) is True
    assert ex.aligned_evidence("白鸦", "白鴉", pairs, require_quotes=False) is False
    assert ex.aligned_evidence("不存在", "ナイ", pairs) is None
    assert ex.strip_ruby("灰{RUBY[D]暗夜之眼}眸") == "灰暗夜之眼眸"


def test_filter_terms_rules():
    import extract_knowledge as ex

    texts = [
        {"speaker": "雷诺", "chinese": "我的希望是……", "japanese": "私の望みは……"},
        {"speaker": "レイノ", "chinese": "白鸦飞过天空。", "japanese": "白いカラスが空を渡る。"},
        {"speaker": "アルマ", "chinese": "「白鸦」大人。", "japanese": "「白鴉」様。"},
        {"speaker": "ルカ", "chinese": "确认死亡。", "japanese": "死を確認。"},
        {"speaker": "レイノ", "chinese": "磁石の命令。", "japanese": "磁石の命令だ。"},
        {"speaker": "レイノ", "chinese": "这是宿命。", "japanese": "これはサダメだ。"},
    ]
    agent = ex.KnowledgeMiningAgent(level=2)
    kept = agent.filter_terms({
        "希望": "希望",           # 在 terms_blacklist.txt 里 → 丢
        "魔力": "魔力",           # 中日同形、语料里也没有支撑 → 丢
        "磁石": "磁石",           # 中日同形、语料里确实这么写 → 留
        "白鸦": "白鴉",           # 引号里才是术语，裸词是普通名词 → 保持原符号
        "死亡": "死",             # 换了写法、日文侧有支持 → 留
        "宿命": "ケツマツ",           # 日文侧从没用过 → 模型自创，丢
        "抓兔子": "ウサギ狩り",   # 本场景没出现，无从判断 → 留
    }, texts)
    assert "希望" not in kept
    assert "魔力" not in kept
    assert "白鸦" not in kept
    assert kept.get("磁石") == "磁石"
    assert kept.get("「白鸦」") == "「白鴉」"
    assert kept.get("死亡") == "死"
    assert "宿命" not in kept
    assert kept.get("抓兔子") == "ウサギ狩り"
    assert agent.reject_reasons.get("希望") == "人工黑名单"
    assert agent.reject_reasons.get("魔力") == "中日同形，缺语料支撑"
    assert agent.reject_reasons.get("宿命") == "语料里找不到对应日文写法"


def test_line_similarity_and_compare():
    assert app_core.line_similarity("おっしゃる通りです。", "おっしゃる通りです。") == 1.0
    assert 0.0 < app_core.line_similarity("さあ、どうでしょうね？", "さあ、どうかしらね？") < 1.0
    cmp = app_core.compare_with_reference(
        "【阿尔玛】こほん…\n【艾琳】まだ起こしてないんだけど…",
        "【アルマ】ゴホッ、ゴホッ…\n【エリン】まだ起こしてないんだけど…",
    )
    assert cmp["lines"] == 2 and cmp["text_lines"] == 2
    assert cmp["worst"][0][1] < 0.3            # 最低分是拟声词那行
    assert cmp["worst"][0][0] == 1             # 行号对得上
    assert 0.5 < cmp["mean"] < 0.7
    # 不带【角色名】时按物理行比对
    plain = app_core.compare_with_reference("あ\nい", "あ\nい")
    assert plain["lines"] == 2 and plain["mean"] == 1.0


def test_full_review_reference_section():
    old_cos, old_chat = app_core.cosine_similarity, app_core.chat
    app_core.cosine_similarity = lambda a, b: 0.62
    app_core.chat = lambda messages, temperature=0.2: (
        '{"accuracy":80,"naturalness":80,"character_consistency":80,"terminology":80,'
        '"format_consistency":80,"world_consistency":80,"overall_score":80,"severity":"minor",'
        '"suggestions":[],"final_version":"こほん…"}'
    )
    try:
        report, info = app_core.full_review(
            "【阿尔玛】咳咳…", "【アルマ】こほん…", "【アルマ】ゴホッ、ゴホッ…"
        )
    finally:
        app_core.cosine_similarity, app_core.chat = old_cos, old_chat
    assert "【参考译文吻合度】" in report
    assert "语义相似度" in report and "字面相似度" in report
    assert "偏离最大的行" not in report        # 报告只给结果，不列逐行对照
    assert info["line"]["lines"] == 1
    assert len(info["line"]["worst"]) == 1    # 明细仍由 compare_with_reference 返回
    assert abs(info["cosine"] - 0.62) < 1e-9


if __name__ == "__main__":
    test_config_and_data(); test_json_parser(); test_term_checker_longest_first(); test_normalize_translation()
    test_drop_bare_duplicate_terms(); test_resolve_speaker_name(); test_real_terms_have_no_bare_duplicate()
    test_aligned_evidence(); test_filter_terms_rules()
    test_line_similarity_and_compare(); test_full_review_reference_section()
    print("ALL CORE TESTS PASSED")
