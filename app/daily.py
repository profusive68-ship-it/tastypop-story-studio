"""launchd / 터미널에서 1회 실행되는 일일 자동 제작 엔트리.

사용:
    .venv/bin/python -m app.daily          # 예약 조건(요일·시간) 확인 후 실행
    .venv/bin/python -m app.daily --force  # 조건 무시하고 즉시 1편 제작
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    from app import scheduler

    res = scheduler.daily_job(force=args.force)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
