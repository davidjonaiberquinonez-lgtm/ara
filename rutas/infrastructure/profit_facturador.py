"""Factura REAL desde Profit ERP (CRISTM25): JOIN `not_ent` <-> `reng_fac` <-> `factura`.

Espejo Python de `scripts/auditar_profit_10_notas.js` (verificado 10/10 contra
192.168.4.20\\profitserver / CRISTM25). Mapeo empírico del esquema:

  - `not_ent.fact_num`  = número de nota de entrega. status '2'/'T' = nota
    TOTALIZADA (facturada); '0' = pendiente; `anulada` = booleano.
  - `reng_fac.num_doc`  = documento que originó el renglón (la nota, con
    tipo_doc 'E'/'N'/''); la columna de factura del renglón (num_fac |
    num_fact | num_fat | fact_num | factura | nro_factura — resuelta por
    INFORMATION_SCHEMA; en CRISTM25 es `fact_num`) guarda el número de la
    factura totalizada.
  - `factura.fact_num`  = número de factura. status 'T'/'0'/'2' = VIGENTE;
    cualquier status con 'P' (Presupuesto/Pendiente) se EXCLUYE; `anulada`
    invalida la factura.

Falla SIEMPRE en modo degradado: ante error de conexión o consulta devuelve
{} y el flujo de la ruta continúa (las tarjetas muestran 'Sin Factura').
"""
import os
import time
import traceback
from typing import Dict, List, Optional

# Resolución dinámica de columnas (igual que el script de auditoría): nunca se
# asumen nombres; se verifican contra INFORMATION_SCHEMA en el arranque.
_CAND_FACTURA = ["num_fac", "num_fact", "num_fat", "fact_num", "factura", "nro_factura"]
_CAND_DOC = ["num_doc", "nro_doc", "documento"]
_CAND_STATUS = ["status", "statu", "estatus", "estado"]
_CAND_ANULADA = ["anulada", "anulado"]

_DRIVER = os.environ.get("PROFIT_DB_DRIVER", "SQL Server")
_HOST = os.environ.get("PROFIT_DB_HOST", "192.168.4.20")
_PORT = os.environ.get("PROFIT_DB_PORT", "1433")
_DB = os.environ.get("PROFIT_DB_NAME", "CRISTM25")
_USER = os.environ.get("PROFIT_DB_USER", "profit")
_PASS = os.environ.get("PROFIT_DB_PASS", "profit")
_TIMEOUT_S = int(os.environ.get("PROFIT_DB_TIMEOUT_S", "8"))


def _es_verdadero(v) -> bool:
    return v in (True, 1) or str(v).strip().upper() == "TRUE"


