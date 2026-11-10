# Models: register, licences, benchmarks

Every model Mint-Gate runs, why it is there, and the benchmark that earned it its place
(CLAUDE.md "Model-selection rules": benchmark before adopting, generalisation over in-distribution
accuracy, domain-aware evaluation, hardware budget, licences, fewer models wins ties).

## Licence register

| Model | Role | Weights | Licence | Status |
|---|---|---|---|---|
| CLIP ViT-B/32 (`openai/clip-vit-base-patch32`) | AI-detector backbone (frozen, weight 0.3); was the v1 copy score | Hugging Face | MIT | **in use** |
| SigLIP 2 B/16-256 (`google/siglip2-base-patch16-256`) | AI-detector backbone (frozen, weight 0.7) | Hugging Face (vision tower only) | Apache-2.0 | **in use** (Phase 5) |
| Linear probes (`model/ai_detector.json`, format 2) | AI-generated probability | trained here | MIT (code) / dataset terms (some research-only) | **in use** |
| Permissive probe (`model/ai_detector.permissive.json`) | CLIP-B/32 probe, MIT / CDLA-P / public-domain / CC BY data only | trained here | MIT / CDLA-P / CC BY | permissive profile |
| CLIP ViT-L/14, SigLIP 2 So400m/16, DINOv2 S/14 + B/14 (as AI backbones) | AI-detector candidates | Hugging Face | MIT / Apache-2.0 | benchmarked: L/14 + So400m win but break the CPU budget; DINOv2 loses |
| SigLIP 2 B/16 + LoRA (r 8, q/v) | AI-detector candidate | trained here | Apache-2.0 | benchmarked, **rejected** (no gain over the frozen probe) |
| TruFor (GRIP-UNINA, `third_party/trufor`) | manipulation score + localisation map, REVIEW-only | grip.unina.it, converted to safetensors | **non-commercial / nonprofit only** | **in use, GPU profile only**; never in the permissive profile |
| c2pa-python 0.38, invisible-watermark 0.2 | provenance (C2PA, SD / SDXL watermarks) | PyPI | MIT / Apache-2.0, MIT | **in use** |
| ResNet-18 (`model/best_model.pt`) | v1-v4 forgery probability, REVIEW-only | trained here (ImageNet init, BSD-3) | BSD-3 / dataset terms | **retired** in Phase 5 (chance on real forgeries) |
| pHash 64-bit (`imagehash`) | v1 exact / near-exact filter | algorithm | BSD-2 | **retired** (PDQ); benchmark baseline only |
| ORB + RANSAC (OpenCV) | v1 geometric verification | algorithm | Apache-2.0 | **retired**; benchmark baseline only |
| PDQ (`pdqhash` 0.2.8, Meta ThreatExchange) | exact / near-exact hash (`copy_exact`) | algorithm | BSD-3 | **in use** |
| SSCD `sscd_disc_mixup` (TorchScript, SHA-256 pinned) | copy descriptor + region crops (`copy_semantic`) | Meta, dl.fbaipublicfiles.com | MIT (repository licence covers the released models); trained on DISC21 | **in use** |
| DINOv2 ViT-S/14 (`facebook/dinov2-small`) | fusion descriptor (weight 0.2) | Hugging Face | Apache-2.0 | **in use**; ViT-B/14 benchmarked, not needed |
| ALIKED-n16 (kornia port) | keypoints + descriptors | `kornia/aliked` | BSD-3 | benchmarked, **rejected** by the gate |
| LightGlue (ALIKED weights, kornia) | keypoint matching | cvg/LightGlue v0.1_arxiv | Apache-2.0 | benchmarked, **rejected** by the gate |
| Stable Diffusion 1.5 (img2img) | benchmark only: "re-generation" attack | runwayml/stable-diffusion-v1-5 | CreativeML OpenRAIL-M | benchmark tooling, never deployed |

Not used: SuperPoint (its weights are non-commercial: ALIKED replaces it), DINOv3 (custom
licence, not needed unless DINOv2 proves insufficient).
**Permissive-only configuration** (`MINTGATE_PERMISSIVE_ONLY=1`): copy engine (all MIT / BSD / Apache-2.0),
provenance, the permissive CLIP-B/32 probe and `policy.permissive.json`; TruFor and every
research-only training source are excluded. Its metrics are in the AI-detector section below.

## AI-image detector (Phase 5)

`training/ai_backbones.py` (features / evaluate / ship), results `backend/model/benchmarks/ai_detector.json`
(+ `ai_detector_table.md`). Recipe for every candidate: frozen backbone, L2-normalised embedding of
the detector view (256 px + JPEG), standardised, balanced logistic regression, C chosen on `val`
(UnivFD, Ojha et al. CVPR 2023). Thresholds exactly as the policy calibrates them: review / fail =
95th / 99th percentile of genuine `val` probabilities (clipped to [0.5, 0.95] / [review + 0.02, 0.99]).

### The out-of-source protocol, v2

v1's check (480 genuine NFTs of 4 unseen collections + 200 Flux images) saturated: the CLIP-B/32
probe scored AUC 0.9998, so it could no longer rank detectors. v2 (`training/oos_data.py`) keeps that
exact sample and adds **sources and generators that are in no training split**:

| Group | n | Source |
|---|---|---|
| Genuine NFT collections | 947 | v1's 4 huggingnft collections + Alpacadabraz, Mini Mutants + 40 on-chain collections of the Phase 4 index not used in training (9 tokens each, originals via Alchemy's CDN) |
| Genuine pixel art | 54 | on-chain Moonbirds, Moonbirds Oddities, Nouns, Lil Nouns, Pixelmon, Chimpers, Meebits |
| Genuine paintings | 300 | Rijksmuseum (CC0), training paintings are WikiArt / Met only |
| Genuine photos | 397 | COCO val2017 (CC BY / BY-SA / PD only) + DOCCI; training photos are ImageNet only |
| AI, 2025-26 generators | 533 | OpenFakeTiny test (CC BY-NC 4.0, evaluation only): z-image-turbo 179, Flux.2-klein 118, Illustrious 82, GPT-image-1.5 62, Midjourney 7 44, Recraft v2/v3 17, Seedream 5, Ideogram 2, Aurora, Lumina, Nano Banana Pro, ... (video frames dropped) |
| AI, Flux.1-dev | 200 | LukasT9 (v1 sample, Apache-2.0) |

