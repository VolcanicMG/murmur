# Third-party notices

murmur's own license is not stated here. This file lists what else ships with, or is downloaded by, murmur. Versions are the exact pins in `requirements.txt` / `requirements-build.txt`; transitive versions are those resolved in the build environment (Python 3.14, Windows). License identifiers are SPDX, taken from each package's own metadata or the upstream repository's license; full texts are in each package's `*.dist-info` inside `_internal/` or at the upstream URL.

## 1. Python packages in the build

Direct dependencies (`requirements.txt`):

| Package | Version | License | Upstream |
|---|---|---|---|
| onnx-asr | 0.12.0 | MIT | https://github.com/istupakov/onnx-asr |
| onnxruntime | 1.30.0 | MIT | https://onnxruntime.ai |
| sounddevice | 0.5.6 | MIT | https://python-sounddevice.readthedocs.io (bundles PortAudio, MIT-style) |
| keyboard | 0.13.5 | MIT | https://github.com/boppreh/keyboard |
| pystray | 0.19.5 | LGPL-3.0-only | https://github.com/moses-palmer/pystray |
| pyperclip | 1.11.0 | BSD-3-Clause | https://github.com/asweigart/pyperclip |
| pywin32 | 312 | PSF-2.0 | https://github.com/mhammond/pywin32 |
| numpy | 2.5.3 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 (its wheel also carries OpenBLAS/GCC runtime libraries under their own licenses) | https://numpy.org |
| huggingface-hub | 2.1.1 | Apache-2.0 | https://github.com/huggingface/huggingface_hub |
| pillow | 12.3.0 | MIT-CMU | https://python-pillow.github.io |
| uiautomation | 2.0.29 | Apache-2.0 | https://github.com/yinkaisheng/Python-UIAutomation-for-Windows |
| comtypes | 1.4.17 | MIT | https://github.com/enthought/comtypes |

Transitive packages pulled in by the above (installed in the build venv; PyInstaller bundles those the app imports):

| Package | Version | License | Upstream |
|---|---|---|---|
| anyio | 4.15.1 | MIT | https://github.com/agronholm/anyio |
| cffi | 2.1.1 | MIT-0 | https://cffi.readthedocs.io |
| pycparser | 3.0 | BSD-3-Clause | https://github.com/eliben/pycparser |
| click | 8.5.0 | BSD-3-Clause | https://github.com/pallets/click |
| colorama | 0.4.6 | BSD-3-Clause | https://github.com/tartley/colorama |
| filelock | 4.0.12 | MIT | https://github.com/tox-dev/filelock |
| flatbuffers | 25.12.19 | Apache-2.0 | https://github.com/google/flatbuffers |
| fsspec | 2026.9.0 | BSD-3-Clause | https://github.com/fsspec/filesystem_spec |
| h11 | 0.16.0 | MIT | https://github.com/python-hyper/h11 |
| hf-xet | 1.6.0 | Apache-2.0 | https://github.com/huggingface/xet-core |
| httpx2 | 2.13.1 | BSD-3-Clause | https://github.com/pydantic/httpx2 |
| httpcore2 | 2.13.1 | BSD-3-Clause | https://github.com/pydantic/httpx2 |
| idna | 3.20 | BSD-3-Clause | https://github.com/kjd/idna |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause | https://github.com/pypa/packaging |
| protobuf | 7.36.2 | BSD-3-Clause | https://github.com/protocolbuffers/protobuf |
| PyYAML | 6.0.3 | MIT | https://pyyaml.org |
| setuptools | 84.0.0 | MIT | https://github.com/pypa/setuptools |
| six | 1.17.0 | MIT | https://github.com/benjaminp/six |
| tqdm | 4.70.1 | MPL-2.0 AND MIT | https://tqdm.github.io |
| typing_extensions | 4.16.0 | PSF-2.0 | https://github.com/python/typing_extensions |

