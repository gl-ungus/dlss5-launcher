"""Benchmark genuine one-step image-to-image inference before integrating it."""
import os
from pathlib import Path
os.environ.setdefault('HF_HOME', str(Path(__file__).resolve().parents[1] / 'runtime/photo-cache'))
import json
import time
import torch
from diffusers import AutoPipelineForImage2Image, AutoencoderTiny

torch.set_num_threads(4)
torch.backends.cudnn.benchmark = True
vae = AutoencoderTiny.from_pretrained('madebyollin/taesd', torch_dtype=torch.float16)
pipe = AutoPipelineForImage2Image.from_pretrained('stabilityai/sd-turbo',
    torch_dtype=torch.float16, variant='fp16', vae=vae).to('cuda')
pipe.set_progress_bar_config(disable=True)
with torch.inference_mode():
    embeds = pipe.encode_prompt('photorealistic photograph, natural lighting, realistic materials, detailed real world scene',
        'cuda', 1, False)[0]
    for w,h in [(512,288),(384,216),(256,144)]:
        frame=torch.rand(1,3,h,w,device='cuda',dtype=torch.float16)*2-1
        timings=[]
        for i in range(12):
            start=time.perf_counter()
            result=pipe(prompt_embeds=embeds, image=frame, strength=.5, num_inference_steps=2,
                        guidance_scale=0.,output_type='pt').images
            torch.cuda.synchronize()
            if i>=3: timings.append((time.perf_counter()-start)*1000)
        print(json.dumps(dict(size=[w,h],mean_ms=sum(timings)/len(timings),min_ms=min(timings),max_ms=max(timings))),flush=True)