Every image gets the training normalisation. The set is split 50 / 50 per group into **select**
(candidates are ranked here) and **report** (the shipped detector's numbers), so no reported number
is one the choice was made on. **Primary metric:** balanced accuracy at the review threshold =
(AI flagged + 1 - mean genuine false-alarm rate over the 4 genuine domains) / 2: it scores the
operating point the policy deploys, and every genuine domain counts equally. **Gate:** paired
bootstrap (2,000 resamples of the same images, stratified by label x domain) of the gain over
CLIP-B/32; a candidate beats the baseline only if the 95 % CI lies above 0 and its OOS AUC is not
significantly worse.

### Candidates (OOS select half, n = 1,240)

| Candidate | CPU s / image ¹ | in-source test AUC | Flux (held out) flagged | OOS bal. acc. | Δ vs B/32 [95 % CI] | OOS AUC [95 % CI] | Decision |
|---|---|---|---|---|---|---|---|
| CLIP ViT-B/32 (v1) | 0.4 | 0.982 | 94.3 % | 77.3 % | baseline | 0.878 [0.859, 0.896] | baseline |
| DINOv2 ViT-S/14 | 0.34 | 0.961 | 90.3 % | 70.1 % | -7.2 pp [-10.4, -3.8] | 0.847 [0.826, 0.867] | worse |
| DINOv2 ViT-B/14 | 1.1 | 0.973 | 89.4 % | 68.3 % | -9.0 pp [-12.7, -5.2] | 0.829 [0.806, 0.850] | worse |
| CLIP ViT-L/14 | 4.1 | 0.992 | 97.2 % | 80.8 % | +3.5 pp [+0.9, +6.3] | 0.901 [0.883, 0.917] | beats, CPU budget ✗ |
| SigLIP 2 So400m/16-256 | 5.1 | 0.995 | 98.8 % | 81.6 % | +4.3 pp [+1.5, +7.3] | 0.903 [0.886, 0.918] | beats, CPU budget ✗ |
| SigLIP 2 B/16-256 | 1.1 | 0.993 | 98.3 % | 80.7 % | +3.4 pp [+0.3, +6.5] | 0.902 [0.885, 0.918] | beats |
| **CLIP-B/32 + SigLIP 2 B/16 (w 0.3 / 0.7)** | 0.4 + 1.1 | 0.994 | 98.9 % | **81.9 %** | **+4.6 pp [+1.8, +7.6]** | 0.909 [0.892, 0.924] | **shipped** |
| SigLIP 2 B/16 + CLIP-L/14 | 1.1 + 4.1 | 0.996 | 98.8 % | 84.0 % | +6.7 pp [+3.9, +9.5] | 0.922 [0.907, 0.936] | best, CPU budget ✗ |
| SigLIP 2 B/16 + So400m | 1.1 + 5.1 | 0.996 | 99.0 % | 82.3 % | +5.0 pp [+2.3, +7.9] | 0.912 [0.896, 0.927] | CPU budget ✗ |

All 15 pairwise probe ensembles are in `ai_detector.json`; every SigLIP 2 / CLIP-L variant beats
B/32, every DINOv2-only variant loses (DINOv2 features are excellent for copy detection, not for
real-vs-generated). ¹ Raw backbone forward pass, 2 CPU threads, measured with random weights while
another process loaded the laptop; the end-to-end pipeline numbers below are the binding ones.

**Decision.** The hardware rule (deployed CPU inference ≤ 1.5 s per upload) rules out CLIP-L/14 and
So400m (4-5 s each on CPU). Among the CPU-feasible winners, the B/32 + SigLIP 2 ensemble is
significantly better than SigLIP 2 alone on the select half (+1.2 pp [+0.04, +2.5], AUC +0.007
[+0.001, +0.013]), so it ships (`model/ai_detector.json`, format 2; B/32 is kept because it adds
measurable accuracy, not for compatibility).

### Shipped detector on the held-out report half (n = 1,191)

| | CLIP-B/32 (v1) | **B/32 + SigLIP 2 (shipped)** |
|---|---|---|
| OOS ROC-AUC | 0.894 [0.875, 0.911] | **0.925 [0.909, 0.938]** |
| AI flagged (≥ review), 2025-26 generators + Flux.1, n = 364 | 75.8 % [71.2, 79.9] | **88.7 % [85.1, 91.6]** |
| AI auto-failed (≥ fail) | 46.2 % [41.1, 51.3] | **69.0 % [64.0, 73.5]** |
| Genuine NFT collections flagged / failed, n = 455 | 5.7 % / 3.3 % | 7.2 % [5.2, 10.0] / 3.3 % [2.0, 5.4] |
| Genuine pixel art flagged / failed, n = 24 | 33.3 % / 16.7 % | 29.2 % [14.9, 49.2] / 16.7 % [6.7, 35.9] |
| Genuine paintings flagged / failed, n = 150 | 2.7 % / 0.7 % | 6.7 % [3.7, 11.8] / 0.0 % [0.0, 2.5] |
| Genuine photos flagged / failed, n = 198 | 45.0 % / 16.7 % | 52.5 % [45.6, 59.4] / 25.2 % [19.7, 31.7] |
| v1 protocol (240 genuine NFTs / 100 Flux of this half) | 100 % / 94 % correct | 100 % / 98 % correct |

**Finding: genuine photographs from new sources are the weak spot of every detector.** At the
in-source operating point, 45-53 % of COCO / DOCCI photos are flagged (both halves, n = 200 / 197:
B/32 COCO 32 %, DOCCI 60 %; SigLIP 2 alone COCO 21 %, DOCCI 75 %). DOCCI's professional high-resolution photos look "too clean" after
downscaling, the way generator output does; the training photos (ImageNet) are small, noisy JPEGs.
v1 had the same weakness; v1's out-of-source check simply contained no photos. The ensemble raises
photo auto-fails from 16.7 % to 25.2 % (CIs overlap) while catching 13 pp more AI images. Mitigation
in this phase: trusted C2PA camera credentials cap the AI detector at REVIEW (`provenance` check);
the real fix is genuine high-resolution photos in training (Phase 7+).

Per generator (select half, flagged): Flux.1-dev 98 %, Flux.2-klein 86 %, z-image-turbo 86 %,
Illustrious 80 %, GPT-image-1.5 77 %, Midjourney 7 73 %, Recraft v3 86 %; groups with fewer than
10 images are listed in the JSON only.

### Rejected on the way

| Candidate | Evidence (OOS select half, paired bootstrap) | Decision |
|---|---|---|
| SigLIP 2 B/16 + LoRA (rank 8 on q/v, 3 epochs, augmentation, best val epoch) | vs B/32 +2.5 pp [-0.6, +5.6]; vs frozen SigLIP 2 -0.9 pp [-3.9, +1.9]; vs shipped ensemble -2.1 pp [-5.0, +0.6]; in-source test AUC 0.987 (frozen 0.993) | **rejected**: fine-tuning fits the training generators, exactly the UnivFD finding |
| Shipped ensemble + permissive genuine extras in training (1,000 public-domain paintings, 800 CC BY COCO-train photos) | OOS without COCO (same source): -0.7 pp [-2.8, +1.4]; halves DOCCI photo auto-fails (48 % → 23 %), clears painting flags, costs ~5 pp AI recall | not significantly better: not adopted (a trade-off for Phase 7, see the photo finding) |
| Dynamic int8 (PyTorch, all Linear layers) for the CPU profile | -14.8 pp [-18.1, -11.7], mean probability shift 0.39 (`benchmark/int8_gate.py`) | **rejected** (accuracy does not hold) |
| ONNX Runtime fp32 for the CPU profile | parity cosine ≥ 0.999999; B/32 169 → 94 ms, SigLIP 2 565 → 459 ms standalone (`benchmark/onnx_backbones.py`) | built, **opt-in** (`MINTGATE_CPU_ONNX=1`): in the full worker process on this Windows laptop the copy engine's native libraries crash next to it; verify on Linux in Phase 7 |

### CPU latency (the hardware budget)

`benchmark.latency pipeline --device cpu` (whole `MintGatePipeline.analyse`, 40 benchmark uploads,
2 threads, live 285k index), all measured on the same day, back to back, on a laptop shared with
other jobs (absolute numbers vary ±25 % between runs; compare rows, not days):

| CPU profile | p50 | p95 |
|---|---|---|
| pipeline without any AI detector | 0.70 s | 1.0 s |
| v1 detector (B/32 probe), ResNet-18 retired | 0.92 s | 1.24 s |
| **shipped: B/32 + SigLIP 2, PyTorch fp32** | **1.48-1.57 s** | **1.86-2.00 s** |
| same, int8 (rejected on accuracy) | 1.20 s | 1.51 s |

Components (`benchmark.latency_ai`): B/32 174 ms, SigLIP 2 570 ms, provenance 5 ms p50 / 60 ms p95.
**The shipped CPU profile exceeds the 1.5 s budget** (Phase 4: p50 1.10 s, p95 1.35 s on a quieter
day). No candidate that beats B/32 fits it on this laptop with PyTorch; ONNX Runtime would remove
~180 ms and is the planned fix once it runs next to the copy engine (Phase 7, Linux container). The
GPU profile (RTX 3050) is unaffected. `AI_DETECTOR_FILE=model/benchmarks/ai_detector_v1.json`
restores the v1 detector for a deployment that must meet 1.5 s at v1 accuracy.

### Permissive-only configuration (`--permissive-only`)

Fits the probe on training sources whose licence allows commercial use only: huggingnft collections
(MIT), Rapidata DALL·E 3 / Midjourney / SD3 (CDLA-Permissive-2.0), SDXL (MIT). Excluded: Tiny-GenImage
(CC BY-NC-SA), WikiArt, Art-Images, Real-vs-AI-Art (research use) and the local CryptoPunks / AI-art
folders (unclear terms). All backbones are MIT / Apache-2.0, so the weights are fine; the *data* is
the problem. Results (`ai_detector_permissive.json`, report half):

| Permissive-only probe | OOS AUC | AI flagged | Genuine NFT / pixel / painting / photo flagged |
|---|---|---|---|
| CLIP-B/32 | 0.770 | 98.9 % | 10.3 % / 16.7 % / **100 %** / **100 %** |
| SigLIP 2 B/16 | 0.786 | 98.4 % | 11.4 % / 16.7 % / **100 %** / **100 %** |

Its only genuine class is NFT collection art, so it learns "NFT style vs everything else" (val AUC
1.000, a shortcut): it flags every painting and photo. **Fix (shipped):** `training/fetch_permissive.py`
adds 1,000 public-domain paintings (ArtBench-PD) and 800 CC BY / BY-SA COCO *train* photos to the
permissive training data. Then (report half, `model/ai_detector.permissive.json`, CLIP-B/32: it ties
SigLIP 2 at 81.5 vs 81.6 % on the select half, and the smaller model wins ties):

| Permissive probe + extras | OOS bal. acc. | OOS AUC | AI flagged | Genuine NFT / pixel / painting / photo flagged |
|---|---|---|---|---|
| CLIP-B/32 (shipped, permissive profile) | 83.1 % | 0.929 [0.914, 0.944] | 83.5 % [79.4, 87.0] | 7.9 % / 33.3 % / 0.0 % / 28.3 % |

Caveat: COCO is now in-source for this variant (train vs val images); DOCCI and every other group
stay out-of-source. The permissive profile has its own policy (`policy.permissive.json`, AI review
0.5 / fail 0.706, no TruFor).

### Adding a new generator (`training/retrain_gate.py`)

1. Collect >= 300 images of the new generator (`--images DIR` or `--hf REPO`), never from a source
   that is in the out-of-source set (the script refuses if the generator name appears there).
2. `python -m training.retrain_gate --images DIR --generator NAME` encodes them with the shipped
   backbones, refits the probe on train + 80 % of the new images, recalibrates the thresholds on val.
3. It ships (`--apply`) only if (a) the paired-bootstrap 95 % CI of the OOS-select balanced-accuracy
   change is above -1 pp (non-inferiority), (b) no genuine domain's OOS false-alarm rate rises by more
   than 2 pp, and (c) the new generator's held-out 10 % is detected at least as well as before.
   The report goes to `model/benchmarks/retrain_gate_<NAME>.json`.
4. After `--apply`: `training.ai_backbones features` (the manifest changed), `training.calibrate`,
   `training.evaluate_system`, and keep at least one generator held out of training.

**Demonstration (HiDream-I1, 420 images, Rapidata, CDLA-Permissive-2.0; `retrain_gate_hidream-i1.json`):**
the shipped detector already catches the held-out 10 % (40 / 40, [91.2, 100] %); retraining changes
the OOS primary metric by +0.05 pp [-0.5, +0.6] but raises the OOS photo false-alarm rate by 2.01 pp
(limit 2 pp), so the gate **refused** to ship the retrained probe. Nothing was applied.

## Phase 5b: NFT-native evaluation (real copymints, real AI collections)

Phase 5's sets were mostly not NFT-native (OpenFake = photo-realistic news, forgery benchmark = edited
photos, NFT copies = synthetic attacks). Phase 5b measures the gate on what happens on NFT marketplaces,
with **real on-chain tokens only** (no self-generated images: the real AI set has 518 images, above the
300 needed). Every contract was checked with Alchemy `getContractMetadata` and a public page.

