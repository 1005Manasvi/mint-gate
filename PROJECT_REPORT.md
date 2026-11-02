# Mint-Gate: AI Image Forensics as a Gate Before NFT Minting

**Project report**: Manasvi Patil · October 2026

## 1. Problem

An NFT proves *who owns* a token and *where it came from*; it proves nothing about the artwork.
Plagiarised, edited, cloned or AI-generated images mint as easily as originals, and a smart
contract cannot inspect an image. Mint-Gate runs AI image forensics **off-chain, before minting**,
and uses the blockchain only for what it is good at: a public, tamper-evident record of every
verdict, plus an on-chain rule that refuses to mint without a passing one.

## 2. System

| Stage | Technique | Detects |
|---|---|---|
| Preprocessing | format allow-list, decompression-bomb guard, RGB, SHA-256 of pixels, *detector view* (same resize + JPEG as the training data) | malformed uploads; stable image identity |
| ResNet-18 | ImageNet-pretrained, fully fine-tuned on GPU | AI-generated and manipulated images |
| AI-content detector | CLIP ViT-B/32 linear probe (Ojha et al., CVPR 2023) + spectral fingerprint (Durall et al., CVPR 2020) | AI generators, including unseen ones |
| Copy-move | 16×16 block matching, shift-vector voting; score = dominance of one displacement | cloned regions |
| CLIP copy score | similarity to the protected collection, relative to each reference's own nearest neighbour | semantic copies |
| pHash / ORB | 64-bit DCT hash; ORB keypoints + RANSAC homography, against *sparse* references | near-exact and geometric copies |
| Policy engine | per-check risk (0.5 at REVIEW, 1.0 at FAIL), weighted 0–100 score | PASS / REVIEW / FAIL |

**Protected reference collection:** 9,000 CryptoPunks, 1,657 museum/WikiArt paintings, and
every artwork minted through Mint-Gate.

## 3. Data and methodology

**Dataset (17,086 images),** normalised identically for both classes:

| Class | Sources |
|---|---|
| Authentic (7,728) | 3,875 photographs (ImageNet via Tiny-GenImage), 1,853 human paintings/drawings (WikiArt, The Met, Unsplash), 2,000 CryptoPunks |
| AI-generated (9,358) | ADM, BigGAN, GLIDE, SD 1.5, VQDM, Wukong (Tiny-GenImage); Ideogram, SDXL, Civitai, Copilot, OpenArt; local AI-art set; **Midjourney (held out)** |

- **Splits.** Random 80/10/10 train/val/test. Midjourney (554 images) is reserved as `test_unseen`.
- **Bias control.** In GenImage the real images are JPEG and the fakes PNG, a known shortcut
  ("Fake or JPEG?", Grommelt et al. 2024). Every image is resized to a 256 px short side and
  re-encoded as JPEG (quality 88–96), so format and resolution carry no label.
- **Synthetic manipulations.** 35 % of authentic training images also appear with an on-the-fly
  copy-move or splice, labelled *forged*.
- **Robustness augmentation.** Random JPEG (q 60–95), down/up-scaling and blur.
- **ResNet-18 training.** All layers, AdamW + OneCycle (max LR 3e-4), label smoothing 0.05,
  mixed precision, batch 48, 10 epochs on an RTX 3050 Laptop GPU (about 90 s/epoch). The best
  epoch is selected by validation ROC-AUC.
- **AI detector.** Logistic regressions on CLIP embeddings and on spectral features, with C chosen
  on validation. The ensemble weight was also chosen on validation and came out at 1.0: the
  spectral model adds nothing on real-world data.

## 4. Results

### 4.1 ResNet-18 (test, n = 1,891)

| Metric | Value |
|---|---|
| Accuracy | **85.1 %** |
| ROC-AUC | **0.941** |
| Precision / recall (forged) | 92.7 % / 81.3 % |
| Confusion [[TN, FP], [FN, TP]] | [[701, 72], [209, 909]] |
| After JPEG q=70 | 87.1 % accuracy, AUC 0.945 |
| After 50 % downscaling | 82.3 % accuracy, AUC 0.921 |
| Unseen Midjourney detected | 45.5 % |

Accuracy by group:
- Genuine: paintings 85.7 %, photos 88.9 %, pixel art 99.5 %.
- Manipulated: 93.3 %.
- Generators: SD 1.5 85.7 %, Wukong 83.6 %, BigGAN 83.1 %, art generators 82.2 %, GLIDE 79.5 %,
  VQDM 52.4 %, ADM 45.8 %.

### 4.2 AI-generated content detector

| Model | Test AUC (n = 1,621) | Unseen Midjourney vs genuine, AUC (n = 1,327) |
|---|---|---|
| Spectral fingerprint | 0.761 | 0.752 |
| **CLIP linear probe** | **0.966** (90.6 % acc.) | **0.926** (85.2 % acc.; 77 % of Midjourney detected) |

The fine-tuned CNN catches only 45.5 % of an unseen generator. The frozen-CLIP probe catches
77 %, which replicates the finding of Ojha et al. that CLIP features generalise better to new
generators.

### 4.3 Calibration

All REVIEW/FAIL thresholds are percentiles of the scores of *genuine* validation/test art:

