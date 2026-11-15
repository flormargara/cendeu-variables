# cendeu-variables

Variables de comportamiento crediticio y un scorecard a partir de la **Central de Deudores del Sistema Financiero (BCRA)**, usando el archivo bianual `24DSFAAAAMM` (24 meses de historia de todos los deudores del país).

Pensado para ser consumido por un motor de scoring como **paquete Python** (o vía CLI en un batch mensual). Todo el procesamiento pesado corre en DuckDB.

> ⚠️ **Los datos no se versionan.** La base contiene datos personales (Ley 25.326). El `.gitignore` excluye `*.txt`, `*.7z`, `*.parquet` y `data/`. En el repo sólo hay código, el modelo (parámetros y conteos agregados por bin) y reportes agregados.

## Instalación

```bash
pip install "git+https://github.com/flormargara/cendeu-variables.git"
# desarrollo
python3.12 -m venv .venv && .venv/bin/pip install -e ".[dev]"
```

Requiere Python ≥ 3.10. Para descomprimir el archivo del BCRA hace falta `7z` (`brew install p7zip`).

## v2 (en construcción): variables `CEN_*` desde el catálogo

Rediseño de las variables (ver [PLAN.md](PLAN.md)). Las definiciones viven en [`catalogo/variables.yaml`](catalogo/variables.yaml): cada variable trae descripción, fórmula, fuente, nulos y su expresión SQL sobre series mensuales densas por CUIT. El SQL se arma desde el catálogo; no hay SQL de variables escrito a mano.

```bash
# 1. Paquetes del mes (BCRA + padrón ARCA) a Parquet
python estudios/convertir_paquete_mensual.py ~/Downloads data --periodo 202609 --padron 20261030
# 2. Base limpia: 24DSF + mensual del último mes, equivalencias de entidades, CUITs reemplazados, pesos constantes
cendeu base data/deudores_24m data/v2/base --mensual data/deudores_mensual \
    --padron data/padron/corte=20261030/padron.parquet --uno-dsf data/desde_sit1/periodo=202609/data.parquet --base-ipc 202609
# 3. Variables a cada fecha de observación (con target a 6 meses si hay desempeño)
cendeu variables-v2 data/v2/base data/v2/variables --obs 202509 202510 202511 202512 202601 202602 202603
# 4. Subida a S3 (una carpeta por período, verifica tamaños)
cendeu subir data/v2/variables variables --simular
```

Informe de poder (`cendeu poder`) en [`reportes/poder_v2/`](reportes/poder_v2/), con su página en [`reportes/poder_v2/pagina/`](reportes/poder_v2/pagina/) (publicada como artifact en claude.ai). Para actualizarla después de recalcular el informe: `cendeu pagina-poder` arma los tres CSV y se reemplazan en el panel de fuentes de la página; el HTML no cambia.

Documentación generada desde el catálogo (`cendeu documentar`): [`docs/variables.md`](docs/variables.md) y [`docs/diccionario_variables.csv`](docs/diccionario_variables.csv).

Referencias públicas (sin datos personales) en [`referencias/`](referencias/): tipo de entidad, fideicomisos de recupero y equivalencias por fusiones.

## Flujo mensual (v1)

1. **Descarga**: ARCA con clave fiscal → *BCRA – Cheques y Deudores* → *Deudores* → `24DSFAAAAMM.7Z` (aceptar la DJ de la Ley 25.326).
2. **Pipeline**:

```bash
7z x 24DSF202609.7Z -odata/24DSF202609
cendeu ipc --desde 202409 --hasta 202609 --salida data/ipc.csv
cendeu convertir data/24DSF202609/24DSF.txt data/deudores_24m --corte 202609 --control data/24DSF202609/24DSF_control.txt
cendeu variables data/deudores_24m data/vars_202609.parquet --obs 202609 --ipc data/ipc.csv
cendeu scorear  data/vars_202609.parquet modelos/scorecard_cendeu_202509.json data/scores_202609.parquet
```

Tiempos de referencia (M-series, 10 núcleos, 16 GB): conversión ~2 min, variables ~6 min, scoring ~2 s.

Para **reentrenar** se necesita un corte con historia + desempeño, p. ej. observar 202509 con 12 meses de target:

```bash
cendeu variables data/deudores_24m data/vars_202509.parquet --obs 202509 --ipc data/ipc.csv --desempeno 12
cendeu iv       data/vars_202509.parquet --resumen reportes/iv_gini_resumen.csv --bins reportes/iv_gini_bins.csv
cendeu entrenar data/vars_202509.parquet modelos/scorecard_cendeu_202509.json
```

## Uso desde el motor de scoring

