"""Revisión programada del corte mensual: ``cendeu revisar`` (lo corre launchd todos los días).

1. Elige el corte: uno que haya quedado a medias en ``_actualizaciones/`` o, si no hay, el mes siguiente al
   último cargado.
2. Si los paquetes de ese corte no están completos en la carpeta de entrada, espera (avisa una sola vez
   cuando llegó una parte).
3. Si están completos, corre ``cendeu actualizar`` y avisa al empezar y al terminar, o si frenó (los avisos de
   error no se repiten mientras el error sea el mismo).

``cendeu programar`` instala la tarea en launchd (``~/Library/LaunchAgents/com.cendeu.revisar.plist``).
launchd no puede leer ``~/Downloads`` ni ``~/Documents``: los datos y la entrada van en otra carpeta
(por defecto ``~/cendeu/datos`` y ``~/cendeu/entrada``).
"""
from __future__ import annotations

import json
import os
import platform
import plistlib
import subprocess
import sys
import traceback
from pathlib import Path

from . import actualizar as act

ETIQUETA = "com.cendeu.revisar"
DATOS = "~/cendeu/datos"
ENTRADA = "~/cendeu/entrada"


def notificar(titulo: str, texto: str) -> None:
    """Notificación de macOS (en otros sistemas sólo queda en el log)."""
    if platform.system() != "Darwin":
        return
    esc = lambda s: s.replace("\\", "\\\\").replace('"', '\\"')
    subprocess.run(["osascript", "-e", f'display notification "{esc(texto)}" with title "{esc(titulo)}" sound name "default"'],
                   capture_output=True)


class _Registro:
    def __init__(self, dir_trabajo: Path):
        dir_trabajo.mkdir(parents=True, exist_ok=True)
        self.log = dir_trabajo / "revisar.log"
        self.avisos = dir_trabajo / "avisos.json"

    def escribir(self, texto: str) -> None:
        with self.log.open("a", encoding="utf-8") as f:
            f.write(f"{act._ahora()}  {texto}\n")
        print(texto, flush=True)

    def nuevo(self, clave: str, texto: str) -> bool:
        """True si este aviso no se dio todavía con el mismo texto (para no repetirlo todos los días)."""
        previos = json.loads(self.avisos.read_text()) if self.avisos.exists() else {}
        if previos.get(clave) == texto:
            return False
        previos[clave] = texto
        self.avisos.write_text(json.dumps(previos, indent=2, ensure_ascii=False))
        return True


def corte_a_revisar(datos: Path) -> int:
    """Un corte que quedó a medias (tiene estado sin resumen) o, si no hay, el próximo."""
    pendientes = []
    for e in (datos / "_actualizaciones").glob("*/estado.json"):
        if e.parent.name.isdigit() and not act.Estado(e.parent).hecho("resumen"):
            pendientes.append(int(e.parent.name))
    return min(pendientes) if pendientes else act.proximo_corte(datos)


def _lock(datos: Path):
    """Evita dos revisiones a la vez (una actualización tarda ~25 min)."""
    ruta = datos / "_actualizaciones" / "revisar.lock"
    if ruta.exists():
        try:
            os.kill(int(ruta.read_text()), 0)
            return None   # el proceso sigue vivo
        except (ValueError, ProcessLookupError, PermissionError):
            pass          # lock viejo de una corrida que murió
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(str(os.getpid()))
    return ruta