### The collections

**Copymints** (`training/copymint_data.py` → `data/copymint/`, never added to `reference_items`), 300
tokens each, sampled at random from every token on-chain, files = the token image as Alchemy's CDN
serves it (`cachedUrl`, = what a copyminter uploads; 1 file via the on-chain `tokenURI` + IPFS, 2
AVIF files via Alchemy's PNG rendition because Mint-Gate does not accept AVIF):

| Collection | Chain, contract | Copies | Pairing (checked on pixels) | Evidence |
|---|---|---|---|---|
| RR/BAYC | Ethereum `0x2EE6AF0dFf3A1CE3F7E3414C52c48fd50d73691e` | BAYC, same art | metadata name "#7400" = BAYC id; 299 / 299 verified | contract name "Bored Ape Yacht Club", OpenSea `rrbayc` ("conceptual art by Ryder Ripps"), Yuga Labs v. Ripps (C.D. Cal., 2022) |
| CryptoPhunks V2 | Ethereum `0xf07468eAd8cf26c752C676E43C814FEe9c8CF402` | CryptoPunks, mirrored | same id; 275 / 275 verified (25 are the held-out punks) | contract CryptoPhunksV2, OpenSea `crypto-phunks` (verified): "CryptoPhunks face left" |
| Phunky Ape Yacht Club | Ethereum `0x176e0fe17314def59f0f06e976e1b74203be4a55` | BAYC, mirrored | same id; 300 / 300 verified | contract PhunkyApeYachtClub (Etherscan-verified), OpenSea `phunkyapeyachtclub` ("Not affiliated with Yuga Labs") |

Pairing check = mean absolute RGB difference of the foreground at 64 px (copy un-mirrored, area
averaged): verified pairs 1-8 / 255, wrong tokens ~40. 55 of the seed CryptoPunks carry Larva Labs'
red / purple "for sale" / "bid" backgrounds (the copies have the standard blue), hence foreground only.

**AI-generated collections** (`training/ai_nft_data.py` → `data/ai_nft/` + OOS group `ai_nft:*`,
own select / report split; list approved by the owner on 2026-11-10). Label = the creator's own
statement in the token / contract metadata (`data/ai_nft/collections.json` keeps it verbatim); 40
tokens per collection (Mappings / New Realities 39: two tokens without an image). None is in the
training manifest or the Phase 5 OOS set.

| Collection | Chain, contract | Minted | Stated method |
|---|---|---|---|
| BrainDrops #1 Brain Loops | Ethereum `0xdfde78d2baec499fe18f2be74b6c287eed9511d7` (BrainDrops, project = token id / 10⁶) | 2021-11 | text-to-image model |
| BrainDrops #3 Genesis | same contract | 2021-11 | "raw AI images" |
| BrainDrops #4 DreamScapes | same | 2021-12 | VQGAN+CLIP |
| BrainDrops #5 Confluence | same | 2022-01 | neural generative model trained on the artist's sketchbook |
| BrainDrops #11 ClipMatrix Creatures | same | 2022-03 | AI text-to-3D (animated; first frame) |
| BrainDrops #15 Life In West America (Roope Rainisto) | same | 2023-02 | custom-trained generative models ("post-photography") |
| BrainDrops #16 All AI Art Looks The Same | same | 2023-03 | 14 AI artists |
| BrainDrops #17 Materia Mania | same | 2023-03 | diffusion models |
| BrainDrops #25 Mappings | same | 2023-08 | generative AI process |
| BrainDrops #30 corpo\|real | same | 2024-01 | AI-collaborative fashion |
| BrainDrops #32 Anthrocinematica | same | 2024-03/04 | AI video (stills) |
| BrainDrops #35 New Realities | same | 2024-06 | AI latent space |
| Bastard GAN Punks V2 | Ethereum `0x31385d3520bced94f77aae104b406994d8f2168c` | 2021-03/06 | GAN trained on CryptoPunks attributes (pixel art) |

Dropped: Winds of Yawanawa (Refik Anadol, "Data Paintings": no AI method stated) and the BrainDrops
projects that state no method. **Gap:** no anime collection with a verifiable AI claim was found.
BrainDrops ships originals of up to 44 MB / 100 MP: 57 files above the 25 MB fetch cap were fetched as
Alchemy's 2,048 px rendition (Mappings 39, New Realities 14, Materia Mania 3, All AI Art 1), and 95 of the
518 kept files exceed Mint-Gate's 10 MB upload limit (15 the 40 MP limit). The end-to-end run therefore
submits what an artist would (`ai_nft_data.as_upload`: the original if accepted, else 2,048 px JPEG q95);
the provenance check reads the original bytes.

### AI detection on real AI collections (`training.ai_backbones evaluate`, no retraining)

Shipped detector (CLIP-B/32 + SigLIP 2 B/16, unchanged thresholds) flags **444 / 518 = 85.7 % [82.4, 88.5]**
of the AI-collection tokens at the review threshold; the other candidates on disk: SigLIP 2 alone 81.3 %,
CLIP-B/32 alone (= v1) 74.5 %, DINOv2 S/B 60.8 / 70.3 %. (CLIP-L/14 and So400m were not re-run: their
weights were deleted after Phase 5, they lost on CPU cost.) With the new group the OOS AUC is 0.911
[0.900, 0.920] (all), report half 0.914; Phase 5's numbers without it: `model/benchmarks/ai_detector_phase5.json`.

| Collection | Flagged (95 % CI), n = 40 |
|---|---|
| DreamScapes, Genesis, Materia Mania, corpo\|real, Anthrocinematica | 100 % [91.2, 100] each |
| Brain Loops | 97.5 % [87.1, 99.6] |
| Mappings, New Realities (n = 39) | 97.4 % [86.8, 99.6] |
| Life In West America | 90.0 % [77.0, 96.0] |
| All AI Art Looks The Same | 87.5 % [73.9, 94.5] |
| Confluence | 80.0 % [65.2, 89.5] |
| ClipMatrix Creatures | 65.0 % [49.5, 77.9] |
| **Bastard GAN Punks V2** | **0.0 % [0.0, 8.8]** |

Misses: **GAN pixel art is invisible to the detector** (24 × 24 punks re-drawn by a GAN look like any
other pixel-art collection; genuine pixel art is the detector's other weak domain, so there is no room to
lower the threshold there). ClipMatrix (3D renders, first GIF frame) and Confluence (a model trained on
the artist's own sketches, then hand-processed) are the other weak spots: collection labels are noisy
by nature (an artist may retouch outputs), which is why every number is per collection.

**Provenance on the original files** (`training.provenance_eval --nft`, `model/benchmarks/provenance_nft.json`):
no AI collection file carries an AI watermark or generator metadata; **18 / 518 (3.5 % [2.2, 5.4])** carry
Adobe Photoshop C2PA credentials that declare AI-assisted editing (9 corpo|real, 9 New Realities) →
advisory REVIEW, never AI evidence. Copymints: no signal at all (0 / 900). On-chain NFT art has
essentially no provenance trail: detection has to come from the pixels.

### Copy detection on real copymints: what the live index revealed

The first run looked perfect (every copymint stopped) and was wrong: three bugs in the index and the
member rule, each fixed and re-measured.

1. **New tokens entered the index as one-off works.** `collection_member` was only ever set by migration
   0004; `repo.add_nft_items` did not set it, so every token added later (Phase 5b's BAYC backfill, and
   any new mint `ingest sync` picks up) was a "one-off" and *its siblings* "copied" it. Fixed in
   `repo.add_nft_items` (+ test), the 4,000 rows repaired, and plain `ingest embed` now warns about
   rows without the `nn_sscd` density term (only `--follow` / `--rebuild-nn` maintain it).
2. **The index cap left 40 % of BAYC unprotected** (6,000 of 10,000 apes): the most-copied collection
   is now complete (288,860 ready references).
3. **The member pixel rule could not verify a real copy, and matched genuine siblings.** A PDQ-close
   collection member counted as an exact copy if ≤ 13 of 128 × 128 pixels differed (NEAREST, level 48).
   On real files: (a) a full-size upload vs the indexed reference (a LANCZOS 128 px JPEG of Alchemy's
   250 px thumbnail) differs by hundreds of pixels at every edge, so **0 of 874 real copies and 1 of 401
   full-size re-uploads of indexed on-chain tokens passed it** (Phase 4's "98 % of exact re-uploads FAIL"
   re-uploaded the 128 px thumbnail itself); (b) mirrored copies were never tried (PDQ searched the 8
   dihedral variants, the pixel check did not); (c) **44 of 892 genuine new CryptoPunks (4.9 %) matched a
   different punk**: they differ only by dark-on-dark traits (a mole, a beard, an eyebrow shade) that a
   48-level threshold cannot see. Phase 5's footnote ("15 of 18 punk FAILs are dataset duplicates") was
   wrong: the 10,000 seed punks hold only 4 duplicate-image pairs.

**The gate** (`benchmark/member_rule.py`, `model/benchmarks/member_rule.json`): 18 pixel-rule candidates
(NEAREST / LANCZOS / BOX × as uploaded / dihedral × level 48 / 24 / 16), each with its budget from the
benchmark exactly as Phase 4 did (half the closest genuine VAL sibling), measured on the benchmark TEST
split, on 1,893 live genuine uploads that are not indexed (the held-out punks, OOS NFT / pixel art) and on
the 874 copymints with an indexed original. Shift tolerance and a localised-difference statistic were
also tried offline: **no pixel statistic against 128 px JPEG thumbnails separates a copy of detailed art
from a sibling** (copies of BAYC differ by more than siblings of pixel art do). So the pixel rule's job is
now to be safe, and a new, separate signal catches re-mints:

| Member rule | Budget | Benchmark member copies accepted | Live genuine matched to another token |
|---|---|---|---|
| Phase 4: NEAREST, as uploaded, level 48 | 13 px | 15 / 140 | **45** (44 new punks + 1) |
| **Shipped: BOX, dihedral, level 16** | 22 px | 7 / 140 | **6** (5 new punks + 1) |

**New check `copy_member`** (REVIEW-only, weight 0): the upload's SSCD (as uploaded and mirrored) vs the
top-10 collection members, margin = cos(upload, member) − nn_sscd(member): closer to a token than that
token's own nearest sibling. Threshold 0.15 = max(99.8th percentile of the benchmark's genuine VAL
margins, largest margin of a live calibration half + 0.01); measured on data it did not see:
benchmark TEST member copies flagged 18.6 % [13.0, 25.8] (always the right member), genuine NFT
distractors 1 / 1,465 (0.07 %), live held-out genuine 0 / 624 [0, 0.6 %]. Why not a lower threshold:
at 0.11 RR/BAYC would be caught 64 %, but benchmark genuine flags rise to 0.8 %.

**Result on the real copymints** (`training.copymint_eval`, full pipeline, policy 5.1.0 vs the Phase 4
rule on the same repaired index; `model/copymint_eval.json`, `copymint_eval_phase4rule.json`):

| Copymint (n = 300 each) | PASS, Phase 4 rule | **PASS, copy engine 1.1.0** | Stopped by a copy check | Report names the right original |
|---|---|---|---|---|
| RR/BAYC (same art) | 68.7 % [63.2, 73.7] | **43.3 % [37.9, 49.0]** | 36.7 % `copy_member` REVIEW | 296 / 299 |
| Phunky Ape Yacht Club (mirrored) | 66.0 % [60.5, 71.1] | **40.7 % [35.3, 46.3]** | 38.3 % `copy_member` REVIEW | 296 / 300 |
| CryptoPhunks V2 (mirrored) | 96.7 % [94.0, 98.2] | **94.7 % [91.5, 96.7]** | 2.3 % (6 `copy_exact` FAIL via the dihedral rule) | 107 / 275 |

(The rest of "stopped" is TruFor's REVIEW, which fires on ~1 in 3 BAYC images: coincidental, not copy
evidence.) Misses and their causes: every miss had its original among the PDQ candidates except
`pdq_far` (CryptoPhunks 33, PHAYC 37: the mirrored PDQ is > 31 bits away) and 25 phunks of held-out
punks (original not indexed, by design); the rest fail the pixel rule on resampling, and SSCD cannot
separate punk siblings at all (phunk margins −0.09 median). **Open limit, planned for a later phase:**
the index keeps 128 px JPEG thumbnails, too lossy to prove identity for detailed art; storing larger
references (256-512 px) for the most-copied collections is the fix to test next (owner decision
2026-11-10: document now, fix later).

## End to end: policy 5.1.0 (`training/evaluate_system.py`, GPU profile)

Full pipeline against the live index (288,860 references), **n = 300 uploads per category** (fewer
where the pool is smaller: n = pool), drawn without replacement, Wilson 95 % CIs
(`model/system_eval.json`, `assets/system_evaluation.png`; the policy 5.0.0 run is kept in
`model/system_eval_policy5.0.0.json`). `oos_*` = out-of-source sets. New in Phase 5b: the three real
copymint collections (each copy uploaded as the original token *file*) and the real AI NFT collections.

| Upload | n | PASS | REVIEW | FAIL | 5.0.0 PASS / FAIL |
|---|---|---|---|---|---|
| Genuine NFT-collection art (test split) ² | 300 | 86.7 % [82.3, 90.0] | 12.3 % | 1.0 % [0.3, 2.9] | 92.7 / 1.0 |
| Genuine NFT art, new collections (oos) ² | 300 | 79.7 % [74.8, 83.8] | 17.0 % | 3.3 % [1.8, 6.0] | 84.0 / 3.3 |
| Genuine pixel-art punk (test split) ¹ | 300 | **96.0 % [93.1, 97.7]** | 2.0 % | **2.0 % [0.9, 4.3]** | 92.0 / 6.0 |
| Genuine pixel art, new collections (oos) | 54 | 59.3 % [46.0, 71.3] | 22.2 % | 18.5 % [10.4, 30.8] | 66.7 / 18.5 |
| Genuine painting (test split) | 295 | 76.6 % [71.5, 81.1] | 16.6 % | 6.8 % [4.4, 10.2] | 76.6 / 6.8 |
| Genuine painting, Rijksmuseum (oos) | 300 | 84.3 % [79.8, 88.0] | 15.7 % | 0.0 % [0.0, 1.3] | 84.3 / 0.0 |
| Genuine photo (test split) | 129 | 81.4 % [73.8, 87.2] | 17.1 % | 1.6 % [0.4, 5.5] | 81.4 / 1.6 |
| Genuine photo, COCO + DOCCI (oos) | 300 | 42.3 % [36.9, 48.0] | 33.7 % | 24.0 % [19.5, 29.1] | 42.3 / 24.0 |
| Modern AI (DALL·E 3, MJ, SD3, SDXL) | 300 | 0.3 % [0.1, 1.9] | 1.0 % | 98.7 % [96.6, 99.5] | 0.3 / 98.7 |
| Unseen Flux (held out of training) | 300 | 2.0 % [0.9, 4.3] | 2.7 % | 95.3 % [92.3, 97.2] | 2.0 / 95.3 |
| 2025-26 generators + Flux.1-dev (oos) | 300 | 11.3 % [8.2, 15.4] | 24.0 % | 64.7 % [59.1, 69.9] | 11.3 / 64.7 |
| Other AI-art generators | 300 | 3.0 % [1.6, 5.6] | 18.3 % | 78.7 % [73.7, 82.9] | 3.0 / 78.7 |
| Older generators (GenImage) | 158 | 3.8 % [1.8, 8.0] | 18.4 % | 77.8 % [70.8, 83.6] | 3.8 / 77.8 |
| **Real AI NFT collections (BrainDrops + BGAN Punks, oos)** | 300 | **8.3 % [5.7, 12.0]** | 25.7 % | **66.0 % [60.5, 71.1]** | new |
| Exact copy of a protected work | 300 | 0.0 % [0.0, 1.3] | 0.0 % | 100 % [98.7, 100] | 0.0 / 100 |
| Edited copy of a protected painting | 300 | 1.7 % [0.7, 3.8] | 30.7 % | 67.7 % [62.2, 72.7] | 1.7 / 67.7 |
| Copy-move forgery | 295 | 0.7 % [0.2, 2.4] | 16.3 % | 83.0 % [78.3, 86.9] | 0.7 / 83.0 |
| Spliced forgery | 295 | 0.0 % [0.0, 1.3] | 78.3 % | 21.7 % [17.4, 26.8] | 0.0 / 21.7 |
| Re-upload of an indexed Ethereum NFT's 128 px thumbnail ³ | 300 | 2.0 % [0.9, 4.3] | 0.7 % | 97.3 % [94.8, 98.6] | 1.0 / 98.3 |
| Edited copy of an indexed Ethereum NFT | 300 | 64.0 % [58.4, 69.2] | 27.3 % | 8.7 % [6.0, 12.4] | 65.7 / 10.7 |
| Edited copy of a generative-collection NFT | 300 | 58.3 % [52.7, 63.8] | 40.7 % | 1.0 % [0.3, 2.9] | 60.7 / 0.3 |
| **Real copymint: RR/BAYC (same art, token file)** | 300 | **43.3 % [37.9, 49.0]** | 56.7 % | 0.0 % [0.0, 1.3] | new |
| **Real copymint: Phunky Ape Yacht Club (mirrored)** | 300 | **40.7 % [35.3, 46.3]** | 59.3 % | 0.0 % [0.0, 1.3] | new |
| **Real copymint: CryptoPhunks V2 (mirrored punks)** | 300 | **94.7 % [91.5, 96.7]** | 3.3 % | 2.0 % [0.9, 4.3] | new |

Pooled: genuine PASS **77.4 % [75.5, 79.1]** (n = 1,978), genuine wrongly failed **6.2 % [5.2, 7.4]**
(5.0.0: 78.5 % / 6.8 %). Forgeries stopped (REVIEW or FAIL) **78.5 % [77.4, 79.7]** (n = 4,648),
auto-failed 53.2 % [51.8, 54.6]; the drop vs 5.0.0's 87.1 % is the new, harder categories, not a
regression: on 5.0.0's own forgery categories 5.1.0 stops **87.4 % [86.2, 88.4]** (n = 3,448, auto-failed
65.8 %), and the four Phase 5b categories alone are stopped 53.2 % [50.4, 56.1] (n = 1,200). v1's forgery
categories: 92.0 % [90.9, 93.0] stopped. False alarms per domain (REVIEW or FAIL / FAIL): collection NFTs
16.8 % / 2.2 % (n = 600, see ²), pixel art 9.6 % / 4.5 % (354), paintings 19.5 % / 3.4 % (595),
**photos 45.9 % / 17.2 % (429)**.

