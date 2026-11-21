#!/bin/bash
# TastyPop Studio — 맥북 원클릭 실행
# 더블클릭하면 가상환경 구성 → 필요한 패키지 설치 → 대시보드(http://localhost:8766) 실행
cd "$(dirname "$0")" || exit 1
set -u
BOLD="\033[1m"; GREEN="\033[0;32m"; YELLOW="\033[0;33m"; RED="\033[0;31m"; NC="\033[0m"

echo -e "${BOLD}🧀 TastyPop Studio${NC} 를 시작합니다…\n"

pause_exit () {
  echo ""
  echo -e "${YELLOW}계속하려면 아무 키나 누르세요…${NC}"
  read -n 1 -s
  exit "${1:-1}"
}

# 1) Python 3 확인 -----------------------------------------------------------
PY=""
for c in python3.12 python3.11 python3.10 python3; do
  if command -v "$c" >/dev/null 2>&1; then
    if "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3,9) else 1)' 2>/dev/null; then PY="$c"; break; fi
  fi
done
if [ -z "$PY" ]; then
  echo -e "${RED}Python 3 를 찾을 수 없습니다.${NC}"
  echo "터미널에서 아래를 먼저 실행해 주세요:"
  echo "  xcode-select --install      (또는)  brew install python"
  pause_exit 1
fi
echo -e "· Python: $($PY -V 2>&1)"

# 2) 가상환경 ---------------------------------------------------------------
if [ ! -d ".venv" ]; then
  echo -e "· 처음 실행이라 가상환경(.venv)을 만듭니다 (1~2분)"
  "$PY" -m venv .venv || { echo -e "${RED}가상환경 생성 실패${NC}"; pause_exit 1; }
fi
# shellcheck disable=SC1091
source .venv/bin/activate

# 3) 패키지 설치 ------------------------------------------------------------
if [ ! -f ".venv/.installed_v$(cat VERSION 2>/dev/null || echo 1)" ]; then
  echo -e "· 필요한 패키지를 설치합니다 (처음 한 번만, 3~6분)"
  python -m pip install --upgrade pip >/dev/null 2>&1
  if ! python -m pip install -r requirements.txt; then
    echo -e "${YELLOW}· 기본 저장소 설치 실패 → 백업 저장소로 재시도${NC}"
    python -m pip install -r requirements.txt -i https://download.pytorch.org/whl/cpu >/dev/null 2>&1 || \
    python -m pip install -r requirements.txt --index-url https://pypi.org/simple
  fi
  touch ".venv/.installed_v$(cat VERSION 2>/dev/null || echo 1)"
fi

# 4) ffmpeg ----------------------------------------------------------------
if ! command -v ffmpeg >/dev/null 2>&1 && [ ! -x ".venv/bin/ffmpeg" ]; then
  echo -e "${YELLOW}· ffmpeg 가 없습니다. 자동 대체 바이너리를 사용합니다.${NC}"
  echo "  (더 빠른 성능을 원하시면 터미널에서: brew install ffmpeg)"
fi

# 5) 서버 실행 --------------------------------------------------------------
PORT=$(python - <<'PY'
try:
    from app.settings import load_settings
    print(load_settings()["server"]["port"])
except Exception:
    print(8766)
PY
)
echo -e "${GREEN}· 대시보드 주소: http://localhost:${PORT}${NC}"
echo -e "· 이 창을 닫으면 프로그램이 종료됩니다.\n"
( sleep 4; open "http://localhost:${PORT}" >/dev/null 2>&1 ) &
python -m uvicorn app.main:app --host 127.0.0.1 --port "$PORT" || {
  echo -e "${RED}서버 실행 실패${NC}"
  pause_exit 1
}
