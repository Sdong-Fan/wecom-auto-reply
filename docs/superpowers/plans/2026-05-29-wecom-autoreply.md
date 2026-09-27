# 企业微信智能客服 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a WeCom auto-reply system that extracts knowledge from chat records and training videos, then automatically responds to external customer 1-on-1 messages via the official WeCom callback API.

**Architecture:** Docker Compose with 4 services — gateway (FastAPI receiving WeCom callbacks + RAG processing), qdrant (vector DB), redis (message queue), pipeline (offline knowledge extraction). LLM calls go through a multi-model pool with DeepSeek as primary and OpenAI/Qwen as fallback, with a circuit breaker. Knowledge pipeline processes chat exports and training videos into embeddings stored in Qdrant.

**Tech Stack:** Python 3.11+, FastAPI, Qdrant, Redis+Redis Queue, DeepSeek API, OpenAI Whisper, Docker Compose, ngrok (dev), pycryptodome (AES), httpx (async HTTP)

---

## File Structure Map

```
d:\your-project\                    # Project root
├── docker-compose.yml                      # 4 services: gateway, qdrant, redis, pipeline
├── .env.example                            # Template for all config values
├── .gitignore                              # Ignore data/, .env, __pycache__
├── README.md                               # Setup and usage guide
├── requirements.txt                        # Python dependencies
├── gateway\                                # WeCom Gateway service
│   ├── __init__.py
│   ├── Dockerfile
│   ├── main.py                             # FastAPI app, /callback endpoint
│   ├── crypto.py                           # AES encrypt/decrypt, signature verify
│   ├── handler.py                          # Message routing + confidence gating
│   └── wecom_client.py                     # WeCom API client (send message, get token)
├── rag\                                    # RAG Engine (shared module)
│   ├── __init__.py
│   ├── embed_query.py                      # Query → vector via DeepSeek Embedding API
│   ├── generator.py                        # Prompt template + generate via llm_client
│   ├── guard.py                            # Two-layer quality checks
│   └── llm_client.py                       # DeepSeek API chat wrapper (simple retry)
├── pipeline\                               # Knowledge extraction (offline batch)
│   ├── __init__.py
│   ├── Dockerfile
│   ├── chat_importer.py                    # Parse WeCom chat export .txt files
│   ├── video_transcribe.py                 # Whisper ASR for training videos
│   ├── chunker.py                          # Semantic text splitting
│   ├── embedder.py                         # DeepSeek Embedding → Qdrant upsert
│   └── run_pipeline.py                     # CLI entry point for full pipeline run
├── tests\
│   ├── __init__.py
│   ├── test_crypto.py                      # Unit: encrypt/decrypt roundtrip, signature
│   ├── test_guard.py                       # Unit: retrieval threshold, reply quality
│   ├── test_retriever.py                   # Integration: Qdrant search
│   ├── test_chunker.py                     # Unit: text splitting
│   ├── test_llm_client.py                   # Unit: DeepSeek chat wrapper
│   ├── test_chat_importer.py               # Unit: chat log parsing
│   └── test_handler.py                     # Integration: full message pipeline
├── data\                                   # Runtime data dir (gitignored)
│   ├── chat_raw\                           # Raw chat export .txt files
│   ├── videos\                             # Training video .mp4 files
│   ├── qdrant\                             # Qdrant persistent storage
│   └── pending\                            # Escalated messages for human review
└── scripts\
    └── dev.ps1                             # PowerShell one-command dev startup
```

**Key interfaces between modules:**

| Module | Exports | Consumed by |
|--------|---------|-------------|
| `gateway.crypto` | `verify_url_signature()`, `decrypt_message()`, `encrypt_message()` | `gateway.main` |
| `gateway.wecom_client` | `send_text_message()`, `get_access_token()` | `gateway.handler` |
| `gateway.handler` | `handle_message()` | `gateway.main` |
| `rag.embed_query` | `embed_query(text) -> list[float]` | `gateway.main` |
| `rag.retriever` | `search(query_vector, client, collection, top_k)` | `gateway.handler` |
| `rag.guard` | `check_retrieval()`, `check_reply()` | `gateway.handler` |
| `rag.generator` | `generate_reply(question, chunks)` | `gateway.handler` |
| `rag.llm_client` | `chat(messages, temperature, max_tokens)` | `rag.generator`, `rag.guard` |
| `pipeline.chunker` | `split_text(text, chunk_size, overlap)` | `pipeline.chat_importer`, `pipeline.video_transcribe` |
| `pipeline.embedder` | `embed_and_store(chunks, collection)` | `pipeline.run_pipeline` |
| `pipeline.chat_importer` | `parse_chat_file(filepath) -> List[QAPair]` | `pipeline.run_pipeline` |
| `pipeline.video_transcribe` | `transcribe_video(filepath) -> str` | `pipeline.run_pipeline` |

---

### Task 1: Project Scaffold

**Files:**
- Create: `d:\your-project\.gitignore`
- Create: `d:\your-project\.env.example`
- Create: `d:\your-project\requirements.txt`
- Create: `d:\your-project\gateway\__init__.py`
- Create: `d:\your-project\rag\__init__.py`
- Create: `d:\your-project\pipeline\__init__.py`
- Create: `d:\your-project\tests\__init__.py`

- [ ] **Step 1: Write .gitignore**

```gitignore
# Secrets
.env

# Python
__pycache__/
*.pyc
*.pyo
.venv/
venv/

# Data (runtime)
data/chat_raw/*
data/videos/*
data/qdrant/*
data/pending/*
!data/chat_raw/.gitkeep
!data/videos/.gitkeep
!data/qdrant/.gitkeep
!data/pending/.gitkeep

# IDE
.vscode/
.idea/

# Whisper models
whisper_models/
```

- [ ] **Step 2: Write .env.example**

```bash
# === WeCom / 企业微信 ===
WECOM_CORP_ID=your_corp_id
WECOM_AGENT_ID=1000001
WECOM_SECRET=your_app_secret
WECOM_TOKEN=your_callback_token
WECOM_ENCODING_AES_KEY=your_43_char_aes_key

# === DeepSeek API (主模型) ===
DEEPSEEK_API_KEY=sk-your-deepseek-key
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1

# === OpenAI API (备选1, 可选) ===
OPENAI_API_KEY=sk-your-openai-key
OPENAI_BASE_URL=https://api.openai.com/v1

# === Qwen / 通义千问 (备选2, 可选) ===
QWEN_API_KEY=your-qwen-key
QWEN_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1

# === Qdrant ===
QDRANT_URL=http://qdrant:6333
QDRANT_COLLECTION=knowledge_base

# === Redis ===
REDIS_URL=redis://redis:6379/0

# === Gateway ===
GATEWAY_PORT=8000
LOG_LEVEL=INFO
```

- [ ] **Step 3: Write requirements.txt**

```
fastapi==0.115.6
uvicorn[standard]==0.34.0
httpx==0.28.1
pycryptodome==3.21.0
qdrant-client==1.12.2
redis==5.2.1
rq==2.0.0
openai==1.59.0
openai-whisper==20240930
pytest==8.3.4
pytest-asyncio==0.25.0
python-dotenv==1.0.1
defusedxml==0.7.1
```

- [ ] **Step 4: Write empty __init__.py files**

All `__init__.py` files are empty (just to make Python package imports work).

- [ ] **Step 5: Create data directory .gitkeep files**

```bash
mkdir -p data/chat_raw data/videos data/qdrant data/pending
touch data/chat_raw/.gitkeep data/videos/.gitkeep data/qdrant/.gitkeep data/pending/.gitkeep
```

---

### Task 2: WeCom Crypto (encrypt/decrypt/signature)

**Files:**
- Create: `d:\your-project\tests\test_crypto.py`
- Create: `d:\your-project\gateway\crypto.py`

- [ ] **Step 1: Write failing test for signature verification**

