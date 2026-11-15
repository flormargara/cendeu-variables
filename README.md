# cendeu-variables

Variables de comportamiento crediticio a partir de la **Central de Deudores del Sistema Financiero (BCRA)**: el bianual `24DSF` (24 meses de historia de todos los deudores del país), el `deudores.txt` mensual, el 1DSF, el maestro de entidades y el padrón de ARCA.

El repo construye, documenta y valida las variables; los modelos que las usan (por ejemplo, un modelo de PD) se desarrollan en otros proyectos. Se usa como **paquete Python** o por **CLI** (`cendeu <comando>`) en un batch mensual. Todo el procesamiento pesado corre en DuckDB. El plan, las decisiones y los pendientes están en [PLAN.md](PLAN.md).

> ⚠️ **Los datos no se versionan.** La base contiene datos personales (Ley 25.326). El `.gitignore` excluye `*.txt`, `*.7z`, `*.parquet` y `data/`. En el repo hay código, catálogo, documentación y reportes agregados. Los datos viven en `s3://data-cendeu-raw` (layout en [PLAN.md](PLAN.md#layout-s3)).

## Instalación

```bash
pip install "git+https://github.com/flormargara/cendeu-variables.git"
# desarrollo
python3.12 -m venv .venv && .venv/bin/pip install -e ".[dev]"
```

Requiere Python ≥ 3.10. Para descomprimir los archivos del BCRA hace falta `7z` (`brew install p7zip`). La subida a S3 usa la CLI de AWS con el perfil `fmargara`.

## Estructura

| Carpeta | Contenido |
|---|---|
| [`catalogo/variables.yaml`](catalogo/variables.yaml) | Fuente única de las variables v2: nombre, definición, fuente, nulos, uso y expresión SQL de cada una |
| [`src/cendeu/`](src/cendeu/) | Paquete: `v2.py` (base y variables), `catalogo.py` (carga, validación y documentación), `poder.py` (informe de poder), `validacion_v2.py` (validación multivariada), `s3.py` (subida), `pipeline.py`, `scorecard.py` y `scoring.py` (v1) |
| [`referencias/`](referencias/) | Datos públicos versionados: IPC INDEC, tipo de entidad, fideicomisos de recupero y equivalencias por fusiones |
| [`docs/`](docs/) | Documento técnico y diccionario de variables (generados desde el catálogo) y el informe de variables mix |
| [`reportes/`](reportes/) | Estudio del 24DSF, fuentes BCRA y ARCA, informe de poder v2 y su página, validación multivariada |
| [`estudios/`](estudios/) | Scripts de estudio y el conversor de los paquetes mensuales |
| [`modelos/`](modelos/) | Scorecard v1 (línea de base para comparar) |
| [`infra/`](infra/) | Política IAM del bucket |

## Flujo v2

### 1. Descarga

En ARCA con clave fiscal → *BCRA – Cheques y Deudores* → *Deudores* (aceptar la DJ de la Ley 25.326): el bianual `24DSFAAAAMM`, el mensual `AAAAMMDEUDORES`, `1DSFAAAAMM`, las rectificativas (`AAAAMMINFRET`, `AAAAMMINFRETPA`) y el padrón (`AAAAMMDDPADRON`).

### 2. Conversión a Parquet

```bash
7z x 24DSF202609.7Z -odata/24DSF202609
cendeu convertir data/24DSF202609/24DSF.txt data/deudores_24m --corte 202609 --control data/24DSF202609/24DSF_control.txt
python estudios/convertir_paquete_mensual.py ~/Downloads data --periodo 202609 --padron 20261030
```

El conversor del paquete mensual valida el largo de cada registro contra su LEAME antes de convertir.

### 3. Base, variables, informe y documentación

```bash
# IPC: se versiona en referencias/ipc.csv; volver a bajarlo cuando el INDEC publique el último mes
cendeu ipc --desde 202409 --hasta 202609 --salida referencias/ipc.csv

# Base limpia: 24DSF + mensual en el mes de corte, equivalencias de entidades, CUITs reemplazados, pesos constantes
cendeu base data/deudores_24m data/v2/base --mensual data/deudores_mensual \
    --padron data/padron/corte=20261030/padron.parquet --uno-dsf data/desde_sit1/periodo=202609/data.parquet --base-ipc 202609

# Variables del catálogo a cada fecha de observación (con target a 6 meses si hay desempeño)
cendeu variables-v2 data/v2/base data/v2/variables --obs 202509 202510 202511 202512 202601 202602 202603

# Informe de poder (muestra del 10%) y valor incremental contra el scorecard v1
cendeu poder data/v2/variables --vars-v1 data/vars_cendeu_202509.parquet
cendeu pagina-poder          # CSV de la página del informe

# Validación multivariada: ajuste por segmento, fuera de muestra y comparación con las variables v1
cendeu validar-v2 data/v2/variables --vars-v1 202509=data/vars_cendeu_202509.parquet 202603=data/vars_cendeu_202603_d6.parquet

# Catálogo: validar y regenerar la documentación
cendeu catalogo
cendeu documentar

# Subida a S3 (una carpeta por período, verifica tamaños, nunca borra)
cendeu subir data/v2/base base
cendeu subir data/v2/variables variables
```

Si una subida se corta por la red, `aws s3 sync <carpeta> s3://data-cendeu-raw/<prefijo> --profile fmargara` copia sólo lo que falta; después conviene volver a comparar los tamaños.

Tiempos de referencia (M-series, 10 núcleos, 16 GB, `--memoria 10GB`): base ~6 min; variables ~45 s por parte de ~1,3 M de CUITs (16 partes por fecha, ~12 min por fecha); informe de poder, unos minutos. Con menos de 10 GB fallan pasos que DuckDB no puede volcar a disco. `variables-v2 --retomar` saltea las partes ya escritas si una corrida se corta.

Cada base guarda en `_meta.json` el hash del IPC y de las referencias que usó, y si algún mes del IPC estaba imputado.

### Salidas

- **Variables**: `variables/periodo_obs=AAAAMM/parte_XX.parquet`, una fila por CUIT presente en la central en esa fecha. Columnas: `CUIT`, `PERIODO_OBS`, `EN_POBLACION` (situación ≤ 2, no fallecido), `CEN_TGT_SIT3P_6M` (nulo si el desempeño no está completo) y las 149 variables `CEN_*`.
- **Documentación**: [`docs/variables.md`](docs/variables.md) y [`docs/diccionario_variables.csv`](docs/diccionario_variables.csv).
- **Validación multivariada**: [`reportes/validacion_v2/`](reportes/validacion_v2/). Un scorecard por segmento (situación 1 y 2) ajustado sólo para probar las variables en conjunto: poder fuera de muestra, estabilidad y comparación con las variables v1. No es un modelo para producción.
- **Informe de poder**: [`reportes/poder_v2/`](reportes/poder_v2/) y su página en [`reportes/poder_v2/pagina/`](reportes/poder_v2/pagina/), publicada como artifact en claude.ai. Para actualizar la página después de recalcular el informe, se corre `cendeu pagina-poder` y se reemplazan los tres CSV en el panel de fuentes; el HTML no cambia.

### Agregar o cambiar una variable

1. Editar `catalogo/variables.yaml`: una entrada nueva o un valor más en el `expandir` de una familia.
2. `cendeu catalogo` valida nombre, operación, bloque, fuente, tipo y uso.
3. Agregar el caso a `tests/test_v2.py` si la lógica es nueva.
4. `cendeu documentar` regenera el documento técnico y el diccionario.

Reglas que aplica el generador: una variable con `disponible_desde` posterior a la fecha de observación queda nula (evita usar fotos de hoy en fechas pasadas), y una con `requiere_mes_previo` queda nula si el mes anterior a su ventana no está en la historia.

## v1 (línea de base)

Las variables y el scorecard originales se mantienen sólo como referencia para medir cuánto mejoran las variables v2. Cuando exista el proyecto de modelos, el scorecard y el scoring v1 pasan a ese proyecto.

```bash
cendeu variables data/deudores_24m data/vars_202609.parquet --obs 202609 --ipc referencias/ipc.csv
cendeu scorear  data/vars_202609.parquet modelos/scorecard_cendeu_202509.json data/scores_202609.parquet
# reentrenar (corte con 12 meses de desempeño)
cendeu variables data/deudores_24m data/vars_202509.parquet --obs 202509 --ipc referencias/ipc.csv --desempeno 12
cendeu iv       data/vars_202509.parquet --resumen reportes/iv_gini_resumen.csv --bins reportes/iv_gini_bins.csv
cendeu entrenar data/vars_202509.parquet modelos/scorecard_cendeu_202509.json
```

**Modelo v1** ([`modelos/scorecard_cendeu_202509.json`](modelos/scorecard_cendeu_202509.json)): personas humanas en situación 1 a 202509, target situación ≥3 en 202510–202609 (tasa 14,3%). Binning monótono, WoE, selección forward y regresión logística; 600 puntos = odds 50:1, PDO 20. Gini test 0,54, KS 0,41. Variables: `n_no_bancos_actual`, `ratio_deuda_vs_max`, `n_ent_actual`, `ent_nuevas_6`, `crec_deuda_hist`, `meses_desde_sit2p`, `racha_sit1_actual`. Sin validación *out-of-time*.

### Uso desde el motor de scoring

```python
import cendeu

modelo = cendeu.cargar_modelo("modelos/scorecard_cendeu_202509.json")
cendeu.scorear_parquet("data/vars_202609.parquet", modelo, "data/scores_202609.parquet")   # batch
df_scores = cendeu.scorear_df(df_variables, modelo)    # cuit, score, pd, pts_<variable>...
cendeu.variables_requeridas(modelo)                    # columnas que el motor tiene que proveer
sql = cendeu.sql_score(modelo, "mi_tabla_de_variables")  # SQL para embeber en otro DuckDB
```

Los puntos por variable (`pts_<variable>`) sirven como *reason codes*.

## Datos de entrada

**`24DSF.txt`** (no sigue el diseño del `LEAME DEUDORES.pdf`, que corresponde al mensual): registro de 378 caracteres, validado contra `24DSF_control.txt`.

| Posición | Campo |
|---|---|
| 1–5 | Código de entidad (alineado a izquierda) |
| 6–7 | Tipo de identificación (11 = CUIT/CUIL/CDI) |
| 8–18 | CUIT |
| 19 + 15·(k−1) | Bloque mes *k*: situación (2) + monto en miles de $ con coma decimal (12) + flag de proceso judicial (1). *k*=1 es el mes de corte, *k*=24 es 23 meses antes |

El 24DSF omite en su primer mes a algunas entidades (69 en el corte 202609); por eso la base v2 toma el mes de corte del `deudores.txt` mensual. Los diseños del mensual, el 1DSF, las rectificativas y el padrón, y sus particularidades, están en [`reportes/fuentes_bcra_arca.md`](reportes/fuentes_bcra_arca.md).

## Tests

```bash
.venv/bin/pytest -q
```

Usan datos sintéticos: el formato del `24DSF` (v1), casos calculados a mano para la base y las variables v2 (fusiones, huecos, CUIT reemplazado, fuga del 1DSF, borde de la historia) y variables de comportamiento conocido para el informe de poder.
