#!/usr/bin/env node
"use strict";
/**
 * ollamaVisionService.js — Visor Híbrido en Node.js: análisis de imágenes de
 * artículos 100% con Ollama Local (localhost:11434).
 *
 * Elimina la dependencia de NVIDIA NIM: el pipeline completo (visión +
 * estructuración a JSON) se resuelve localmente:
 *
 *   1. VISION   — llava:7b       /api/generate  (prompt comercial + imagen base64)
 *   2. JSON     — qwen2.5-coder:7b  /api/generate  con format:"json" nativo.
 *                Si la respuesta viene vacía o con JSON inválido, reintenta
 *                automáticamente con un prompt simplificado (hasta MAX_REINTENTOS).
 *
 * Contrato de uso (CommonJS):
 *   const { OllamaVisionService } = require("./ollamaVisionService.js");
 *   const svc = new OllamaVisionService();
 *   const res = await svc.analizarImagen("ruta/o/buffer.jpg", { json: true });
 *
 * Logs requeridos por el objetivo:
 *   [NodeVisor] 🖼️ Procesando imagen con llava:7b (Local)...
 *   [NodeVisor] ✅ Imagen analizada con éxito en [X]ms.
 *
 * Sin llamadas salientes a nvidia.com: baseUrl solo apunta a localhost.
 */
const fs = require("fs");
const path = require("path");

const DEFAULT_BASE_URL = "http://localhost:11434";
const DEFAULT_MODELO_VISION = "llava:7b";
const DEFAULT_MODELO_JSON = "qwen2.5-coder:7b";
const DEFAULT_TIMEOUT_MS = 15000; // máximo 15s en local
const MAX_REINTENTOS_JSON = 2;
const MAX_BASE64_TAMANIO = 12 * 1024 * 1024; // 12 MB de imagen (base64 de ~9MB)

// Prompt de análisis comercial (objetivo del módulo).
const PROMPT_ANALISIS =
  "Analiza esta imagen de producto comercial. Identifica: nombre del producto, " +
  "lote/fecha de vencimiento visible, empaque (caja/ampolla/frasco) y condici\u00f3n visual.";

// Prompt SIMPLIFICADO de reintento (misma sem\u00e1ntica, menor exigencia).
const PROMPT_ANALISIS_SIMPLE =
  "Describe el producto de esta imagen en campos cortos: nombre, lote, vencimiento, empaque, condicion.";

// Plantilla para estructurar el texto de visión a JSON con qwen2.5-coder:7b.
const PLANTILLA_JSON = (textoVision) =>
  `Convierte el siguiente texto de visi\u00f3n a JSON plano con las claves ` +
  `nombre, lote, vencimiento, empaque, condicion. Texto: ${textoVision}`;

class ErrorVision extends Error {}

class OllamaVisionService {
  /**
   * @param {object} [opciones]
   * @param {string} [opciones.baseUrl]       Base de Ollama (default localhost:11434).
   * @param {string} [opciones.modeloVision]  Modelo de visión (default llava:7b).
   * @param {string} [opciones.modeloJson]    Modelo de estructuración (default qwen2.5-coder:7b).
   * @param {number} [opciones.timeoutMs]     Timeout por petición (default 15000 ms).
   * @param {function} [opciones.log]         Logger opcional (default console.log).
   */
  constructor(opciones = {}) {
    this.baseUrl = (opciones.baseUrl || DEFAULT_BASE_URL).replace(/\/+$/, "");
    this.modeloVision = opciones.modeloVision || DEFAULT_MODELO_VISION;
    this.modeloJson = opciones.modeloJson || DEFAULT_MODELO_JSON;
    this.timeoutMs = Math.min(
      Math.max(Number(opciones.timeoutMs) || DEFAULT_TIMEOUT_MS, 1000),
      15000
    );
    this.log = typeof opciones.log === "function" ? opciones.log : console.log;
  }

