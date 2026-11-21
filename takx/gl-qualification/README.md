<!-- Copyright 2026 uds-takserver authors -->
<!-- SPDX-License-Identifier: GPL-3.0-only -->

# Hosted Mesa + Xvfb OpenGL ES 3.0 qualification harness

This opt-in Linux `amd64` harness qualifies real windowed OpenGL ES 3.0+ rendering under `Xvfb` and Mesa `llvmpipe` (`LIBGL_ALWAYS_SOFTWARE=1`), providing the hosted graphics environment required to exercise the staged TAKX Kernel rendering PRs ([#498](https://github.com/jonathanagustin/uds-takserver/pull/498), [#499](https://github.com/jonathanagustin/uds-takserver/pull/499), [#589](https://github.com/jonathanagustin/uds-takserver/pull/589)).

## Controls

`gl_controls.c` compiles against system X11, EGL, and GLES3 libraries (`-Wall -Wextra -Werror`) and verifies four real pixel-readback controls on an Xvfb window surface:

1. **`xvfb_es3_clear_readback`**: Opens an Xvfb X11 display/window, initializes EGL + OpenGL ES 3.x on Mesa `llvmpipe`, clears the framebuffer, and verifies exact RGBA readback.
2. **`textured_polygon_atlas_readback`** (supports `#498` / `#424`): Compiles GLSL ES 3.00 vertex/fragment shaders, uploads a 2×2 RGBA texture atlas (`GL_NEAREST`, `GL_CLAMP_TO_EDGE`), renders a textured polygon sampling a specific atlas quadrant, and verifies exact readback.
3. **`context_destroy_recreate_invalidation`** (supports `#499` / `#486`): Destroys the initial GLES3 context, creates a fresh context on the same Xvfb surface, asserts prior texture/program handles are invalidated (`glIsTexture`/`glIsProgram == GL_FALSE`), renders in the recreated context, and verifies readback.
4. **`layer_visibility_clear_redraw`** (supports `#589`): Renders a visible foreground polygon pass, verifies foreground pixels, disables the layer, clears and redraws without the layer, and verifies zero stale cached pixels remain.

Every run hashes all loaded ELF providers reported by `dl_iterate_phdr` and writes `gl-qualification-receipt.json` with explicit scope bounds (`software_mesa_xvfb: true`, `hardware_gpu_accepted: false`, `sdk_accepted: false`, `client_accepted: false`).

## Usage

Offline verification (no compiler or Xvfb):

```sh
python3 -B takx/gl-qualification/qualify.py --phase verify
python3 -B -m unittest discover -s takx/gl-qualification -p test_qualify.py -v
```

Hosted / local Mesa + Xvfb qualification:

```sh
python3 -B takx/gl-qualification/qualify.py --phase qualify --output /tmp/takx-gl-qual
```
