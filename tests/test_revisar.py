"""Tests de la revisión programada (cendeu revisar / programar) sin launchd ni datos reales."""
from __future__ import annotations

import os

import pytest

from cendeu import actualizar as act
from cendeu import revisar as rev


@pytest.fixture
def entorno(tmp_path):
    datos, entrada = tmp_path / "datos", tmp_path / "entrada"
    (datos / "deudores_24m" / "periodo=202609").mkdir(parents=True)
    entrada.mkdir()
    avisos = []
    return datos, entrada, avisos, (lambda t, x: avisos.append(x))


def _paquetes(entrada, faltan=()):
    for n in ["24DSF202610", "202610DEUDORES", "1DSF202610", "202610INFRET", "202610INFRETPA", "20261201PADRON"]:
        if n not in faltan:
            (entrada / n).mkdir()


def test_espera_sin_paquetes(entorno):
    datos, entrada, avisos, aviso = entorno
    assert rev.revisar(datos, str(entrada), aviso=aviso) == "esperando"
    assert avisos == [] and "202610" in (datos / "_actualizaciones" / "revisar.log").read_text()


def test_incompleto_avisa_una_sola_vez(entorno):
    datos, entrada, avisos, aviso = entorno
    _paquetes(entrada, faltan=("1DSF202610",))
    assert rev.revisar(datos, str(entrada), aviso=aviso) == "incompleto"
    assert rev.revisar(datos, str(entrada), aviso=aviso) == "incompleto"
    assert len(avisos) == 1 and "1DSF202610" in avisos[0]


def test_completo_corre_y_avisa(entorno):
    datos, entrada, avisos, aviso = entorno
    _paquetes(entrada)
    llamadas = []

    def correr(corte, ent, d, **kw):
        llamadas.append((corte, kw))
        return {"pasos": {"monitoreo": {"psi_max": 0.02, "alertas": []}, "subir": {"simulado": True}}}

    assert rev.revisar(datos, str(entrada), aviso=aviso, correr=correr, simular_s3=True) == "actualizado"
    assert llamadas == [(202610, {"simular_s3": True})]
    assert "arranca" in avisos[0] and "Corte 202610 actualizado (S3 simulado)" in avisos[1]
    assert not (datos / "_actualizaciones" / "revisar.lock").exists()


def test_controles_con_error_no_repite_aviso(entorno):
    datos, entrada, avisos, aviso = entorno
    _paquetes(entrada)
    r = {"filas": [{"control": "Entidades con tipo asignado", "nivel": "error", "detalle": "sin tipo: [999]"}], "errores": 1}

    def correr(*a, **k):
        raise act.ControlesConError(r)

    assert rev.revisar(datos, str(entrada), aviso=aviso, correr=correr) == "controles"
    assert rev.revisar(datos, str(entrada), aviso=aviso, correr=correr) == "controles"
    assert sum("frenó en controles" in a for a in avisos) == 1


def test_error_inesperado_libera_el_lock(entorno):
    datos, entrada, avisos, aviso = entorno
    _paquetes(entrada)

    def correr(*a, **k):
        raise RuntimeError("disco lleno")

    assert rev.revisar(datos, str(entrada), aviso=aviso, correr=correr) == "error"
    assert "disco lleno" in avisos[-1] and not (datos / "_actualizaciones" / "revisar.lock").exists()
    assert "Traceback" in (datos / "_actualizaciones" / "revisar.log").read_text()


def test_lock_vivo_y_lock_viejo(entorno):
    datos, entrada, avisos, aviso = entorno
    lock = datos / "_actualizaciones" / "revisar.lock"
    lock.parent.mkdir(parents=True)
    lock.write_text(str(os.getpid()))            # proceso vivo: no corre
    assert rev.revisar(datos, str(entrada), aviso=aviso) == "ocupado"
    lock.write_text("999999999")                 # proceso muerto: lo pisa
    assert rev.revisar(datos, str(entrada), aviso=aviso) == "esperando"


def test_retoma_corte_a_medias(entorno):
    datos, _, _, _ = entorno
    (datos / "_actualizaciones" / "202610").mkdir(parents=True, exist_ok=True)
    est = act.Estado(datos / "_actualizaciones" / "202610")
    est.guardar("promover")
    (datos / "deudores_24m" / "periodo=202610").mkdir()   # ya promovido: el próximo sería 202611
    assert rev.corte_a_revisar(datos) == 202610
    for p in act.PASOS:
        est.guardar(p)
    assert rev.corte_a_revisar(datos) == 202611


def test_plist(tmp_path):
    pl = rev.plist(tmp_path, "~/cendeu/datos", "~/cendeu/entrada", 9, 30, "10GB")
    assert pl["Label"] == rev.ETIQUETA and pl["StartCalendarInterval"] == {"Hour": 9, "Minute": 30}
    args = pl["ProgramArguments"]
    assert args[:2] == ["/usr/bin/caffeinate", "-i"] and args[2].endswith("cendeu") and "revisar" in args
    assert "~" not in " ".join(args)   # rutas absolutas para launchd
    assert pl["WorkingDirectory"] == str(tmp_path) and "/opt/homebrew/bin" in pl["EnvironmentVariables"]["PATH"]
