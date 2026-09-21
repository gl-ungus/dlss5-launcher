#!/usr/bin/env bash
set -euo pipefail
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"
uv venv --python 3.12 --allow-existing runtime/photo-venv
uv pip install --python runtime/photo-venv/bin/python -r launcher/photo-requirements.txt
runtime/photo-venv/bin/python - <<'PY'
from pathlib import Path
from huggingface_hub import snapshot_download
cache=str(Path('runtime/photo-cache/hub').resolve())
snapshot_download('stabilityai/sd-turbo', cache_dir=cache,
    allow_patterns=['*.json','tokenizer/*','*.fp16.safetensors'])
snapshot_download('madebyollin/taesd', cache_dir=cache,
    allow_patterns=['*.json','*.safetensors'])
PY
bash launcher/build_photo.sh
