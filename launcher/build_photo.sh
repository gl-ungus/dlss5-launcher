#!/usr/bin/env bash
set -euo pipefail
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"
mkdir -p runtime/photo/share/vulkan/implicit_layer.d
g++ -std=c++17 -O2 -fPIC -shared launcher/photo_bridge.cpp -o runtime/libphoto_bridge.so
sources=(DLSS5VKLayer/layer_linux/src/{layer,shader_vk,dlssnr_pass,composition,capture,scaler_vk,hotkey}.cpp)
flags=(-std=c++17 -O2 -fPIC -shared -pthread -I DLSS5VKLayer/standalone_runner/third_party
       -static-libstdc++ -static-libgcc -Wl,--version-script=DLSS5VKLayer/layer_linux/dlssnr.map -Wl,-Bsymbolic)
g++ "${flags[@]}" "${sources[@]}" -o runtime/libVkLayer_photo.so
g++ -m32 -DDLSSNR_LAYER_32 "${flags[@]}" "${sources[@]}" -o runtime/libVkLayer_photo32.so
python3 - <<'PY'
import json
from pathlib import Path
root=Path.cwd()
for bits, suffix in [(64,''), (32,'32')]:
    layer=dict(name='VK_LAYER_NV_dlssnr'+('_32' if bits==32 else ''), type='GLOBAL',
               library_path=str(root/f'runtime/libVkLayer_photo{suffix}.so'),
               api_version='1.3.277', library_arch=str(bits), implementation_version='1',
               description='Experimental local photorealistic AI (not DLSS)',
               enable_environment={'VKLayer_DLSS5':'1'}, disable_environment={'DLSSNR_DISABLE':'1'})
    path=root/f'runtime/photo/share/vulkan/implicit_layer.d/photo{suffix}.json'
    path.write_text(json.dumps(dict(file_format_version='1.2.0',layer=layer),indent=2)+'\n')
PY
