import os
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_openai import ChatOpenAI


def get_llm_with_tools(llm_tools: list[dict]):
    """
    Build a tool-bound LLM based on LLM_PROVIDER ('gemini', 'openai', or 'auto').
    - 'gemini': Uses Gemini 3.8 Flash with Gemini 3.8 Pro fallback.
    - 'openai': Uses OpenAI gpt-4o-mini with gpt-4o fallback.
    - 'auto'  : Uses Gemini first and automatically falls back to OpenAI on 429/503 errors.
    """
    provider = os.getenv("LLM_PROVIDER", "gemini").strip().lower()
    google_key = os.getenv("GOOGLE_API_KEY", "").strip()
    openai_key = os.getenv("OPENAI_API_KEY", "").strip()

    if provider == "openai":
        if not openai_key:
            raise RuntimeError("LLM_PROVIDER=openai is set, but OPENAI_API_KEY is missing!")
        primary = ChatOpenAI(model="gpt-4o-mini", temperature=0, timeout=120, max_retries=2).bind_tools(llm_tools)
        fallback = ChatOpenAI(model="gpt-4o", temperature=0, timeout=120, max_retries=2).bind_tools(llm_tools)
        print("🧠 Active LLM Provider: OpenAI (gpt-4o-mini -> gpt-4o)")
        return primary.with_fallbacks([fallback])

    if provider == "auto":
        if not google_key or not openai_key:
            raise RuntimeError("LLM_PROVIDER=auto requires both GOOGLE_API_KEY and OPENAI_API_KEY!")
        gemini_llm = ChatGoogleGenerativeAI(
            model="gemini-3.8-flash", temperature=0, timeout=120, max_retries=1
        ).bind_tools(llm_tools)
        openai_llm = ChatOpenAI(
            model="gpt-4o-mini", temperature=0, timeout=120, max_retries=2
        ).bind_tools(llm_tools)
        print("🧠 Active LLM Provider: Auto (Gemini 3.8 Flash -> OpenAI gpt-4o-mini fallback)")
        return gemini_llm.with_fallbacks([openai_llm])

    # Default: 'gemini'
    if not google_key:
        raise RuntimeError("LLM_PROVIDER=gemini is set, but GOOGLE_API_KEY is missing!")
    primary = ChatGoogleGenerativeAI(
        model="gemini-3.8-flash", temperature=0, timeout=120, max_retries=2
    ).bind_tools(llm_tools)
    fallback = ChatGoogleGenerativeAI(
        model="gemini-3.8-pro", temperature=0, timeout=120, max_retries=2
    ).bind_tools(llm_tools)
    print("🧠 Active LLM Provider: Gemini (gemini-3.8-flash -> gemini-3.8-pro)")
    return primary.with_fallbacks([fallback])

