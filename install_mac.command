#!/bin/bash
# ============================================================
#  COVER AUDIO TOOL - CAI DAT CHO MAC (chay 1 lan duy nhat)
#  Bam dup file nay trong Finder. Yeu cau: Python 3.11, ffmpeg
#  (cai bang Homebrew - xem README / HUONG_DAN.md).
# ============================================================
cd "$(dirname "$0")" || exit 1

pause_exit() {
    echo
    read -r -p "Nhan Enter de dong cua so..." _
    exit "${1:-0}"
}

echo
echo "============================================================"
echo "  COVER AUDIO TOOL - Dang cai dat cho Mac..."
echo "============================================================"
echo

# --- Tim Python 3.11 / 3.10 ---
PY=""
for cand in python3.11 python3.10 /opt/homebrew/bin/python3.11 /usr/local/bin/python3.11 python3; do
    if command -v "$cand" >/dev/null 2>&1; then
        ver=$("$cand" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null)
        if [ "$ver" = "3.11" ] || [ "$ver" = "3.10" ]; then
            PY="$cand"
            break
        fi
    fi
done
if [ -z "$PY" ]; then
    echo "[LOI] Khong tim thay Python 3.11 (hoac 3.10)."
    echo "  Cai bang Homebrew:  brew install python@3.11"
    echo "  hoac tai tu https://www.python.org/downloads/macos/"
    pause_exit 1
fi
echo "Dung Python: $PY ($("$PY" --version 2>&1))"

echo "[1/5] Tao virtual environment (.venv)..."
if [ ! -d ".venv" ]; then
    "$PY" -m venv .venv || { echo "[LOI] Khong tao duoc virtual env."; pause_exit 1; }
else
    echo "      .venv da ton tai - bo qua."
fi
VPY=".venv/bin/python"

echo "[2/5] Nang cap pip..."
"$VPY" -m pip install --upgrade pip

echo "[3/5] Cai PyTorch (ban cho Mac)..."
"$VPY" -m pip install torch==2.5.1 || { echo "[LOI] Cai torch that bai."; pause_exit 1; }

echo "[4/5] Cai cac thu vien con lai (co the mat vai phut)..."
"$VPY" -m pip install -r requirements.txt || { echo "[LOI] Cai dependencies that bai."; pause_exit 1; }

echo "[5/5] Cai trinh duyet Chromium cho Playwright (tu dong hoa Suno)..."
"$VPY" -m playwright install chromium || \
    echo "[CANH BAO] Cai Chromium that bai - chay lai: .venv/bin/python -m playwright install chromium"

# --- Kiem tra ffmpeg (bat buoc cho tron nhac) ---
if ! command -v ffmpeg >/dev/null 2>&1; then
    echo
    echo "[CANH BAO] Khong tim thay ffmpeg."
    echo "  Cai bang lenh:  brew install ffmpeg"
fi

# --- Tao file .env neu chua co ---
if [ ! -f ".env" ] && [ -f ".env.example" ]; then
    cp .env.example .env
    echo
    echo "[!] Da tao file .env tu mau."
fi

chmod +x start_mac.command 2>/dev/null

echo
echo "============================================================"
echo "  CAI DAT XONG! Bam dup start_mac.command de mo Cover Audio Tool."
echo "============================================================"
pause_exit 0