  /**
   * Pipeline completo: imagen -> llava:7b -> (opcional) qwen2.5-coder:7b JSON.
   *
   * @param {string|Buffer} imagen  Ruta al archivo o Buffer con la imagen.
   * @param {object} [opts]
   * @param {boolean} [opts.json]        Estructurar la salida a JSON (default true).
   * @param {boolean} [opts.soloVision]  True = solo llava, sin estructuración.
   * @returns {Promise<object>}
   */
  async analizarImagen(imagen, opts = {}) {
    const base64 = this._aBase64(imagen);
    const t0 = Date.now();

    this.log(`[NodeVisor] \u{1F5BC}\uFE0F Procesando imagen con ${this.modeloVision} (Local)...`);

    const textoVision = await this._generarVision(base64);

    if (opts.soloVision || opts.json === false) {
      return this._responder(base64, textoVision, null, Date.now() - t0, 0);
    }

    const { datos, reintentos } = await this._estructurarJsonConReintento(textoVision);
    return this._responder(base64, textoVision, datos, Date.now() - t0, reintentos);
  }

  /**
   * Envía la imagen a llava:7b (POST /api/generate, stream:false).
   * @returns {Promise<string>} Texto plano del modelo.
   */
  async _generarVision(base64) {
    const payload = {
      model: this.modeloVision,
      prompt: PROMPT_ANALISIS,
      images: [base64],
      stream: false,
    };
    const texto = await this._postGenerate(payload);
    if (!texto || !texto.trim()) {
      throw new ErrorVision(
        `llava:${this.modeloVision} devolvi\u00f3 respuesta vac\u00eda. ` +
          "Verifique que el modelo est\u00e1 instalado: ollama pull llava:7b"
      );
    }
    return texto.trim();
  }

  /**
   * Estructura el texto de visión a JSON con qwen2.5-coder:7b (format:"json").
   * Reintenta con prompt simplificado si el JSON sale vacío/inválido.
   *
   * @returns {Promise<{datos: object|null, reintentos: number}>}
   */
  async _estructurarJsonConReintento(textoVision) {
    const visto = new Set();
    let ultimoError = null;

    for (let intento = 0; intento <= MAX_REINTENTOS_JSON; intento++) {
      const textoJson = await this._generarJson(textoVision, intento);
      const datos = this._parsearJson(textoJson);
      const marca = datos ? JSON.stringify(datos) : textoJson;

      if (datos !== null && !visto.has(marca)) {
        return { datos, reintentos: intento };
      }
      if (datos === null) {
        ultimoError = "JSON vac\u00edo o inv\u00e1lido del modelo de estructuraci\u00f3n";
      }
      visto.add(marca);
    }

    throw new ErrorVision(
      `No se pudo obtener JSON estructurado tras ${MAX_REINTENTOS_JSON + 1} intento(s): ` +
        String(ultimoError || "respuesta inv\u00e1lida")
    );
  }

  /**
   * Pide a qwen2.5-coder:7b convertir el texto de visión a JSON.
   * @param {string} textoVision Texto crudo de llava.
   * @param {number} intento     0 = plantilla normal, >0 = prompt simplificado.
   */
  async _generarJson(textoVision, intento) {
    const prompt = intento === 0 ? PLANTILLA_JSON(textoVision) : PROMPT_ANALISIS_SIMPLE;
    const payload = {
      model: this.modeloJson,
      prompt,
      format: "json", // modo nativo JSON de Ollama
      stream: false,
      options: { temperature: 0.1, num_predict: 256 },
    };
    const texto = await this._postGenerate(payload);
    return (texto || "").trim();
  }

