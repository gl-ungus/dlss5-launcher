"""Local SD-Turbo image-to-image worker. No NGX/DLSS model is used here."""
from __future__ import annotations

import argparse
import ctypes as C
import json
import os
from pathlib import Path
import signal
import threading
import time

ROOT = Path(__file__).resolve().parents[1]

DEFAULT_PROMPT = ('photorealistic photograph of a real world scene, realistic materials, '
                  'natural lighting, realistic textures, detailed photography')


class PhotoModel:
    """Configurable denoising, with static noise and CUDA graphs to cut CPU overhead.

    The distilled SD-Turbo model can predict a final image in one step. This is
    not an interrupted multi-step diffusion run. Fixed noise reduces random
    variation; it does not provide video-model temporal consistency.
    """
    def __init__(self, prompt=DEFAULT_PROMPT, change=.35, steps=1):
        if steps not in (1, 2, 3, 4):
            raise ValueError('SD-Turbo supports 1–4 denoising steps in this launcher.')
        os.environ['HF_HOME'] = str(ROOT / 'runtime/photo-cache')
        os.environ['HF_HUB_OFFLINE'] = '1'
        import torch
        from diffusers import AutoPipelineForImage2Image, AutoencoderTiny
        self.torch = torch
        torch.set_num_threads(4)
        if not torch.cuda.is_available():
            raise RuntimeError('CUDA is unavailable; fake AI rendering requires the NVIDIA GPU.')
        torch.backends.cudnn.benchmark = True
        vae = AutoencoderTiny.from_pretrained('madebyollin/taesd', dtype=torch.float16)
        pipe = AutoPipelineForImage2Image.from_pretrained('stabilityai/sd-turbo',
            dtype=torch.float16, variant='fp16', vae=vae).to('cuda')
        pipe.set_progress_bar_config(disable=True)
        with torch.inference_mode():
            self.embeds = pipe.encode_prompt(prompt, 'cuda', 1, False)[0]
        self.unet, self.vae = pipe.unet.eval(), pipe.vae.eval()
        pipe.scheduler.set_timesteps(1000)
        index = 999 - round(min(.85, max(.2, change)) * 999)
        indices = [min(999, index + round((1000-index)*i/steps)) for i in range(steps)]
        self.sigmas = [float(pipe.scheduler.sigmas[i]) for i in indices] + [0.0]
        self.timesteps = [pipe.scheduler.timesteps[i].to('cuda') for i in indices]
        self.prediction = pipe.scheduler.config.prediction_type
        if self.prediction not in ('epsilon', 'v_prediction'):
            raise RuntimeError(f'Unsupported prediction type: {self.prediction}')
        del pipe
        self.shape = None
        self.graph = None

    def _forward(self):
        t = self.torch
        frame = self.input.to(t.float16).permute(2, 0, 1).unsqueeze(0) / 127.5 - 1
        encoded = self.vae.encode(frame).latents * self.vae.config.scaling_factor
        sample = encoded + self.noise * self.sigmas[0]
        for i, timestep in enumerate(self.timesteps):
            sigma, next_sigma = self.sigmas[i:i+2]
            prediction = self.unet(sample / (sigma**2 + 1)**.5,
                                   timestep, encoder_hidden_states=self.embeds).sample
            if self.prediction == 'epsilon':
                clean = sample - sigma * prediction
            else:
                clean = prediction * (-sigma / (sigma**2 + 1)**.5) + sample / (sigma**2 + 1)
            # Deterministic Euler step towards the next noise level; the final
            # step returns the clean prediction. Extra steps refine this latent,
            # rather than re-encoding and reapplying the effect from scratch.
            sample = clean if next_sigma == 0 else sample + (sample-clean) / sigma * (next_sigma-sigma)
        decoded = self.vae.decode(clean / self.vae.config.scaling_factor).sample
        generated = (decoded[0].permute(1, 2, 0) / 2 + .5).clamp(0, 1)
        rgb = (generated * 255).to(t.uint8)
        return t.cat((rgb, t.full_like(rgb[:, :, :1], 255)), dim=2)

    def prepare(self, width, height):
        t = self.torch
        if self.shape == (width, height):
            return
        if width % 8 or height % 8 or min(width, height) < 64 or max(width, height) > 512:
            raise ValueError('Photo input must be 64–512 pixels, in multiples of eight.')
        self.graph = None
        self.input = t.zeros((height, width, 3), device='cuda', dtype=t.uint8)
        self.noise = t.randn((1, 4, height//8, width//8), device='cuda', dtype=t.float16,
                             generator=t.Generator(device='cuda').manual_seed(42))
        stream = t.cuda.Stream()
        stream.wait_stream(t.cuda.current_stream())
        with t.inference_mode(), t.cuda.stream(stream):
            for _ in range(3):
                self.output = self._forward()
        t.cuda.current_stream().wait_stream(stream)
        self.graph = t.cuda.CUDAGraph()
        with t.inference_mode(), t.cuda.graph(self.graph):
            self.output = self._forward()
        self.shape = (width, height)

    def render(self, rgba):
        import numpy as np
        self.prepare(rgba.shape[1], rgba.shape[0])
        self.input.copy_(self.torch.from_numpy(np.ascontiguousarray(rgba[:, :, :3])))
        self.graph.replay()
        return self.output.cpu().numpy()


def extent(width, height, edge):
    scale = edge / max(width, height)
    return tuple(max(64, int(v * scale / 8 + .5) * 8) for v in (width, height))


def bridge_api():
    lib = C.CDLL(str(ROOT / 'runtime/libphoto_bridge.so'))
    definitions = {
        'photo_open': ([C.c_char_p], C.c_void_p),
        'photo_tick': ([C.c_void_p], None), 'photo_quit': ([C.c_void_p], C.c_int),
        'photo_ready': ([C.c_void_p], None), 'photo_close': ([C.c_void_p], None),
        'photo_take': ([C.c_void_p, C.c_void_p, C.c_size_t, C.POINTER(C.c_uint32), C.POINTER(C.c_uint32)], C.c_uint32),
        'photo_answer': ([C.c_void_p, C.c_uint32, C.c_void_p, C.c_uint32, C.c_uint32, C.c_float], None),
    }
    for name, (args, result) in definitions.items():
        getattr(lib, name).argtypes = args
        getattr(lib, name).restype = result
    return lib


def write_status(path, **values):
    values['pid'] = os.getpid()
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(values))
    temp.replace(path)