| Check | REVIEW / FAIL | Genuine flagged | Forgeries caught (REVIEW) | AUC |
|---|---|---|---|---|
| ResNet-18 | 0.621 / 0.901 | 5.1 % / 1.0 % | — | 0.941 |
| AI detector | 0.737 / 0.977 | 5.1 % / 1.0 % | — | 0.966 |
| Copy-move | 3.07 / 4.26 | 5 % / 1.25 % | 68.8 % | 0.90 |
| CLIP copy score: paintings | 0.049 / 0.119 | 10 % | 61.7 % | 0.81 |
| CLIP copy score: pixel-art NFTs | same | 0 % | 0 % | 0.08 |

![Calibration](assets/calibration.png)

**Finding: similarity depends on collection density.** In a generative collection, a genuine new
item differs from existing ones by 0.1–3 % of pixels. Its nearest neighbour is 0.99 similar
(median), compared with 0.88 for paintings. Edited copies end up *less* similar to the collection
than genuine siblings, which inverts the ROC-AUC (0.08). Mint-Gate therefore:
1. measures copy evidence relative to each reference's own neighbourhood;
2. evaluates pHash and ORB only against references in sparse regions (nearest-neighbour
   similarity < 0.95);
3. relies on exact-match blocking (off-chain, plus `ImageAlreadyMinted` on-chain) for dense
   collections.

### 4.4 End-to-end evaluation (pipeline + policy v2.2.0, n = 50 per category)

![System evaluation](assets/system_evaluation.png)

| Upload | PASS | REVIEW | FAIL |
|---|---|---|---|
| Genuine painting / drawing | 84 % | 6 % | 10 % |
| Genuine photograph | 82 % | 18 % | 0 % |
| Genuine new pixel-art NFT | 62 % | 32 % | 6 % |
| AI art generators | 6 % | 32 % | 62 % |
| AI photo generators | 16 % | 34 % | 50 % |
| Midjourney (unseen) | 32 % | 46 % | 22 % |
| Exact copy of a protected work | 0 % | 0 % | 100 % |
| Edited copy of a protected painting | 0 % | 56 % | 44 % |
| Edited copy of a pixel-art NFT | 90 % | 10 % | 0 % |
| Copy-move forgery | 0 % | 0 % | 100 % |
| Spliced forgery | 2 % | 8 % | 90 % |

Summary:
- Genuine uploads: 76 % PASS, 5.3 % wrongly FAILed.
- Forgeries: **81.8 % stopped** (REVIEW or FAIL), 58.5 % rejected automatically.
- Analysis takes about 0.5–1.7 s per image (GPU).

## 5. Blockchain layer

`MintGateNFT.sol` is ERC-721 + OpenZeppelin AccessControl, Solidity 0.8.24, deployed on Ethereum Sepolia.

| Function | Role | Behaviour |
|---|---|---|
| `recordVerification` | VERIFIER | Stores every verdict immutably: report hash, image hash, score, decision, policy hash and version. Failures included. |
| `resolveReview` | REVIEWER | Human decision on REVIEW verdicts only. |
| `mintVerified` | VERIFIER | Reverts unless PASS or REVIEW_APPROVED. One token per report and one per image hash. |
| `getVerification`, `tokenOfImage`, `reportOfToken` | public | Provenance queries. |

- **IPFS.** The artwork, ERC-721 metadata and full verification report are pinned to IPFS. The
  report hash on-chain lets anyone check the pinned report.
- **Tamper evidence.** `/api/verify` recomputes the report hash and compares it with the chain.
  In testing, editing a stored report to read "PASS, 99" was detected, and the contract still
  refused the mint.
- **Durability.** On start-up the server rebuilds its list of minted artworks from
  `VerifiedMint` events plus IPFS. Duplicate protection therefore survives restarts on ephemeral
  hosting; this was verified by restarting the server and re-uploading a minted image.
- **Tests.** Contract 7/7. The end-to-end API flow (analyse → review → mint → verify → restart
  sync) was tested on a local chain, and the deploy package was tested in production mode.

## 6. Limitations and future work

1. **Error rates.** About 5 % of genuine art is wrongly rejected and about 20 % goes to review.
   18 % of forgeries pass. Each check is calibrated to a 5 % false-alarm rate, so the union over
   six checks is higher.
2. **Generalisation.** The CNN generalises poorly to unseen generators (45.5 % on Midjourney)
   and to ADM/VQDM. Newer generators (Flux, DALL·E 3, SD 3) are not covered. Future work:
   larger and more recent training data, and fine-tuning CLIP with LoRA.
3. **Generative collections.** Edited copies of items from a self-similar collection cannot be
   distinguished from genuine siblings by appearance alone. Collection-aware trait parsing is
   needed.
4. **Trust model.** The platform's verifier key mints on artists' behalf (gas is sponsored).
   Production would use separate verifier/reviewer keys behind a multisig, plus EIP-712 signed
   attestations so artists can mint from their own wallets.
5. **Data licensing.** Training data includes CC BY-NC-SA and research-use sets, so the project
   is non-commercial.

## 7. Reproducibility

From `backend/`:
`training.fetch_realworld` → `train_cnn --epochs 10` → `train_ai_detector` → `build_index` →
`calibrate --n 240` → `evaluate_system --n 50`.

Outputs are in `backend/model/` (`model_meta.json`, `metrics.json`, `ai_detector.json`,
`system_eval.json`) and `backend/policy.json` (`calibration`).