¹ The remaining FAILs are `copy_exact` matches to a *different* punk (a sibling within the member pixel
budget). Policy 5.0.0's note called 15 of its 18 FAILs dataset duplicates (the same image under two IDs);
Phase 5b's live gate showed they were sibling false matches of the 128 px NEAREST rule, which the
BOX / dihedral / level-16 rule cuts from 18 to 6.
² These "genuine" sets are images of tokens that **are** in the index (huggingnft BAYC / Azuki / MAYC and
the OOS on-chain tokens), so a full-size upload of one is a re-mint of an existing token. The new
REVIEW-only `copy_member` check flags 18 + 17 of them: checked for the 353-image test pool, 22 of its 23
`copy_member` flags point to the image's own token (PDQ ≤ 22 bits, or the identical seed file) and 1 to a
sibling (102 bits). FAIL is unchanged (only REVIEW can come from `copy_member`).
³ This category re-uploads the index's own 128 px thumbnail, so it measures index recall, not a real
re-upload: Phase 5b showed that uploading the token's **file** fails the 128 px pixel rule for detailed
art (the RR/BAYC / PHAYC rows: 0 % FAIL, caught only by `copy_member` REVIEW). The −1 pp vs 5.0.0 is
within the CIs (2 `copy_exact` FAILs moved to PASS under the stricter member rule).