```python
# tests/test_crypto.py
import hashlib

def test_verify_signature_valid():
    """WeCom callback URL verification: correct signature passes."""
    from gateway.crypto import verify_signature

    token = "test_token"
    timestamp = "1620000000"
    nonce = "test_nonce"
    echostr = "test_echo"

    # Replicate WeCom signature algorithm
    params = sorted([token, timestamp, nonce, echostr])
    joined = "".join(params)
    expected_sig = hashlib.sha1(joined.encode()).hexdigest()

    assert verify_signature(expected_sig, timestamp, nonce, echostr, token) is True


def test_verify_signature_invalid():
    from gateway.crypto import verify_signature

    token = "test_token"
    timestamp = "1620000000"
    nonce = "test_nonce"
    echostr = "test_echo"

    assert verify_signature("bad_signature", timestamp, nonce, echostr, token) is False
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd d:\your-project
pip install pytest
python -m pytest tests/test_crypto.py -v
# Expected: FAIL - module not found
```

- [ ] **Step 3: Write crypto.py implementation**

```python
# gateway/crypto.py
"""WeCom message encryption/decryption (AES-256-CBC) and signature verification.

WeCom callback protocol:
- Signature: SHA1(sort([token, timestamp, nonce, echostr_or_msg]))
- Message encryption: AES-256-CBC with PKCS#7 padding
- Encrypted payload: Base64(16_bytes_random + 4_bytes_msg_len + msg + corpid)
"""

import base64
import hashlib
import struct
import time
from Crypto.Cipher import AES
from Crypto.Random import get_random_bytes


def verify_signature(signature: str, timestamp: str, nonce: str,
                     echostr: str, token: str) -> bool:
    """Verify WeCom callback signature (URL validation and message reception)."""
    params = sorted([token, timestamp, nonce, echostr])
    joined = "".join(params)
    expected = hashlib.sha1(joined.encode()).hexdigest()
    return signature == expected


def decrypt_message(encrypted: str, encoding_aes_key: str) -> tuple[str, str]:
    """Decrypt an encrypted WeCom message.

    Returns (decrypted_xml, corp_id).
    Raises ValueError if decryption or corp_id validation fails.
    """
    aes_key = base64.b64decode(encoding_aes_key + "=")
    cipher = AES.new(aes_key, AES.MODE_CBC, iv=aes_key[:16])
    encrypted_bytes = base64.b64decode(encrypted)
    decrypted = cipher.decrypt(encrypted_bytes)

    # PKCS#7 unpad
    pad_len = decrypted[-1]
    decrypted = decrypted[:-pad_len]

    # Parse structure: 16 bytes random + 4 bytes msg_len + msg + corpid
    msg_len = struct.unpack("!I", decrypted[16:20])[0]
    msg = decrypted[20:20 + msg_len].decode("utf-8")
    corp_id = decrypted[20 + msg_len:].decode("utf-8")

    return msg, corp_id


def encrypt_message(reply_xml: str, encoding_aes_key: str, corp_id: str) -> str:
    """Encrypt a reply message for WeCom.

    Returns Base64-encoded encrypted payload.
    """
    aes_key = base64.b64decode(encoding_aes_key + "=")
    cipher = AES.new(aes_key, AES.MODE_CBC, iv=aes_key[:16])

    msg_bytes = reply_xml.encode("utf-8")
    random_prefix = get_random_bytes(16)
    msg_len = struct.pack("!I", len(msg_bytes))
    payload = random_prefix + msg_len + msg_bytes + corp_id.encode("utf-8")

    # PKCS#7 pad
    block_size = 32
    pad_len = block_size - (len(payload) % block_size)
    payload += bytes([pad_len] * pad_len)

    encrypted = cipher.encrypt(payload)
    return base64.b64encode(encrypted).decode()
```

- [ ] **Step 4: Run test to verify it passes**

```bash
python -m pytest tests/test_crypto.py -v
# Expected: PASS
```

- [ ] **Step 5: Add roundtrip test and run**

Add to `tests/test_crypto.py`:

```python
def test_encrypt_decrypt_roundtrip():
    import base64
    from Crypto.Random import get_random_bytes
    from gateway.crypto import encrypt_message, decrypt_message

    # Generate a random 43-char AES key (base64 of 32 bytes)
    aes_key = base64.b64encode(get_random_bytes(32)).decode()
    corp_id = "test_corp"
    original_xml = "<xml><ToUserName>user</ToUserName><Content>hello</Content></xml>"

    encrypted = encrypt_message(original_xml, aes_key, corp_id)
    decrypted_xml, decrypted_corp_id = decrypt_message(encrypted, aes_key)

    assert decrypted_xml == original_xml
    assert decrypted_corp_id == corp_id


def test_decrypt_message_wrong_key_raises():
    import base64
    import pytest
    from Crypto.Random import get_random_bytes
    from gateway.crypto import encrypt_message, decrypt_message

    aes_key = base64.b64encode(get_random_bytes(32)).decode()
    wrong_key = base64.b64encode(get_random_bytes(32)).decode()
    corp_id = "test_corp"
    original_xml = "<xml><ToUserName>user</ToUserName><Content>hello</Content></xml>"

    encrypted = encrypt_message(original_xml, aes_key, corp_id)

    # Decrypting with wrong key should produce garbage (won't raise consistently
    # because AES-CBC doesn't authenticate, but the corpid won't match)
    _decrypted, wrong_corp = decrypt_message(encrypted, wrong_key)
    assert wrong_corp != corp_id
```

```bash
python -m pytest tests/test_crypto.py -v
# Expected: 4 passed
```

---

### Task 3: DeepSeek LLM Client (simple async chat wrapper)

**Files:**
- Create: `d:\your-project\rag\llm_client.py`

- [ ] **Step 1: Write llm_client.py**

```python
# rag/llm_client.py
"""DeepSeek API chat wrapper with simple retry."""

import os
import logging
from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

_client: AsyncOpenAI | None = None
MAX_RETRIES = 2


def get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(
            api_key=os.environ["DEEPSEEK_API_KEY"],
            base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"),
            timeout=60.0,
        )
    return _client


async def chat(messages: list[dict], temperature: float = 0.7,
               max_tokens: int = 1024) -> str:
    """Send chat completion to DeepSeek API with retry.

    Returns the reply text.
    Raises RuntimeError if all retries fail.
    """
    client = get_client()
    last_error = None

    for attempt in range(MAX_RETRIES + 1):
        try:
            response = await client.chat.completions.create(
                model="deepseek-chat",
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return response.choices[0].message.content or ""
        except Exception as e:
            last_error = e
            logger.warning(f"DeepSeek API attempt {attempt + 1}/{MAX_RETRIES + 1} failed: {e}")
            if attempt < MAX_RETRIES:
                import asyncio
                await asyncio.sleep(1.0 * (attempt + 1))  # backoff

    raise RuntimeError(f"DeepSeek API failed after {MAX_RETRIES + 1} attempts: {last_error}")
```

- [ ] **Step 2: Verify module loads**

```bash
cd d:\your-project && python -c "from rag.llm_client import chat; print('OK')"
# Expected: OK
```

No unit test needed for this module — it's a thin wrapper over the OpenAI SDK and is exercised by integration tests later.

---

### Task 4: RAG Retriever (Qdrant search)

**Files:**
- Create: `d:\your-project\rag\retriever.py`

- [ ] **Step 1: Write retriever.py**

