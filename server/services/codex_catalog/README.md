# Codex custom-model fallback

`prompt.md` is an unmodified copy of the unknown-model coding instructions from
OpenAI Codex, tag `rust-v0.166.0-alpha.5.1`:

https://github.com/openai/codex/blob/rust-v0.166.0-alpha.5.1/codex-rs/models-manager/prompt.md

The `rust-v0.144.6` fallback has identical bytes. SHA-256:

`ac8ae107a0d72fe3476b430afb161ea4e67da2e446d778aefc44828160559807`

Codex's native model catalog requires instructions. Supplying an empty string
would clear the client's coding baseline, rather than preserve it. The adapter
therefore uses this client fallback only when the selected upstream supplies no
native model instructions. This file does not declare upstream reasoning,
context-window, image, service-tier or tool capabilities.

Models with their own native `base_instructions` or
`model_messages.instructions_template` retain those instructions. A provider
whose model names match a Codex built-in family may have a family-specific
baseline; that baseline should be supplied in its native metadata if needed.

The upstream Apache 2.0 license and attribution notice are retained alongside
the asset as `LICENSE.txt` and `NOTICE.txt`.
