"""Cerebro Laya: la política de inversión que sustituye a los profesores LLM.

Laya (convaiinnovations/laya, Apache-2.0) NO es un LLM: es un modelo de decisión de una
sola pasada (codificador ModernBERT ~400M + cabeza de decisión ~25M) que, dado un
ESTADO en texto y una pregunta de tipo `choice`, devuelve una distribución calibrada
sobre las opciones en ~100 ms (GPU). No escribe comandos ni llama APIs: por eso aquí
el "cuerpo" (quién mira las cotizaciones y lanza las órdenes) es código determinista, y
Laya decide, para cada acción del mercado, VENDER / MANTENER / COMPRAR.

Entrenamiento: el codificador se congela y se entrena solo la cabeza (type_emb,
head, scorer, act_head), que es lo que Laya expone para afinar. La cabeza entrenada se
guarda aparte (safetensors pequeño) y se carga encima del checkpoint base.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional, Sequence

import numpy as np
import torch

BASE = os.environ.get("LAYA_MODEL", "convaiinnovations/laya")

ACTIONS = ("sell", "hold", "buy")
QUESTION = {
    "t": "choice",
    "ins": ("You run a small investment account that must pay its own hosting bill every day. "
            "Looking at this stock and your account, what should you do with it now?"),
    "crit": {
        "sell": "Sell the whole position now, or stay out of it if you hold none",
        "hold": "Keep everything as it is and wait",
        "buy": "Buy: put a slice of the available cash into this stock",
    },
}


class LayaPolicy:
    def __init__(self, device: Optional[str] = None, head_path: Optional[Path] = None):
        import laya
        self.agent = laya.load(BASE, device=device or ("cuda" if torch.cuda.is_available() else "cpu"))
        self.model = self.agent.model
        self.tok = self.agent.tok
        self.device = self.agent.device
        self.max_len = int(self.agent.cfg.get("max_len", 512))
        self.head_max_len = int(self.agent.cfg.get("head_max_len", 192))
        for p in self.model.encoder.parameters():
            p.requires_grad_(False)
        if head_path:
            self.load_head(head_path)
        self.model.eval()

    # ---- cabeza entrenable ---------------------------------------------------
    def head_params(self):
        return [p for n, p in self.model.named_parameters() if not n.startswith("encoder.")]

    def head_state(self) -> dict:
        return {k: v.detach().cpu().clone() for k, v in self.model.state_dict().items()
                if not k.startswith("encoder.")}

    def set_head_state(self, sd: dict) -> None:
        missing = self.model.load_state_dict(sd, strict=False)
        bad = [k for k in missing.missing_keys if not k.startswith("encoder.")]
        if bad or missing.unexpected_keys:
            raise ValueError(f"cabeza incompatible: faltan {bad[:3]} sobran {missing.unexpected_keys[:3]}")

    def save_head(self, path: Path) -> None:
        from safetensors.torch import save_file
        path.parent.mkdir(parents=True, exist_ok=True)
        save_file({k: v.contiguous() for k, v in self.head_state().items()}, str(path))

    def load_head(self, path: Path) -> None:
        from safetensors.torch import load_file
        self.set_head_state(load_file(str(path)))

    # ---- codificación ----------------------------------------------------------
    def encode(self, states: Sequence[str]) -> dict:
        from laya.common import QTYPES, build_sequence, collate_items
        items = []
        for s in states:
            ids, markers = build_sequence(self.tok, s, QUESTION, self.max_len, self.head_max_len)
            if len(markers) != len(ACTIONS):
                raise ValueError("el estado desborda la secuencia: las opciones no caben")
            items.append({"ids": ids, "markers": markers, "qtype": QTYPES["choice"]})
        return collate_items([items], self.tok.pad_token_id)

    def n_tokens(self, state: str) -> int:
        return len(self.tok(state, add_special_tokens=False)["input_ids"])

    def logits(self, batch: dict, grad: bool = False) -> torch.Tensor:
        dev = self.device
        with torch.set_grad_enabled(grad), torch.autocast(device_type=dev.type, dtype=torch.bfloat16,
                                                          enabled=dev.type == "cuda"):
            lg, _ = self.model(batch["input_ids"].to(dev), batch["attention_mask"].to(dev),
                               batch["marker_pos"].to(dev), batch["marker_mask"].to(dev),
                               batch["qtype"].to(dev), detach_encoder=True)
        return lg.float()[:, : len(ACTIONS)]

    @torch.no_grad()
    def probs(self, states: Sequence[str], batch_size: int = 32) -> np.ndarray:
        out = []
        for i in range(0, len(states), batch_size):
            b = self.encode(states[i: i + batch_size])
            out.append(torch.softmax(self.logits(b), -1).cpu().numpy())
        return np.concatenate(out) if out else np.zeros((0, len(ACTIONS)))
