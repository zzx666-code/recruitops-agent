"""Approved model providers and their wire protocols."""
import re
from urllib.parse import urlsplit

DEEPSEEK_BASE = "https://api.deepseek.com"
DEEPSEEK_MODELS = ("deepseek-flash", "deepseek-v4-pro")
DEEPSEEK_MESSAGES = DEEPSEEK_BASE + "/anthropic/v1/messages"
DEEPSEEK_RESPONSES = DEEPSEEK_BASE + "/responses"
ZHIPU_BASE = "https://open.bigmodel.cn/api/paas/v4"
ZHIPU_CODING_BASE = "https://open.bigmodel.cn/api/coding/paas/v4"
ZAI_CODING_BASE = "https://api.z.ai/api/coding/paas/v4"
MODEL_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$")
PROVIDER_BASES = {
    "deepseek": (DEEPSEEK_BASE,),
    "zhipu": (ZHIPU_BASE,),
    "zhipu_coding": (ZHIPU_CODING_BASE, ZAI_CODING_BASE),
}
PROVIDER_STYLES = {"deepseek": "anthropic", "zhipu": "openai",
                   "zhipu_coding": "openai"}


def official_base(value: str) -> str:
    parsed = urlsplit(value.strip().rstrip("/"))
    if (parsed.scheme != "https" or parsed.hostname != "api.deepseek.com"
            or parsed.port not in (None, 443)
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path not in ("", "/v1")):
        raise ValueError("Only the official DeepSeek API is supported")
    return DEEPSEEK_BASE


def official_model(value: str) -> str:
    if value not in DEEPSEEK_MODELS:
        raise ValueError("Select deepseek-flash or deepseek-v4-pro")
    return value


def official_endpoint(value: str) -> str:
    parsed = urlsplit(value.strip().rstrip("/"))
    official_base(f"{parsed.scheme}://{parsed.netloc}")
    if (parsed.query or parsed.fragment or parsed.path not in {
            "/anthropic/v1/messages", "/chat/completions", "/responses",
            "/v1/chat/completions", "/v1/responses"}):
        raise ValueError("Unsupported DeepSeek endpoint")
    return value.strip().rstrip("/")


def validate_connection(provider: str, api_style: str, base_url: str, model: str) -> str:
    """Return a canonical approved base without allowing credential-bearing URLs."""
    if PROVIDER_STYLES.get(provider) != api_style:
        raise ValueError("Unsupported model provider or protocol")
    parsed = urlsplit(base_url.strip().rstrip("/"))
    if (parsed.scheme != "https" or parsed.username or parsed.password or parsed.query
            or parsed.fragment or parsed.port not in (None, 443)):
        raise ValueError("Use an approved HTTPS model API address")
    canonical = f"https://{parsed.hostname}{parsed.path}" if parsed.hostname else ""
    if provider == "deepseek":
        canonical = official_base(base_url)
        official_model(model)
    elif canonical not in PROVIDER_BASES[provider] or not MODEL_NAME_RE.fullmatch(model):
        raise ValueError("Unsupported model API address or model name")
    return canonical


def completion_endpoint(provider: str, base_url: str) -> str:
    if provider == "deepseek":
        return official_base(base_url) + "/anthropic/v1/messages"
    validate_connection(provider, "openai", base_url, "glm-4.7")
    return base_url.rstrip("/") + "/chat/completions"


def codex_adapter_base(port: int) -> str:
    if not 1 <= port <= 65535:
        raise ValueError("Invalid local model adapter port")
    return f"http://127.0.0.1:{port}/api/codex-model"
