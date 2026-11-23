"""Azure Function (modelo de programación v2 de Python).

POST /api/estandarizar-emis
Cuerpo: {"archivo_origen": "...", "fecha_extraccion": "...", "modelo": "prebuilt-layout", "tablas": [...]}
        Opcional: "parrafos": [...] para llenar NAICS, descripción, información básica y ejecutivos.
Respuesta: el JSON estandarizado por secciones (200), o {"error": "..."} (400 o 500).
"""

import json
import logging

import azure.functions as func

from emis_estandarizador import Catalogo, ErrorEntrada, Estandarizador

app = func.FunctionApp(http_auth_level=func.AuthLevel.FUNCTION)

# El catálogo se carga una sola vez por instancia, no en cada llamada.
_CATALOGO = Catalogo.cargar()


def _respuesta(cuerpo, estado):
    return func.HttpResponse(
        json.dumps(cuerpo, ensure_ascii=False),
        status_code=estado,
        mimetype="application/json",
        charset="utf-8",
    )


@app.route(route="estandarizar-emis", methods=["POST"])
def estandarizar_emis(req: func.HttpRequest) -> func.HttpResponse:
    try:
        entrada = req.get_json()
        # Logic Apps a veces envía el JSON como texto; se acepta igual.
        if isinstance(entrada, str):
            entrada = json.loads(entrada)
    except ValueError:
        return _respuesta({"error": "El cuerpo de la solicitud no es un JSON válido."}, 400)

    try:
        salida = Estandarizador(_CATALOGO).procesar(entrada)
    except ErrorEntrada as error:
        return _respuesta({"error": str(error)}, 400)
    except Exception:  # noqa: BLE001 - cualquier otra falla se registra y responde 500
        logging.exception("Error inesperado al estandarizar el reporte EMIS")
        return _respuesta({"error": "Error interno al estandarizar el reporte."}, 500)

    calidad = salida["calidad"]
    logging.info(
        "EMIS estandarizado: archivo=%s idioma=%s estado=%s advertencias=%d",
        salida["metadata"].get("archivo_origen"),
        salida["metadata"].get("idioma_reporte"),
        calidad["estado"],
        len(calidad["advertencias"]),
    )
    return _respuesta(salida, 200)