```python
import cendeu

modelo = cendeu.cargar_modelo("modelos/scorecard_cendeu_202509.json")

# batch: todos los CUIT de un corte
cendeu.scorear_parquet("data/vars_202609.parquet", modelo, "data/scores_202609.parquet")

# online / por lote de solicitudes: DataFrame con las variables de cada CUIT
df_scores = cendeu.scorear_df(df_variables, modelo)   # cuit, score, pd, pts_<variable>...

# columnas que el motor tiene que proveer
cendeu.variables_requeridas(modelo)

# o embeber el SQL en otro motor DuckDB
sql = cendeu.sql_score(modelo, "mi_tabla_de_variables")
```

Salida del scoring: `cuit, periodo_obs, en_poblacion_modelo, score, pd, pts_<variable>` (los puntos por variable sirven como *reason codes*).

## Datos de entrada: layout del `24DSF.txt`

**No sigue el diseño del `LEAME DEUDORES.pdf`** (ese corresponde al mensual `AAAAMMDEUDORES`). Registro de 378 caracteres, validado contra `24DSF_control.txt`:

| Posición | Campo |
|---|---|
| 1–5 | Código de entidad (alineado a izquierda) |
| 6–7 | Tipo de identificación (11 = CUIT/CUIL/CDI) |
| 8–18 | CUIT |
| 19 + 15·(k−1) | Bloque mes *k*: situación (2) + monto en miles de $ con coma decimal (12) + flag (1). *k*=1 es el mes de corte, *k*=24 es 23 meses antes |

Notas de calidad del corte 202609: 98% de pares entidad×período coinciden exacto con el control; 214 pares (30 entidades) donde el control trae exactamente el doble; el control incluye un mes extra (202409) que no está en el archivo; 288 grupos CUIT×entidad×mes duplicados (se agregan: peor situación, suma de montos).

## Variables (`cendeu variables`)

Una fila por CUIT a la fecha de observación. Montos en miles de $ constantes del mes de observación (deflactados por IPC INDEC). Situación 11 (cubierta con garantía "A") se trata como 1. `mb` = meses hacia atrás.

| Grupo | Variables |
|---|---|
| Situación | `sit_actual`, `sit_pond_actual`, `peor_sit_3/6/hist`, `peor_sit_prev3`, `meses_sit2p_6/hist`, `meses_sit3p_6/hist`, `meses_desde_sit2p/3p` (NULL = nunca), `tuvo_sit5_hist`, `recuperado`, `empeoro_3`, `racha_sit1_actual`, `cambios_sit_hist` |
| Endeudamiento | `deuda_actual`, `deuda_prom_3/6`, `deuda_max_hist`, `crec_deuda_3/6/hist`, `ratio_deuda_vs_max`, `deuda_2p_actual`, `prop_deuda_sit2p` |
| Relación con el sistema | `n_ent_actual`, `n_ent_max_hist`, `n_ent_hist`, `n_bancos_actual`, `n_no_bancos_actual`, `solo_no_bancario`, `ent_nuevas_3/6`, `ent_canceladas_6/hist`, `antiguedad_meses`, `meses_con_deuda_hist` |
| Target (si `--desempeno > 0`) | `peor_sit_fut`, `target_sit3p` (situación ≥3 en cualquier entidad en los meses siguientes) |

"Banco" = código de entidad < 1000 (aproximación: las compañías financieras reguladas quedan como no bancarias).

## Modelo actual: `modelos/scorecard_cendeu_202509.json`

- **Población**: personas humanas en situación 1 a 202509. **Target**: situación ≥3 en 202510–202609 (tasa 14,3%).
- **Método**: binning monótono (mín. 5% por bin, NULL como bin propio), WoE, selección forward por ganancia de AUC en test con filtro de correlación (|r| > 0,7) y de signo, regresión logística. Escala: 600 puntos = odds 50:1, PDO 20.
- **Desempeño** (muestra 20%, split 70/30 por hash de CUIT): **Gini test 0,54**, KS 0,41.
- **Variables**: `n_no_bancos_actual`, `ratio_deuda_vs_max`, `n_ent_actual`, `ent_nuevas_6`, `crec_deuda_hist`, `meses_desde_sit2p`, `racha_sit1_actual`.

Limitaciones conocidas:
- Sin validación *out-of-time*: con 24 meses sólo hay una ventana completa (12 + 12). Recalibrar cuando haya cortes nuevos.
- El target se activa con una sola entidad en situación ≥3 sin importar el monto; probablemente infla el peso de la cantidad de acreedores.
- IPC del último mes imputado con el anterior hasta que se publique (`imputado=1` en el CSV).

## Tests

```bash
.venv/bin/pytest -q
```

Usan datos sintéticos en el formato del `24DSF` (incluye el caso validado a mano contra la base real).