def serve(args):
    import numpy as np
    config = json.loads(args.config.read_text())
    args.status.parent.mkdir(parents=True, exist_ok=True)
    args.shm.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    lib = bridge_api()
    handle = lib.photo_open(os.fsencode(args.shm))
    if not handle:
        raise RuntimeError('Another photo worker owns this shared-memory session.')
    done = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: done.set())
    signal.signal(signal.SIGINT, lambda *_: done.set())
    def heartbeat():
        while not done.wait(.2):
            lib.photo_tick(handle)
    ticker = threading.Thread(target=heartbeat, daemon=True)
    ticker.start()
    try:
        write_status(args.status, state='loading', label='Loading SD-Turbo…')
        model = PhotoModel(config['prompt'], config['change'], config.get('steps', 1))
        w, h = extent(config['width'], config['height'], config['edge'])
        write_status(args.status, state='loading', label='Warming up the image model…')
        model.prepare(w, h)
        lib.photo_ready(handle)
        write_status(args.status, state='ready', label='Fake AI ready', resolution=[w, h])
        buffer = np.empty((512*512*4,), dtype=np.uint8)
        width, height = C.c_uint32(), C.c_uint32()
        frames = 0
        started = last_status = time.monotonic()
        while not done.is_set() and not lib.photo_quit(handle):
            seq = lib.photo_take(handle, buffer.ctypes.data, buffer.nbytes, C.byref(width), C.byref(height))
            if not seq:
                done.wait(.0005)
                continue
            start = time.perf_counter()
            source = buffer[:width.value*height.value*4].reshape(height.value, width.value, 4)
            answer = model.render(source)
            ms = (time.perf_counter()-start)*1000
            lib.photo_answer(handle, seq, answer.ctypes.data, width.value, height.value, ms)
            frames += 1
            now = time.monotonic()
            if now-last_status > 1:
                write_status(args.status, state='active', label='Fake AI is processing frames',
                             fps_limit=config.get('fps', 30), steps=config.get('steps', 1),
                             frames=frames, average_fps=frames/(now-started), inference_ms=ms,
                             resolution=[width.value,height.value])
                last_status = now
    except Exception as exc:
        write_status(args.status, state='error', label=str(exc))
        raise
    finally:
        done.set(); ticker.join()
        lib.photo_close(handle)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--shm', type=Path)
    parser.add_argument('--config', type=Path)
    parser.add_argument('--status', type=Path)
    parser.add_argument('--benchmark', action='store_true')
    args = parser.parse_args()
    if args.benchmark:
        import numpy as np
        model = PhotoModel()
        for w, h in [(512, 288), (384, 216), (256, 144)]:
            frame = np.random.default_rng(42).integers(0,256,(h,w,4),dtype=np.uint8)
            model.prepare(w,h)
            timings=[]
            for i in range(45):
                start=time.perf_counter(); model.render(frame)
                if i>=5: timings.append((time.perf_counter()-start)*1000)
            print(json.dumps(dict(size=[w,h],mean_ms=float(np.mean(timings)),p95_ms=float(np.percentile(timings,95)))),flush=True)
    else:
        serve(args)


if __name__ == '__main__':
    main()