What changed vs policy 5.0.0 (same AI detector, same TruFor): only the copy engine (1.1.0) and the
REVIEW-only `copy_member` check. Genuine punks wrongly failed 6.0 → 2.0 %; re-mints of indexed tokens
now reach REVIEW (real RR/BAYC 31 → 57 % stopped, PHAYC 34 → 59 %); edited copies of collection NFTs
are more often REVIEW (39 → 41 %), edited on-chain copies FAIL less (`copy_exact` 7 → 1, now REVIEW).
CryptoPhunks stays the open gap: mirrored 24 px punks are siblings to every signal at 128 px. AI: the
detector stops 91.7 % of real AI NFT collections end to end (detector alone 85.7 %, see Phase 5b), the
detector alone flags none of the BGAN Punks (GAN pixel art). The weak spots stay explicit: new pixel-art
collections (n = 54), photographs from new sources, and copymints of detailed art without larger
reference images.

## Manipulation forensics (Phase 5): TruFor vs ResNet-18 vs block matching

`benchmark/build_forgery.py` + `benchmark/forgery.py`, results `backend/model/benchmarks/forgery.json`
(+ `forgery_table.md`). **Real** manipulated photos with ground-truth masks, from the
KhangTruong/Tampered-Images-Combined-Dataset-2 bundle (card CC BY-SA 4.0, each component keeps its
research-use terms; evaluation only), up to 300 per dataset and label. Image level: ROC-AUC where
pristine images exist, detection rate at the review threshold (95th percentile of genuine `val`
art, the policy's rule). Pixel level: F1 of the binarised map at a **fixed** 0.5 threshold (no
per-image oracle threshold), averaged over manipulated images. 95 % CIs: Wilson / bootstrap.

