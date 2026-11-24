"""Function 3 de AGENT-000002 · Fase 2.

POST /api/preparar_analisis  Industria y métrica ancla desde el Sector priorizado, bloqueo de datos grave,
                             catálogo y anclas para los agentes. Corre antes del investigador.
POST /api/calcular_kpis      KPIs por año y valor de la serie del catálogo, en USD.
POST /api/armar_scorecard    Banda y bandera de cada KPI contra los cortes del scorecard.

La configuración (catálogo, cortes, fórmulas, IPC por país, tabla de sectores) vive en config/ y la
genera la skill atomo-config-fase2. Ver README.md.
"""
import json
import logging

import azure.functions as func

import configuracion
from kpi_engine import calcular_todo
from preparar import preparar
from scorecard import armar_scorecard as _armar_scorecard

app = func.FunctionApp(http_auth_level=func.AuthLevel.FUNCTION)


def _respuesta(cuerpo, codigo):
    # allow_nan=False: un NaN o infinito rompería el Parse JSON del Logic App; mejor un 500 claro.
    return func.HttpResponse(json.dumps(cuerpo, ensure_ascii=False, allow_nan=False), status_code=codigo,
                             mimetype="application/json", charset="utf-8")


def _leer(req):
    entrada = req.get_json()
    if isinstance(entrada, str):  # Logic Apps a veces manda el JSON como texto
        entrada = json.loads(entrada)
    return entrada


def _ejecutar(req, requeridos, funcion, nombre):
    try:
        payload = _leer(req)
    except ValueError:
        return _respuesta({"error": "El body no es un JSON válido"}, 400)
    if not isinstance(payload, dict) or any(c not in payload for c in requeridos):
        return _respuesta({"error": f"El body debe traer al menos {requeridos}"}, 400)
    try:
        resultado = funcion(payload, configuracion.cargar())
    except ValueError as e:
        return _respuesta({"error": str(e)}, 422)
    except Exception:
        logging.exception("Error inesperado en %s", nombre)
        return _respuesta({"error": f"Error inesperado en {nombre}; revisa los logs de la Function"}, 500)
    return _respuesta(resultado, 200)


@app.route(route="preparar_analisis", methods=["POST"])
def preparar_analisis(req: func.HttpRequest) -> func.HttpResponse:
    return _ejecutar(req, ["cifras"], preparar, "preparar_analisis")


@app.route(route="calcular_kpis", methods=["POST"])
def calcular_kpis(req: func.HttpRequest) -> func.HttpResponse:
    return _ejecutar(req, ["kpis", "cifras"], calcular_todo, "calcular_kpis")


@app.route(route="armar_scorecard", methods=["POST"])
def armar_scorecard(req: func.HttpRequest) -> func.HttpResponse:
    return _ejecutar(req, ["kpis", "industria"], _armar_scorecard, "armar_scorecard")
