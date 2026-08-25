# Motor de inferencia — staged, sin activar

Ollama sigue activo hoy (`http://localhost:11434`, `NvidiaBrainClient.php` +
`chat_routes.py`/`ara_vision.py`). Nada de esto se activa hasta que llegue
la GDX y se decida el swap.

## Por qué vLLM/Triton y no seguir con Ollama en la GDX

Ollama sirve un modelo a la vez, bien para dev en CPU/GPU compartida. La GDX
va a correr chat + auditorías + OCR + notas en paralelo — necesita batching
continuo y throughput real de GPU. vLLM (paged attention, batching continuo,
API compatible OpenAI `/v1/chat/completions`) o NVIDIA Triton (multi-modelo,
multi-framework, mejor para mezclar LLM + modelos de visión OCR en el mismo
server) — decisión final se toma con la GDX en mano y benchmarks reales, no
antes.

## Abstracción: `INFERENCE_ENGINE`

`engine_config.py` resuelve un solo config dict a partir de una env var, para
que el día del swap sea cambiar `INFERENCE_ENGINE=vllm` en `.env`, no
reescribir `NvidiaBrainClient.php` ni `chat_routes.py`. Los 3 motores hablan
protocolo compatible OpenAI Chat Completions (Ollama lo soporta en
`/v1/chat/completions` desde hace rato; vLLM lo expone nativo; Triton necesita
el backend `vllm_backend` o un wrapper OpenAI-compatible encima) — por eso el
cliente PHP/Python no debería necesitar tocarse, solo la URL base y el modelo.

## Migración real (cuando llegue la GDX, NO ahora)

1. Confirmar que `NvidiaBrainClient.php` y `chat_routes.py` usan el endpoint
   `/v1/chat/completions` (compatible OpenAI) en vez de `/api/chat` (nativo
   Ollama) — si ya usan el compatible, el swap es solo cambiar
   `OLLAMA_BASE_URL`/`INFERENCE_ENGINE`; si usan el nativo, hay que adaptar el
   parseo de la respuesta primero.
2. Levantar vLLM o Triton en la GDX, benchmarking real con los modelos que
   hoy corren en Ollama (`OLLAMA_MODEL` en `.env`).
3. Cambiar `INFERENCE_ENGINE` + `*_BASE_URL` en `.env.gb10`, correr
   `validate_3capas.py` + arnés de adaptadores antes de cortar Ollama.
4. Dejar Ollama como fallback local unos días (circuit breaker ya existe en
   `TecnologiaAdapter`/`BaseAdapter` — mismo patrón sirve para el fallback de
   motor de inferencia).
