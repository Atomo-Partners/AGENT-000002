# Function: estandarizar-emis

Recibe el JSON que entrega Azure AI Document Intelligence (`prebuilt-layout`) para un reporte EMIS, en inglés o en español. Devuelve toda la información del reporte organizada por secciones, con los valores como números y un bloque de calidad. El catálogo `catalogo/catalogo_emis.json` define qué cuentas existen, en qué sección va cada una y qué falta.

## Estructura

```
func-emis/
├── function_app.py              # Endpoint HTTP POST /api/estandarizar-emis
├── host.json                    # Configuración del host de Azure Functions
├── requirements.txt             # Dependencias que se instalan en Azure
├── requirements-dev.txt         # Dependencias para correr las pruebas
├── local.settings.json          # Configuración para correr en tu equipo (no se publica)
├── .funcignore                  # Lo que no se sube a Azure
├── catalogo/
│   └── catalogo_emis.json       # Plantilla EMIS: cuentas, secciones, etiquetas en/es y validaciones
├── emis_estandarizador/
│   ├── catalogo.py              # Carga el catálogo y arma los índices por etiqueta
│   ├── estandarizador.py        # Lógica principal: tablas → secciones
│   ├── validaciones.py          # Reglas contables del catálogo
│   └── utilidades.py            # Números, años, fechas y normalización de etiquetas
├── ejemplos/                    # Salidas de ejemplo (Dromos en inglés y Moda Pap en español)
└── tests/                       # Pruebas con pytest y datos de entrada
```

## Contrato

**Entrada** (POST, `Content-Type: application/json`):

```json
{
  "archivo_origen": "EEFF EMIS Dromos.pdf",
  "fecha_extraccion": "2026-11-13T14:28:29Z",
  "modelo": "prebuilt-layout",
  "tablas": [ ...analyzeResult.tables de Document Intelligence... ],
  "parrafos": [ ...opcional: analyzeResult.paragraphs... ]
}
```

Solo `tablas` es obligatorio. Con `parrafos` se llenan además la clasificación NAICS, la descripción del negocio y la información básica.

**Salida (200)**, en este orden de secciones:

| Sección | Contenido |
| --- | --- |
| `metadata` | Archivo, fechas, modelo, idioma detectado, unidades y versiones |
| `empresa` | Nombre, estado, forma legal, EMISid, NIT, fecha de constitución, sede, teléfono, fax y email |
| `clasificacion_naics`, `descripcion_negocio`, `informacion_basica`, `ejecutivos_clave` | Solo si llegan `parrafos`; si no, `null` |
| `resumen_financiero` | Años y filas del resumen financiero del reporte |
| `empresas_similares` | Una entrada por empresa, con las filas partidas ya unidas |
| `estados_financieros` | `detalles_reporte`, `periodos` (fechas y tasas de cambio por año), `estado_resultados`, `balance_general`, `flujo_de_caja` y `otra_informacion` |
| `ratios` | `detalles_reporte`, nueve grupos de ratios y `otra_informacion` |
| `filas_no_reconocidas` | Filas con valores que no están en el catálogo; el dato no se pierde |
| `calidad` | Estado, años, años faltantes, secciones ausentes, cuentas faltantes, correcciones, validaciones y advertencias |

Cada cuenta o ratio sale así:

```json
{ "id": "gross_profit", "etiqueta": "Gross Profit", "padre": null, "nivel": 0, "unidad": "usd_miles",
  "valores": { "2020": 2571.17, "2021": 2349.88, "2023": 5564.06, "2024": 7530.57, "2025": 6892.41 },
  "fuente": { "pagina": 6, "tabla": 7, "fila": 7 } }
```

Convenciones de los valores:
- Montos en miles de USD.
- Porcentajes como fracción: 20.05% se guarda como `0.2005`.
- Múltiplos sin la "x": 1.41x se guarda como `1.41`.
- Celdas vacías y `N/D` como `null`.

