"""Validaciones contables definidas en el catálogo. Una regla solo se evalúa en un año
si todos sus términos tienen valor ese año; si falta alguno, queda como 'no_evaluable'.
Los términos marcados como 'opcionales' en la regla cuentan como cero cuando el reporte no los trae."""

from collections import OrderedDict


def _anios(valores):
    anios = set()
    for serie in valores.values():
        anios.update(serie.keys())
    return sorted(anios)


def _resultado(esperado, calculado, tolerancia):
    diferencia = round(calculado - esperado, 6)
    estado = "ok" if abs(diferencia) <= tolerancia else "falla"
    return OrderedDict([("estado", estado), ("esperado", esperado), ("calculado", round(calculado, 6)),
                        ("diferencia", diferencia)])


def validar(catalogo, valores, tasas, resumen):
    config = catalogo.tolerancias
    tolerancia = config["tolerancia"]
    anios = _anios(valores)
    salida = []

    for regla in config["reglas"]:
        tipo = regla.get("tipo", "suma")
        resultados = OrderedDict()

        if tipo == "suma":
            opcionales = set(regla.get("opcionales") or [])
            for anio in anios:
                esperado = valores.get(regla["resultado"], {}).get(anio)
                # Un término opcional que el reporte no trae cuenta como cero.
                terminos = [valores.get(t, {}).get(anio) for t in regla["suma"]]
                terminos = [0.0 if v is None and t in opcionales else v for t, v in zip(regla["suma"], terminos)]
                if esperado is None or any(t is None for t in terminos):
                    resultados[anio] = {"estado": "no_evaluable"}
                else:
                    resultados[anio] = _resultado(esperado, sum(terminos), tolerancia)
            severidad = "error"

        elif tipo == "cruce_tasas":
            for anio in anios:
                esperado = valores.get(regla["resultado"], {}).get(anio)
                termino = valores.get(regla["termino"], {}).get(anio)
                t_res = tasas.get(anio, {}).get("tasa_flujo") or tasas.get(anio, {}).get("tasa_resultados")
                t_bal = tasas.get(anio, {}).get("tasa_balance")
                if None in (esperado, termino, t_res, t_bal) or not t_bal:
                    resultados[anio] = {"estado": "no_evaluable"}
                else:
                    resultados[anio] = _resultado(esperado, termino * t_res / t_bal, tolerancia)
            severidad = "advertencia"

        elif tipo == "resumen":
            for id_, fila in resumen.items():
                if fila["unidad"] == "texto":
                    continue
                tol = regla["tolerancia_fraccion"] if fila["unidad"] == "fraccion" else regla["tolerancia_usd_miles"]
                for anio, valor_resumen in fila["valores"].items():
                    valor_estado = valores.get(id_, {}).get(anio)
                    llave = f"{id_}:{anio}"
                    if valor_resumen is None or valor_estado is None:
                        resultados[llave] = {"estado": "no_evaluable"}
                    else:
                        resultados[llave] = _resultado(valor_estado, valor_resumen, tol)
            severidad = "advertencia"
        else:
            continue

        fallas = [k for k, r in resultados.items() if r["estado"] == "falla"]
        evaluados = [k for k, r in resultados.items() if r["estado"] != "no_evaluable"]
        salida.append(OrderedDict([
            ("id", regla["id"]),
            ("descripcion", regla["descripcion"]),
            ("severidad", severidad),
            ("evaluados", len(evaluados)),
            ("fallas", fallas),
            ("resultados", resultados),
        ]))
    return salida