```python
# rag/retriever.py
"""Qdrant vector search for retrieving relevant knowledge chunks."""

import logging
from qdrant_client import QdrantClient
from qdrant_client.models import ScoredPoint

logger = logging.getLogger(__name__)

# Qdrant collection configuration
COLLECTION_NAME = "knowledge_base"
VECTOR_SIZE = 1536  # DeepSeek embedding dimension


def get_client(url: str = "http://localhost:6333") -> QdrantClient:
    """Create a Qdrant client."""
    return QdrantClient(url=url)


def ensure_collection(client: QdrantClient, collection_name: str = COLLECTION_NAME) -> None:
    """Create the collection if it doesn't exist."""
    from qdrant_client.models import Distance, VectorParams
    if not client.collection_exists(collection_name):
        client.create_collection(
            collection_name=collection_name,
            vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
        )
        logger.info(f"Created collection: {collection_name}")


async def search(query_vector: list[float], client: QdrantClient,
                 collection_name: str = COLLECTION_NAME,
                 top_k: int = 5) -> list[ScoredPoint]:
    """Search Qdrant for the most relevant knowledge chunks.

    Returns list of ScoredPoint, each containing:
    - score: similarity score (0-1 for cosine)
    - payload: dict with 'text', 'source', etc.
    """
    results = client.search(
        collection_name=collection_name,
        query_vector=query_vector,
        limit=top_k,
    )
    return results
```

- [ ] **Step 2: Write integration test (requires Qdrant running)**

```python
# tests/test_retriever.py
import pytest
from rag.retriever import get_client, ensure_collection, search, COLLECTION_NAME


@pytest.fixture
def qdrant_client():
    client = get_client()
    ensure_collection(client, COLLECTION_NAME)
    # Insert a test point
    client.upsert(
        collection_name=COLLECTION_NAME,
        points=[{
            "id": "test-1",
            "vector": [0.1] * 1536,
            "payload": {"text": "我们的产品保修期为2年", "source": "chat_export_001.txt"},
        }],
    )
    yield client
    client.delete(collection_name=COLLECTION_NAME)


def test_search_returns_results(qdrant_client):
    """Search with a vector should return the test point."""
    results = search([0.1] * 1536, qdrant_client, top_k=3)
    assert len(results) >= 1
    assert results[0].payload["text"] == "我们的产品保修期为2年"


def test_ensure_collection_idempotent(qdrant_client):
    """Calling ensure_collection on existing collection should not raise."""
    ensure_collection(qdrant_client, COLLECTION_NAME)
    assert qdrant_client.collection_exists(COLLECTION_NAME)
```

```bash
# Requires Qdrant running on localhost:6333 (for now, run via Docker)
docker run -d -p 6333:6333 qdrant/qdrant
python -m pytest tests/test_retriever.py -v
# Expected: 2 passed
```

---

### Task 5: RAG Guard (quality checks)

**Files:**
- Create: `d:\your-project\tests\test_guard.py`
- Create: `d:\your-project\rag\guard.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_guard.py
import pytest
from rag.guard import check_retrieval, check_reply, UNCERTAINTY_KEYWORDS


class MockScoredPoint:
    def __init__(self, score, text):
        self.score = score
        self.payload = {"text": text}


def test_high_score_direct_reply():
    """Score > 0.7 should return 'generate'."""
    result = check_retrieval([MockScoredPoint(0.85, "产品保修2年")])
    assert result.decision == "generate"
    assert result.confidence == "high"


def test_medium_score_low_confidence():
    """Score 0.4-0.7 should return 'generate' with low confidence."""
    result = check_retrieval([MockScoredPoint(0.55, "产品保修2年")])
    assert result.decision == "generate"
    assert result.confidence == "low"


def test_low_score_escalate():
    """Score < 0.4 should return 'escalate'."""
    result = check_retrieval([MockScoredPoint(0.25, "不太相关的文本")])
    assert result.decision == "escalate"


def test_empty_results_escalate():
    """No results should return 'escalate'."""
    result = check_retrieval([])
    assert result.decision == "escalate"


def test_uncertainty_keyword_detected():
    """Reply containing uncertainty keywords should fail quality check."""
    assert check_reply("我不确定这个是不是对的", []) is False


def test_confident_reply_passes():
    """Reply without uncertainty keywords should pass basic check."""
    assert check_reply("产品保修期为2年，从购买之日起计算。", []) is True
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest tests/test_guard.py -v
# Expected: FAIL - module not found
```

- [ ] **Step 3: Write guard.py**

```python
# rag/guard.py
"""Two-layer quality guard for RAG auto-reply system.

Layer 1: Retrieval relevance check (similarity score thresholds).
Layer 2: Generated reply quality check (uncertainty keywords + entity grounding).
"""

import re
import logging
from dataclasses import dataclass
from qdrant_client.models import ScoredPoint

logger = logging.getLogger(__name__)

UNCERTAINTY_KEYWORDS = [
    "我不确定", "我不太确定", "可能", "也许", "大概", "据我所知",
    "我不是很了解", "建议咨询", "请咨询", "详情请咨询",
    "这个我不清楚", "不好意思", "抱歉我不太清楚",
    "或许可以", "可能需要", "您可以试试",
]

HIGH_CONFIDENCE_THRESHOLD = 0.7
LOW_CONFIDENCE_THRESHOLD = 0.4


@dataclass
class RetrievalResult:
    decision: str       # "generate" | "escalate"
    confidence: str     # "high" | "low" | "none"


def check_retrieval(results: list[ScoredPoint]) -> RetrievalResult:
    """Layer 1: Check if retrieved knowledge is relevant enough.

    Returns RetrievalResult with decision and confidence level.
    """
    if not results:
        return RetrievalResult(decision="escalate", confidence="none")

    top_score = results[0].score

    if top_score > HIGH_CONFIDENCE_THRESHOLD:
        return RetrievalResult(decision="generate", confidence="high")
    elif top_score >= LOW_CONFIDENCE_THRESHOLD:
        return RetrievalResult(decision="generate", confidence="low")
    else:
        return RetrievalResult(decision="escalate", confidence="none")


def check_reply(reply: str, context_chunks: list[str]) -> bool:
    """Layer 2: Check if the generated reply is trustworthy.

    Returns True if reply passes quality check, False if should be escalated.
    """
    # Rule 1: Uncertainty keyword detection
    for keyword in UNCERTAINTY_KEYWORDS:
        if keyword in reply:
            logger.info(f"Reply blocked: contains uncertainty keyword '{keyword}'")
            return False

    # Rule 2: Basic hallucination check - numbers in reply should appear in context
    numbers_in_reply = set(re.findall(r'\d+', reply))
    if numbers_in_reply:
        context_text = " ".join(context_chunks)
        numbers_in_context = set(re.findall(r'\d+', context_text))
        unknown_numbers = numbers_in_reply - numbers_in_context
        if len(unknown_numbers) > 0 and len(unknown_numbers) >= len(numbers_in_reply) * 0.3:
            logger.info(f"Reply blocked: contains ungrounded numbers {unknown_numbers}")
            return False

    return True
```

- [ ] **Step 4: Run test to verify it passes**

```bash
python -m pytest tests/test_guard.py -v
# Expected: 6 passed
```

---

### Task 6: RAG Generator (prompt + DeepSeek)

**Files:**
- Create: `d:\your-project\rag\generator.py`

- [ ] **Step 1: Write generator.py**

```python
# rag/generator.py
"""Generate customer reply using retrieved knowledge and DeepSeek API."""

import logging
from rag.llm_pool import LLMPool

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """你是一个专业的销售客服助手。你的回答必须：

1. **只使用提供的知识片段来回答**，不得编造任何信息
2. 如果知识片段不足以回答客户问题，请回复"需要人工处理"
3. 回复要简洁、专业、友好，像真人销售在跟客户对话
4. 如果客户问的是价格/优惠/折扣等敏感信息，回复"我需要帮您确认，稍后回复您"
5. 不要透露你是AI或机器人
6. 不要使用"根据知识库"、"根据提供的信息"等暴露AI身份的表述

可用的知识片段：
{context}

请根据以上信息回复客户的问题。"""


def build_prompt(question: str, context_chunks: list[str]) -> list[dict]:
    """Build the messages for LLM chat completion."""
    context = "\n\n---\n\n".join(context_chunks)
    return [
        {"role": "system", "content": SYSTEM_PROMPT.format(context=context)},
        {"role": "user", "content": question},
    ]


async def generate_reply(question: str, context_chunks: list[str],
                         llm_pool: LLMPool) -> str:
    """Generate a reply using the LLM pool with knowledge context.

    Args:
        question: The customer's original message.
        context_chunks: Retrieved knowledge text chunks.
        llm_pool: The LLM pool for model selection and fallback.

    Returns:
        Generated reply text.
    """
    messages = build_prompt(question, context_chunks)
    reply = await llm_pool.chat(messages, temperature=0.3, max_tokens=512)
    return reply.strip()
```

