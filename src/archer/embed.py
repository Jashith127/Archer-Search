"""Text embeddings for history recall and result ranking, computed on this computer.

Three backends share one interface (`Embedder.encode(texts) -> unit vectors`):

- **Static model** (default): Model2Vec's potion models, distilled sentence embeddings that are a
  token lookup plus a mean, so a search's 20 snippets embed in about a millisecond on any CPU with
  no GPU or PyTorch. The model (30 MB for potion-base-8M) downloads once into `data/models/`.
- **Ollama**: an embedding model served by a local Ollama (`nomic-embed-text` by default), for
  people who already run one and want stronger matches.
- **Lexical**: hashed words, word pairs and character trigrams. No download, always available,
  and what every feature falls back to while a model loads or when it can't be fetched. It matches
  spelling variants and shared words, not meaning.

Vectors are stored with the name of the model that made them, so switching models re-embeds
lazily instead of comparing vectors from different spaces.
"""
from __future__ import annotations

import asyncio
import logging
import re
import threading
import time
import zlib
from pathlib import Path

import httpx
import numpy as np

from archer import tls
from archer.config import DATA_DIR, settings

log = logging.getLogger("archer")

STATIC_MODELS = {
    "potion-base-8M": "minishlab/potion-base-8M",
    "potion-retrieval-32M": "minishlab/potion-retrieval-32M",
}
RETRY_AFTER = 3600  # seconds before a failed model download is tried again
MAX_CHARS = 600  # titles and snippets; longer text adds little to a mean of token vectors

_WORD = re.compile(r"[a-z0-9]+(?:[.'][a-z0-9]+)*")
_STOP = frozenset("a an and are as at be by for from how i in is it my of on or the to vs was what when where which who why with will can do does you your me".split())


def words(text: str) -> list[str]:
    """Lower-case content words, with a light plural/verb-ending fold ("monitors" -> "monitor")."""
    out = []
    for w in _WORD.findall(text.lower()):
        if w in _STOP:
            continue
        if len(w) > 4 and w.endswith("ies"):
            w = w[:-3] + "y"
        elif len(w) > 3 and w.endswith("s") and not w.endswith(("ss", "us", "is")):
            w = w[:-1]
        out.append(w)
    return out


class Embedder:
    name = "base"
    ready = True

    def encode(self, texts: list[str]) -> np.ndarray:
        raise NotImplementedError

    async def aencode(self, texts: list[str]) -> np.ndarray:
        return self.encode(texts)


class LexicalEmbedder(Embedder):
    """Signed feature hashing into a fixed space: words weigh most, trigrams catch typos and variants."""
    name = "lexical"
    dims = 1024

    def _vector(self, text: str) -> np.ndarray:
        vec = np.zeros(self.dims, dtype=np.float32)
        tokens = words(text[:MAX_CHARS])
        feats: list[tuple[str, float]] = [(w, 1.0) for w in tokens]
        feats += [(f"{a}_{b}", 0.5) for a, b in zip(tokens, tokens[1:])]
        for w in tokens:
            padded = f"#{w}#"
            feats += [(padded[i:i + 3], 0.25) for i in range(len(padded) - 2)]
        for feat, weight in feats:
            h = zlib.crc32(feat.encode())
            vec[h % self.dims] += weight if h & 0x80000000 else -weight
        norm = float(np.linalg.norm(vec))
        return vec / norm if norm else vec

    def encode(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dims), dtype=np.float32)
        return np.stack([self._vector(t) for t in texts])


class StaticEmbedder(Embedder):
    """A Model2Vec model, downloaded once to data/models and loaded in the background."""

    def __init__(self, key: str, folder: Path):
        self.name = key
        self.repo = STATIC_MODELS[key]
        self.folder = folder / key
        self.model = None
        self.ready = False
        self.failed = ""
        self.failed_at = 0.0
        self._lock = threading.Lock()

    def load(self) -> bool:
        with self._lock:
            if self.failed and time.time() - self.failed_at > RETRY_AFTER:
                self.failed = ""  # offline at startup: try the download again later
            if self.model is not None or self.failed:
                return self.model is not None
            try:
                from model2vec import StaticModel
                if not (self.folder / "model.safetensors").exists():
                    from huggingface_hub import snapshot_download
                    self.folder.mkdir(parents=True, exist_ok=True)
                    snapshot_download(self.repo, local_dir=self.folder,
                                      allow_patterns=["*.json", "*.safetensors", "*.txt", "*.md"])
                self.model = StaticModel.from_pretrained(self.folder, force_download=False)
                self.ready = True
                log.info("embedding model %s loaded", self.name)
            except Exception as exc:  # network, disk, or a missing optional package: stay lexical
                self.failed, self.failed_at = type(exc).__name__, time.time()
                log.warning("embedding model %s unavailable (%s); using lexical matching", self.name, self.failed)
            return self.model is not None

    def known_word(self, word: str) -> bool:
        """Whether the model's vocabulary has this as a whole word: "studio" yes, "seltos" no."""
        if not hasattr(self, "_vocab"):
            self._vocab = set(self.model.tokenizer.get_vocab())
        return any(k in self._vocab for k in (word, "\u2581" + word, "\u0120" + word))

    def encode(self, texts: list[str]) -> np.ndarray:
        vecs = self.model.encode([t[:MAX_CHARS] for t in texts], use_multiprocessing=False)
        vecs = np.asarray(vecs, dtype=np.float32)
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        return vecs / np.where(norms == 0, 1, norms)


