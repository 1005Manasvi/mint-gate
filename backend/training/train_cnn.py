"""Fine-tune ResNet-18 (all layers, GPU) on the real-world dataset: authentic vs forged.

forged = AI-generated (modern + older generators, AI art) + authentic images with a
synthetic copy-move / splice. Flux is never seen in training; the
`test_unseen` split measures generalisation to a new generator.

Robustness augmentations simulate what happens to images on the internet
(re-compression, resizing, blur), so the model cannot rely on fragile
high-frequency traces alone.

Evaluation (written to model/model_meta.json and model/metrics.json):
  * test         - held-out images from the training distribution
  * test_unseen  - Flux only (generator never seen in training)
  * robustness   - test set after JPEG q=70 and after 50 % downscaling
  * per-source / per-generator accuracy

Run from backend/:   python -m training.train_cnn --epochs 8
"""

import argparse
import io
import json
import random
import time
from collections import defaultdict

import numpy as np
import torch
import torch.nn as nn
from PIL import Image, ImageFilter
from sklearn.metrics import accuracy_score, confusion_matrix, f1_score, precision_score, recall_score, roc_auc_score
from torch.utils.data import DataLoader
from torchvision import transforms

from config import MODEL_DIR, RESNET_META, RESNET_WEIGHTS
from detection.resnet_classifier import IMG_SIZE, MEAN, STD, build_model, eval_transform
from training.realworld_data import FORGED, ForgeryDataset

CLASSES = ["authentic", "forged"]


class RandomRecompress:
    """Real-world degradations: JPEG re-compression, down/up-scaling, mild blur."""

    def __call__(self, img: Image.Image) -> Image.Image:
        r = random.random()
        if r < 0.35:
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=random.randint(60, 95))
            buf.seek(0)
            img = Image.open(buf).convert("RGB")
        elif r < 0.5:
            s = random.uniform(0.5, 0.9)
            w, h = img.size
            img = img.resize((max(32, int(w * s)), max(32, int(h * s))), Image.BILINEAR).resize((w, h), Image.BILINEAR)
        elif r < 0.6:
            img = img.filter(ImageFilter.GaussianBlur(random.uniform(0.3, 1.2)))
        return img


train_transform = transforms.Compose(
    [
        RandomRecompress(),
        transforms.RandomResizedCrop(IMG_SIZE, scale=(0.4, 1.0), ratio=(0.75, 1.33)),
        transforms.RandomHorizontalFlip(),
        transforms.ToTensor(),
        transforms.Normalize(MEAN, STD),
    ]
)