---

### Task 6.5: Query Embedding (customer message → vector)

**Files:**
- Create: `d:\your-project\rag\embed_query.py`

- [ ] **Step 1: Write embed_query.py**

```python
# rag/embed_query.py
"""Generate embedding vector for a customer query using DeepSeek Embedding API."""

import os
import logging
from openai import OpenAI

logger = logging.getLogger(__name__)

EMBEDDING_MODEL = "text-embedding-3-small"

_client: OpenAI | None = None


def get_embedding_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(
            api_key=os.environ["DEEPSEEK_API_KEY"],
            base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"),
        )
    return _client


def embed_query(text: str) -> list[float]:
    """Generate embedding vector for a single text query.

    Args:
        text: The customer message to embed.

    Returns:
        List of 1536 floats (embedding vector).
    """
    client = get_embedding_client()
    response = client.embeddings.create(
        model=EMBEDDING_MODEL,
        input=text,
    )
    return response.data[0].embedding
```

- [ ] **Step 2: No separate test needed** — this is a thin wrapper around the DeepSeek API and is exercised by the integration test in Task 18.

---

### Task 7: WeCom API Client (send message, get token)

**Files:**
- Create: `d:\your-project\gateway\wecom_client.py`

- [ ] **Step 1: Write wecom_client.py**

```python
# gateway/wecom_client.py
"""WeCom API client for sending messages and managing access tokens."""

import time
import logging
import httpx

logger = logging.getLogger(__name__)

# Token cache
_token_cache: dict = {"token": "", "expires_at": 0}


async def get_access_token(corp_id: str, secret: str) -> str:
    """Get a valid WeCom API access token (cached)."""
    global _token_cache
    if _token_cache["token"] and time.time() < _token_cache["expires_at"] - 60:
        return _token_cache["token"]

    url = "https://qyapi.weixin.qq.com/cgi-bin/gettoken"
    params = {"corpid": corp_id, "corpsecret": secret}

    async with httpx.AsyncClient() as client:
        resp = await client.get(url, params=params, timeout=10.0)
        resp.raise_for_status()
        data = resp.json()

    if data.get("errcode") != 0:
        raise RuntimeError(f"Failed to get access token: {data}")

    _token_cache["token"] = data["access_token"]
    _token_cache["expires_at"] = time.time() + data["expires_in"]
    return _token_cache["token"]


async def send_text_message(access_token: str, agent_id: str,
                            user_id: str, content: str) -> dict:
    """Send a text message to a WeCom user via the app API.

    Args:
        access_token: Valid WeCom API access token.
        agent_id: The app's AgentID.
        user_id: The external user's ID (from callback message).
        content: Text content to send.

    Returns:
        API response dict with errcode and errmsg.
    """
    url = f"https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token={access_token}"
    payload = {
        "touser": user_id,
        "msgtype": "text",
        "agentid": int(agent_id),
        "text": {"content": content},
    }

    async with httpx.AsyncClient() as client:
        resp = await client.post(url, json=payload, timeout=10.0)
        resp.raise_for_status()
        return resp.json()
```

---

### Task 8: Message Handler (orchestrator)

**Files:**
- Create: `d:\your-project\gateway\handler.py`
- Create: `d:\your-project\tests\test_handler.py`

- [ ] **Step 1: Write handler.py**

```python
# gateway/handler.py
"""Message handling pipeline: receive → retrieve → generate → guard → reply/escalate."""

import json
import logging
import os
from datetime import datetime, timezone
from qdrant_client import QdrantClient

from rag.retriever import search
from rag.guard import check_retrieval, check_reply
from rag.generator import generate_reply
from rag.llm_pool import LLMPool

logger = logging.getLogger(__name__)

PENDING_DIR = os.environ.get("PENDING_DIR", "data/pending")


async def handle_message(
    user_id: str,
    content: str,
    timestamp: str,
    query_vector: list[float],
    qdrant_client: QdrantClient,
    llm_pool: LLMPool,
    collection_name: str = "knowledge_base",
) -> dict:
    """Process an incoming customer message through the full pipeline.

    Returns:
        dict with keys:
        - action: "reply" | "silent_escalate"
        - reply: str (only if action == "reply")
        - reason: str (explanation of decision)
    """
    # Step 1: Retrieve relevant knowledge
    results = search(query_vector, qdrant_client, collection_name, top_k=5)
    retrieved_texts = [r.payload.get("text", "") for r in results]

    # Step 2: Layer 1 — retrieval quality check
    retrieval_check = check_retrieval(results)
    if retrieval_check.decision == "escalate":
        await _escalate(user_id, content, timestamp, retrieved_texts,
                        reason=f"low_retrieval_score_{results[0].score if results else 'none'}")
        return {"action": "silent_escalate", "reason": "low retrieval confidence"}

    # Step 3: Generate reply
    reply = await generate_reply(content, retrieved_texts, llm_pool)

    # Step 4: Layer 2 — reply quality check
    if not check_reply(reply, retrieved_texts):
        await _escalate(user_id, content, timestamp, retrieved_texts,
                        reason="reply_quality_failed", draft_reply=reply)
        return {"action": "silent_escalate", "reason": "reply quality check failed"}

    # Auto-escalate if LLM explicitly asks for human
    if "需要人工处理" in reply or "需要帮您确认" in reply:
        await _escalate(user_id, content, timestamp, retrieved_texts,
                        reason="llm_requested_escalation", draft_reply=reply)
        return {"action": "silent_escalate", "reason": "LLM requested human"}

    return {"action": "reply", "reply": reply}


async def _escalate(user_id: str, content: str, timestamp: str,
                    context_chunks: list[str], reason: str = "",
                    draft_reply: str | None = None) -> None:
    """Silently escalate: write to pending dir, no reply to customer."""
    os.makedirs(PENDING_DIR, exist_ok=True)

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"{PENDING_DIR}/{ts}_{user_id}.json"
    record = {
        "user_id": user_id,
        "customer_message": content,
        "message_timestamp": timestamp,
        "reason": reason,
        "retrieved_context": context_chunks,
        "draft_reply": draft_reply,
        "status": "pending",
        "escalated_at": datetime.now(timezone.utc).isoformat(),
    }
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=2)
    logger.info(f"Escalated to pending: {filename}")
```

- [ ] **Step 2: Write integration test (handler with mock LLM)**

```python
# tests/test_handler.py
import json
import os
import pytest
from unittest.mock import AsyncMock, MagicMock

from gateway.handler import handle_message, _escalate
from rag.llm_pool import LLMPool, ModelConfig
from rag.guard import RetrievalResult


class MockScoredPoint:
    def __init__(self, score, text):
        self.score = score
        self.payload = {"text": text}


@pytest.fixture
def mock_qdrant():
    client = MagicMock()
    client.search.return_value = [
        MockScoredPoint(0.85, "产品保修期为2年，自购买之日起计算"),
    ]
    return client


@pytest.fixture
def mock_qdrant_low_score():
    client = MagicMock()
    client.search.return_value = [
        MockScoredPoint(0.35, "不太相关的内容"),
    ]
    return client


@pytest.mark.asyncio
async def test_handler_reply_high_confidence(mock_qdrant):
    """High retrieval score + good reply → should reply."""
    pool = LLMPool(
        models={"primary": ModelConfig(api_key="sk-test", base_url="http://localhost:9999/v1", model="test")},
        fallback_order=["primary"],
    )
    # Override chat to return a valid reply
    pool.chat = AsyncMock(return_value="产品的保修期是2年，从购买之日开始计算。")

    result = await handle_message(
        user_id="test_user", content="保修多久？", timestamp="123456",
        query_vector=[0.1] * 1536, qdrant_client=mock_qdrant,
        llm_pool=pool,
    )

    assert result["action"] == "reply"
    assert "2年" in result["reply"]


@pytest.mark.asyncio
async def test_handler_escalate_low_retrieval(mock_qdrant_low_score):
    """Low retrieval score → should escalate without calling LLM."""
    pool = LLMPool(
        models={"primary": ModelConfig(api_key="sk-test", base_url="http://localhost:9999/v1", model="test")},
        fallback_order=["primary"],
    )

    result = await handle_message(
        user_id="test_user", content="你们公司上市了吗？", timestamp="123456",
        query_vector=[0.1] * 1536, qdrant_client=mock_qdrant_low_score,
        llm_pool=pool,
    )

    assert result["action"] == "silent_escalate"
```

