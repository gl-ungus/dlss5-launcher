# DLSS 5 on this RTX 4070

The DLSS NR mode is an experimental community compatibility setup. Successful
NGX calls establish execution, not equivalence to NVIDIA's supported integration
or demonstration image quality.

## Fake photorealistic AI mode

Restart the launcher and choose **Renderer → Fake · photorealistic AI (SD-Turbo)**.
Choose a game, describe the desired look, and click **Launch with Fake AI**.
Start with **Balanced · 384 pixels**. **Fast · 256 pixels** leaves more GPU time
for the game; **Detailed · 512 pixels** can fall below the 30 AI FPS target.
The output is enlarged to the game window's resolution. The reimagination slider
controls how far the model departs from the original. Prompt and resolution
changes take effect on the next game launch.

This mode uses the smaller SD 2.1-based **SD-Turbo**, TAESD, FP16 inference and
CUDA graphs, entirely locally. It does not use SDXL or DLSS. **Denoising steps**
selects 1–4 real refinement steps before decoding, with one as the default.
More steps cost AI FPS and may miss the 30 FPS target. These refine the latent
within one image generation; they do not apply the entire image model repeatedly.
**AI FPS limit** offers 10, 15, 20, 30, 45, 60, or Unlimited. The default is
30. It limits how often a new frame is sent to the model, without capping the
game. Choose both settings before launching; a cap is not a promise that the
model can reach that rate. Try 2 steps at 20 FPS when favoring refinement over
update frequency.
Fixed noise limits random changes but cannot ensure temporal consistency; expect
flickering, changed geometry, invented detail, and unreadable text.
The gentler default uses 35% reimagination. The displayed result is the model's
output, without original-image mixing or blending of previous frames.

The Vulkan layer runs asynchronously: at most one frame is in flight, and the
next input is the newest game frame available when the model finishes. Game
presentation does not synchronously wait for a model response. It reuses the latest completed
AI image between updates; an image older than the larger of 100 ms or two AI
frame intervals plus 50 ms is discarded and the original
game is shown instead. This avoids a growing queue but cannot guarantee 30 AI
FPS in a GPU-heavy game. The live status measures **new AI frames** alongside
the chosen FPS limit. It does not equate repeated presentations
of the same AI image with new generated frames.

Measured on this RTX 4070, the 384×216 model produced **38–41 AI FPS** while
vkcube presented at about 144 FPS with a 1280×720 output. Model time was about
23–26 ms. These are test-scene measurements, not a guarantee for other games.
The live test also suspends inference, verifies that original frames continue,
and tests effect toggling and renderer stop/resume while the game stays open.
Results: [photo-live-check.json](verification/photo-live-check.json).
The Gamescope compatibility-window test produced **34–37 AI FPS** at a
2544×1364 output with a 384×208 model input; see
[photo-gamescope.json](verification/photo-gamescope.json).

The old 1000% slider is now labeled **Output amplification**, belongs to DLSS NR,
and does not mean ten model passes. Fake AI has its own reimagination control.

Reinstall dependencies/models and build the isolated 64/32-bit capture layers:

```bash
bash launcher/setup_photo.sh
```

This downloads about 2.3 GB of model data and installs a dedicated Python/CUDA
environment in `runtime/photo-venv`. It requires `uv`, a C++ compiler and 32-bit
C++ development libraries. Existing DLSS runtime binaries are retained. Runtime
log: `runtime/photo/worker.log`. The separate model cache is `runtime/photo-cache`.

Verification:

```bash
python3 -m unittest discover -s tests -v
runtime/photo-venv/bin/python launcher/photo_worker.py --benchmark
python3 tests/photo_live_check.py
```

