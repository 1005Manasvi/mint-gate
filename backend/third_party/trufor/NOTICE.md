# TruFor (vendored)

Source: https://github.com/grip-unina/TruFor (`test_docker/src/models`, commit cloned 2026-11-06).
Copyright (c) 2023 GRIP-UNINA (TruFor) and the CMX authors (`models/cmx`, see LICENSE_CMX.txt).

**Licence: non-commercial / nonprofit use only** (LICENSE.txt). Mint-Gate's permissive-only
configuration (`MINTGATE_PERMISSIVE_ONLY=1`) never imports this package.

Modifications (licence condition ii: all original notices are retained):
- `timm.models.layers` imports replaced by `_timm.py` (DropPath, to_2tuple, trunc_normal_)
- absolute `models.DnCNN` import made relative
- weights are not included: `python -m benchmark.forgery fetch` downloads them (SHA-256 pinned)

Paper: F. Guillaro, D. Cozzolino, A. Sud, N. Dufour, L. Verdoliva, "TruFor: Leveraging all-round
clues for trustworthy image forgery detection and localization", CVPR 2023.