def revisar(datos: str | Path = DATOS, entrada: str = ENTRADA, *, aviso=notificar, correr=act.actualizar,
            **kw_actualizar) -> str:
    """Devuelve el resultado: ``ocupado``, ``esperando``, ``incompleto``, ``actualizado``, ``controles`` o ``error``."""
    datos = Path(datos).expanduser()
    reg = _Registro(datos / "_actualizaciones")
    lock = _lock(datos)
    if lock is None:
        reg.escribir("otra revisión está corriendo")
        return "ocupado"
    try:
        corte = corte_a_revisar(datos)
        ent = act.sincronizar_entrada(entrada, datos / "_actualizaciones" / str(corte) / "entrada", kw_actualizar.get("perfil", "fmargara"))
        det = act.detectar(ent, corte)
        if not det["encontrados"]:
            reg.escribir(f"corte {corte}: todavía no hay paquetes en {ent}")
            return "esperando"
        if not det["completo"]:
            texto = f"Llegó una parte del corte {corte}. Faltan: {', '.join(det['faltan'])}"
            reg.escribir(texto)
            if reg.nuevo(f"{corte}/incompleto", texto):
                aviso("CENDEU", texto)
            return "incompleto"
        reg.escribir(f"corte {corte}: paquetes completos, arranca la actualización")
        if reg.nuevo(f"{corte}/inicio", "inicio"):
            aviso("CENDEU", f"Corte {corte} completo: arranca la actualización (~25 min)")
        try:
            estado = correr(corte, str(ent), datos, **kw_actualizar)
        except act.ControlesConError as e:
            errores = [f["control"] for f in e.resultado["filas"] if f["nivel"] == "error"]
            texto = f"Corte {corte}: frenó en controles ({'; '.join(errores)}). No se tocó nada."
            reg.escribir(f"{texto}\n{e.code}")
            if reg.nuevo(f"{corte}/controles", texto):
                aviso("CENDEU: revisar el corte", texto)
            return "controles"
        mon = estado["pasos"].get("monitoreo", {})
        psi = f"PSI máx. {mon['psi_max']:.3f}" if mon.get("psi_max") is not None else "sin PSI"
        sim = " (S3 simulado)" if estado["pasos"].get("subir", {}).get("simulado") else ""
        texto = f"Corte {corte} actualizado{sim}: {psi}, {len(mon.get('alertas', []))} variables con PSI alto. " \
                f"Resumen en reportes/actualizaciones/{corte}.md"
        reg.escribir(texto)
        aviso("CENDEU", texto)
        return "actualizado"
    except Exception as e:  # cualquier otra falla: aviso con el mensaje y el traceback en el log
        texto = f"Falló la revisión: {type(e).__name__}: {e}"
        reg.escribir(f"{texto}\n{traceback.format_exc()}")
        if reg.nuevo("error", texto):
            aviso("CENDEU: error", texto[:200])
        return "error"
    finally:
        lock.unlink(missing_ok=True)


# --------------------------------------------------------------------------- launchd
def ruta_plist() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{ETIQUETA}.plist"


def plist(repo: Path, datos: str, entrada: str, hora: int, minuto: int, memoria: str) -> dict:
    """Definición de la tarea: todos los días a la hora indicada (si la Mac estaba dormida, corre al despertar).
    ``caffeinate -i`` evita que se duerma a mitad de una actualización."""
    cendeu = Path(sys.executable).parent / "cendeu"
    log = Path(datos).expanduser() / "_actualizaciones" / "launchd.log"
    path = ":".join(dict.fromkeys(p for p in [str(Path(sys.executable).parent), "/opt/homebrew/bin", "/usr/local/bin",
                                              "/usr/bin", "/bin", "/usr/sbin", "/sbin"]))
    return {
        "Label": ETIQUETA,
        "ProgramArguments": ["/usr/bin/caffeinate", "-i", str(cendeu), "--memoria", memoria, "revisar",
                             "--datos", str(Path(datos).expanduser()), "--entrada",
                             entrada if entrada.startswith("s3://") else str(Path(entrada).expanduser())],
        "WorkingDirectory": str(repo),   # catalogo/ y referencias/ se leen relativos al repo
        "StartCalendarInterval": {"Hour": hora, "Minute": minuto},
        "StandardOutPath": str(log), "StandardErrorPath": str(log),
        "EnvironmentVariables": {"PATH": path, "HOME": str(Path.home())},
        "ProcessType": "Background",
    }


def programar(repo: str | Path, datos: str = DATOS, entrada: str = ENTRADA, hora: int = 9, minuto: int = 0,
              memoria: str = "10GB") -> Path:
    """Instala (o reemplaza) la tarea en launchd para el usuario actual."""
    ruta = ruta_plist()
    ruta.parent.mkdir(parents=True, exist_ok=True)
    (Path(datos).expanduser() / "_actualizaciones").mkdir(parents=True, exist_ok=True)
    dominio = f"gui/{os.getuid()}"
    subprocess.run(["launchctl", "bootout", f"{dominio}/{ETIQUETA}"], capture_output=True)
    with ruta.open("wb") as f:
        plistlib.dump(plist(Path(repo).resolve(), datos, entrada, hora, minuto, memoria), f)
    subprocess.run(["launchctl", "bootstrap", dominio, str(ruta)], check=True, capture_output=True)
    return ruta


def quitar() -> bool:
    ruta = ruta_plist()
    subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{ETIQUETA}"], capture_output=True)
    if ruta.exists():
        ruta.unlink()
        return True
    return False


def correr_ahora() -> None:
    """Dispara la tarea instalada sin esperar a la hora programada."""
    subprocess.run(["launchctl", "kickstart", f"gui/{os.getuid()}/{ETIQUETA}"], check=True)


def instalada() -> dict | None:
    if not ruta_plist().exists():
        return None
    with ruta_plist().open("rb") as f:
        return plistlib.load(f)
