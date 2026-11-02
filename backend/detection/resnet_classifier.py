"""ResNet-18 authentic-vs-forged classifier.

The class order is read from `model_meta.json` written at training time,
instead of being hard-coded; a hard-coded index is what caused the old
"AI probability" to be reported inverted.
"""
import json

import torch
import torch.nn as nn
from PIL import Image
from torchvision import models, transforms

from config import RESNET_META, RESNET_WEIGHTS

DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")
IMG_SIZE = 224
MEAN, STD = [0.485, 0.456, 0.406], [0.229, 0.224, 0.225]

eval_transform = transforms.Compose([
    transforms.Resize(256),
    transforms.CenterCrop(IMG_SIZE),
    transforms.ToTensor(),
    transforms.Normalize(MEAN, STD),
])


def build_model(num_classes=2, pretrained=False):
    weights = models.ResNet18_Weights.IMAGENET1K_V1 if pretrained else None
    m = models.resnet18(weights=weights)
    m.fc = nn.Linear(m.fc.in_features, num_classes)
    return m


class ResNetClassifier:
    def __init__(self):
        meta = json.loads(RESNET_META.read_text()) if RESNET_META.exists() else {}
        # Legacy checkpoint (ImageFolder order): AI_cleaned=0, originals_cleaned=1
        self.classes = meta.get("classes", ["forged", "authentic"])
        self.forged_idx = self.classes.index("forged")
        self.metrics = meta.get("test_metrics", {})
        self.version = meta.get("version", "legacy")
        self.model = build_model(len(self.classes))
        state = torch.load(RESNET_WEIGHTS, map_location=DEVICE, weights_only=True)
        self.model.load_state_dict(state)
        self.model.to(DEVICE).eval()

    @torch.no_grad()
    def predict(self, img: Image.Image) -> float:
        """Probability that the artwork is forged (AI-generated or manipulated).

        Averages the centre crop and its mirror image (cheap test-time augmentation).
        `img` should already be `preprocessing.detector_view(...)` - the training normalisation.
        """
        x = eval_transform(img.convert("RGB")).unsqueeze(0)
        x = torch.cat([x, torch.flip(x, dims=[3])]).to(DEVICE)
        probs = torch.softmax(self.model(x), dim=1).mean(0)
        return float(probs[self.forged_idx])