---

### Task 9: FastAPI Gateway (main entry point)

**Files:**
- Create: `d:\your-project\gateway\main.py`

- [ ] **Step 1: Write main.py**

```python
# gateway/main.py
"""FastAPI Gateway for WeCom callback handling + RAG auto-reply."""

import os
import logging
import xml.etree.ElementTree as ET

from fastapi import FastAPI, Request, HTTPException, Query
from fastapi.responses import PlainTextResponse
from qdrant_client import QdrantClient

from gateway.crypto import verify_signature, decrypt_message, encrypt_message
from gateway.wecom_client import get_access_token, send_text_message
from gateway.handler import handle_message
from rag.retriever import get_client as get_qdrant, COLLECTION_NAME, ensure_collection
from rag.embed_query import embed_query
from rag.llm_pool import LLMPool, ModelConfig

# --- Config ---
logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

CORP_ID = os.environ["WECOM_CORP_ID"]
AGENT_ID = os.environ["WECOM_AGENT_ID"]
SECRET = os.environ["WECOM_SECRET"]
TOKEN = os.environ["WECOM_TOKEN"]
ENCODING_AES_KEY = os.environ["WECOM_ENCODING_AES_KEY"]
QDRANT_URL = os.environ.get("QDRANT_URL", "http://localhost:6333")

# --- LLM Pool Setup ---
def build_llm_pool() -> LLMPool:
    models = {}
    fallback = []

    # Primary: DeepSeek
    if os.environ.get("DEEPSEEK_API_KEY"):
        models["deepseek"] = ModelConfig(
            api_key=os.environ["DEEPSEEK_API_KEY"],
            base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"),
            model="deepseek-chat",
        )
        fallback.append("deepseek")

    # Fallback 1: OpenAI
    if os.environ.get("OPENAI_API_KEY"):
        models["openai"] = ModelConfig(
            api_key=os.environ["OPENAI_API_KEY"],
            base_url=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
            model="gpt-4o-mini",
        )
        fallback.append("openai")

    # Fallback 2: Qwen
    if os.environ.get("QWEN_API_KEY"):
        models["qwen"] = ModelConfig(
            api_key=os.environ["QWEN_API_KEY"],
            base_url=os.environ.get("QWEN_BASE_URL",
                                     "https://dashscope.aliyuncs.com/compatible-mode/v1"),
            model="qwen-turbo",
        )
        fallback.append("qwen")

    return LLMPool(models=models, fallback_order=list(models.keys()) if fallback else ["deepseek"])


# --- App ---
app = FastAPI(title="WeCom Auto Reply", version="0.1.0")
qdrant_client: QdrantClient | None = None
llm_pool: LLMPool | None = None


@app.on_event("startup")
async def startup():
    global qdrant_client, llm_pool
    qdrant_client = get_qdrant(QDRANT_URL)
    ensure_collection(qdrant_client, COLLECTION_NAME)
    llm_pool = build_llm_pool()
    logger.info("Gateway started")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/wecom/callback")
async def verify_url(
    msg_signature: str = Query(..., alias="msg_signature"),
    timestamp: str = Query(...),
    nonce: str = Query(...),
    echostr: str = Query(...),
):
    """WeCom callback URL verification (GET)."""
    if not verify_signature(msg_signature, timestamp, nonce, echostr, TOKEN):
        raise HTTPException(status_code=403, detail="Signature verification failed")

    decrypted, _ = decrypt_message(echostr, ENCODING_AES_KEY)
    return PlainTextResponse(decrypted)


@app.post("/wecom/callback")
async def receive_message(
    request: Request,
    msg_signature: str = Query(..., alias="msg_signature"),
    timestamp: str = Query(...),
    nonce: str = Query(...),
):
    """Receive and process incoming WeCom messages (POST)."""
    body = await request.body()
    body_str = body.decode("utf-8")

    # Parse XML to get Encrypt field
    root = ET.fromstring(body_str)
    encrypt_elem = root.find("Encrypt")
    if encrypt_elem is None or encrypt_elem.text is None:
        raise HTTPException(status_code=400, detail="Missing Encrypt field")

    # Verify signature
    if not verify_signature(msg_signature, timestamp, nonce, encrypt_elem.text, TOKEN):
        raise HTTPException(status_code=403, detail="Signature verification failed")

    # Decrypt
    decrypted_xml, corp_id = decrypt_message(encrypt_elem.text, ENCODING_AES_KEY)
    if corp_id != CORP_ID:
        raise HTTPException(status_code=400, detail="CorpID mismatch")

    # Parse decrypted XML
    msg = ET.fromstring(decrypted_xml)
    msg_type = msg.find("MsgType")
    content_elem = msg.find("Content")
    from_user = msg.find("FromUserName")
    create_time = msg.find("CreateTime")

    if msg_type is None or from_user is None:
        raise HTTPException(status_code=400, detail="Missing required fields")

    msg_type = msg_type.text or "text"
    from_user = from_user.text or ""
    content = content_elem.text if content_elem is not None else ""
    create_time = create_time.text if create_time is not None else ""

    # Only handle text messages
    if msg_type != "text" or not content:
        logger.info(f"Ignoring non-text message type: {msg_type}")
        return PlainTextResponse("")

    # WeCom requires response within 5 seconds
    # For now, process inline. With Redis+worker in production, enqueue and return immediately.
    logger.info(f"Message from {from_user}: {content}")

    # Generate embedding for the customer query
    try:
        query_vector = embed_query(content)
    except Exception as e:
        logger.error(f"Failed to embed query: {e}")
        query_vector = [0.0] * 1536  # fallback, will produce low retrieval scores → escalate

    result = await handle_message(
        user_id=from_user,
        content=content,
        timestamp=create_time,
        query_vector=query_vector,
        qdrant_client=qdrant_client,
        llm_pool=llm_pool,
    )

    if result["action"] == "reply":
        # Send reply via WeCom API
        access_token = await get_access_token(CORP_ID, SECRET)
        send_resp = await send_text_message(access_token, AGENT_ID, from_user, result["reply"])
        if send_resp.get("errcode") != 0:
            logger.error(f"Failed to send reply: {send_resp}")
    else:
        logger.info(f"Silent escalation for {from_user}: {result.get('reason')}")

    # Always return empty 200 (WeCom doesn't use the response body)
    return PlainTextResponse("")
```

---

### Task 10: Embedding helper (DeepSeek Embedding API)

**Files:**
- Create: `d:\your-project\pipeline\embedder.py`

- [ ] **Step 1: Write embedder.py**

