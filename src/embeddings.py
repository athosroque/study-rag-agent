import logging
from typing import List, Optional
from src.config import settings

logger = logging.getLogger(__name__)


class EmbeddingManager:
    _instance: Optional["EmbeddingManager"] = None
    _embedder = None

    def __init__(self):
        self.provider = settings.EMBEDDING_PROVIDER.lower()
        self.model_name = settings.EMBEDDING_MODEL
        self.dimension = settings.EMBEDDING_DIM
        self._init_embedder()

    @classmethod
    def get_instance(cls) -> "EmbeddingManager":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _init_embedder(self):
        if self.provider == "fastembed":
            try:
                from fastembed import TextEmbedding
                logger.info(f"Carregando modelo FastEmbed: {self.model_name}...")
                self._embedder = TextEmbedding(model_name=self.model_name)
                logger.info(f"FastEmbed ({self.model_name}) carregado com sucesso.")
            except ImportError:
                logger.warning("FastEmbed não instalado. Tentando alternativas...")
                self._fallback_init()
            except Exception as e:
                logger.error(f"Erro ao inicializar FastEmbed: {e}. Usando fallback.")
                self._fallback_init()
        elif self.provider in ["openai", "litellm"]:
            self._init_openai()
        else:
            self._fallback_init()

    def _init_openai(self):
        try:
            from langchain_openai import OpenAIEmbeddings
            self._embedder = OpenAIEmbeddings(
                model=self.model_name,
                openai_api_base=settings.LITELLM_BASE_URL,
                openai_api_key=settings.LITELLM_API_KEY
            )
            logger.info("OpenAIEmbeddings configurado via LiteLLM/OpenAI.")
        except Exception as e:
            logger.error(f"Erro ao inicializar OpenAIEmbeddings: {e}")
            self._fallback_init()

    def _fallback_init(self):
        try:
            from sentence_transformers import SentenceTransformer
            logger.info(f"Tentando sentence-transformers com {self.model_name}...")
            self._embedder = SentenceTransformer(self.model_name)
            self.provider = "sentence_transformers"
        except Exception:
            logger.warning("Nenhum backend de embedding vetorial pesado encontrado. Ativando MockDeterministicEmbedder.")
            self._embedder = MockDeterministicEmbedder(dim=self.dimension)
            self.provider = "mock"

    def embed_query(self, text: str) -> List[float]:
        """Gera o embedding para um texto único (query)."""
        if self._embedder is None:
            self._init_embedder()

        if self.provider == "fastembed":
            generator = self._embedder.embed([text])
            vector = list(next(generator))
            return [float(x) for x in vector]
        elif self.provider in ["openai", "litellm"]:
            return self._embedder.embed_query(text)
        elif self.provider == "sentence_transformers":
            vector = self._embedder.encode(text)
            return [float(x) for x in vector]
        elif self.provider == "mock":
            return self._embedder.embed(text)
        else:
            raise ValueError(f"Provedor de embedding desconhecido: {self.provider}")

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        """Gera embeddings para múltiplos textos em lote."""
        if not texts:
            return []
        if self._embedder is None:
            self._init_embedder()

        if self.provider == "fastembed":
            generator = self._embedder.embed(texts)
            return [[float(x) for x in vec] for vec in generator]
        elif self.provider in ["openai", "litellm"]:
            return self._embedder.embed_documents(texts)
        elif self.provider == "sentence_transformers":
            vectors = self._embedder.encode(texts)
            return [[float(x) for x in vec] for vec in vectors]
        elif self.provider == "mock":
            return [self._embedder.embed(t) for t in texts]
        else:
            raise ValueError(f"Provedor de embedding desconhecido: {self.provider}")


class MockDeterministicEmbedder:
    """Embedder determinístico para testes e ambientes sem rede externa."""
    def __init__(self, dim: int = 384):
        self.dim = dim

    def embed(self, text: str) -> List[float]:
        import hashlib
        import math
        vec = [0.0] * self.dim
        hash_val = hashlib.sha256(text.encode("utf-8")).digest()
        for i in range(self.dim):
            byte_idx = i % len(hash_val)
            val = float(hash_val[byte_idx]) / 255.0 - 0.5
            vec[i] = val
        # Normaliza vetor para norma unitária
        norm = math.sqrt(sum(x * x for x in vec)) or 1.0
        return [x / norm for x in vec]


embedding_manager = EmbeddingManager.get_instance()
