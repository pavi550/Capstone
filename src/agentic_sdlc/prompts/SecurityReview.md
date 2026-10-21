# SecurityReview (7a)

Look for security holes, not functional bugs:

- **SAST**: injection (SQL, shell, template), `eval`/`exec`, unsafe deserialisation,
  weak crypto, disabled TLS verification, path traversal.
- **Secrets**: hard-coded passwords, API keys, tokens, private keys.
- **Dependencies / CVE**: third-party versions with known vulnerabilities.
- **OWASP Top 10** and **OWASP Top 10 for LLM Applications** (prompt injection,
  excessive agency, insecure output handling).
