"""LoRA fine-tune of a frozen backbone for AI-image detection (Phase 5 candidate).

The UnivFD finding is that *freezing* the backbone is what makes the probe generalise; fine-tuning
tends to fit the training generators. This script tests that on our protocol: rank-8 LoRA on the
attention q / v projections (Hu et al. 2021) + a linear head, trained on the realworld `train`
split with internet-style augmentation (JPEG 60-95, resize, blur, crop, flip), AMP, <= 4 GB VRAM.
The epoch with the best `val` AUC is kept. Probabilities for every realworld and out-of-source
image go to data/ai_bench/probs_<key>-lora.npz, which `training.ai_backbones evaluate` scores like
any other candidate (same thresholds rule, same gate). Nothing here is shipped unless it wins.

Run from backend/:   python -m training.lora_finetune --key siglip2-b16 --epochs 3
"""

import argparse
import io
import math
import random
import time

import numpy as np
import torch
from PIL import Image, ImageFilter
from torch import nn

from detection.backbones import Backbone
from training import stats
from training.ai_backbones import BENCH_DIR, _load_oos, oos_manifest
from training.realworld_data import load_manifest, open_image


class LoRALinear(nn.Module):
    def __init__(self, base: nn.Linear, r=8, alpha=16):
        super().__init__()
        self.base = base
        self.a = nn.Parameter(torch.randn(r, base.in_features) / math.sqrt(base.in_features))
        self.b = nn.Parameter(torch.zeros(base.out_features, r))
        self.scale = alpha / r

    def forward(self, x):
        return self.base(x) + (x @ self.a.t() @ self.b.t()) * self.scale


def add_lora(model, names=("q_proj", "v_proj", "query", "value")):
    n = 0
    for mod in list(model.modules()):
        for child_name, child in list(mod.named_children()):
            if child_name in names and isinstance(child, nn.Linear):
                setattr(mod, child_name, LoRALinear(child))
                n += 1
    return n


def augment(img, rng):
    if rng.random() < 0.5:
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=rng.randint(60, 95))
        buf.seek(0)
        img = Image.open(buf).convert("RGB")
    if rng.random() < 0.2:
        s = rng.uniform(0.5, 0.9)
        w, h = img.size
        img = img.resize((max(32, int(w * s)), max(32, int(h * s))), Image.BILINEAR).resize((w, h), Image.BILINEAR)
    if rng.random() < 0.1:
        img = img.filter(ImageFilter.GaussianBlur(rng.uniform(0.3, 1.2)))
    w, h = img.size
    s = rng.uniform(0.6, 1.0)
    cw, ch = max(32, int(w * s)), max(32, int(h * s))
    x, y = rng.randint(0, w - cw), rng.randint(0, h - ch)
    img = img.crop((x, y, x + cw, y + ch))
    if rng.random() < 0.5:
        img = img.transpose(Image.FLIP_LEFT_RIGHT)
    return img


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", default="siglip2-b16")
    ap.add_argument("--epochs", type=int, default=3)
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--lr", type=float, default=2e-4)
    ap.add_argument("--score-only", action="store_true", help="load the saved checkpoint, only score")
    args = ap.parse_args()
    torch.manual_seed(0)
    rng = random.Random(0)
    bb = Backbone(args.key, dtype=torch.float32)
    for p in bb.model.parameters():
        p.requires_grad_(False)
    n = add_lora(bb.model)
    bb.model.to(bb.device)
    dim = bb.features_from_pixels(bb.pixels([Image.new("RGB", (64, 64))])).shape[-1]
    head = nn.Linear(dim, 1).to(bb.device)
    params = [p for p in bb.model.parameters() if p.requires_grad] + list(head.parameters())
    print(f"LoRA on {n} projections, {sum(p.numel() for p in params) / 1e6:.2f}M trainable", flush=True)
    opt = torch.optim.AdamW(params, lr=args.lr, weight_decay=0.01)
    scaler = torch.amp.GradScaler()
    rw = load_manifest()
    train = [r for r in rw if r["split"] == "train"]
    val_idx = [i for i, r in enumerate(rw) if r["split"] == "val"]
    steps = args.epochs * math.ceil(len(train) / args.batch)
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=args.lr, total_steps=steps, pct_start=0.1)

    def forward(images, train_mode):
        bb.model.train(train_mode)
        with torch.autocast("cuda", dtype=torch.float16):
            f = bb.features_from_pixels(bb.pixels(images))
            f = nn.functional.normalize(f.float(), dim=-1)
            return head(f * 10).squeeze(-1)  # x10: unit-norm features need a larger logit scale

    @torch.inference_mode()
    def predict(recs, loader):
        out = []
        for i in range(0, len(recs), 32):
            out.append(torch.sigmoid(forward([loader(r) for r in recs[i : i + 32]], False)).float().cpu().numpy())
        return np.concatenate(out)

    ckpt = BENCH_DIR / f"{args.key}-lora.pt"
    best, t0 = None, time.time()
    for ep in range(0 if args.score_only else args.epochs):
        rng.shuffle(train)
        for i in range(0, len(train), args.batch):
            batch = train[i : i + args.batch]
            imgs = [augment(open_image(r), rng) for r in batch]
            y = torch.tensor([r["label"] == "ai" for r in batch], dtype=torch.float32, device=bb.device)
            loss = nn.functional.binary_cross_entropy_with_logits(forward(imgs, True).float(), y)
            opt.zero_grad(set_to_none=True)
            scaler.scale(loss).backward()
            scaler.step(opt)
            scaler.update()
            sched.step()
            if (i // args.batch) % 200 == 0:
                print(f"ep {ep} {i}/{len(train)} loss {loss.item():.4f} {time.time() - t0:.0f}s", flush=True)
        pv = predict([rw[i] for i in val_idx], open_image)
        auc = stats.auc(np.array([rw[i]["label"] == "ai" for i in val_idx]), pv)
        print(f"epoch {ep}: val AUC {auc:.4f}", flush=True)
        if best is None or auc > best[0]:
            best = (
                auc,
                ep,
                {k: v.detach().clone() for k, v in bb.model.state_dict().items() if k.endswith((".a", ".b"))},
                {k: v.detach().clone() for k, v in head.state_dict().items()},
            )
            # saved at once: scoring 23k images later must never cost the training run
            torch.save({"lora": best[2], "head": best[3], "key": args.key, "val_auc": auc, "epoch": ep}, ckpt)
    if best is None:  # --score-only
        c = torch.load(ckpt, map_location=bb.device)
        best = (c["val_auc"], c["epoch"], c["lora"], c["head"])
    auc, ep, lora_state, head_state = best
    bb.model.load_state_dict(lora_state, strict=False)
    head.load_state_dict(head_state)
    print(f"best epoch {ep} (val AUC {auc:.4f}); scoring every image", flush=True)
    oos = oos_manifest()
    p_rw, p_oos = predict(rw, open_image), predict(oos, _load_oos)
    out = BENCH_DIR / f"probs_{args.key}-lora.npz"
    np.savez(out, rw=p_rw, oos=p_oos, oos_files=[r["file"] for r in oos], val_auc=auc, epoch=ep)
    print("saved", out, flush=True)


if __name__ == "__main__":
    main()
