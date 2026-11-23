"""Function 3 de AGENT-000002 · Fase 2.

POST /api/calcular_kpis     Pasos 22 a 24: KPIs por año y valor de la serie del catálogo.
POST /api/armar_scorecard   Pasos 25 y 26: banda y bandera de cada KPI contra el scorecard.
"""
import json
import logging

import azure.functions as func

from kpi_engine import calcular_todo
from scorecard import armar_scorecard as _armar_scorecard

app = func.FunctionApp(http_auth_level=func.AuthLevel.FUNCTION)


def _respuesta(cuerpo, codigo):
    return func.HttpResponse(json.dumps(cuerpo, ensure_ascii=False), status_code=codigo,
                             mimetype="application/json", charset="utf-8")


def _leer(req):
    entrada = req.get_json()
    if isinstance(entrada, str):  # Logic Apps a veces manda el JSON como texto
        entrada = json.loads(entrada)
    return entrada


@app.route(route="calcular_kpis", methods=["POST"])
def calcular_kpis(req: func.HttpRequest) -> func.HttpResponse:
    try:
        payload = _leer(req)
    except ValueError:
        return _respuesta({"error": "El body no es un JSON válido"}, 400)
    if not isinstance(payload, dict) or "cifras" not in payload or "kpis" not in payload:
        return _respuesta({"error": "El body debe traer al menos 'kpis' y 'cifras'"}, 400)
    try:
        resultado = calcular_todo(payload)
    except ValueError as e:
        return _respuesta({"error": str(e)}, 422)
    except Exception:
        logging.exception("Error inesperado calculando KPIs")
        return _respuesta({"error": "Error inesperado calculando KPIs; revisa los logs de la Function"}, 500)
    return _respuesta(resultado, 200)


@app.route(route="armar_scorecard", methods=["POST"])
def armar_scorecard(req: func.HttpRequest) -> func.HttpResponse:
    try:
        payload = _leer(req)
    except ValueError:
        return _respuesta({"error": "El body no es un JSON válido"}, 400)
    if not isinstance(payload, dict) or "kpis" not in payload or "filas_scorecard" not in payload:
        return _respuesta({"error": "El body debe traer al menos 'kpis' y 'filas_scorecard'"}, 400)
    try:
        resultado = _armar_scorecard(payload)
    except Exception:
        logging.exception("Error inesperado armando el scorecard")
        return _respuesta({"error": "Error inesperado armando el scorecard; revisa los logs de la Function"}, 500)
    return _respuesta(resultado, 200)
