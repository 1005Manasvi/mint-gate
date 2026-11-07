"""The three timm helpers TruFor's SegFormer encoder imports (replaces the timm dependency)."""

import collections.abc
from itertools import repeat

import torch
from torch import nn


def to_2tuple(x):
    if isinstance(x, collections.abc.Iterable) and not isinstance(x, str):
        return tuple(x)
    return tuple(repeat(x, 2))


def trunc_normal_(tensor, mean=0.0, std=1.0, a=-2.0, b=2.0):
    return nn.init.trunc_normal_(tensor, mean=mean, std=std, a=a, b=b)


class DropPath(nn.Module):
    """Stochastic depth; identity at inference (the only way Mint-Gate runs TruFor)."""

    def __init__(self, drop_prob=0.0):
        super().__init__()
        self.drop_prob = drop_prob

    def forward(self, x):
        if self.drop_prob == 0.0 or not self.training:
            return x
        keep = 1 - self.drop_prob
        mask = x.new_empty((x.shape[0],) + (1,) * (x.ndim - 1)).bernoulli_(keep)
        return x * mask / keep
