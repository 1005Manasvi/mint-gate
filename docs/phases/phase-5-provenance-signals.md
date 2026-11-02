# Phase 5: Provenance signals and ML hardening

## Start prompt (paste into a new chat)
> Read CLAUDE.md and docs/phases/PROGRESS.md, then execute docs/phases/phase-5-provenance-signals.md.
> Honest evaluation only (held-out + out-of-source, 95 % CIs). Update README/PROJECT_REPORT numbers, PROGRESS.md, commit.

## Goal
Add cheap, high-precision provenance evidence, catch art stolen from the open web, and make
the ML evaluation rigorous enough to survive expert questioning.

## Tasks
1. **C2PA Content Credentials** check (`c2pa-python`): if a manifest exists, verify the signature
   chain and report the claim generator (Photoshop, a camera, or an AI tool). A valid AI-tool claim → FAIL;
   a valid capture/edit chain → positive evidence (lowers risk).
2. **AI watermark / metadata detection**: invisible watermarks (`invisible-watermark` DWT-DCT used by
   Stable Diffusion), plus PNG text chunks / EXIF software tags (DALL·E, Midjourney, ComfyUI parameters).
   This is a new `provenance` check in the policy.
3. **Reverse image search** (optional key): Google Cloud Vision *Web Detection* (free 1k/month) or
   TinEye; report the earliest matching page and its date. Advisory → REVIEW, never auto-FAIL.
4. **Real manipulation data**: evaluate copy-move/splice on CASIA v2 and Columbia (or IMD2020);
   report real-forgery recall next to the synthetic numbers.
5. **Statistics**: re-run `evaluate_system` with n ≥ 300 per category; bootstrap **95 % confidence
   intervals** for every reported rate; update README / PROJECT_REPORT tables.
6. **Generalisation upgrade**: try a LoRA fine-tune of CLIP (or a DINOv2 probe) and compare on the
   out-of-source protocol (`training/independent_check.py`). Ship only if it beats the current probe there.
7. **Retraining gate**: a script that, given new generator samples, retrains the probe and refuses to
   ship unless out-of-source metrics do not regress. Document how to add a new generator.
8. Licensing: a `--permissive-only` dataset option (drops NC/research-only sources) and its
   metrics, so a commercial path exists.

## Acceptance criteria
- New `provenance` check live with tests; C2PA and watermark fixtures pass.
- Every headline metric in the README has a 95 % CI and n.
- The real-forgery evaluation is reported, whatever the result.
