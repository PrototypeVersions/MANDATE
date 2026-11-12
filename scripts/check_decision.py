"""Check a decision file against the rules before committing it: python3 scripts/check_decision.py <file>"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts import engine  # noqa: E402


def main() -> int:
    path = Path(sys.argv[1])
    settings = json.loads((engine.ROOT / "config" / "settings.json").read_text(encoding="utf-8"))
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"REFUSED: not valid JSON ({exc})")
        return 1
    clean, err = engine.validate_decision(doc, path.stem, settings)
    if err:
        print("REFUSED:", err)
        return 1
    if path.parent.name != "inbox":
        print("Note: the file must be in decisions/inbox/ to be recorded.")
    print(f"OK: {clean['portfolio']} decision with {len(clean['orders'])} order(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