class OllamaEmbedder(Embedder):
    """Embeddings from a local Ollama's /api/embed. Async only; never a paid service."""

    def __init__(self, model: str, base: str):
        self.model = model
        self.name = f"ollama:{model}"
        self.base = base.rstrip("/")
        self.ready = True
        self.failed = ""

    def encode(self, texts: list[str]) -> np.ndarray:  # pragma: no cover - sync callers use the lexical fallback
        raise RuntimeError("Ollama embeddings are async")

    async def aencode(self, texts: list[str]) -> np.ndarray:
        parts = []
        async with httpx.AsyncClient(timeout=30, verify=tls.context()) as client:
            for start in range(0, len(texts), 128):  # a long history's first run goes in batches
                resp = await client.post(f"{self.base}/api/embed", json={"model": self.model, "input": [t[:MAX_CHARS] for t in texts[start:start + 128]]})
                resp.raise_for_status()
                parts.append(np.asarray(resp.json()["embeddings"], dtype=np.float32))
        vecs = np.concatenate(parts)
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        return vecs / np.where(norms == 0, 1, norms)


LEXICAL = LexicalEmbedder()


class Embeddings:
    """The configured embedder, with the lexical one standing in until (or unless) it is ready."""

    def __init__(self, folder: Path = DATA_DIR / "models"):
        self.folder = folder
        self._models: dict[str, Embedder] = {}

    def configured(self) -> Embedder:
        choice = settings.semantic_model
        if choice == "lexical":
            return LEXICAL
        if choice == "ollama":
            base = settings.ai_base_url if settings.ai_provider == "ollama" and settings.ai_base_url else "http://127.0.0.1:11434"
            key = f"ollama:{settings.ollama_embed_model}@{base}"
            if key not in self._models:
                self._models[key] = OllamaEmbedder(settings.ollama_embed_model, base.removesuffix("/v1"))
            return self._models[key]
        if choice not in self._models:
            self._models[choice] = StaticEmbedder(choice, self.folder)
        return self._models[choice]

    def warm(self) -> None:
        """Load the static model off the request path (called from a background thread at startup)."""
        model = self.configured()
        if isinstance(model, StaticEmbedder):
            model.load()

    def active(self) -> Embedder:
        model = self.configured()
        if isinstance(model, StaticEmbedder) and not model.ready:
            if (not model.failed or time.time() - model.failed_at > RETRY_AFTER) and not model._lock.locked():
                threading.Thread(target=model.load, daemon=True).start()
            return LEXICAL
        return model

    async def encode(self, texts: list[str]) -> tuple[str, np.ndarray]:
        """(model name, unit vectors). Falls back to lexical vectors if the model errors."""
        model = self.active()
        if not texts:
            return model.name, np.zeros((0, 1), dtype=np.float32)
        try:
            if len(texts) > 64 and not isinstance(model, OllamaEmbedder):
                # A first run over a long history: keep the server answering meanwhile.
                return model.name, await asyncio.to_thread(model.encode, texts)
            return model.name, await model.aencode(texts)
        except (httpx.HTTPError, ValueError, KeyError, RuntimeError) as exc:
            if isinstance(model, OllamaEmbedder):
                log.warning("Ollama embeddings failed (%s); using lexical matching", type(exc).__name__)
            return LEXICAL.name, LEXICAL.encode(texts)

    def status(self) -> dict:
        model = self.configured()
        if model is LEXICAL:
            return {"name": "lexical", "state": "ready", "label": "word matching (no model)"}
        if isinstance(model, StaticEmbedder):
            state = "ready" if model.ready else ("failed" if model.failed else "loading")
            return {"name": model.name, "state": state, "label": model.name,
                    "detail": "Couldn't download the model, so Archer matches words instead." if model.failed else ""}
        return {"name": model.name, "state": "ready", "label": f"Ollama · {model.model}"}


embeddings = Embeddings()