```python
# pipeline/embedder.py
"""Generate embeddings via DeepSeek API and upsert to Qdrant."""

import hashlib
import logging
import os
from openai import OpenAI
from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct
from rag.retriever import ensure_collection

logger = logging.getLogger(__name__)

EMBEDDING_MODEL = "text-embedding-3-small"
VECTOR_SIZE = 1536


def get_embedding(text: str, client: OpenAI) -> list[float]:
    """Get embedding vector for a single text."""
    response = client.embeddings.create(
        model=EMBEDDING_MODEL,
        input=text,
    )
    return response.data[0].embedding


def chunk_id(text: str) -> str:
    """Generate a unique ID from chunk text content."""
    return hashlib.md5(text.encode()).hexdigest()[:16]


def embed_and_store(
    chunks: list[str],
    qdrant: QdrantClient,
    collection_name: str = "knowledge_base",
    openai_client: OpenAI | None = None,
    source: str = "unknown",
) -> int:
    """Embed all chunks and upsert into Qdrant. Returns count of points upserted."""
    if openai_client is None:
        openai_client = OpenAI(
            api_key=os.environ["DEEPSEEK_API_KEY"],
            base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"),
        )

    ensure_collection(qdrant, collection_name)

    points = []
    for i, chunk in enumerate(chunks):
        if not chunk.strip():
            continue
        embedding = get_embedding(chunk, openai_client)
        points.append(PointStruct(
            id=chunk_id(chunk),
            vector=embedding,
            payload={"text": chunk, "source": source, "index": i, "chunk_id": chunk_id(chunk)},
        ))
        if (i + 1) % 50 == 0:
            logger.info(f"Embedded {i + 1}/{len(chunks)} chunks")

    if points:
        qdrant.upsert(collection_name=collection_name, points=points)
        logger.info(f"Stored {len(points)} chunks to Qdrant")

    return len(points)
```

---

### Task 11: Chat Importer (parse WeCom export files)

**Files:**
- Create: `d:\your-project\tests\test_chat_importer.py`
- Create: `d:\your-project\pipeline\chat_importer.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_chat_importer.py
import tempfile
import os
from pipeline.chat_importer import parse_chat_file, QAPair


SAMPLE_CHAT = """
2025-12-01 14:23:05 销售-张三
你好，请问有什么可以帮您的？

2025-12-01 14:23:30 客户-李四
请问你们产品的保修期是多久？

2025-12-01 14:24:10 销售-张三
您好李总！我们的产品保修期是2年，从购买之日开始计算。保修期内出现任何质量问题免费维修。

2025-12-01 14:25:00 客户-李四
好的，谢谢！
"""


def test_parse_chat_extracts_qa_pairs():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False,
                                      encoding="utf-8") as f:
        f.write(SAMPLE_CHAT)
        tmp_path = f.name

    try:
        pairs = parse_chat_file(tmp_path)
        assert len(pairs) >= 1
        qa = pairs[0]
        assert "保修期" in qa.question
        assert "2年" in qa.answer
        assert qa.salesperson == "张三"
    finally:
        os.unlink(tmp_path)


def test_parse_chat_filters_noise():
    """Short messages like '好的谢谢' should not generate QA pairs."""
    noise_chat = """
2025-12-01 14:23:05 销售-张三
你好

2025-12-01 14:23:30 客户-李四
你好

2025-12-01 14:24:10 销售-张三
有什么可以帮您？

2025-12-01 14:25:00 客户-李四
好的谢谢
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False,
                                      encoding="utf-8") as f:
        f.write(noise_chat)
        tmp_path = f.name

    try:
        pairs = parse_chat_file(tmp_path)
        assert len(pairs) == 0
    finally:
        os.unlink(tmp_path)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest tests/test_chat_importer.py -v
# Expected: FAIL
```

- [ ] **Step 3: Write chat_importer.py**

```python
# pipeline/chat_importer.py
"""Parse WeCom chat export .txt files into structured Q&A pairs."""

import re
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# WeCom export format: "2025-12-01 14:23:05 发言人名称"
TIMESTAMP_LINE = re.compile(r"^(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})\s+(.+)$")

# Noise patterns that shouldn't trigger a QA pair
NOISE_PATTERNS = [
    r"^(你好|您好|在吗|哈喽|hi|hello)[\s!！。.,，]*$",
    r"^(好的|OK|ok|收到|明白|嗯嗯|哦哦|谢谢|感谢)[\s!！。.,，]*$",
    r"^[\s!！。.,，?!？!！]*$",  # Empty or punctuation-only
    r"^\[图片\]$", r"^\[语音\]$", r"^\[视频\]$", r"^\[文件\]$",
    r"^\{.*\}$",  # JSON/template data
]

MIN_QUESTION_LEN = 4   # Minimum chars for a question
MIN_ANSWER_LEN = 8     # Minimum chars for an answer


@dataclass
class QAPair:
    question: str
    answer: str
    salesperson: str = ""
    date: str = ""
    product: str = ""


def _is_noise(text: str) -> bool:
    text = text.strip()
    for pattern in NOISE_PATTERNS:
        if re.match(pattern, text, re.IGNORECASE):
            return True
    return False


def _extract_name(speaker_raw: str) -> tuple[str, str]:
    """Split '销售-张三' into ('销售', '张三')."""
    parts = speaker_raw.split("-", 1)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()
    return "", speaker_raw.strip()


def parse_chat_file(filepath: str) -> list[QAPair]:
    """Parse a WeCom chat export file into Q&A pairs.

    Heuristic: consecutive messages where one is from '客户' (or '客户-*')
    and the next substantive message is from '销售-*' become a Q&A pair.
    """
    with open(filepath, "r", encoding="utf-8") as f:
        lines = f.readlines()

    # Parse into message list
    messages = []
    current_timestamp = ""
    current_speaker = ""
    current_text: list[str] = []

    for line in lines:
        line = line.strip()
        if not line:
            continue
        match = TIMESTAMP_LINE.match(line)
        if match:
            # Save previous message
            if current_text and current_speaker:
                messages.append({
                    "timestamp": current_timestamp,
                    "speaker": current_speaker,
                    "text": "\n".join(current_text),
                })
            current_timestamp = match.group(1)
            current_speaker = match.group(2)
            current_text = []
        else:
            current_text.append(line)

    # Save last message
    if current_text and current_speaker:
        messages.append({
            "timestamp": current_timestamp,
            "speaker": current_speaker,
            "text": "\n".join(current_text),
        })

    # Extract Q&A pairs: customer question → sales answer
    pairs = []
    i = 0
    while i < len(messages):
        msg = messages[i]
        role, name = _extract_name(msg["speaker"])

        # Look for customer questions
        if role == "客户" and not _is_noise(msg["text"]) and len(msg["text"]) >= MIN_QUESTION_LEN:
            question = msg["text"]
            # Find next substantive sales reply
            for j in range(i + 1, min(i + 5, len(messages))):
                next_msg = messages[j]
                next_role, next_name = _extract_name(next_msg["speaker"])
                if next_role == "销售" and not _is_noise(next_msg["text"]) and len(next_msg["text"]) >= MIN_ANSWER_LEN:
                    pairs.append(QAPair(
                        question=question,
                        answer=next_msg["text"],
                        salesperson=next_name,
                        date=msg["timestamp"][:10],
                    ))
                    i = j  # Skip to the answer
                    break
        i += 1

    logger.info(f"Extracted {len(pairs)} QA pairs from {filepath}")
    return pairs
```

- [ ] **Step 4: Run test to verify it passes**

```bash
python -m pytest tests/test_chat_importer.py -v
# Expected: 2 passed
```

---

### Task 12: Text Chunker

**Files:**
- Create: `d:\your-project\tests\test_chunker.py`
- Create: `d:\your-project\pipeline\chunker.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_chunker.py
from pipeline.chunker import split_text, split_qa_pairs


def test_split_text_basic():
    text = "第一段内容。" * 100 + "\n\n" + "第二段内容。" * 100
    chunks = split_text(text, chunk_size=200, overlap=50)
    assert len(chunks) >= 2
    assert all(len(c) > 0 for c in chunks)


def test_short_text_single_chunk():
    text = "很短的文本"
    chunks = split_text(text, chunk_size=200, overlap=50)
    assert len(chunks) == 1
    assert chunks[0] == text


def test_split_qa_pairs_formats():
    from pipeline.chat_importer import QAPair
    pairs = [
        QAPair(question="保修多久？", answer="产品保修期为2年。", salesperson="张三"),
        QAPair(question="怎么退货？", answer="7天内可以无理由退货。", salesperson="李四"),
    ]
    chunks = split_qa_pairs(pairs)
    assert len(chunks) == 2
    assert "保修" in chunks[0]
    assert "退货" in chunks[1]
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest tests/test_chunker.py -v
# Expected: FAIL
```