class Degraded:
    """Robustness-test transform. A class (not a closure) so DataLoader workers can pickle it on Windows."""

    def __init__(self, kind):
        self.kind = kind

    def __call__(self, img):
        if self.kind == "jpeg70":
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=70)
            buf.seek(0)
            img = Image.open(buf).convert("RGB")
        elif self.kind == "half":
            img = img.resize((max(32, img.width // 2), max(32, img.height // 2)), Image.BILINEAR)
        return eval_transform(img)


@torch.no_grad()
def predict(model, ds, device, batch, workers):
    model.eval()
    dl = DataLoader(ds, batch_size=batch, num_workers=workers, pin_memory=True)
    probs, labels = [], []
    for x, y in dl:
        with torch.autocast(device.type, enabled=device.type == "cuda"):
            out = model(x.to(device, non_blocking=True))
        probs.append(torch.softmax(out.float(), 1)[:, FORGED].cpu())
        labels.append(y)
    return torch.cat(probs).numpy(), torch.cat(labels).numpy()


def metrics(p, y, groups=None):
    pred = (p >= 0.5).astype(int)
    m = {
        "n": int(len(y)),
        "accuracy": round(float(accuracy_score(y, pred)), 4),
        "precision_forged": round(float(precision_score(y, pred, zero_division=0)), 4),
        "recall_forged": round(float(recall_score(y, pred, zero_division=0)), 4),
        "f1_forged": round(float(f1_score(y, pred, zero_division=0)), 4),
        "roc_auc": round(float(roc_auc_score(y, p)), 4) if len(set(y)) > 1 else None,
        "confusion_matrix [[TN,FP],[FN,TP]]": confusion_matrix(y, pred, labels=[0, 1]).tolist(),
    }
    if groups is not None:
        acc = defaultdict(list)
        for g, a in zip(groups, pred == y):
            acc[g].append(a)
        m["accuracy_by_group"] = {g: round(float(np.mean(v)), 4) for g, v in sorted(acc.items())}
    return m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--epochs", type=int, default=8)
    ap.add_argument("--batch", type=int, default=48)
    ap.add_argument("--lr", type=float, default=3e-4)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--freeze", action="store_true", help="train only layer4 + fc (CPU fallback)")
    ap.add_argument("--eval-only", action="store_true", help="skip training; evaluate saved weights")
    args = ap.parse_args()

    torch.manual_seed(0)
    random.seed(0)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print("device:", device, torch.cuda.get_device_name(0) if device.type == "cuda" else "", flush=True)

    train_ds = ForgeryDataset(["train"], train_transform, train=True, seed=1)
    val_ds = ForgeryDataset(["val"], eval_transform, train=False, seed=2)
    test_ds = ForgeryDataset(["test"], eval_transform, train=False, seed=3)
    unseen_ds = ForgeryDataset(["test_unseen"], eval_transform, train=False, manip_fraction=0)
    print(f"train {len(train_ds)} | val {len(val_ds)} | test {len(test_ds)} | unseen {len(unseen_ds)}", flush=True)

    kw = dict(batch_size=args.batch, num_workers=args.workers, pin_memory=True, persistent_workers=args.workers > 0)
    train_dl = DataLoader(train_ds, shuffle=True, drop_last=True, **kw)

    model = build_model(2, pretrained=True).to(device)
    if args.freeze:
        for name, p in model.named_parameters():
            p.requires_grad = name.startswith(("layer4", "fc"))
    n_auth = sum(1 for _, l, _ in train_ds.items if l == 0)
    n_forged = len(train_ds) - n_auth
    weight = torch.tensor([len(train_ds) / (2 * n_auth), len(train_ds) / (2 * n_forged)], device=device)
    criterion = nn.CrossEntropyLoss(weight=weight, label_smoothing=0.05)
    opt = torch.optim.AdamW([p for p in model.parameters() if p.requires_grad], lr=args.lr, weight_decay=1e-4)
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=args.lr, total_steps=args.epochs * len(train_dl))
    scaler = torch.amp.GradScaler(enabled=device.type == "cuda")

    best_auc, history = -1.0, []
    if args.eval_only:  # keep the training history from the previous run
        prev = json.loads((MODEL_DIR / "metrics.json").read_text()) if (MODEL_DIR / "metrics.json").exists() else {}
        history = prev.get("history", [])
        best_auc = max((h.get("val_auc") or 0 for h in history), default=-1.0)
    for epoch in range(1, (0 if args.eval_only else args.epochs) + 1):
        model.train()
        t, seen, loss_sum, correct = time.time(), 0, 0.0, 0
        for step, (x, y) in enumerate(train_dl, 1):
            x, y = x.to(device, non_blocking=True), y.to(device, non_blocking=True)
            opt.zero_grad(set_to_none=True)
            with torch.autocast(device.type, enabled=device.type == "cuda"):
                out = model(x)
                loss = criterion(out, y)
            scaler.scale(loss).backward()
            scaler.step(opt)
            scaler.update()
            sched.step()
            seen += len(y)
            loss_sum += loss.item() * len(y)
            correct += (out.argmax(1) == y).sum().item()
            if step % 100 == 0:
                print(f"  epoch {epoch} step {step}/{len(train_dl)} loss {loss_sum / seen:.4f}", flush=True)

        vp, vy = predict(model, val_ds, device, args.batch, args.workers)
        vm = metrics(vp, vy)
        row = {
            "epoch": epoch,
            "train_loss": round(loss_sum / seen, 4),
            "train_acc": round(correct / seen, 4),
            "val_acc": vm["accuracy"],
            "val_auc": vm["roc_auc"],
            "sec": int(time.time() - t),
        }
        history.append(row)
        print(f"epoch {epoch}: {row}", flush=True)
        if vm["roc_auc"] > best_auc:
            best_auc = vm["roc_auc"]
            torch.save(model.state_dict(), RESNET_WEIGHTS)
            print("  saved best model", flush=True)

    model.load_state_dict(torch.load(RESNET_WEIGHTS, map_location=device, weights_only=True))
    tp, ty = predict(model, test_ds, device, args.batch, args.workers)
    results = {
        "test": metrics(tp, ty, test_ds.kind),
        "test_by_source": metrics(tp, ty, test_ds.source)["accuracy_by_group"],
    }
    up, uy = predict(model, unseen_ds, device, args.batch, args.workers)
    results["test_unseen"] = {"n": int(len(uy)), "recall_forged": round(float((up >= 0.5).mean()), 4)}
    for kind in ("jpeg70", "half"):
        d = ForgeryDataset(["test"], Degraded(kind), train=False, seed=3)
        p, y = predict(model, d, device, args.batch, args.workers)
        results[f"robust_{kind}"] = metrics(p, y)
    print("RESULTS:", json.dumps(results, indent=2), flush=True)

    meta = {
        "version": time.strftime("resnet18-realworld-%Y%m%d-%H%M"),
        "classes": CLASSES,
        "img_size": IMG_SIZE,
        "epochs": args.epochs,
        "batch": args.batch,
        "lr": args.lr,
        "optimizer": "AdamW + OneCycle, label smoothing 0.05",
        "device": device.type,
        "fine_tuned": "layer4+fc" if args.freeze else "all layers",
        "train_size": len(train_ds),
        "dataset": "realworld manifest (see training/fetch_realworld.py)",
        "test_metrics": results["test"],
        "results": results,
        "best_val_auc": best_auc,
    }
    RESNET_META.write_text(json.dumps(meta, indent=2))
    (MODEL_DIR / "metrics.json").write_text(json.dumps({"history": history, **results}, indent=2))


if __name__ == "__main__":
    main()
