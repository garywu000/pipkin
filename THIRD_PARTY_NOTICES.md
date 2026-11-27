# Third-party notices

- **KaTeX** supplies math rendering. See [the bundled KaTeX license](dist/vendor/KaTeX-LICENSE).
- **xterm.js** and its WebGL addon supply the terminal. See [the bundled xterm.js license](dist/vendor/xterm-LICENSE).
- **Ollama** is installed separately. Models installed through Ollama carry their own licenses.
- **whisper.cpp** (MIT License, Copyright (c) 2023-2026 The ggml authors) is compiled into the app as `whisper-cli`, the dictation engine. Pinned to v1.9.5.
- **OpenAI Whisper models** (MIT License), converted to the ggml format by the whisper.cpp project, are downloaded only when you choose one in Settings › Voice.
- **sherpa-onnx** (Apache License 2.0, Copyright (c) the k2-fsa authors) is compiled into the natural voice helper (`pipkin-tts`, built from `tts/pipkin-tts.c`). Pinned to v2.0.1. Its license ships in `licenses/`.
- **ONNX Runtime** (MIT License, Copyright (c) Microsoft Corporation) runs the voice model. Its license and third-party notices ship in `licenses/`.
- **Kokoro-82M** voice model (Apache License 2.0, by hexgrad) and its pronunciation dictionaries are downloaded only when you choose the natural voice in Settings › Voice. Pipkin keeps only the model, voices, tokens and English dictionaries from the pack.
