"""Bi-encoder relevance scoring — the real baseline 4.

`LexicalScorer` exists so the harness runs dependency-free, but it is not the baseline
that matters. Baseline 4 is "pure semantic retrieval, no personalization" — Kong's
announced roadmap, what Bedrock AgentCore ships, and what the published tool-retrieval
results use. Scoring it with TF-IDF understates it, and understating the competitor
biases the Phase 0 kill gate in our favour.

Default model is ``mangopy/ToolRet-trained-e5-base-v2``: a retriever fine-tuned for tool
retrieval, released alongside ToolRet. A general-purpose embedder would be a weaker
opponent than a vendor would actually deploy, which is the same bias one step milder.
"""

from __future__ import annotations

from .catalog import Catalog, Tool

DEFAULT_MODEL = "mangopy/ToolRet-trained-e5-base-v2"

# Published on the ToolRet leaderboard, evaluated on this exact data with nDCG@10 and
# Completeness@10. Running one of these is the only external reference point we have:
# without it a poor `semantic-retrieval` score is uninterpretable, because we cannot
# distinguish a real finding from a weak implementation of ours.
CALIBRATION_MODELS = {
    "e5-base": "intfloat/e5-base-v2",
    "bge-large": "BAAI/bge-large-en-v1.5",
    "toolret-e5-base": "mangopy/ToolRet-trained-e5-base-v2",
    "toolret-e5-large": "mangopy/ToolRet-trained-e5-large-v2",
    "toolret-bge-large": "mangopy/ToolRet-trained-bge-large-en-v1.5",
}

# e5-family models expect these prefixes; omitting them measurably degrades retrieval.
_QUERY_PREFIX = "query: "
_DOC_PREFIX = "passage: "


class EmbeddingScorer:
    """Cosine similarity between query and tool embeddings.

    Tool embeddings are computed once per catalog and cached; query embeddings are
    cached by string, since a sweep re-scores the same task at every budget.
    """

    def __init__(
        self,
        catalog: Catalog,
        model_name: str = DEFAULT_MODEL,
        model=None,
        use_prefixes: bool = True,
    ) -> None:
        if model is None:
            from sentence_transformers import SentenceTransformer  # optional extra

            model = SentenceTransformer(model_name)
        self._model = model
        self._use_prefixes = use_prefixes

        tools = list(catalog)
        docs = [self._doc_text(t) for t in tools]
        vectors = self._encode(docs)
        self._tool_vectors = {t.key: v for t, v in zip(tools, vectors)}
        self._query_cache: dict[str, object] = {}

    def _doc_text(self, tool: Tool) -> str:
        text = f"{tool.name}\n{tool.description}"
        return f"{_DOC_PREFIX}{text}" if self._use_prefixes else text

    def _encode(self, texts: list[str]):
        return self._model.encode(texts, normalize_embeddings=True, show_progress_bar=False)

    def score(self, task: str, tool: Tool) -> float:
        vector = self._tool_vectors.get(tool.key)
        if vector is None:
            return 0.0
        query = self._query_cache.get(task)
        if query is None:
            text = f"{_QUERY_PREFIX}{task}" if self._use_prefixes else task
            query = self._encode([text])[0]
            self._query_cache[task] = query
        # Vectors are L2-normalised, so the dot product is the cosine.
        return float(sum(q * d for q, d in zip(query, vector)))