  /**
   * POST a {baseUrl}/api/generate con fetch nativo + AbortController (timeout).
   * @throws {ErrorVision} Fallo de red, timeout o HTTP no-2xx.
   */
  async _postGenerate(payload) {
    const control = new AbortController();
    const timer = setTimeout(() => control.abort(), this.timeoutMs);
    try {
      const resp = await fetch(`${this.baseUrl}/api/generate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
        signal: control.signal,
      });
      if (!resp.ok) {
        throw new ErrorVision(
          `Ollama respondi\u00f3 HTTP ${resp.status} para el modelo ${payload.model}`
        );
      }
      const data = await resp.json();
      return String(data.response ?? "");
    } catch (e) {
      if (e && e.name === "AbortError") {
        throw new ErrorVision(
          `Timeout de ${this.timeoutMs} ms hacia Ollama (${this.baseUrl}).`
        );
      }
      if (e instanceof ErrorVision) throw e;
      throw new ErrorVision(
        `No se pudo conectar con Ollama local (${this.baseUrl}): ${e && e.message ? e.message : e}`
      );
    } finally {
      clearTimeout(timer);
    }
  }

  /**
   * Convierte la entrada (ruta o Buffer) a base64 sin data-URL.
   * @throws {ErrorVision} Entrada inválida o imagen demasiado grande.
   */
  _aBase64(imagen) {
    let buffer;
    if (Buffer.isBuffer(imagen)) {
      buffer = imagen;
    } else if (typeof imagen === "string") {
      buffer = fs.readFileSync(path.resolve(imagen));
    } else {
      throw new ErrorVision("La imagen debe ser una ruta (string) o un Buffer.");
    }
    if (buffer.length > MAX_BASE64_TAMANIO) {
      throw new ErrorVision(
        `Imagen de ${buffer.length} bytes supera el l\u00edmite de ${MAX_BASE64_TAMANIO} bytes.`
      );
    }
    return buffer.toString("base64");
  }

  /**
   * Extrae el primer JSON válido de un texto (soporta ```json ... ``` y {...}).
   * @returns {object|null}
   */
  _parsearJson(texto) {
    if (!texto) return null;
    const conFences = texto.match(/```(?:json)?\s*([\s\S]*?)```/i);
    const candidatos = [];
    if (conFences) candidatos.push(conFences[1].trim());
    const directo = texto.match(/[\[{][\s\S]*[\]]}/);
    if (directo) candidatos.push(directo[0]);
    candidatos.push(texto);

    for (const c of candidatos) {
      if (!c) continue;
      try {
        const obj = JSON.parse(c);
        if (obj !== null && typeof obj === "object") return obj;
      } catch {
        /* siguiente candidato */
      }
    }
    return null;
  }

  /** Normaliza la respuesta final y registra el log de éxito requerido. */
  _responder(base64, textoVision, datos, latenciaMs, reintentos) {
    this.log(`[NodeVisor] \u2705 Imagen analizada con \u00e9xito en ${latenciaMs}ms.`);
    return {
      ok: true,
      motor: "ollama_local",
      modelo_vision: this.modeloVision,
      modelo_json: datos !== null ? this.modeloJson : null,
      texto_vision: textoVision,
      datos,
      reintentos_json: reintentos,
      base64_length: base64.length,
      latencia_ms: latenciaMs,
    };
  }
}

async function main() {
  const args = process.argv.slice(2);
  const svc = new OllamaVisionService({ log: (m) => console.log(m) });
  try {
    const res = await svc.analizarImagen(args[0], { json: true });
    console.log(JSON.stringify(res, null, 2));
  } catch (e) {
    console.error(`[NodeVisor] \u274C ${e.message}`);
    process.exit(1);
  }
}

// Autoejecución solo cuando se invoca directamente:
//   node src/services/ollamaVisionService.js [ruta_imagen]
if (require.main === module) {
  main().catch((e) => {
    console.error(`[NodeVisor] \u274C ${e && e.message ? e.message : e}`);
    process.exit(1);
  });
}

module.exports = { OllamaVisionService, ErrorVision, DEFAULT_BASE_URL };
