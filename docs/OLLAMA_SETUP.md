# Local Ollama evidence review

ScholarRadar uses Ollama only to interpret one isolated roster card or faculty
profile. Deterministic checks validate every quoted fact before canonical data is
created. Monthly directory refresh jobs use the same path and cache unchanged
content by its SHA-256 hash.

Local development enables Ollama automatically after `ollama ps` shows a usable
model. These optional `.env` settings make that configuration explicit:

```dotenv
OLLAMA_ENABLED=true
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen2.5-coder:7b
OLLAMA_TIMEOUT_SECONDS=300
OLLAMA_COOLDOWN_SECONDS=900
```

Use `OLLAMA_MAX_CONCURRENCY=1` operationally: the roster worker currently
processes one model request at a time inside each directory job. If one request
fails, the database-backed circuit breaker prevents subsequent directory jobs
from repeatedly waiting for the unavailable model during the cooldown.

Apply the additive cache table before enabling the feature:

```bash
make schema
```

The request disables reasoning output, limits context to 4096 tokens, caps the
JSON response, and keeps the model warm for ten minutes. Then restart
`make start`. A process started before these settings or code changes will not
load them. Set `OLLAMA_ENABLED=false` whenever the local server is unavailable.