class ProfitFacturador:
    """Resuelve el número de factura real de cada nota vía SQL Server (Profit).

    Conexión perezosa (solo al primer uso), columnas resueltas una sola vez y
    consultas en lote (IN (...)) para no golpear Profit nota por nota.
    """

    def __init__(self, conn_str: Optional[str] = None, timeout: float = None):
        self._conn_str = conn_str or (
            f"DRIVER={{{_DRIVER}}};SERVER={_HOST},{_PORT};DATABASE={_DB};"
            f"UID={_USER};PWD={_PASS}"
        )
        self._timeout = timeout if timeout is not None else _TIMEOUT_S
        self._conn = None
        self._columnas: Dict[str, List[str]] = {}
        self._col_fac_reng = None
        self._col_doc_reng = None
        self._col_fac_fac = None
        self._col_status_fac = None
        self._col_anulada_fac = None
        self._col_nota_notent = None
        self._col_status_notent = None
        self._col_anulada_notent = None
        self._esquema_listo = False
        self._esquema_error: Optional[str] = None

    # ── Conexión y esquema (perezoso, cacheado) ───────────────────────────────
    def _conectar(self):
        if self._conn is None:
            import pyodbc  # import local: dependencia solo de este adaptador

            # Candado anti-zombi (v4.36): desactiva el pooling del ODBC
            # Driver Manager a nivel de proceso — equivalente Python del
            # ConnectionPooling=0 ya usado en el lado PHP (ConnectionWrapper).
            pyodbc.pooling = False
            self._conn = pyodbc.connect(self._conn_str, timeout=self._timeout)
        return self._conn

    def _columnas_tabla(self, tabla: str) -> List[str]:
        if tabla not in self._columnas:
            cur = self._conectar().cursor()
            self._columnas[tabla] = [
                r[0]
                for r in cur.execute(
                    "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
                    "WHERE TABLE_NAME = ? AND TABLE_SCHEMA = 'dbo' "
                    "ORDER BY ORDINAL_POSITION",
                    tabla,
                ).fetchall()
            ]
        return self._columnas[tabla]

    @staticmethod
    def _resolver(cols: List[str], candidatas: List[str]) -> Optional[str]:
        lower = {c.lower(): c for c in cols}
        for c in candidatas:
            if c.lower() in lower:
                return lower[c.lower()]
        return None

    def _resolver_esquema(self) -> None:
        """Resuelve las columnas clave de reng_fac / factura / not_ent una vez."""
        if self._esquema_listo or self._esquema_error:
            return
        cols_reng = self._columnas_tabla("reng_fac")
        cols_fac = self._columnas_tabla("factura")
        cols_not = self._columnas_tabla("not_ent")
        self._col_fac_reng = self._resolver(cols_reng, _CAND_FACTURA)
        self._col_doc_reng = self._resolver(cols_reng, _CAND_DOC)
        self._col_fac_fac = self._resolver(cols_fac, _CAND_FACTURA)
        self._col_status_fac = self._resolver(cols_fac, _CAND_STATUS)
        self._col_anulada_fac = self._resolver(cols_fac, _CAND_ANULADA)
        self._col_nota_notent = self._resolver(cols_not, _CAND_FACTURA)
        self._col_status_notent = self._resolver(cols_not, _CAND_STATUS)
        self._col_anulada_notent = self._resolver(cols_not, _CAND_ANULADA)
        faltantes = [
            t
            for t in (
                self._col_fac_reng,
                self._col_doc_reng,
                self._col_fac_fac,
                self._col_status_fac,
            )
            if not t
        ]
        if faltantes:
            raise RuntimeError(
                f"Esquema Profit incompleto (reng_fac/factura): faltan columnas."
            )
        self._esquema_listo = True

    @staticmethod
    def _es_factura_valida(status: Optional[str], anulada) -> bool:
        if _es_verdadero(anulada):
            return False
        s = str(status or "").strip().upper()
        if "P" in s:
            return False
        return s in ("T", "0", "2")

    @staticmethod
    def _es_nota_totalizada(status: Optional[str]) -> bool:
        s = str(status or "").strip().upper()
        if "P" in s:
            return False
        return s in ("T", "2")

    def _en_lotes(self, valores: List[str], tam: int = 500):
        for i in range(0, len(valores), tam):
            yield valores[i : i + tam]

    # ── Consultas en lote ─────────────────────────────────────────────────────
    def _notas_totalizadas(self, notas: List[str]) -> set:
        """Conjunto de notas cuyo encabezado not_ent está TOTALIZADO ('T'/'2')."""
        resultado: set = set()
        cur = self._conectar().cursor()
        for lote in self._en_lotes(notas):
            marks = ",".join("?" * len(lote))
            cond_status = (
                f"UPPER(CAST([{self._col_status_notent}] AS NVARCHAR(10))) IN ('T','2')"
                if self._col_status_notent
                else "1=1"
            )
            cond_anulada = (
                f"COALESCE([{self._col_anulada_notent}],0) NOT IN (1)"
                if self._col_anulada_notent
                else "1=1"
            )
            filas = cur.execute(
                f"SELECT [{self._col_nota_notent}] AS nota FROM [not_ent] "
                f"WHERE [{self._col_nota_notent}] IN ({marks}) "
                f"AND {cond_status} AND {cond_anulada}",
                *lote,
            ).fetchall()
            for f in filas:
                resultado.add(str(f.nota).strip())
        return resultado

    def _facturas_de_renglones(self, notas: List[str]) -> Dict[str, List[str]]:
        """{nota: [facturas distintas]} desde reng_fac.num_doc (tipo_doc E/N/'')."""
        cur = self._conectar().cursor()
        mapa: Dict[str, List[str]] = {n: [] for n in notas}
        for lote in self._en_lotes(notas):
            marks = ",".join("?" * len(lote))
            filas = cur.execute(
                f"SELECT [{self._col_doc_reng}] AS nota, [{self._col_fac_reng}] AS factura "
                f"FROM [reng_fac] "
                f"WHERE [{self._col_doc_reng}] IN ({marks}) "
                f"AND UPPER(CAST([tipo_doc] AS NVARCHAR(10))) IN ('E','N','')",
                *lote,
            ).fetchall()
            for f in filas:
                nota = str(f.nota or "").strip()
                factura = str(f.factura or "").strip()
                if nota in mapa and factura and factura not in mapa[nota]:
                    mapa[nota].append(factura)
        return mapa

    def _facturas_validas(self, facturas: List[str]) -> set:
        """Facturas VIGENTES (status T/0/2, no anuladas, sin 'P') en tabla factura."""
        facturas = list(dict.fromkeys(facturas))
        if not facturas:
            return set()
        cur = self._conectar().cursor()
        validas: set = set()
        for lote in self._en_lotes(facturas):
            marks = ",".join("?" * len(lote))
            filas = cur.execute(
                f"SELECT [{self._col_fac_fac}] AS factura, "
                f"UPPER(CAST([{self._col_status_fac}] AS NVARCHAR(10))) AS status, "
                f"{'[' + self._col_anulada_fac + ']' if self._col_anulada_fac else '0'} AS anulada "
                f"FROM [factura] WHERE [{self._col_fac_fac}] IN ({marks})",
                *lote,
            ).fetchall()
            for f in filas:
                if self._es_factura_valida(f.status, f.anulada):
                    validas.add(str(f.factura).strip())
        return validas

    def num_fact_por_notas(self, notas: List[str]) -> Dict[str, List[str]]:
        """Números de factura REALES totalizados por nota (JOIN not_ent/reng_fac/factura).

        Regla de negocio (espejo auditar_profit_10_notas.js):
          1. La nota debe existir en `not_ent` TOTALIZADA ('T'/'2') y no anulada.
          2. Sus renglones (`reng_fac.num_doc` = nota, tipo_doc 'E'/'N'/'') portan
             la factura; si no hay renglones referenciados, fallback directo
             `reng_fac.factura = nota`.
          3. La factura debe estar VIGENTE (status 'T'/'0'/'2', no anulada, sin 'P').
        Nota sin factura totalizada => lista vacía (nunca se iguala factura=nota).
        """
        notas = [str(n or "").strip() for n in notas if str(n or "").strip()]
        if not notas:
            return {}
        t0 = time.perf_counter()
        try:
            self._resolver_esquema()
            totalizadas = self._notas_totalizadas(notas)
            mapa = self._facturas_de_renglones(notas)

            # Fallback directo (espejo script): renglones con columna de factura = nota.
            sin_renglones = [n for n in notas if not mapa[n]]
            if sin_renglones:
                cur = self._conectar().cursor()
                for n in sin_renglones:
                    filas = cur.execute(
                        f"SELECT [{self._col_fac_reng}] AS factura FROM [reng_fac] "
                        f"WHERE [{self._col_fac_reng}] = ?",
                        n,
                    ).fetchall()
                    mapa[n] = list(
                        dict.fromkeys(
                            str(f.factura).strip()
                            for f in filas
                            if f.factura is not None and str(f.factura).strip()
                        )
                    )

            todas_facturas = [f for fs in mapa.values() for f in fs]
            validas = self._facturas_validas(todas_facturas)
            resultado: Dict[str, List[str]] = {}
            for n in notas:
                if n not in totalizadas:
                    resultado[n] = []  # nota pendiente/anulada => sin factura
                    continue
                resultado[n] = [f for f in mapa.get(n, []) if f in validas]
            print(
                f"[ProfitFacturador] {len(notas)} nota(s) consultadas: "
                f"{sum(1 for v in resultado.values() if v)} con factura real "
                f"({time.perf_counter() - t0:.2f}s)"
            )
            return resultado
        finally:
            # Candado anti-zombi (v4.36): no queda ninguna conexión viva
            # esperando a la próxima llamada.
            self.cerrar()

    # ── Sede real por nota/factura (co_sucu) ──────────────────────────────────
    # Campo real y directo confirmado en vivo (v4.33): `co_sucu` vive en
    # `not_ent`, `factura` y `dev_cli` — '01'=SAN CRISTOBAL, '02'=BARQUISIMETO.
    # Verificado contra notas reales: 72150694/72163501/72001032 → '02'
    # (BQTO); 10927 → '01' (SC). Reemplaza cualquier heurística por rango de
    # número (72.000.000): esto es el dato real por documento, no una
    # inferencia.
    _MAPA_SEDE_CO_SUCU = {"01": "SC", "02": "BQTO"}
    _TABLAS_SEDE = ("not_ent", "factura", "dev_cli")

    def sedes_por_notas(self, notas: List[str]) -> Dict[str, str]:
        """Sede real ('SC'|'BQTO') de cada nota/factura, vía `co_sucu` en Profit.

        Busca en lote en `not_ent` (pedidos), `factura` (solo-factura) y
        `dev_cli` (notas de crédito) — la primera tabla donde aparece la nota
        gana. Notas no encontradas en ninguna simplemente no aparecen en el
        dict de retorno (el llamador decide qué hacer con las que faltan).
        Best-effort: NUNCA lanza excepción, degrada a {} si Profit no responde.
        """
        notas = [str(n or "").strip() for n in notas if str(n or "").strip()]
        if not notas:
            return {}
        t0 = time.perf_counter()
        resultado: Dict[str, str] = {}
        try:
            cur = self._conectar().cursor()
            pendientes = list(dict.fromkeys(notas))
            for tabla in self._TABLAS_SEDE:
                if not pendientes:
                    break
                cols = self._columnas_tabla(tabla)
                col_fac = self._resolver(cols, _CAND_FACTURA)
                col_sucu = self._resolver(cols, ["co_sucu"])
                if not col_fac or not col_sucu:
                    continue
                for lote in self._en_lotes(pendientes):
                    marks = ",".join("?" * len(lote))
                    filas = cur.execute(
                        f"SELECT [{col_fac}] AS nota, [{col_sucu}] AS sucu "
                        f"FROM [{tabla}] WHERE [{col_fac}] IN ({marks})",
                        *lote,
                    ).fetchall()
                    for f in filas:
                        nota = str(f.nota).strip()
                        sucu = str(f.sucu or "").strip()
                        sede = self._MAPA_SEDE_CO_SUCU.get(sucu)
                        if sede and nota not in resultado:
                            resultado[nota] = sede
                pendientes = [n for n in pendientes if n not in resultado]
        except Exception as e:
            traceback.print_exc()
            print(f"[ProfitFacturador] sedes_por_notas falló: {e}")
            return resultado
        finally:
            self.cerrar()  # candado anti-zombi (v4.36)
        print(
            f"[ProfitFacturador] sedes_por_notas: {len(resultado)}/{len(notas)} "
            f"notas resueltas por co_sucu ({time.perf_counter() - t0:.2f}s)"
        )
        return resultado

    # ── Peso real por nota (reng_nde × art.peso) ──────────────────────────────
    # Confirmado en vivo (v4.38): `art.peso` (Kg por unidad) tiene datos reales
    # en 10.042/11.425 artículos (~88%). No hay columna de peso en not_ent/
    # factura/reng_nde directamente — se calcula: para cada renglón de la nota
    # en reng_nde, cantidad (stotal_art si > 0, si no total_art) × art.peso
    # del mismo co_art, sumado por fact_num. Verificado con nota real
    # 72110888: 1 renglón, MISC0155, cant=6, peso_u=0.097 → 0.582 Kg.
    def pesos_por_notas(self, notas: List[str]) -> Dict[str, float]:
        """Peso real en Kg por nota, vía reng_nde × art.peso. Best-effort:
        degrada a {} si Profit no responde o reng_nde/art no tienen las
        columnas esperadas. Notas sin renglones o sin peso de artículo
        conocido no aparecen en el resultado (el llamador las trata como
        'sin dato', nunca se inventa un peso)."""
        notas = [str(n or "").strip() for n in notas if str(n or "").strip()]
        if not notas:
            return {}
        t0 = time.perf_counter()
        try:
            cols_reng = self._columnas_tabla("reng_nde")
            col_fk = self._resolver(cols_reng, _CAND_FACTURA)
            col_cant_total = self._resolver(cols_reng, ["total_art"])
            col_cant_surtida = self._resolver(cols_reng, ["stotal_art"])
            col_co_art = self._resolver(cols_reng, ["co_art"])
            if not (col_fk and col_cant_total and col_co_art):
                return {}

            cur = self._conectar().cursor()
            renglones: List[tuple] = []  # (nota, co_art, cantidad)
            for lote in self._en_lotes(notas):
                marks = ",".join("?" * len(lote))
                cols_sql = f"[{col_fk}], [{col_co_art}], [{col_cant_total}]"
                if col_cant_surtida:
                    cols_sql += f", [{col_cant_surtida}]"
                filas = cur.execute(
                    f"SELECT {cols_sql} FROM [reng_nde] WHERE [{col_fk}] IN ({marks})",
                    *lote,
                ).fetchall()
                for f in filas:
                    nota = str(f[0]).strip()
                    co_art = str(f[1] or "").strip()
                    total = float(f[2] or 0)
                    surtida = float(f[3] or 0) if col_cant_surtida else 0.0
                    cantidad = surtida if surtida > 0 else total
                    if co_art and cantidad > 0:
                        renglones.append((nota, co_art, cantidad))

            if not renglones:
                return {}

            # Pesos unitarios en lote (co_art únicos), cacheado por tabla ya
            # cacheada de columnas — nueva consulta batch, no una por artículo.
            co_arts = list(dict.fromkeys(r[1] for r in renglones))
            pesos_unitarios: Dict[str, float] = {}
            for lote in self._en_lotes(co_arts):
                marks = ",".join("?" * len(lote))
                filas = cur.execute(
                    f"SELECT co_art, peso FROM [art] WHERE co_art IN ({marks})", *lote
                ).fetchall()
                for f in filas:
                    pesos_unitarios[str(f.co_art).strip()] = float(f.peso or 0)

            resultado: Dict[str, float] = {}
            for nota, co_art, cantidad in renglones:
                peso_u = pesos_unitarios.get(co_art, 0.0)
                if peso_u <= 0:
                    continue
                resultado[nota] = resultado.get(nota, 0.0) + cantidad * peso_u

            print(
                f"[ProfitFacturador] pesos_por_notas: {len(resultado)}/{len(notas)} "
                f"notas con peso real calculado ({time.perf_counter() - t0:.2f}s)"
            )
            return resultado
        except Exception as e:
            traceback.print_exc()
            print(f"[ProfitFacturador] pesos_por_notas falló: {e}")
            return {}
        finally:
            self.cerrar()  # candado anti-zombi (v4.36)

    # ── Notas de crédito (dev_cli) ─────────────────────────────────────────────
    # Verificado en vivo (v4.25, solo lectura): la tabla real de notas de
    # crédito/devolución en Profit es `dev_cli` — el módulo de rutas antes
    # solo "adivinaba" NOTA_CREDITO por texto ('NC-' en el número/cliente),
    # sin confirmar contra la BD. Columnas útiles: fact_num (número de la NC),
    # nc_num (factura original que afecta), co_cli, descrip (motivo),
    # co_tran (código de transporte/ruta — mismo campo que not_ent/factura),
    # saldo/saldoNCR. `dev_cli.nombre` viene vacío en la BD real: el nombre
    # del cliente se resuelve con JOIN a `clientes.cli_des`.
    def verificar_nota_credito(self, numero: str) -> dict:
        """Verifica si `numero` existe como nota de crédito real en `dev_cli`.

        Retorna {"existe": bool, "fact_num", "nc_num" (factura afectada),
        "co_cli", "cliente" (razón social), "descripcion", "co_tran" (ruta/
        zona), "saldo", "anulada"} o {"existe": False, "error": ...} en modo
        degradado (Profit no disponible / tabla no encontrada). NUNCA lanza
        excepción — mismo criterio best-effort del resto del adaptador.
        """
        numero = str(numero or "").strip()
        if not numero:
            return {"existe": False, "error": "Número de nota de crédito vacío."}
        try:
            cols = self._columnas_tabla("dev_cli")
            if not cols:
                return {"existe": False, "error": "Tabla dev_cli no encontrada en Profit."}
            cur = self._conectar().cursor()
            fila = cur.execute(
                "SELECT d.fact_num, d.nc_num, d.co_cli, d.descrip, d.co_tran, "
                "d.saldo, d.saldoNCR, d.anulada, c.cli_des "
                "FROM dev_cli d LEFT JOIN clientes c ON LTRIM(RTRIM(c.co_cli)) = LTRIM(RTRIM(d.co_cli)) "
                "WHERE LTRIM(RTRIM(d.fact_num)) = ?",
                numero,
            ).fetchone()
        except Exception as e:
            traceback.print_exc()
            return {"existe": False, "error": f"No se pudo consultar dev_cli: {e}"}
        finally:
            self.cerrar()  # candado anti-zombi (v4.36)

        if fila is None:
            return {"existe": False, "fact_num": numero}
        return {
            "existe": True,
            "fact_num": str(fila.fact_num or "").strip(),
            "factura_afectada": str(fila.nc_num or "").strip() or None,
            "co_cli": str(fila.co_cli or "").strip(),
            "cliente": str(fila.cli_des or "").strip() or None,
            "descripcion": str(fila.descrip or "").strip() or None,
            "co_tran": str(fila.co_tran or "").strip() or None,
            "saldo": float(fila.saldo or 0),
            "saldo_nc": float(fila.saldoNCR or 0),
            "anulada": _es_verdadero(fila.anulada),
        }

    def disponible(self) -> bool:
        """True si Profit responde (verificación sin lanzar excepciones)."""
        try:
            self._resolver_esquema()
            return True
        except Exception as e:
            traceback.print_exc()
            print(f"[ProfitFacturador] Profit no disponible: {e}")
            return False
        finally:
            self.cerrar()  # candado anti-zombi (v4.36)

    def cerrar(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None
