"""Variables de comportamiento y scorecard sobre la Central de Deudores del BCRA (bianual 24DSF)."""
from .pipeline import construir_variables, convertir, descargar_ipc, iv_gini, validar_contra_control
from .scorecard import entrenar
from .scoring import cargar_modelo, scorear_df, scorear_parquet, sql_score, variables_requeridas

__all__ = [
    "descargar_ipc", "convertir", "validar_contra_control", "construir_variables", "iv_gini",
    "entrenar", "cargar_modelo", "scorear_parquet", "scorear_df", "sql_score", "variables_requeridas",
]
__version__ = "0.1.0"
