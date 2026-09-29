import argparse
import json
import sys

from extract_knowledge import KnowledgeMiningAgent, OUTPUT_DIR


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--level", type=int, default=3, choices=[1, 2, 3])
    args = parser.parse_args()

    try:
        agent = KnowledgeMiningAgent(level=args.level)
        agent.run()

        print(json.dumps({
            "success": True,
            "level": agent.level,
            "mode": agent.mode,
            "scenes": len(agent.scene_worldviews),
            "texts": agent.total_texts,
            "characters": len(agent.all_characters),
            "terms": len(agent.all_terms),
            "world_setting_len": len(agent.world_setting),
            "output_dir": str(OUTPUT_DIR),
        }, ensure_ascii=False))

    except Exception as e:
        print(json.dumps({"success": False, "error": str(e)}, ensure_ascii=False))
        sys.exit(1)


if __name__ == "__main__":
    main()