| Dataset (n manipulated / pristine) | ResNet-18 AUC | TruFor AUC | TruFor flagged / pristine flagged | TruFor pixel F1 | copy-move pixel F1 |
|---|---|---|---|---|---|
| Columbia uncompressed splicing (180 / -) | - | - | **98.3 %** [95.2, 99.4] / - | **0.800** [0.760, 0.838] | 0.038 |
| COVERAGE copy-move (100 / 100) | 0.486 [0.401, 0.567] | **0.769** [0.702, 0.834] | 34.0 % / 2.0 % | **0.524** [0.449, 0.599] | 0.106 |
| CocoGlide diffusion inpainting (300 / 300) | 0.579 [0.533, 0.625] | **0.768** [0.729, 0.804] | 32.3 % / 5.3 % | 0.390 [0.347, 0.431] | 0.021 |
| IMD2020 generated subset (263 / 263) | 0.501 [0.452, 0.551] | 0.498 [0.447, 0.546] | 5.7 % / 5.7 % | 0.118 | 0.008 |
| CASIA v2 (300 / -) ¹ | - | - | 90.3 % [86.5, 93.2] / - | 0.828 [0.797, 0.861] | 0.021 |
| IMD2020 real-life (232 / -) ¹ | - | - | 82.3 % [76.9, 86.7] / - | 0.724 [0.683, 0.762] | 0.021 |

¹ **In TruFor's training data** (its CAT-Net training mix includes CASIA v2 and IMD2020): reported for
completeness, not as out-of-source evidence. Columbia, COVERAGE and CocoGlide are not.

Paired bootstrap of the AUC difference TruFor - ResNet-18: CocoGlide **+0.189 [+0.132, +0.247]**,
COVERAGE **+0.283 [+0.172, +0.395]**, IMD2020 generated -0.003 [-0.076, +0.068] (both at chance).
Block-matching copy-move flags 11 % of COVERAGE's real copy-moves (AUC 0.553): it only catches
naive, unprocessed clones.

**Genuine images flagged at the review threshold** (the question for NFT art: does a camera-trace
detector misfire on drawings and renders?):

| Signal | realworld test (n = 873) | OOS NFT (947) | OOS pixel art (54) | OOS paintings (300) | OOS photos (397) |
|---|---|---|---|---|---|
| ResNet-18 | 6.9 % | **42.7 %** [39.6, 45.8] | **63.0 %** | 13.3 % | 42.6 % |
| TruFor | 4.7 % | 6.4 % [5.1, 8.2] | 7.4 % | 6.7 % | 7.8 % |
| copy-move | 5.1 % | 3.7 % | 5.6 % | 1.3 % | 1.8 % |

TruFor would *auto-fail* (fail threshold) 1.1 % / 0 % / 0.3 % / 1.3 % of them.

**Decisions.**
- **ResNet-18 is retired.** On real forgeries it is at chance (AUC 0.49-0.58), on new NFT collections
  it flags 43 %, and the AI probe covers its other role (v1 out-of-source AUC 0.79 vs 0.92+).
- **TruFor is adopted as the `manipulation` check, REVIEW-only** (`max_status`): it beats ResNet-18
  significantly wherever it can be tested out of its training distribution, and stays at 5-8 %
  false alarms on NFT art. REVIEW-only because artists legitimately composite and retouch their own
  work (manipulated is not forged), and because generated-image manipulations (IMD2020 generated)
  defeat it. Its map + reliability becomes the evidence image (`manipulation_heatmap`).
- **GPU profile only, and never in the permissive configuration.** TruFor's code and weights are
  **non-commercial** (GRIP-UNINA licence, `third_party/trufor/LICENSE.txt`), and it needs 2 s (256 px),
  7 s (512 px) and 26-36 s (1024 px) per image on 2 CPU threads; on the RTX 3050 p50 169 ms,
  p95 990 ms (max side 1024). `MINTGATE_TRUFOR=auto` turns it on only with a GPU; the CPU
  deployment has no manipulation-localisation model, only block-matching copy-move.
- The published checkpoint is a pickle; it is loaded once with `torch.load(weights_only=True)` (its
  only non-tensor globals, numpy scalar / dtype, allow-listed) and converted to safetensors
  (`benchmark.forgery fetch`, SHA-256 in `model/trufor/manifest.json`). The runtime never unpickles.

## Provenance signals (Phase 5)

`detection/provenance.py` reads the **raw upload bytes** (every other check sees re-encoded pixels):

| Signal | Says | Policy value |
|---|---|---|
| C2PA manifest, content intact, IPTC `trainedAlgorithmicMedia` | generated by AI | 1.0 (FAIL) |
| SD 1.x/2.x or SDXL DWT-DCT invisible watermark (≤ 12 / 136 resp. ≤ 4 / 48 bit errors) | generated by Stable Diffusion | 1.0 |
| A1111 `parameters`, ComfyUI graph, IPTC `trainedAlgorithmicMedia` in XMP, generator name in a software field | written by a generator | 1.0 |
| C2PA `compositeWithTrainedAlgorithmicMedia` / `compositeSynthetic`, or a reverse-search full match | AI-assisted edit / already on the web | 0.6 (REVIEW) |
| C2PA chain trusted (`C2PA_TRUST_ANCHORS`) with only capture / human-edit actions | camera / human made it | -1: `positive_evidence` caps the AI detector at REVIEW |

Design choices: an AI claim is believed from any *intact* manifest (nobody forges evidence against
their own upload), positive evidence only from a *trusted* one (a self-signed "camera" manifest is
trivial to forge). Credential problems are not integrity problems: OpenAI's GPT-image manifests carry
an **expired** signing certificate (`signingCredential.expired`, state "invalid") while every hash
matches, so they still count as AI claims, never as trusted evidence. `algorithmicMedia` (generative
code art such as Art Blocks) is not AI. Product names count only in software / tool fields (a human's
description that mentions Stable Diffusion does not). Watermark decoders run only where a native
generator output is possible (sides divisible by 8, ≤ 0.8 MP for SD 1/2, ≤ 1.6 MP for SDXL): the
mark does not survive resizing or a single JPEG q90 re-save (measured), and decoding costs
~350 ms / MP on 2 CPU threads. Reverse search (Google Vision Web Detection or TinEye) is off unless
configured, because it sends the upload to a third party; it is advisory only.

**On real files** (`training/provenance_eval.py`, original bytes streamed, `model/benchmarks/provenance.json`):

| | n | AI evidence [95 % CI] | via |
|---|---|---|---|
| AI files (OpenFake 2025-26 generators + Flux.1-dev) | 729 | **15.9 %** [13.4, 18.8] | metadata 88, C2PA 28 |
| Midjourney 7 | 44 | 100 % [92.0, 100] | IPTC XMP |
| GPT-image-1.5 | 61 | 47.5 % [35.5, 59.8] | C2PA (OpenAI, expired cert) |
| z-image-turbo / Illustrious | 178 / 80 | 18.0 % / 13.8 % | ComfyUI / A1111 metadata |
| Flux.1-dev / Flux.2 / Recraft | 200 / 118 / 14 | 0 % | none |
| **Genuine files** (COCO, DOCCI, ImageNet, 4 HF + 23 on-chain NFT collections) | 1,452 | **0.0 %** [0.0, 0.3] | none |