- [ ] **Step 3: Write chunker.py**

```python
# pipeline/chunker.py
"""Text chunking for knowledge base preparation.

Uses sentence-boundary-aware splitting with configurable overlap.
"""

import re
import logging

logger = logging.getLogger(__name__)

# Chinese/English sentence boundary patterns
SENTENCE_BOUNDARY = re.compile(r'[。！？!?\n](?=\s*\S)')


def split_text(text: str, chunk_size: int = 500, overlap: int = 100) -> list[str]:
    """Split text into chunks roughly `chunk_size` chars, with `overlap` chars overlap.

    Splits on sentence boundaries when possible.
    """
    if len(text) <= chunk_size:
        return [text]

    sentences = SENTENCE_BOUNDARY.split(text)

    # If the regex didn't find boundaries (single long line), fall back to character chunks
    if len(sentences) <= 1:
        chunks = []
        for i in range(0, len(text), chunk_size - overlap):
            chunk = text[i:i + chunk_size]
            if chunk.strip():
                chunks.append(chunk)
        return chunks

    chunks = []
    current = ""
    for sentence in sentences:
        if len(current) + len(sentence) > chunk_size and current:
            chunks.append(current.strip())
            # Keep overlap from the end of current chunk
            overlap_text = current[-overlap:] if len(current) > overlap else current
            current = overlap_text + sentence
        else:
            current += sentence

    if current.strip():
        chunks.append(current.strip())

    return chunks


def split_qa_pairs(qa_pairs: list, chunk_size: int = 500, overlap: int = 100) -> list[str]:
    """Convert QA pairs to searchable text chunks.

    Each QA pair becomes: "客户问题: {question}\n销售回答: {answer}"
    Combined pairs that exceed chunk_size are split.
    """
    chunks = []
    for qa in qa_pairs:
        text = f"客户问题: {qa.question}\n销售回答: {qa.answer}"
        chunks.append(text)
    return chunks
```

- [ ] **Step 4: Run test to verify it passes**

```bash
python -m pytest tests/test_chunker.py -v
# Expected: 3 passed
```

---

### Task 13: Video Transcriber (Whisper)

**Files:**
- Create: `d:\your-project\pipeline\video_transcribe.py`

- [ ] **Step 1: Write video_transcribe.py**

```python
# pipeline/video_transcribe.py
"""Transcribe training videos to text using OpenAI Whisper (local)."""

import logging
import os

logger = logging.getLogger(__name__)

# Whisper is imported lazily because it's a heavy dependency
_whisper_model = None


def get_model(model_size: str = "medium"):
    """Load Whisper model (lazy, cached)."""
    global _whisper_model
    if _whisper_model is None:
        import whisper
        logger.info(f"Loading Whisper model: {model_size}")
        _whisper_model = whisper.load_model(model_size)
    return _whisper_model


def transcribe_video(video_path: str, model_size: str = "medium",
                     language: str = "zh") -> dict:
    """Transcribe a video file to text.

    Args:
        video_path: Path to .mp4 or other video file.
        model_size: 'tiny', 'small', 'medium', 'large'. Medium recommended for Chinese.
        language: Language code, 'zh' for Chinese.

    Returns:
        dict with keys:
        - text: Full transcribed text.
        - segments: List of {start, end, text} for each segment.
    """
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Video not found: {video_path}")

    model = get_model(model_size)
    logger.info(f"Transcribing: {video_path}")
    result = model.transcribe(video_path, language=language, verbose=False)

    segments = [
        {"start": s["start"], "end": s["end"], "text": s["text"].strip()}
        for s in result["segments"]
    ]

    logger.info(f"Transcribed {len(segments)} segments from {video_path}")
    return {
        "text": result["text"].strip(),
        "segments": segments,
    }


def transcribe_videos_in_directory(video_dir: str, model_size: str = "medium") -> list[dict]:
    """Batch transcribe all videos in a directory.

    Returns list of transcribe results, each with added 'video_file' key.
    """
    results = []
    for filename in sorted(os.listdir(video_dir)):
        if filename.lower().endswith((".mp4", ".avi", ".mov", ".mkv", ".wmv", ".flv", ".webm")):
            filepath = os.path.join(video_dir, filename)
            try:
                result = transcribe_video(filepath, model_size)
                result["video_file"] = filename
                results.append(result)
            except Exception as e:
                logger.error(f"Failed to transcribe {filename}: {e}")
    return results
```

---

### Task 14: Pipeline Runner (CLI)

**Files:**
- Create: `d:\your-project\pipeline\run_pipeline.py`

- [ ] **Step 1: Write run_pipeline.py**

```python
# pipeline/run_pipeline.py
"""CLI entry point for running the knowledge extraction pipeline."""

import argparse
import logging
import os
import sys

from openai import OpenAI
from qdrant_client import QdrantClient

from pipeline.chat_importer import parse_chat_file
from pipeline.video_transcribe import transcribe_videos_in_directory
from pipeline.chunker import split_text, split_qa_pairs
from pipeline.embedder import embed_and_store

logging.basicConfig(level="INFO",
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="Run the knowledge extraction pipeline")
    parser.add_argument("--chat-dir", default="data/chat_raw",
                        help="Directory with WeCom chat export .txt files")
    parser.add_argument("--video-dir", default="data/videos",
                        help="Directory with training videos")
    parser.add_argument("--skip-videos", action="store_true",
                        help="Skip video transcription")
    parser.add_argument("--skip-chat", action="store_true",
                        help="Skip chat import")
    parser.add_argument("--chunk-size", type=int, default=500)
    parser.add_argument("--whisper-model", default="medium",
                        choices=["tiny", "small", "medium", "large"])
    args = parser.parse_args()

    # Setup clients
    qdrant = QdrantClient(url=os.environ.get("QDRANT_URL", "http://localhost:6333"))
    openai_client = OpenAI(
        api_key=os.environ["DEEPSEEK_API_KEY"],
        base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"),
    )

    all_chunks: list[str] = []

    # 1. Import chat records
    if not args.skip_chat and os.path.isdir(args.chat_dir):
        logger.info(f"=== Importing chat records from {args.chat_dir} ===")
        for filename in sorted(os.listdir(args.chat_dir)):
            if filename.endswith(".txt"):
                filepath = os.path.join(args.chat_dir, filename)
                qa_pairs = parse_chat_file(filepath)
                if qa_pairs:
                    chunks = split_qa_pairs(qa_pairs)
                    # Mark source
                    for c in chunks:
                        all_chunks.append(c)
                    logger.info(f"  {filename}: {len(qa_pairs)} QA pairs → {len(chunks)} chunks")

    # 2. Transcribe videos
    if not args.skip_videos and os.path.isdir(args.video_dir):
        logger.info(f"=== Transcribing videos from {args.video_dir} ===")
        results = transcribe_videos_in_directory(args.video_dir, args.whisper_model)
        for r in results:
            chunks = split_text(r["text"], args.chunk_size)
            for c in chunks:
                all_chunks.append(c)
            logger.info(f"  {r['video_file']}: {len(r['segments'])} segments → {len(chunks)} chunks")

    # 3. Embed and store
    if all_chunks:
        logger.info(f"=== Embedding and storing {len(all_chunks)} chunks ===")
        count = embed_and_store(all_chunks, qdrant, openai_client=openai_client)
        logger.info(f"Done! Stored {count} chunks to Qdrant.")
    else:
        logger.warning("No chunks generated. Is data/chat_raw/ or data/videos/ populated?")


if __name__ == "__main__":
    main()
```

