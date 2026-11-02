# Phase 5: Modern AI detection, manipulation forensics and provenance signals

## Start prompt (paste into a new chat)
> Read CLAUDE.md and docs/phases/PROGRESS.md, then execute docs/phases/phase-5-provenance-signals.md.
> Every model must beat the current one on the out-of-source protocol before shipping.
> Honest evaluation only (95 % CIs). Update README/PROJECT_REPORT numbers, PROGRESS.md, commit.

## Goal
Upgrade AI-image detection and manipulation forensics to 2023–2025 methods, add cheap
high-precision provenance evidence (C2PA, watermarks, web search), and make every reported
number statistically defensible.

## Step 1: AI-image detection upgrade (benchmark-gated)
Current: linear probe on CLIP ViT-B/32 (test AUC 0.982; out-of-source 100 % genuine / 94 % Flux).
| Candidate backbone (frozen + linear probe, the UnivFD recipe) | Year | Notes |
|---|---|---|
| **SigLIP 2** (B/16 or So400m if it fits in memory) | 2025 | Strongest recent image encoder; Apache-2.0 |
| CLIP ViT-L/14 (OpenAI / OpenCLIP) | 2021–23 | The original UnivFD backbone |
| DINOv2 ViT-B/14 | 2023 | Complementary low-level features |
- Also try a **probe ensemble** (SigLIP 2 + CLIP), and a light **LoRA fine-tune** of the best backbone.
- **Selection metric**: the out-of-source protocol (`training/independent_check.py`, extend it with a
  generator unseen by *all* candidates, e.g. newer Flux/SD3.5/Imagen samples), then the held-out test AUC.
- Add newer generator data if available (Flux, SD 3.5, Ideogram, recent Midjourney) and keep one generator held out.

## Step 2: manipulation forensics upgrade
- **TruFor** (CVPR 2023): pixel-level localisation + reliability map for splicing / copy-move / inpainting.
  Replaces the ResNet-18 manipulation signal and supplements block-matching copy-move; its heatmap
  becomes the evidence image. Check the licence (likely research-only) and record it in `docs/models.md`.
- Evaluate on **real** forgery datasets: CASIA v2, Columbia, IMD2020 (and CocoGlide for inpainting).
  Report image-level AUC and pixel-level F1, next to the synthetic numbers.
- Retire the ResNet-18 if TruFor + the AI probe cover its role on the benchmarks (fewer models = faster, simpler).

## Step 3: provenance signals (cheap, high precision)
1. **C2PA Content Credentials** (`c2pa-python`): verify the manifest signature chain. A valid AI-generator
   claim → FAIL; a valid capture/edit chain → positive evidence (lowers risk).
2. **AI watermarks / metadata**: `invisible-watermark` (DWT-DCT used by Stable Diffusion pipelines),
   PNG text chunks and EXIF software tags (ComfyUI / A1111 parameters, DALL·E, Midjourney).
3. **Reverse image search** (optional key): Google Cloud Vision *Web Detection* (free 1k/month) or TinEye.
   Report the earliest matching page and date. Advisory → REVIEW, never auto-FAIL.
4. All three feed a new `provenance` policy check (with tests and fixture files).

## Step 4: statistics and policy
- Re-run `evaluate_system` with n ≥ 300 per category; bootstrap **95 % confidence intervals** for every rate.
- Recalibrate the policy; report **per-domain** false-alarm rates (paintings, collection NFTs, photos, pixel art).
- **Retraining gate script**: given new generator samples → retrain the probe → ship only if the
  out-of-source metrics do not regress. Document "how to add a new generator" in `docs/models.md`.
- Licensing: a `--permissive-only` option (drops NC/research-only data and weights) with its own metrics.

## Acceptance criteria
- `docs/models.md`: one table per task (baseline vs candidates vs shipped) with licences.
- The shipped AI detector beats CLIP-B/32 on the out-of-source protocol; real-forgery results are reported, whatever they are.
- Every headline number in the README has n and a 95 % CI.
