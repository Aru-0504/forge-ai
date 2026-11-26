# Security Model

ForgeAI ingests untrusted documents and executes generated agents with tools, so every boundary enforces explicit controls:

1. **Upload Limits & Path Sanitization (`src/forgeai/security/upload_guard.py`, `sanitize.py`)**
   - Enforces file extension allowlist, max file size (`FORGEAI_MAX_UPLOAD_MB`), max file count (`FORGEAI_MAX_FILES_PER_RUN`), aggregate batch size, binary magic-header blocking (`MZ`, `ELF`, Mach-O, `#!/`), DOCX zip-bomb & traversal guards, and filename path-traversal stripping.
2. **Prompt-Injection Hygiene (`src/forgeai/core/prompts/templates.py`, `runtime/agent.py`, `judge_stage.py`)**
   - Document text is **always** wrapped as quoted data inside `<document_data source="..." chunk_id="...">...</document_data>` with embedded closing tags escaped and explicit data-only framing.
   - Direct user injection attempts are blocked before tool execution.
   - `evalgen` guarantees an **indirect document prompt-injection** test case (`doc_prompt_injection`) where a document chunk contains `"ignore previous instructions"`, and `judge` verifies the agent ignores it and emits no forbidden payload.
3. **Safe Calculator (`src/forgeai/security/safe_math.py`)**
   - Uses `ast.parse(..., mode="eval")` with strict operator/function whitelists and expression length, AST node, exponent, and magnitude caps. Never calls `eval()` or `exec()`.
4. **HTTP Tool Host Allowlist & SSRF Protection (`src/forgeai/security/url_guard.py`, `tools/builtins.py`)**
   - `http_get` is disabled by default unless `FORGEAI_HTTP_ALLOWED_HOSTS` is explicitly set.
   - Rejects non-HTTP(S) schemes, embedded URL credentials, internal/cloud metadata hostnames (`localhost`, `*.local`, `*.internal`, `metadata.google.internal`), and any direct or DNS-resolved private/loopback/link-local/reserved IP ranges. Disables automatic HTTP redirects.
5. **Secrets & Container Hardening**
   - Zero secrets in the repository; all configuration flows through environment variables (`.env.example` committed, `.env` gitignored).
   - Optional constant-time `X-API-Key` auth on API endpoints; Docker image runs as non-root user `forgeai`.