`calidad.estado` puede tomar tres valores:
- `ok`: todo cuadra.
- `con_advertencias`: hay secciones ausentes, años faltantes, filas no reconocidas o cruces que no cuadran.
- `error`: una regla contable de los estados no cuadra. El JSON se entrega completo igual; el flujo sigue y queda marcado.

**Errores:**
- `400` con `{"error": "..."}` si el cuerpo no es JSON o no trae `tablas`.
- `500` si ocurre una falla inesperada; el detalle queda en Application Insights.

## Correr en tu equipo

Requisitos: Python 3.11 y [Azure Functions Core Tools v4](https://learn.microsoft.com/azure/azure-functions/functions-run-local).

```bash
cd func-emis
python -m venv .venv
source .venv/bin/activate          # en Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
python -m pytest -q                # deben pasar 11 pruebas
func start                         # levanta http://localhost:7071/api/estandarizar-emis
```

Prueba con el JSON de Dromos, en otra terminal:

```bash
curl -X POST http://localhost:7071/api/estandarizar-emis \
  -H "Content-Type: application/json" \
  --data-binary @tests/datos/cifras_emis_dromos.json
```

## Desplegar en Azure

Plan Flex Consumption, Python 3.11, región West US 2. Reemplaza los nombres si tu convención es otra.

```bash
az login
az account set --subscription <id-de-la-suscripcion>

# 1. Cuenta de almacenamiento que exige toda Function App (3 a 24 caracteres, solo minúsculas y números)
az storage account create \
  --name standinoagt2emisdev \
  --resource-group rg-atomo-andino-dev \
  --location westus2 \
  --sku Standard_LRS \
  --allow-blob-public-access false

# 2. Function App
az functionapp create \
  --name func-andino-agt000002-emis-dev-westus2 \
  --resource-group rg-atomo-andino-dev \
  --storage-account standinoagt2emisdev \
  --flexconsumption-location westus2 \
  --runtime python \
  --runtime-version 3.11

# 3. Publicar el código (Azure instala requirements.txt en la nube)
cd func-emis
func azure functionapp publish func-andino-agt000002-emis-dev-westus2

# 4. Ver la URL con su clave
func azure functionapp list-functions func-andino-agt000002-emis-dev-westus2 --show-keys
```

Prueba en Azure:

```bash
curl -X POST "<URL-con-code>" -H "Content-Type: application/json" \
  --data-binary @tests/datos/cifras_emis_dromos.json
```

## Conectar con Logic Apps

Va después del paso 10 del flujo, en el que se guarda el JSON de cifras:

1. **Compose "JSON cifras".** Debe usar los nombres de este contrato:

    ```json
    {
      "archivo_origen": "@{first(body('Filtrar_PDF'))?['Name']}",
      "fecha_extraccion": "@{utcNow()}",
      "modelo": "prebuilt-layout",
      "tablas": "@body('Consultar_analisis')?['analyzeResult']?['tables']",
      "parrafos": "@body('Consultar_analisis')?['analyzeResult']?['paragraphs']"
    }
    ```

    `parrafos` es opcional.
2. **Guardar la URL en Key Vault.** La URL con `?code=` va como secreto `fn-estandarizar-url`, y el flujo la lee con "Get secret", con Secure outputs activado.
3. **Acción HTTP "Estandarizar EMIS".**
    - Method: `POST`.
    - URI: el valor del secreto.
    - Header: `Content-Type: application/json`.
    - Body: `outputs('JSON_cifras')`.
    - Retry policy: exponencial, 2 reintentos.
4. **SharePoint "Create file".** Guarda `_agente/emis_estandarizado.json` con `string(body('Estandarizar_EMIS'))`.

## Actualizar el catálogo

Cuando un reporte traiga una cuenta que no está en el catálogo, la cuenta aparece en `filas_no_reconocidas` y en las advertencias. Para incorporarla:

1. Agrégala en `catalogo/catalogo_emis.json`, en `cuentas`, con su `id`, `seccion`, `padre`, `nivel`, `unidad` y sus etiquetas en `en` o `es`.
2. Sube la `version` del catálogo.
3. Corre las pruebas.
4. Vuelve a publicar.