Cheap and precise, but low recall: most generators write no provenance, and any re-encode or
screenshot strips it. It complements the learned detector; it cannot replace it. No file in either
set carried an SD / SDXL watermark (their generators are not SD pipelines; the fixtures in
`tests/fixtures/provenance/` cover the decoder with the real encoders).

## Copy-detection benchmark (Phase 4)

`backend/benchmark/` builds and scores it; every number below is reproducible from the commands in
the module docstrings. Results: `backend/model/benchmarks/copydet.json` (+ `copydet_table.md`).

**Dataset** (`benchmark.build_copydet`): a reference DB of **4,000** protected works (1,000
WikiArt / museum paintings; 1,500 CryptoPunks + 1,500 tokens of 7 real collections) and **6,567
queries**: **600 attacked copies** (300 per domain) and **5,967 distractors** (genuine non-copies:
other paintings and real photos; for NFTs, *siblings from the same collections*), so ~10 distractors
per copy. Distractors get the same attack mix, so "was edited" is never a copy signal. Distractors
that were dataset duplicates of a reference (PDQ ≤ 8 bits *and* 32×32 grey MAE < 4) were dropped
(7 paintings, 0 NFTs after the pixel check). 36 NFT "regen" distractors could not be generated
(SD 1.5 img2img outgrew the 4 GB GPU next to the desktop), leaving 2,964 NFT distractors.

**Attacks** (14, `benchmark/attacks.py`): crop 5–40 %, rotate ±5–30°, flip, perspective,
recolour / hue shift / grey, blur, noise, JPEG q30–95, text / logo / tiled watermark overlay,
border / double frame, screenshot (shrunk into a 16:9 / phone screen with UI bars), **collage**
(the work is 30–60 % of another image), **combo** (2–3 stacked) and **re-generation** (Stable
Diffusion 1.5 img2img, strength 0.2–0.4, run offline). Every query is JPEG re-encoded afterwards.

**Protocol:** DISC21 micro-AP over all (query, reference, score) predictions (top-10 per query),
recall@1/@10, and TPR at 1 % / 5 % FPR (a copy counts only if its top-1 is the right reference
and scores above the distractors' 99th / 95th percentile). Queries are split val / test (copies
by reference): **every parameter is tuned on val, every number below is test**, per domain.

### Results (test split)

| Method | Painting micro-AP | Painting TPR@1 %FPR | Painting R@1 | NFT micro-AP | NFT TPR@1 %FPR | NFT R@1 |
|---|---|---|---|---|---|---|
| v1 pHash 64-bit | 0.443 | 0.431 | 0.500 | 0.302 | 0.207 | 0.464 |
| v1 CLIP copy score (density-relative) | 0.396 | 0.222 | 0.764 | 0.241 | 0.186 | 0.657 |
| **v1 engine** (pHash + CLIP + ORB, policy 3.1.0) | **0.820** | **0.542** | 0.938 | 0.004 ¹ | 0.000 ¹ | 0.000 |
| PDQ | 0.453 | 0.458 | 0.479 | 0.337 | 0.271 | 0.486 |
| PDQ + 8 dihedral query hashes | 0.509 | 0.507 | 0.535 | 0.344 | 0.271 | 0.543 |
| SSCD (raw cosine) | 0.909 | 0.868 | 0.993 | 0.216 | 0.186 | 0.921 |
| SSCD, normalised (α 1.0 · β 0.5) | 0.896 | 0.861 | 0.972 | 0.305 | 0.200 | 0.521 |
| DINOv2-S, normalised | 0.697 | 0.667 | 0.944 | 0.254 | 0.236 | 0.536 |
| DINOv2-B, normalised | 0.689 | 0.597 | 0.938 | 0.281 | 0.257 | 0.636 |
| SSCD + DINOv2-S fusion (w 0.8) | 0.886 | 0.861 | 0.986 | 0.330 | 0.271 | 0.629 |
| **+ SSCD region crops = shipped retrieval** | **0.905** | **0.896** | 0.986 | 0.355 | 0.300 | 0.607 |
| + LightGlue re-rank of the top-10 (not shipped) | 0.925 | 0.924 | 0.993 | 0.355 | 0.271 | 0.750 |
| LightGlue inliers alone, same top-10 (not shipped) | 0.928 | 0.958 | 0.986 | 0.194 | 0.186 | 0.750 |

¹ by design: v1 never counts a generative-collection member unless it is pixel-identical.

Recall@1 by attack (both domains, test), v1 engine → shipped retrieval: blur 0.44 → **1.00**,
flip 0.44 → **1.00**, JPEG 0.53 → **1.00**, recolour 0.38 → **1.00**, overlay 0.36 → 0.91,
crop 0.50 → 0.85, border 0.65 → 0.85, screenshot 0.52 → 0.72, combo 0.52 → 0.71,
re-generation 0.44 → 0.68, **collage 0.27 → 0.67**, perspective 0.53 → 0.65, noise 0.53 → 0.58,
rotate 0.53 → 0.53 (unchanged: the weakest attack for every descriptor).

### Gate decisions (val split, `MIN_GAIN` = 0.005 micro-AP)

| Component | Val evidence | Decision |
|---|---|---|
| SSCD (replaces CLIP copy score) | micro-AP 0.627 (normalised) vs CLIP 0.369 | **adopted** |
| PDQ (replaces pHash) | painting TPR@1 % 0.507 vs 0.431; 256-bit, dihedral | **adopted** (exact-copy check) |
| DINOv2 fusion | 0.6265 → 0.6430 | **adopted** |
| DINOv2-B vs -S | 0.6452 vs 0.6430 (< 0.005) | **ViT-S** (4× cheaper; ties go to the smaller model) |
| SSCD region crops (18 windows) | 0.6430 → 0.6632 | **adopted** |
| ALIKED + LightGlue (replaces ORB) | as re-ranker: pooled 0.4159 → 0.4145; as an extra check at the same ≤ 1 % val genuine-flag budget: copies flagged 48.7 % → 49.1 % (+0.3 pp) | **rejected**: ORB is retired without a replacement; SSCD + regions cover geometric and partial copies |

LightGlue alone is an excellent painting verifier (TPR@1 % 0.958), but SSCD already flags those
copies, so it adds latency without catching more. The first gate (re-rank, pooled micro-AP) was
the wrong yardstick for an independent check: the PDQ pin and the NFT domain, where LightGlue is
disabled for collection members, dominate it. The check-level gate is the one that decided.
`detection/local_match.py` stays for a later evidence-image feature; nothing runs it.

### Region crops vs the CPU budget: two profiles

Crops cost real CPU time (`benchmark.latency components`, 2 threads, p50): 18 × 320 px 3.6 s,
18 × 224 px 1.7 s, 9 × 224 px 0.84 s, **9 × 160 px 0.45 s**, 4 × 224 px 0.39 s. Everything else per
upload costs ~1.0 s (ResNet-18 114 ms, CLIP probe 196, copy-move 206, PDQ 2, SSCD 221, DINOv2-S 166,
+ ~100 ms decode / hashing / SQL). The accuracy gate picks the full 18 × 320 crops (val 0.6632; every
cheaper variant loses > 0.005), but that breaks the 1.5 s budget, so the engine has two profiles:
**GPU** = 18 × 320 crops; **CPU** = the most accurate variant that fits what the budget leaves
(`cpu_crop_variant`). With the *measured* fixed overhead (~280 ms: decode, canonical PNG, report hash,
~12 SQL round trips; first estimated at 100 ms, which picked 9 × 160 and gave p50 1.64 s) only
~295 ms remain, so the **CPU profile runs without region crops**. The profile is part of the
reported engine version (`copy-engine-1.0.0+gpu` / `+cpu`).

