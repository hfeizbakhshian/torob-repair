"""Export `contracts/openapi.json` from the code, with no database or model key needed.

The frontend's TypeScript types are generated from this file, and `npm run api:check`
compares a fresh export against the committed one.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from app.config import REPO_ROOT

TARGET = REPO_ROOT / "contracts" / "openapi.json"


def build() -> dict[str, object]:
    from app.main import create_app

    return create_app().openapi()


def main() -> int:
    document = build()
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"

    if "--check" in sys.argv:
        if not TARGET.exists():
            print(f"قرارداد {TARGET} وجود ندارد؛ ابتدا آن را صادر کنید.", file=sys.stderr)
            return 1
        if TARGET.read_text(encoding="utf-8") != rendered:
            print(
                "قرارداد OpenAPI با کد بک‌اند هم‌خوان نیست؛ "
                "`uv run python -m app.export_openapi` را اجرا کنید.",
                file=sys.stderr,
            )
            return 1
        print("قرارداد OpenAPI با کد هم‌خوان است.")
        return 0

    TARGET.write_text(rendered, encoding="utf-8")
    print(f"قرارداد در {Path(TARGET).relative_to(REPO_ROOT)} نوشته شد.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
