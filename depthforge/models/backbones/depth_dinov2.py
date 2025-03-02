from mmseg.models.builder import BACKBONES, MODELS
from .depthforge import DepthForge
from .dino_v2 import DinoVisionTransformer
from .utils import set_requires_grad, set_train

from .depth_anything_v2.dpt import DepthAnythingV2
import torch
import torch.nn.functional as F


@BACKBONES.register_module()
class DepthForgeDinoVisionTransformer(DinoVisionTransformer):
    def __init__(
        self,
        reins_config=None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.depthforge: DepthForge = MODELS.build(reins_config)

        # DEVICE = (
        #     "cuda"
        #     if torch.cuda.is_available()
        #     else "mps" if torch.backends.mps.is_available() else "cpu"
        # )

        model_configs = {
            "vits": {
                "encoder": "vits",
                "features": 64,
                "out_channels": [48, 96, 192, 384],
            },
            "vitb": {
                "encoder": "vitb",
                "features": 128,
                "out_channels": [96, 192, 384, 768],
            },
            "vitl": {
                "encoder": "vitl",
                "features": 256,
                "out_channels": [256, 512, 1024, 1024],
            },
            "vitg": {
                "encoder": "vitg",
                "features": 384,
                "out_channels": [1536, 1536, 1536, 1536],
            },
        }

        checkpoint = torch.load(
            f"checkpoints/depth_anything_v2_vitl.pth", map_location="cpu"
        )

        self.depth_anything = DepthAnythingV2(**model_configs["vitl"])
        self.depth_anything.load_state_dict(checkpoint, strict=True)
        self.depth_anything = self.depth_anything.cuda().eval()

    def forward_features(self, x, masks=None):
        B, _, h, w = x.shape

        if h == 512:
            x_depth = F.interpolate(x, (518, 518), mode="bilinear", align_corners=False)
        if h == 1024:
            x_depth = F.interpolate(x, (1036, 1036), mode="bilinear", align_corners=False)
        depth_features = self.depth_anything.forward_extra(x_depth)

        H, W = h // self.patch_size, w // self.patch_size
        x = self.prepare_tokens_with_masks(x, masks)

        outs = []
        for idx, blk in enumerate(self.blocks):
            x = blk(x)
            x = self.depthforge.forward(
                x,
                depth_features[idx],
                idx,
                batch_first=True,
                has_cls_token=True,
            )
            if idx in self.out_indices:
                outs.append(
                    x[:, 1:, :].permute(0, 2, 1).reshape(B, -1, H, W).contiguous()
                )
        return self.depthforge.return_auto(outs)

    def train(self, mode: bool = True):
        if not mode:
            return super().train(mode)
        set_requires_grad(self, ["reins"])
        set_train(self, ["reins"])

    def state_dict(self, destination, prefix, keep_vars):
        state = super().state_dict(destination, prefix, keep_vars)
        keys = [k for k in state.keys() if "rein" not in k]
        for key in keys:
            state.pop(key)
            if key in destination:
                destination.pop(key)
        return state
