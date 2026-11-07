"""Frozen vision backbones for the AI-image detector (UnivFD recipe: frozen features + linear probe).

Phase 5 benchmarks several backbones (`training/ai_backbones.py`); the runtime loads only the one(s)
the shipped probe was trained on. Only the **vision tower** is stored: `fetch_vision` copies the
vision tensors of a Hugging Face checkpoint with HTTP range reads (SigLIP 2's checkpoint is 80 %
text tower), saves them as fp16 safetensors in `model/backbones/<key>/` with a SHA-256 manifest, and
`Backbone` loads them offline from there.

Features (L2-normalised float32):
  clip-*     the projected image embedding (`image_embeds`), as the v1 CLIP probe used
  siglip2-*  the attention-pooled embedding (`pooler_output`)
  dinov2-*   [CLS ; mean patch token]
"""

import hashlib
import json
import struct

import numpy as np
import torch
from PIL import Image

from config import MODEL_DIR, env

BACKBONE_DIR = MODEL_DIR / "backbones"
CPU_INT8 = env("MINTGATE_CPU_INT8", "0") == "1"  # rejected by benchmark/int8_gate.py: kept for experiments
# CPU profile: ONNX Runtime fp32 export (benchmark/onnx_backbones.py; parity cosine >= 0.999999,
# 1.2-1.8x faster than PyTorch eager on 2 threads). Opt-in until verified on Linux (Phase 7).
CPU_ONNX = env("MINTGATE_CPU_ONNX", "0") == "1"  # off: crashes next to the copy engine on Windows

REGISTRY = {
    # key: (hub id, family, licence)
    "clip-b32": ("openai/clip-vit-base-patch32", "clip", "MIT"),
    "clip-l14": ("openai/clip-vit-large-patch14", "clip", "MIT"),
    "siglip2-b16": ("google/siglip2-base-patch16-256", "siglip", "Apache-2.0"),
    "siglip2-so400m": ("google/siglip2-so400m-patch16-256", "siglip", "Apache-2.0"),
    "dinov2-s14": ("facebook/dinov2-small", "dino", "Apache-2.0"),
    "dinov2-b14": ("facebook/dinov2-base", "dino", "Apache-2.0"),
}
_DTYPES = {"F32": np.float32, "F16": np.float16, "BF16": None}


def _vision_prefixes(family):
    return {"clip": ("vision_model.", "visual_projection."), "siglip": ("vision_model.",), "dino": ("",)}[family]


def fetch_vision(key: str, force=False):
    """Download config + preprocessor + the vision tensors of REGISTRY[key] into BACKBONE_DIR/key."""
    from huggingface_hub import HfFileSystem
    from transformers import AutoConfig, AutoImageProcessor

    hub, family, _ = REGISTRY[key]
    dest = BACKBONE_DIR / key
    if (dest / "manifest.json").exists() and not force:
        return dest
    dest.mkdir(parents=True, exist_ok=True)
    cfg = AutoConfig.from_pretrained(hub)
    (cfg.vision_config if family != "dino" else cfg).save_pretrained(dest)
    AutoImageProcessor.from_pretrained(hub).save_pretrained(dest)
    from huggingface_hub import hf_hub_download

    fs = HfFileSystem()
    if family == "dino":  # transformers 5 renamed DINOv2's attention keys: let from_pretrained convert
        return _resave_vision(key, hub, family, dest)
    try:  # already in the local HF cache: read it, do not download again
        fh_ctx = open(hf_hub_download(hub, "model.safetensors", local_files_only=True), "rb")
    except Exception:
        if not fs.exists(f"{hub}/model.safetensors"):  # CLIP-B/32: pytorch_model.bin only (cached)
            return _resave_vision(key, hub, family, dest)
        fh_ctx = fs.open(f"{hub}/model.safetensors", "rb", block_size=1 << 24)
    tensors = {}
    with fh_ctx as fh:
        n = struct.unpack("<Q", fh.read(8))[0]
        header = json.loads(fh.read(n))
        base = 8 + n
        keep = sorted(
            (
                (k, v)
                for k, v in header.items()
                if k != "__metadata__" and k.startswith(_vision_prefixes(family)) and v["dtype"] in _DTYPES
            ),
            key=lambda kv: kv[1]["data_offsets"][0],
        )
        # merge adjacent tensors into a few large range reads
        i = 0
        while i < len(keep):
            j, start = i, keep[i][1]["data_offsets"][0]
            while j + 1 < len(keep) and keep[j + 1][1]["data_offsets"][0] == keep[j][1]["data_offsets"][1]:
                j += 1
                if keep[j][1]["data_offsets"][1] - start > 256 << 20:
                    break
            end = keep[j][1]["data_offsets"][1]
            for attempt in range(10):
                try:
                    fh.seek(base + start)
                    blob = fh.read(end - start)
                    break
                except Exception:
                    if attempt == 9:
                        raise
            for k, v in keep[i : j + 1]:
                a, b = v["data_offsets"]
                raw = blob[a - start : b - start]
                if v["dtype"] == "BF16":
                    t = torch.frombuffer(bytearray(raw), dtype=torch.bfloat16)
                else:
                    t = torch.from_numpy(np.frombuffer(raw, dtype=_DTYPES[v["dtype"]]).copy())
                tensors[k] = t.reshape(v["shape"]).to(torch.float16).contiguous()
            print(f"[backbones] {key}: {len(tensors)}/{len(keep)} tensors", flush=True)
            i = j + 1
    return _write(dest, tensors, hub, family)