Build tooling that is not shipped in the app but whose bootloader is: PyInstaller 6.22.3, GPL-2.0-or-later with a special exception that allows the apps it builds to be distributed under any terms (https://pyinstaller.org). Its build-only helpers (altgraph, pefile, pywin32-ctypes, pyinstaller-hooks-contrib) are not included in the app.

Also in the build folder: the CPython 3.14 runtime and standard library (PSF-2.0, https://www.python.org), Tcl/Tk 8.6 (Tcl/Tk license, BSD-style, https://www.tcl-lang.org), OpenSSL 3 (Apache-2.0, https://www.openssl.org), and the Microsoft Visual C++ runtime DLLs (redistributable under Microsoft's VC++ redistribution terms).

## 2. Bundled models

| Model | Version | License | Upstream |
|---|---|---|---|
| NVIDIA Parakeet TDT 0.6B v2 (English), as the ONNX int8 export `istupakov/parakeet-tdt-0.6b-v2-onnx` | v2, int8; Hugging Face revision not pinned by the build | CC-BY-4.0 | https://huggingface.co/nvidia/parakeet-tdt-0.6b-v2 (original), https://huggingface.co/istupakov/parakeet-tdt-0.6b-v2-onnx (ONNX conversion) |
| Silero VAD, as the ONNX export `istupakov/silero-vad-onnx` | Hugging Face revision not pinned by the build | MIT | https://github.com/snakers4/silero-vad (original), https://huggingface.co/istupakov/silero-vad-onnx |

## 3. Downloaded at runtime (not bundled)

Fetched only on first use or when the user clicks the button; never shipped in the release zip.

| Component | Version | License | Upstream |
|---|---|---|---|
| llama.cpp runtime (`llama-server`, Vulkan/CUDA/CPU Windows builds, SHA-256 pinned in `murmur/localllm.py`) | b11456 | MIT | https://github.com/ggml-org/llama.cpp |
| Gemma 4 E4B instruction model, GGUF Q6_K (`lmstudio-community/gemma-4-E4B-it-GGUF`) | Q6_K | Apache-2.0 (per the Hugging Face model card) | https://huggingface.co/google/gemma-4-E4B-it, https://huggingface.co/lmstudio-community/gemma-4-E4B-it-GGUF |

## 4. Website fonts (`docs/fonts`, GitHub Pages site only; not in the app)

| Font | License | Upstream |
|---|---|---|
| Hanken Grotesk | OFL-1.1 | https://github.com/marcologous/hanken-grotesk |
| JetBrains Mono | OFL-1.1 | https://github.com/JetBrains/JetBrainsMono |

The font files are the latin subsets from Fontsource (jsDelivr), fetched 2026-10-11; the upstream version of each was not recorded.

## Obligations for a redistributor

- **Parakeet TDT 0.6B v2 (CC-BY-4.0):** give credit to NVIDIA (and the ONNX converter), link the license, and say the model was modified (converted to ONNX and int8-quantized). This section serves as that notice.
- **pystray (LGPL-3.0):** you must let users replace the library with a modified version and provide its source (linked above). In this build pystray is pure Python inside PyInstaller's archive in `murmur.exe`, which makes replacing it awkward; relinking support (shipping it as a loose module) is an open item.
- **tqdm (MPL-2.0):** the MPL covers tqdm's own files; keep its notices and make its source available (linked above). Unmodified use adds nothing else.
- **PyInstaller bootloader (GPL-2.0-or-later with exception):** the exception covers distributing the built app under any terms; no source offer is required for murmur itself.
- **numpy bundled libraries (OpenBLAS, GCC runtime):** shipped inside `numpy.libs`; their licenses are in numpy's `dist-info`.
- **Gemma (downloaded, not shipped):** Apache-2.0 per the model card; if the user's copy comes from elsewhere, that source's terms apply.
- MIT, BSD, Apache-2.0, PSF, MIT-CMU, MIT-0, 0BSD, OFL-1.1 (fonts: do not sell the fonts on their own; keep the copyright notice): keep the copyright and license text; the `dist-info` folders in `_internal/` carry them.
