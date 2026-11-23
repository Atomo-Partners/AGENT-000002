import json
import logging

import azure.functions as func

from cifras import procesar

app = func.FunctionApp(http_auth_level=func.AuthLevel.FUNCTION)


def _respuesta(cuerpo: dict, estado: int = 200) -> func.HttpResponse:
    return func.HttpResponse(json.dumps(cuerpo, ensure_ascii=False), status_code=estado,
                             mimetype="application/json", charset="utf-8")


@app.route(route="preperfil/cifras", methods=["POST"])
def preperfil_cifras(req: func.HttpRequest) -> func.HttpResponse:
    try:
        cuerpo = req.get_json()
    except ValueError:
        return _respuesta({"error": "El cuerpo no es un JSON válido."}, 400)

    if not isinstance(cuerpo, dict) or not isinstance(cuerpo.get("documentos"), list):
        return _respuesta({"error": "Falta 'documentos' (lista)."}, 400)

    try:
        resultado = procesar(cuerpo)
    except ValueError as e:
        return _respuesta({"error": str(e)}, 400)
    except Exception as e:  # noqa: BLE001
        logging.exception("Error procesando cifras")
        return _respuesta({"error": f"Error interno: {e}"}, 500)

    logging.info("Cifras: %s conceptos, %s años de EBITDA, %s alertas",
                 len(resultado["cifras"]), len(resultado["ebitda"]), len(resultado["alertas"]))
    return _respuesta(resultado)
