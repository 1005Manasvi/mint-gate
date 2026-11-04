# Models: register, licences, benchmarks

Every model Mint-Gate runs, why it is there, and the benchmark that earned it its place
(CLAUDE.md "Model-selection rules": benchmark before adopting, generalisation over in-distribution
accuracy, domain-aware evaluation, hardware budget, licences, fewer models wins ties).

## Licence register

| Model | Role | Weights | Licence | Status |
|---|---|---|---|---|
| CLIP ViT-B/32 (`openai/clip-vit-base-patch32`) | AI-detector backbone (frozen); was the v1 copy score | Hugging Face | MIT | in use (AI detector only) |
| CLIP linear probe (`model/ai_detector.json`) | AI-generated probability | trained here | MIT (code) / dataset terms | in use |
| ResNet-18 (`model/best_model.pt`) | forgery probability, REVIEW-only | trained here (ImageNet init, BSD-3) | BSD-3 / dataset terms | in use (REVIEW only) |
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
**Permissive-only configuration:** every model in the deployed copy engine above is MIT / BSD /
Apache-2.0, so no research-only switch is needed.

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
