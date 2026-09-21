# What a clone does not contain, and where it comes from

This repository carries the launcher, the shared-memory transport, the photo
worker, the tests and the measured results. Everything below is deliberately
absent: it belongs to someone else, or it is a build product, or both. Put the
pieces you need in place yourself and the setup scripts take it from there.

## NVIDIA binaries — you supply your own

| File | What it is | Where yours comes from |
| --- | --- | --- |
| `nvngx_dlss.dll`, `nvngx_dlssg.dll`, `nvngx_dlssnr.dll` | NGX feature libraries, including the neural-rendering model | Your own NVIDIA/DLSS SDK download or a game you own that ships them |
| `sl.common.dll`, `sl.dlss.dll`, `sl.dlss_g.dll`, `sl.dlss_nr.dll`, `sl.interposer.dll`, `sl.nis.dll`, `sl.pcl.dll`, `sl.reflex.dll` | NVIDIA Streamline runtime | The Streamline SDK release matching those features |
| `dlssnr-rtx40.zip` | Community 310.8.0-RTX40 runtime that adds Ada (`sm_89`) kernels | <https://github.com/RankFTW/rhi-repo/releases/tag/dlssnr-310.8.0-RTX40> |

These are licensed to whoever downloads them. Redistributing them here would
be republishing NVIDIA's software, so the repository refuses to hold them and
`.gitignore` keeps them from being added by accident. Drop them in the project
root exactly as the README describes; `runtime/binaries/` is then built from
your copies and never committed.

## Upstream projects — clone them beside this one

| Directory | Project | Source |
| --- | --- | --- |
| `DLSS5VKLayer/` | The Linux Vulkan capture layer and its Windows helper, 0.3.1-1. `launcher/build_photo.sh` compiles the layer from these sources and `launcher/photo_bridge.cpp` includes `common/shm_protocol.h` from it | <https://github.com/bmitch87/DLSS5VKLayer> |
| `DLSS5-Autopilot/` | Where the CUDA-architecture scanner used to check the runtime's kernels comes from | <https://github.com/Kizzuwatnaa/DLSS5-Autopilot> |
| `dlssnr-0.3.1-1-linux-x86_64/`, `dlssnr-linux.tar.gz` | The packaged release of the layer, unpacked | The DLSS5VKLayer release above |

They have their own licences and their own history, so they are referenced
rather than vendored. Clone them into the project root before running
`launcher/setup_photo.sh`.

## Generated at setup time

`runtime/` holds everything the scripts build or download: the dedicated Proton
prefix, the `photo-venv` Python environment, the SD-Turbo and TAESD weights
(about 2.3 GB, from <https://huggingface.co/stabilityai/sd-turbo> and
<https://github.com/madebyollin/taesd>), the compiled 64- and 32-bit layers,
logs, and the launcher's own settings. None of it is committed; recreate it
with:

```bash
bash launcher/setup_photo.sh
```

## Test media and game frames

`testvideo/` and any `.webm`, `.mkv` or `.mp4` beside it are ignored: footage
used to look at the filter is the property of whoever made it. The matched
before/after captures under `verification/celeste/` are frames of Celeste, so
the images are ignored too — the logs, the manifest and the measurements that
cite them are kept, because those are ours.
