"""Client of the local embedding service (Ollama's `GET /api/tags` and `POST /api/embed`)."""
import http.client
import json
import math
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

from val3 import Val3Error
from val3.config import Embedding

KEEP_ALIVE = "24h"  # the service keeps the model loaded: a cold load takes seconds
TEXT_VERSION = 1    # version of entry_text(); bump it when that text changes, so old vectors are not compared
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # local service: no proxy


class EmbeddingError(Val3Error):
    """The embedding service gave no usable vectors, or a vault cannot hold them."""


@dataclass(frozen=True)
class Space:
    """What makes two vectors comparable: the same model build, size and embedded text."""
    model: str
    digest: str
    dims: int
    text_version: int = TEXT_VERSION


def entry_text(title: str, statement: str) -> str:
    return f"{title}\n{statement}"


def digest(settings: Embedding, timeout: float) -> str:
    """Digest of the configured model as the service lists it; a model name alone does not fix the vectors."""
    return _digest(settings, time.monotonic() + timeout)


def embed(settings: Embedding, texts: list[str], timeout: float) -> tuple[Space, list[list[float]]]:
    """Vectors of `texts` and their space; `timeout` bounds both requests together."""
    deadline = time.monotonic() + timeout
    model_digest = _digest(settings, deadline)
    data = _call(settings, "/api/embed", {"model": settings.model, "input": texts, "keep_alive": KEEP_ALIVE},
                 deadline)
    vectors = data.get("embeddings") if isinstance(data, dict) else None
    if (not isinstance(vectors, list) or not vectors or len(vectors) != len(texts)
            or not all(map(_usable, vectors)) or len({len(vector) for vector in vectors}) != 1):
        raise EmbeddingError(f"embedding service {settings.url}: unexpected answer")
    return Space(settings.model, model_digest, len(vectors[0])), vectors


def _digest(settings: Embedding, deadline: float) -> str:
    data = _call(settings, "/api/tags", None, deadline)
    models = data.get("models") if isinstance(data, dict) else None
    names = (settings.model, f"{settings.model}:latest")
    for model in models if isinstance(models, list) else []:
        if isinstance(model, dict) and model.get("name") in names and isinstance(model.get("digest"), str) \
                and model["digest"]:
            return model["digest"]
    raise EmbeddingError(f"embedding service {settings.url}: model {settings.model} is not available")


def _call(settings: Embedding, path: str, body: dict | None, deadline: float) -> object:
    timeout = deadline - time.monotonic()
    if timeout <= 0:
        raise EmbeddingError(f"embedding service {settings.url}: timed out")
    request = urllib.request.Request(f"{settings.url}{path}",
                                     data=None if body is None else json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
    try:
        with _OPENER.open(request, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        raise EmbeddingError(f"embedding service {settings.url}: HTTP {exc.code}") from None
    except urllib.error.URLError as exc:
        raise EmbeddingError(f"embedding service {settings.url}: {exc.reason}") from None
    except (OSError, http.client.HTTPException, ValueError) as exc:  # timeouts, resets, bad JSON
        raise EmbeddingError(f"embedding service {settings.url}: {str(exc) or type(exc).__name__}") from None


def _usable(vector: object) -> bool:
    """Finite numbers, not all zero: a zero vector scores NaN, which PostgreSQL ranks above every floor."""
    return (isinstance(vector, list) and len(vector) > 0
            and all(isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) for x in vector)
            and any(x != 0 for x in vector))


def vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(repr(float(x)) for x in vector) + "]"


def no_table(vault: str) -> EmbeddingError:
    return EmbeddingError(f"vault {vault} has no embedding table: run `val3 vault upgrade {vault}`")
