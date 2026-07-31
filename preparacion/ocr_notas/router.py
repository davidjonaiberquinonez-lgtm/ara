import traceback
from flask import request, jsonify

from .service import OcrNotasService


def register_ocr_notas_routes(app, service: OcrNotasService):
    @app.route("/api/vision/escanear_nota", methods=["GET", "POST"])
    def vision_escanear_nota():
        if request.method == "GET":
            return jsonify({
                "status": "ok",
                "endpoint": "/api/vision/escanear_nota",
                "metodos": ["GET", "POST"],
                "mensaje": "POST con image (multipart/form-data) o image/image_base64 (JSON) para OCR",
            })

        try:
            if "image" in request.files:
                imagen_bytes = request.files["image"].read()
            elif request.is_json:
                data = request.get_json(silent=True) or {}
                b64 = data.get("image") or data.get("image_base64") or ""
                if not b64:
                    return jsonify({
                        "status": "error",
                        "mensaje": "No se recibió imagen. Envíe 'image' (form-data) o 'image'/'image_base64' (JSON base64)",
                        "fase": "validacion_parametros",
                    }), 400
                try:
                    import base64
                    imagen_bytes = base64.b64decode(b64)
                except Exception as e:
                    return jsonify({
                        "status": "error",
                        "mensaje": f"Base64 inválido: {e}",
                        "fase": "decodificacion_base64",
                    }), 400
            else:
                return jsonify({
                    "status": "error",
                    "mensaje": "Envíe image (form-data) o image/image_base64 (JSON base64)",
                    "fase": "validacion_parametros",
                }), 400

            if len(imagen_bytes) < 100:
                return jsonify({
                    "status": "error",
                    "mensaje": f"Imagen demasiado pequeña ({len(imagen_bytes)} bytes). Envíe una foto válida.",
                    "fase": "validacion_imagen",
                }), 400

            import asyncio
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None

            if loop and loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    resultado = pool.submit(
                        asyncio.run, service.procesar(imagen_bytes)
                    ).result()
            else:
                resultado = asyncio.run(service.procesar(imagen_bytes))

            code = 200 if resultado["status"] == "success" else 207
            return jsonify(resultado), code

        except Exception as e:
            traceback.print_exc()
            return jsonify({
                "status": "error",
                "mensaje": str(e),
                "fase": "error_interno_del_servidor",
            }), 500

    @app.route("/api/ocr/procesar-nota", methods=["POST"])
    def ocr_procesar_nota():
        return vision_escanear_nota()
