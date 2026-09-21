"""Opt-in CUDA smoke check for the four-step graph."""
from pathlib import Path
import sys
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'launcher'))
import numpy as np
from photo_worker import PhotoModel

model = PhotoModel(steps=4)
assert len(model.timesteps) == 4
assert all(a > b for a,b in zip(model.sigmas,model.sigmas[1:]))
frame = np.random.default_rng(42).integers(0,256,(144,256,4),dtype=np.uint8)
model.prepare(256,144)
start=time.perf_counter()
result=model.render(frame)
assert result.shape==frame.shape and result.dtype==np.uint8
assert np.all(result[:,:,3]==255) and np.std(result[:,:,:3])>0
print(f'Four-step CUDA graph passed: {(time.perf_counter()-start)*1000:.1f} ms at 256x144')