def _resave_vision(key, hub, family, dest):
    """Fallback for checkpoints without safetensors: load the vision class (cache / download), keep fp16."""
    from transformers import CLIPVisionModelWithProjection, Dinov2Model, SiglipVisionModel

    cls = {"clip": CLIPVisionModelWithProjection, "siglip": SiglipVisionModel, "dino": Dinov2Model}[family]
    model = cls.from_pretrained(hub)
    tensors = {k: v.detach().to(torch.float16).contiguous() for k, v in model.state_dict().items()}
    return _write(dest, tensors, hub, family)


def _write(dest, tensors, hub, family):
    from safetensors.torch import save_file

    out = dest / "model.safetensors"
    save_file(tensors, str(out))
    digest = hashlib.sha256(out.read_bytes()).hexdigest()
    (dest / "manifest.json").write_text(
        json.dumps({"hub_id": hub, "family": family, "tensors": len(tensors), "sha256": digest}, indent=2)
    )
    return dest


class Backbone:
    """A frozen vision encoder, loaded offline from BACKBONE_DIR/<key>."""

    def __init__(self, key: str, device=None, dtype=None, quantize=None):
        from transformers import AutoImageProcessor

        hub, family, self.licence = REGISTRY[key]
        self.key, self.family, self.hub = key, family, hub
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        # fp16 on GPU, fp32 on CPU (fp16 matmuls on CPU are slow)
        self.dtype = dtype or (torch.float16 if self.device == "cuda" else torch.float32)
        src = BACKBONE_DIR / key
        if not (src / "manifest.json").exists():
            fetch_vision(key)
        man = json.loads((src / "manifest.json").read_text())
        weights = src / "model.safetensors"
        if hashlib.sha256(weights.read_bytes()).hexdigest() != man["sha256"]:
            raise RuntimeError(f"{weights}: SHA-256 mismatch")
        self.processor = AutoImageProcessor.from_pretrained(src)
        self.model = self._build(src).to(self.device, self.dtype).eval()
        self.model.requires_grad_(False)  # frozen by design (UnivFD); LoRA adds its own parameters
        # CPU only: PyTorch dynamic int8 quantisation of every nn.Linear (weights int8, activations
        # fp32). ~2x faster on 2 threads; its accuracy is gated like any model change (docs/models.md).
        self.int8 = self.device == "cpu" and (quantize if quantize is not None else CPU_INT8)
        if self.int8:
            self.model = torch.ao.quantization.quantize_dynamic(self.model, {torch.nn.Linear}, dtype=torch.qint8)
        self.ort = None
        onnx = src / "vision.onnx"
        if self.device == "cpu" and CPU_ONNX and not self.int8 and onnx.exists() and "onnx_sha256" in man:
            if hashlib.sha256(onnx.read_bytes()).hexdigest() != man["onnx_sha256"]:
                raise RuntimeError(f"{onnx}: SHA-256 mismatch")
            import onnxruntime as ort

            so = ort.SessionOptions()
            so.intra_op_num_threads, so.inter_op_num_threads = torch.get_num_threads(), 1
            self.ort = ort.InferenceSession(str(onnx), so, providers=["CPUExecutionProvider"])

    def _build(self, src):
        from safetensors.torch import load_file
        from transformers import AutoConfig, CLIPVisionModelWithProjection, Dinov2Model, SiglipVisionModel

        cfg = AutoConfig.from_pretrained(src)
        cls = {"clip": CLIPVisionModelWithProjection, "siglip": SiglipVisionModel, "dino": Dinov2Model}[self.family]
        model = cls(cfg)
        state = load_file(str(src / "model.safetensors"))
        want = set(model.state_dict())
        if not want & set(state):  # transformers 5's SiglipVisionModel has no `vision_model.` prefix
            state = {k.removeprefix("vision_model."): v for k, v in state.items()}
        missing, unexpected = model.load_state_dict({k: v.float() for k, v in state.items()}, strict=False)
        missing = [m for m in missing if "position_ids" not in m]
        if missing or unexpected:
            raise RuntimeError(f"{self.key}: missing {missing[:5]} unexpected {unexpected[:5]}")
        return model

    def pixels(self, images):
        return self.processor(images=[im.convert("RGB") for im in images], return_tensors="pt")["pixel_values"]

    def features_from_pixels(self, px: torch.Tensor) -> torch.Tensor:
        """Differentiable (used by the LoRA fine-tune); returns un-normalised features."""
        px = px.to(self.device, self.dtype)
        if self.family == "clip":
            return self.model(pixel_values=px).image_embeds
        if self.family == "siglip":
            return self.model(pixel_values=px).pooler_output
        h = self.model(pixel_values=px).last_hidden_state
        return torch.cat([h[:, 0], h[:, 1:].mean(1)], dim=-1)

    @torch.inference_mode()
    def encode(self, images) -> np.ndarray:
        if self.ort is not None:  # the export already L2-normalises
            return self.ort.run(None, {"pixel_values": self.pixels(images).numpy()})[0].astype(np.float32)
        f = self.features_from_pixels(self.pixels(images))
        return torch.nn.functional.normalize(f.float(), dim=-1).cpu().numpy().astype(np.float32)

    def encode_one(self, img: Image.Image) -> np.ndarray:
        return self.encode([img])[0]