**Measured end-to-end** (`benchmark.latency pipeline`, the whole `MintGatePipeline.analyse` per
upload against the live 285k-reference index, 30 benchmark queries): **CPU profile (2 threads)
p50 1.10 s, p95 1.35 s** (target ≤ 1.5 s: met); GPU profile (RTX 3050) p50 0.83 s, p95 1.24 s.
CPU timings were taken next to another project's training job on the same laptop; a dedicated
2-vCPU container should be no slower.

### Policy 4.0.0 (`training/calibrate.py`), same test queries

Thresholds from the val distractors (review / fail at 1 % / 0.2 % FPR); the v1 row is policy 3.1.0
(frozen copy: `model/benchmarks/policy_v3.1.0.json`).

| Domain | Engine | Copies flagged | Copies auto-failed | Genuine flagged | Genuine auto-failed |
|---|---|---|---|---|---|
| Paintings | v1 (3.1.0) | 93.1 % | 55.6 % | 5.0 % | 1.35 % |
| Paintings | **v4, GPU profile** | **97.2 %** | **71.5 %** | **2.2 %** | **0.47 %** |
| Paintings | v4, CPU profile (no crops) | 91.7 % | 56.9 % | 2.0 % | 0.27 % |
| NFT collections | v1 (3.1.0) | 6.4 % ² | 0 % | 6.5 % | 0 % |
| NFT collections | **v4 (both profiles)** | 10.7 % | **10.7 %** | **0 %** | **0 %** |

² v1 flagged copies at the same rate as genuine NFTs: chance level.

**Finding 1: PDQ cannot tell collection siblings apart.** CryptoPunks siblings are 0–10 PDQ bits
apart even with 70 % of their pixels different (24×24 pixel art on a flat background). A collection
member therefore counts as an exact copy only when it is also **pixel-identical**: at most
`member_max_diff_px` = 13 pixels of a 128 × 128 RGB **nearest-neighbour** thumbnail differ by more
than 48 levels from the matched reference (half the closest genuine val sibling, 27). Three
earlier rules failed on the way, which is why the margin is there:
- *SSCD cosine ≥ 0.978 next to PDQ* (calibrated on the benchmark's 1,500 punks) failed **36 % of
  genuine new CryptoPunks against the live index** (9,000+ punks: closer siblings exist). A
  threshold set on a small database does not transfer to a 60× larger one.
- *64 px greyscale* missed colour-only differences; *bilinear 128 px* averaged a one-cell accessory
  away and let 3 genuine test siblings through after blur / JPEG attacks.
The PDQ quality gate (< 50 = unreliable) now applies to one-off works only: flat NFT art scores
low quality, and the gate had switched exact-copy detection off for exactly those images.
Known limit: a JPEG re-upload of *textured* collection art (BAYC-style) usually exceeds the pixel
budget, so it is not caught; lossless re-uploads and flat pixel art are.
The CPU profile's painting flag rate (91.7 %) is below v1's (93.1 %) at a far lower false-alarm
rate (2.0 % vs 5.0 %); at equal false alarms it is far ahead (TPR @ 1 % FPR 0.861 vs 0.542).

**Finding 2: edited copies of collection members stay the open problem.** Letting the semantic
score count for members would catch ~30 % of them at 1 % FPR, but it would raise the collection
false-positive rate above v1's 0 %, which Phase 4's acceptance criteria forbid.

## On-chain NFT index (pgvector)

**Contents:** 284,861 fingerprinted references: 269,816 Ethereum tokens from 48 collections
(Alchemy NFT API, cap 6,000 per collection; 6,066 tokens had no thumbnail Alchemy could serve),
15,037 seed works, Mint-Gate mints. Stored per token: PDQ `bit(256)`, SSCD + DINOv2 + fused
`halfvec`, nearest-neighbour similarities, attributes, trait hash, a 128 px thumbnail.
Ingestion: ~30 tokens / s (latency-bound CDN, 32 threads); fingerprinting ~1,900 / min (RTX 3050).

**Search** (`benchmark.latency index`, 200 benchmark queries, `ef_search` 100, Postgres with
`shared_buffers` = 1 GB): fused-vector KNN-50 **p50 7.0 ms / p95 15.3 ms**, SSCD KNN-50 6.4 / 10.3 ms.
Index sizes: fused 726 MB, SSCD 363 MB, PDQ 89 MB. With Docker's default 128 MB `shared_buffers`
every search read ~1,000 pages from disk (p95 130–300 ms), hence `docker-compose.yml`'s settings.

**PDQ is searched exactly, in memory.** The HNSW index over 256-bit codes found a ≤ 8-bit match for
only 94 % of collection members (`ef_search` 1000: 98 %): clusters of identical placeholder art
break the graph, and an exact-copy check must not be approximate. Workers keep all 285k hashes in
memory (9 MB, refreshed every minute, fully every 10), scan the first 128 bits as a lower bound
and finish only near matches: 100 % recall on 300 members, 22 ms per 8-hash lookup without a match,
33 ms p50 with matches (row fetch included). The PDQ HNSW index stays for ad-hoc SQL.

**Recall: the lesson the 4,000-reference benchmark could not teach.** Against the live index, exact
search still finds a painting copy's source in the top-50 every time (100 %, NFTs 98.9 %), but one
HNSW graph over 280k clustered collection items found it only **51 %** of the time (`ef_search`
400: 68 %): the 2.7k one-off works are a sparse region the greedy search rarely reaches. Because
`copy_semantic` only ever counts one-off works, they now have their own partial HNSW indexes
(migration 0005, `WHERE NOT collection_member`): **source in the top-50 for 100 % of painting
copies** at `ef_search` 100 (`benchmark.latency copy_recall`, 268 copies; NFT copies in the shared
graph: 91.7 %, exact 98.9 %). Collection members are searched separately (display + PDQ). Global recall@10 vs exact search of the full graph is 0.58 (0.80 at
`ef_search` 1000), which only affects which similar collection items are displayed.

**Trait duplicates:** an identical trait set means an identical image only sometimes: PDQ-identical
for all 991 Pixelmon pairs (shared placeholder art) and most KaijuKingz pairs, almost never for
Checks (33,861 pairs), Cryptoadz or VeeFriends. Trait hashes are stored and reported
(`python -m ingest stats`); they count as duplicates only with PDQ agreement.

**Live sync:** `python -m ingest sync` follows `Transfer(from=0)` / `TransferSingle(from=0)` of all
tracked collections in 10-block windows (Alchemy free tier) with 3 confirmations; new tokens are
fingerprinted by `ingest embed --follow` within one polling interval (60 s + embedding). The tracked
collections are mostly minted out: 276 polling passes over ~3,900 blocks (~13 h of Ethereum,
blocks 26,332,0xx–26,335,910) saw no new mint, so the end-to-end
"searchable within 10 minutes" path is covered by the fake-Alchemy test (`test_ingest.py`), not a
live measurement.

## ONNX export (`benchmark.export_onnx`)

Queries embedded with ONNX Runtime (2 threads) against PyTorch-embedded references, 600 test queries:

| Model | Variant | Size | cos vs PyTorch (mean / min) | micro-AP Δ | p50 |
|---|---|---|---|---|---|
| SSCD | fp32 | 98 MB | 0.99999 / 0.99996 | +0.000 | 188 ms |
| SSCD | dynamic int8 | 25 MB | 0.910 / 0.614 | (+0.009, noise) | **3,800 ms** |
| DINOv2-S | fp32 | 88 MB | 1.0000 / 0.99993 | 0.000 | 228 ms |
| DINOv2-S | dynamic int8 | 24 MB | 0.959 / 0.779 | −0.008: **fails** the 0.005 bar | 206 ms |

fp32 ONNX is numerically identical to PyTorch and no faster than PyTorch on this CPU; dynamic int8 is
unusable (SSCD: 20× slower through ConvInteger, embeddings drift; DINOv2: accuracy drops). The
deployed CPU profile therefore runs PyTorch fp32; static int8 / fp16 on ARM or AVX-512-VNNI CPUs
is left for Phase 7.