Model references: [SD-Turbo](https://huggingface.co/stabilityai/sd-turbo),
[TAESD](https://github.com/madebyollin/taesd).

Verified on 2026-09-20: CachyOS Linux, RTX 4070 12 GB, NVIDIA 615.71.09,
Proton-CachyOS, and Wayland. DLSS Neural Rendering (NGX Feature 18) creates
successfully and evaluates real frames on the GPU.

Celeste was also tested through its native Vulkan backend, including matched
before/after captures. At the user's request it remains unchanged: **no automatic
launcher hook was installed and no Steam launch option was changed**. Neural
processing was disabled and the helper stopped after the test. The optional
`celeste-dlss5` launcher only enables the effect when explicitly invoked.

## What this repository is not

Nothing NVIDIA ships is in here. A clone contains the launcher, the transport,
the workers, the tests and the measurements — no `nvngx_*.dll`, no Streamline
`sl.*.dll`, no community runtime archive, no model weights, no Proton prefix
and no test footage. Those are other people's to license, and several of them
are larger than anything a repository should carry. `.gitignore` keeps them
out; [THIRD-PARTY.md](THIRD-PARTY.md) says where each one comes from and what
to put where before the setup scripts will run.

## Run

From this directory, open the launcher:

```bash
./dlss5 gui
```

The launcher discovers installed Steam games and lets you add any other executable
or launch script. Pick a game, choose Automatic mode, and press **Launch with DLSS
5**. The green status line means successful neural frames are arriving; the frame
counter and model time are live. Quality, style, strength (including the new
1000% maximum-slop setting, including the **MAX SLOP** button), comparison, and the
effect toggle apply while the game is running. **Stop DLSS 5** releases the helper
without closing the game. Closing the window puts it in the tray; choose Quit from
the tray to stop the helper and exit.

The GUI is single-instance and stores only its own profiles in
`runtime/launcher/settings.json`. It never edits Steam manifests, Steam launch
options, or game files. The **Copy optional Steam launch option** button is the
only place that produces a Steam command, and it only copies text to the clipboard.
Use **Compatibility window · Gamescope** for OpenGL games. **Entire screen ·
fullscreen game** runs the selected game across the full display and processes its
whole output. **Window size** defaults to the connected display's own resolution,
marked *· display* in the list. Leave it there unless you have a reason not to: a
window smaller than the panel makes games that take their video mode from it
render below native resolution, and makes mouse warping land off centre, which
shows up as a view that turns in one direction only. It does not capture unrelated desktop windows; that would require a
separate Wayland/X11 screen-capture compositor. Direct mode is intended
for Vulkan and Proton; for Celeste, Automatic selects its native Vulkan executable.

The command-line wrapper remains available for scripted use:

```bash
./dlss5 start
./dlss5 stop
```

For a native Vulkan application:

```bash
./dlss5 run /path/to/application [arguments]
```

For Steam, use this per-game launch option:

```text
/path/to/dlss5/dlss5 run %command%
```

The target must present through Vulkan: native Vulkan, or a Windows game using
Proton's DXVK/VKD3D. For a game offering multiple graphics backends, select Vulkan
or run its DirectX version through Proton. Steam/game-specific operation still
needs testing for other games. Native `vkcube` and the installed Steam copy of
Celeste have been verified; Celeste was launched directly with Steam running.

The launcher starts the helper, exposes the local Vulkan layer, and enables it
only for the launched application. It preserves the game's original XDG config,
data, and state locations. Run these commands as your ordinary desktop user.

## Controls

```bash
./dlss5 status                 # helper process status
./dlss5 stats                  # frame counters and model status
./dlss5 settings               # current rendering settings
./dlss5 set enabled 0          # turn neural processing off
./dlss5 set enabled 1          # turn it on
./dlss5 set workingscale 0.67   # lower the model resolution to reduce cost
./dlss5 set workingscale 1     # restore full resolution
./dlss5 set compare 2          # comparison wipe
./dlss5 set compare 0          # disable comparison
./dlss5 stop                   # stop the helper and release GPU resources
```

Use the GUI for interactive adjustments. Direct CLI settings operate on live
shared memory; they are not a persistent per-game preset system. The helper
remains running after an application closes, until stopped or the GUI shuts it
down. Use one DLSS 5 application at a time with this shared helper.

## What was combined

- [DLSS5VKLayer 0.3.1-1](https://github.com/bmitch87/DLSS5VKLayer/releases/tag/0.3.1-1)
  captures Vulkan frames on Linux and processes them through its Windows helper.
- [Community 310.8.0-RTX40 runtime](https://github.com/RankFTW/rhi-repo/releases/tag/dlssnr-310.8.0-RTX40)
  adds Ada (`sm_89`) kernels. Its downloaded ZIP matches the SHA-256 digest
  published in the GitHub release metadata.
- The existing `/usr/share/steam/compatibilitytools.d/proton-cachyos-slr/proton`
  runs the helper and supplies DXVK-NVAPI.

The original `nvngx_dlssnr.dll` you place in this directory contains only `sm_120` kernels.
It was left untouched. The active `runtime/binaries/nvngx_dlssnr.dll` contains
`sm_89` and `sm_120`, as checked with the architecture scanner from
[DLSS5-Autopilot](https://github.com/Kizzuwatnaa/DLSS5-Autopilot).

All installation files, the dedicated Proton prefix, helper config, and logs
live under `runtime/`. Shared memory lives at `/tmp/dlssnr-1000/shm.bin`.
No system driver changes or game file replacements were needed. The packaged
helper script has one local change: `DLSSNR_PREFIX_DIR` can select the workspace
prefix instead of the hard-coded home-directory prefix.

## Verification

- 640×360: 180 requests and 180 responses, model active.
- 1280×720: another 300 frames processed; sampled helper total 5.5–6.7 ms.
- 1920×1080: Feature 18 created successfully; final cumulative request, response,
  and successful-evaluation counters all reached 632. Sampled helper total
  10.0–11.2 ms; layer total 11.5–12.6 ms.
- NVIDIA optical flow initialized and completed passes.
- A matched 720p before/after RGBA16F capture differs, with finite values:
  mean absolute RGB change 0.002775 and maximum change 0.210938.

These are synthetic cube measurements, not game FPS. Initial 1080p startup
included passthrough frames while the neural feature initialized. HDR,
frame generation have not been validated. Celeste subsequently processed over
9,000 frames at 2560×1440 with no passthrough frames in the logged intervals;
evidence is in `verification/celeste/`.

Evidence:

- [Structured results](verification/results.json)
- [1080p successful-evaluation counters](verification/1080p-status.txt)
- [1080p helper log](verification/1080p-helper.log)
- [1080p layer log](verification/1080p-layer.log)
- [720p counters](verification/720p-status.txt)
- [File hashes](verification/SHA256SUMS)

The live helper log is `runtime/state/dlssnr/helper.log`. The original matched
capture is in `runtime/state/dlssnr/captures/`: little-endian RGBA16F, 1280×720.

To repeat a short test:

```bash
./dlss5 run vkcube --c 180 --width 1920 --height 1080
```

The launcher preserves Steam's bundled runtime library paths for native Steam
games, in the same order the Steam client exports them, including the runtime's
`pinned_libs_*` and both its `lib` and `usr/lib` halves. This matters for older
32-bit titles such as Half-Life: their launcher scripts replace
`LD_LIBRARY_PATH`, and starting them outside Steam otherwise fails before the
game creates a window with errors such as missing `libvorbis.so.0`. The `lib`
half carries the oldest compatibility libraries, `libpng12.so.0` and
`libgcrypt.so.11`, which Half-Life's `chromehtml.so` and `libcef.so` need; when
they are absent that module silently fails to load and the engine segfaults in
`CBaseUI::Start` during VGUI2 startup.

When that game is launched through Gamescope, those Steam paths are passed only
to the child game. Gamescope itself keeps the system C++ runtime, avoiding
`GLIBCXX_*` and `CXXABI_*` startup errors.

To disable this installation for a game, remove its launch option. `./dlss5 stop`
stops the helper; the Vulkan layer is not registered globally.
