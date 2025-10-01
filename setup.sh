#!/usr/bin/env bash
set -euo pipefail

VENV_DIR="venv"

python3 -m venv "$VENV_DIR"
source "$VENV_DIR/bin/activate"

python -m pip install --upgrade pip
python -m pip install -r requirements.txt

echo "Virtual environment created at '$VENV_DIR'."
echo "Run 'source $VENV_DIR/bin/activate' before executing survey123_to_pdf.py."