---

### Task 15: Docker Compose & Dockerfiles

**Files:**
- Create: `d:\your-project\docker-compose.yml`
- Create: `d:\your-project\gateway\Dockerfile`
- Create: `d:\your-project\pipeline\Dockerfile`

- [ ] **Step 1: Write gateway Dockerfile**

```dockerfile
# gateway/Dockerfile
FROM python:3.11-slim

WORKDIR /app

# System deps for pycryptodome
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libc6-dev && rm -rf /var/lib/apt/lists/*

COPY ../requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY ../gateway/ ./gateway/
COPY ../rag/ ./rag/

ENV PYTHONPATH=/app

CMD ["uvicorn", "gateway.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 2: Write pipeline Dockerfile**

```dockerfile
# pipeline/Dockerfile
FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg gcc libc6-dev && rm -rf /var/lib/apt/lists/*

COPY ../requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY ../pipeline/ ./pipeline/
COPY ../rag/ ./rag/

ENV PYTHONPATH=/app

ENTRYPOINT ["python", "-m", "pipeline.run_pipeline"]
```

- [ ] **Step 3: Write docker-compose.yml**

```yaml
# docker-compose.yml
services:
  gateway:
    build:
      context: .
      dockerfile: gateway/Dockerfile
    ports:
      - "${GATEWAY_PORT:-8000}:8000"
    env_file:
      - .env
    environment:
      - QDRANT_URL=http://qdrant:6333
      - REDIS_URL=redis://redis:6379/0
      - PENDING_DIR=/app/data/pending
    volumes:
      - ./data/pending:/app/data/pending
    depends_on:
      qdrant:
        condition: service_started
      redis:
        condition: service_started
    restart: unless-stopped

  qdrant:
    image: qdrant/qdrant:latest
    ports:
      - "6333:6333"
    volumes:
      - ./data/qdrant:/qdrant/storage
    restart: unless-stopped

  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    restart: unless-stopped

  pipeline:
    build:
      context: .
      dockerfile: pipeline/Dockerfile
    env_file:
      - .env
    environment:
      - QDRANT_URL=http://qdrant:6333
    volumes:
      - ./data/chat_raw:/app/data/chat_raw:ro
      - ./data/videos:/app/data/videos:ro
    profiles:
      - pipeline  # Only runs when explicitly invoked: docker compose --profile pipeline up
    depends_on:
      qdrant:
        condition: service_started
```

---

### Task 16: Dev startup script

**Files:**
- Create: `d:\your-project\scripts\dev.ps1`

- [ ] **Step 1: Write dev.ps1**

```powershell
# scripts/dev.ps1
# One-command dev environment startup for Windows

Write-Host "=== WeCom Auto Reply - Dev Setup ===" -ForegroundColor Cyan

# Check for .env
if (-not (Test-Path ".env")) {
    Write-Host "Creating .env from .env.example. PLEASE EDIT .env with your real keys!" -ForegroundColor Yellow
    Copy-Item ".env.example" ".env"
}

# Start Qdrant and Redis
Write-Host "Starting Qdrant + Redis..." -ForegroundColor Green
docker compose up -d qdrant redis

# Wait for services
Start-Sleep -Seconds 3

# Start gateway in foreground (Ctrl+C to stop)
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "Gateway starting on http://localhost:8000" -ForegroundColor Cyan
Write-Host "If using ngrok, run in another terminal:" -ForegroundColor Cyan
Write-Host "  ngrok http 8000" -ForegroundColor Yellow
Write-Host "============================================" -ForegroundColor Cyan

pip install -r requirements.txt --quiet 2>$null
python -m uvicorn gateway.main:app --host 0.0.0.0 --port 8000 --reload
```

---

### Task 17: README

**Files:**
- Create: `d:\your-project\README.md`

- [ ] **Step 1: Write README.md**

```markdown
# 企业微信智能客服 (WeCom Auto Reply)

从销售聊天记录和培训视频中自动提取知识库，实现企业微信 1对1 私聊客户消息的自动回复。

## 快速开始

### 1. 准备工作

```bash
# 克隆项目
git clone <repo-url>
cd wecom-autoreply

# 复制配置模板，填入你的真实密钥
cp .env.example .env
# 编辑 .env:
#   WECOM_CORP_ID, WECOM_AGENT_ID, WECOM_SECRET, WECOM_TOKEN, WECOM_ENCODING_AES_KEY
#   DEEPSEEK_API_KEY (必填)
#   OPENAI_API_KEY, QWEN_API_KEY (可选，备选模型)
```

### 2. 导入知识库

```bash
# 将聊天记录 .txt 放入 data/chat_raw/
# 将培训视频 .mp4 放入 data/videos/

# 运行知识提取管道（这一步可能需要几分钟到几小时，取决于视频数量）
docker compose --profile pipeline up pipeline
```

### 3. 启动服务

```bash
# 开发环境
.\scripts\dev.ps1

# 或 Docker 部署
docker compose up -d
```

### 4. 配置企业微信回调

```bash
# 启动 ngrok 内网穿透
ngrok http 8000

# 在企业微信管理后台 → 自建应用 → 回调配置
# 填入 ngrok 提供的 HTTPS URL:
#   URL: https://example.ngrok-free.app/wecom/callback
#   Token: 与 .env 中 WECOM_TOKEN 一致
#   EncodingAESKey: 与 .env 中一致
```

## 架构

```
客户消息 → 企微服务器 → POST 回调 → Gateway(FastAPI)
  → Qdrant检索 → 置信度检查 → DeepSeek生成回复
  → 质量检查 → 发送回复 / 静默转人工
```

## 目录结构

```
├── gateway/       # WeCom Gateway (FastAPI)
├── rag/           # RAG Engine (检索/生成/质量检查)
├── pipeline/      # 知识提取管道 (聊天导入/视频转文字/分块/Embedding)
├── tests/         # 测试
├── data/          # 运行时数据
└── scripts/       # 开发脚本
```

## 技术栈

Python 3.11+ / FastAPI / Qdrant / Redis / DeepSeek API / Whisper / Docker
```

---

### Task 18: Integration Verification

**Files:**
- No new files — run the full suite

- [ ] **Step 1: Run all unit tests**

```bash
cd d:\your-project
python -m pytest tests/ -v --ignore=tests/test_retriever.py
# Expected: all pass (test_retriever requires Qdrant running)
```

- [ ] **Step 2: Start Docker services and run integration test**

```bash
docker compose up -d qdrant redis
Start-Sleep -Seconds 5
python -m pytest tests/test_retriever.py -v
# Expected: 2 passed
```

- [ ] **Step 3: Verify gateway starts correctly**

```bash
$env:DEEPSEEK_API_KEY = "sk-test"
$env:WECOM_CORP_ID = "test"
$env:WECOM_AGENT_ID = "1000001"
$env:WECOM_SECRET = "test"
$env:WECOM_TOKEN = "test"
$env:WECOM_ENCODING_AES_KEY = "abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG"
python -m uvicorn gateway.main:app --port 8000 &
Start-Sleep -Seconds 3
Invoke-WebRequest http://localhost:8000/health
# Expected: {"status":"ok"}
```

- [ ] **Step 4: Cleanup**

```bash
docker compose down
```

---

## Verification Checklist

Before considering this implementation complete:

1. **Unit tests pass**: `pytest tests/ -v` (all except retriever if Qdrant not running)
2. **Gateway healthcheck**: `GET /health` returns `{"status":"ok"}`
3. **WeCom callback verification**: GET `/wecom/callback` with valid signature returns decrypted echostr
4. **Docker Compose**: `docker compose up -d` starts all 4 services without errors
5. **Pipeline dry run**: `python -m pytest tests/test_chat_importer.py tests/test_chunker.py` passes
6. **Manual test**: After configuring ngrok + WeCom callback, send a test message and verify reply or escalation
