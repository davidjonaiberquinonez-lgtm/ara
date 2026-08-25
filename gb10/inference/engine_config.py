"""Resuelve config del motor de inferencia (Ollama/vLLM/Triton) desde env.

STAGED — no importado por ara_server.py todavía, salvo el chequeo de
alcanzabilidad en /api/health (ara_server.py::_estado_gb10), que es solo
lectura/diagnóstico y no dispara ninguna inferencia real. Cuando llegue la
GDX y se decida el swap, importar get_inference_client()/MODEL_ROUTES desde
chat_routes.py/ara_vision.py en vez de usar OLLAMA_BASE_URL hardcodeado.

CERO descargas de pesos/binarios en este archivo — solo strings de config.
"""
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class MotorInferencia:
    nombre: str
    base_url: str
    modelo: str
    endpoint_chat: str  # compatible OpenAI en los 3 motores
    timeout_s: int


def resolver_motor() -> MotorInferencia:
    engine = os.getenv("INFERENCE_ENGINE", "ollama").strip().lower()

    if engine == "vllm":
        return MotorInferencia(
            nombre="vllm",
            base_url=os.getenv("VLLM_BASE_URL", "http://localhost:8000"),
            modelo=os.getenv("VLLM_MODEL", os.getenv("OLLAMA_MODEL", "")),
            endpoint_chat="/v1/chat/completions",
            timeout_s=int(os.getenv("VLLM_TIMEOUT_S", "30")),
        )

    if engine == "triton":
        return MotorInferencia(
            nombre="triton",
            base_url=os.getenv("TRITON_BASE_URL", "http://localhost:8080"),
            modelo=os.getenv("TRITON_MODEL", os.getenv("OLLAMA_MODEL", "")),
            endpoint_chat="/v1/chat/completions",  # requiere backend OpenAI-compatible sobre Triton
            timeout_s=int(os.getenv("TRITON_TIMEOUT_S", "30")),
        )

    # default: Ollama (activo hoy, sin cambios)
    return MotorInferencia(
        nombre="ollama",
        base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
        modelo=os.getenv("OLLAMA_MODEL", ""),
        endpoint_chat="/v1/chat/completions",
        timeout_s=int(os.getenv("OLLAMA_TIMEOUT_S", "30")),
    )


# ── Router por capacidad (3 motores especializados, GB10 target) ───────────
# Cada tarea vive en su propio puerto/proceso vLLM en la GB10. Los defaults
# de puerto (8001/8002/8003) son solo placeholders de planificación — se
# activan de verdad recién cuando la GB10 esté en sitio y estos servicios
# vLLM corran ahí; hasta entonces MODEL_ROUTES no se usa desde ningún flujo
# de inferencia real, solo desde el chequeo de alcanzabilidad de /api/health.
MODEL_ROUTES = {
    "VISION": {
        "model_name": "Qwen/Qwen2.5-VL-72B-Instruct",
        "endpoint": os.getenv("VISION_INFERENCE_URL", "http://127.0.0.1:8001/v1"),
        "uso": "Lectura visual de comprobantes de retención, facturas, tablas y documentos.",
    },
    "REASONING": {
        "model_name": "deepseek-ai/DeepSeek-R1-Distill-Qwen-32B",
        "endpoint": os.getenv("REASONING_INFERENCE_URL", "http://127.0.0.1:8002/v1"),
        "uso": "Orquestador ARA, razonamiento paso a paso, código (Python/SQL Server), RAG interno.",
    },
    "AUDIO": {
        "model_name": "openai/whisper-large-v3",
        "endpoint": os.getenv("AUDIO_INFERENCE_URL", "http://127.0.0.1:8003/v1"),
        "uso": "Transcripción local de notas de voz y auditorías de audio.",
    },
}


@dataclass(frozen=True)
class ClienteInferencia:
    task_type: str
    modelo: str
    base_url: str          # ej. http://127.0.0.1:8001/v1
    endpoint_chat: str      # base_url + /chat/completions, listo para requests.post
    timeout_s: int


def get_inference_client(task_type: str) -> ClienteInferencia:
    """Devuelve la config (endpoint + modelo) para la tarea pedida.

    No abre ninguna conexión ni instancia un SDK — el proyecto ya llama a
    endpoints OpenAI-compatible directo con `requests` (mismo patrón que
    NVIDIA NIM en LeerVoucherOcrTool.php), así que esto solo resuelve strings
    de config, igual que resolver_motor().

    Lanza ValueError si task_type no es VISION/REASONING/AUDIO — fail-fast
    en vez de apuntar a un endpoint inventado.
    """
    clave = task_type.strip().upper()
    if clave not in MODEL_ROUTES:
        raise ValueError(
            f"task_type '{task_type}' no reconocido. Válidos: {', '.join(MODEL_ROUTES)}"
        )
    ruta = MODEL_ROUTES[clave]
    base_url = ruta["endpoint"].rstrip("/")
    timeout_env = f"{clave}_INFERENCE_TIMEOUT_S"
    return ClienteInferencia(
        task_type=clave,
        modelo=ruta["model_name"],
        base_url=base_url,
        endpoint_chat=f"{base_url}/chat/completions",
        timeout_s=int(os.getenv(timeout_env, "30")),
    )


if __name__ == "__main__":
    m = resolver_motor()
    print(f"Motor (legacy, único): {m.nombre} | {m.base_url}{m.endpoint_chat} | modelo={m.modelo or '(no configurado)'}")
    print()
    for tarea in MODEL_ROUTES:
        c = get_inference_client(tarea)
        print(f"{tarea:<10} modelo={c.modelo} | {c.endpoint_chat}")
