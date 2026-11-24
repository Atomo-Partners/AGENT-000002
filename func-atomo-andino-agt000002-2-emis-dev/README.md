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
| `calidad` | Estado, años, años faltantes, secciones ausentes, cuentas faltantes, `cuentas_clave_faltantes`, `filas_estados_no_reconocidas`, correcciones, validaciones y advertencias |
| `cifras_compactas` | Vista corta para la Function 3 y los agentes: `estados`, `ratios`, `secciones` (resultados/balance/flujo de cada cuenta), `tasas` por año (`resultados`, `balance`, `flujo`), `moneda_original`, `empleados` (`valor`, `anio`, `texto`) y `calidad` |

### Cambios de la versión 1.2 (catálogo 1.1, 2026-11-24)

- El reporte EMIS real en español (Rentek) dejaba 112 filas sin reconocer: gastos financieros, caja, deuda,
  cuentas por cobrar y todo el flujo de caja. El catálogo ahora trae las etiquetas en español de esas cuentas,
  el título "Estado de Flujo de Efectivo" y cuentas nuevas (prima de emisión, acciones propias, inversiones en
  subsidiarias, propiedades de inversión, activos financieros de corto plazo, intereses pagados, reembolso de
  préstamos, adquisición de subsidiaria, compra de acciones propias, otros flujos de financiación).
- `calidad.cuentas_clave_faltantes`: grupos de cuentas que usan los KPIs y no se pudieron leer (ingresos, EBIT,
  gastos financieros, activos, patrimonio, caja, deuda, cartera, flujo operativo, compra de PPE).
- EMIS llena con 0.00 el detalle del flujo de caja de los años en que solo tiene el total. Bajo un total
  distinto de cero pasan a null (y queda en `correcciones`): todas las líneas hijas si todas valen cero, o un
  subtotal que vale cero junto con todo lo que cuelga de él (Rentek 2021: efectivo generado por la operación,
  depreciación y cambios de capital de trabajo en 0,00). Un 0,00 suelto con detalle alrededor se respeta.
- Las reglas contables aceptan términos `opcionales` que valen cero si el reporte no los trae (prima de emisión,
  acciones propias, activos financieros de corto plazo).
- Una misma etiqueta en inglés y en español ya no se marca como ambigua.
- El EMIS en español entrega como párrafos sueltos (no como tabla) la moneda original ("Unidades originales como
  se reportó: COP Miles") y los bloques de la hoja resumen. Ahora se leen: `cifras_compactas.moneda_original`,
  `ejecutivos_clave` (nombre y cargo), e `informacion_basica.accionistas`, `.subsidiarias` y `.revisor_fiscal`.
  Los pies de página del PDF ("Copyright…", "Page 3 of 13") se descartan para que no corten los bloques.
- Catálogo 1.2: la ficha de la empresa también se lee cuando Document Intelligence pega varios "etiqueta: valor"
  en un solo párrafo (Rentek: "Forma Legal: … Estatus Operacional: … EMISid: …"); la tabla de dos columnas
  "Descripción de Compañías | Productos Principales" se lee por columna; las tablas de ejecutivos (nombre y
  cargo) ya no quedan como filas no reconocidas; etiquetas en español de "Productos Principales",
  "Capital Registrado", "Actividades Secundarias" y "Fax"; títulos de bloque partidos en dos párrafos
  ("Company Tear Sheet -" / "Key Executives").

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
python -m pytest -q                # deben pasar 21 pruebas
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

En el flujo ordenado (`logicapp/agt000002-fase2.workflow.json`) la Function corre dentro de
`Scope_Extraccion_cifras`:

1. `JSON_cifras` arma `archivo_origen`, `fecha_extraccion`, `modelo`, `tablas` y `parrafos` con la salida de
   Document Intelligence (`prebuilt-layout`). Sin `parrafos` no se leen NAICS, descripción, información
   básica (empleados) ni ejecutivos.
2. `EstandarizadorKey` lee la URL con `?code=` del secreto `func2-emis-key` de Key Vault.
3. `Estandarizar_EMIS` hace el POST, con entradas protegidas (la URL lleva la clave) y salidas visibles.
4. `Guardar_Cifras_Est` guarda `_agente/cifras_emis.json`.

Después, la Function 3 (`preparar_analisis`) recibe `cifras_compactas`.

## Actualizar el catálogo

Cuando un reporte traiga una cuenta que no está en el catálogo, la cuenta aparece en `filas_no_reconocidas` y en las advertencias. Para incorporarla:

1. Agrégala en `catalogo/catalogo_emis.json`, en `cuentas`, con su `id`, `seccion`, `padre`, `nivel`, `unidad` y sus etiquetas en `en` o `es`.
2. Sube la `version` del catálogo.
3. Corre las pruebas.
4. Vuelve a publicar.
