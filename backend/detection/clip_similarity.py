"""CLIP (ViT-B/32) image embeddings for semantic similarity search.

CLIP captures *content* rather than pixels, so it finds plagiarised copies
that survive heavy edits (recolouring, re-drawing, style transfer) that
defeat pHash and ORB.
"""

import numpy as np
import torch
from PIL import Image
from transformers import CLIPModel, CLIPProcessor

from config import CLIP_MODEL_NAME

DEVICE = "cuda" if torch.cuda.is_available() else "cpu"


class ClipEncoder:
    def __init__(self):
        try:  # prefer the local cache: no network round-trip, works offline on demo day
            self.model = CLIPModel.from_pretrained(CLIP_MODEL_NAME, local_files_only=True)
            self.processor = CLIPProcessor.from_pretrained(CLIP_MODEL_NAME, local_files_only=True)
        except OSError:  # first run: download (~600 MB)
            self.model = CLIPModel.from_pretrained(CLIP_MODEL_NAME)
            self.processor = CLIPProcessor.from_pretrained(CLIP_MODEL_NAME)
        self.model = self.model.to(DEVICE).eval()

    @torch.no_grad()
    def encode(self, images) -> np.ndarray:
        """L2-normalised float32 embeddings, shape (n, 512)."""
        inputs = self.processor(images=[im.convert("RGB") for im in images], return_tensors="pt")
        feats = self.model.get_image_features(pixel_values=inputs["pixel_values"].to(DEVICE))
        if not isinstance(feats, torch.Tensor):  # newer transformers return a model output
            feats = feats.pooler_output
        feats = torch.nn.functional.normalize(feats.float(), dim=-1)
        return feats.cpu().numpy().astype(np.float32)

    def encode_one(self, img: Image.Image) -> np.ndarray:
        return self.encode([img])[0]
