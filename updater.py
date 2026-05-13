# -*- coding: utf-8 -*-
"""Chequeo de actualizaciones contra GitHub Releases.

Al abrir la app, se consulta el endpoint REST de GitHub
    GET https://api.github.com/repos/<owner>/<repo>/releases/latest

Si el tag remoto es mayor que APP_VERSION local, la UI muestra un banner
no-intrusivo en pantalla 1. Click en "Ver release" abre la URL del release
en el browser default — el usuario descarga el .app o .exe nuevo manualmente
y reemplaza el viejo. NO auto-descargamos / reemplazamos binarios porque:

- En macOS reemplazar un .app mientras esta corriendo requiere truco con
  trampoline scripts y permisos de Quarantine que son fragiles.
- En Windows reemplazar el .exe activo requiere matar el proceso actual
  desde un script externo.

El flujo "abrir release page + descargar manual" es 1 click extra pero
mucho mas robusto y mantenible.

Diseñado para fallar silenciosamente: si no hay internet, si la API
responde 404 (no hay releases todavia), o si el formato cambia, devuelve
None y la app sigue normal sin mostrar banner.
"""

import json
import re
import ssl
import urllib.request
import urllib.error


def _ssl_context():
    """Devuelve un SSL context valido para HTTPS. En macOS con Python.org
    el default no tiene los CA root de Mozilla pre-instalados (SSL
    CERTIFICATE_VERIFY_FAILED). Si certifi esta disponible, usamos su
    bundle; sino cae al default del SO."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


# GitHub repo destino. Si el repo cambia (fork, rename), actualizar aca.
GITHUB_OWNER = "shakedcalzato"
GITHUB_REPO  = "fotos-proforma"

# User-Agent custom: GitHub recomienda mandar uno identificable. Ayuda a que
# eventuales rate-limits sean atribuibles a esta app.
USER_AGENT   = "FotosProforma-UpdateChecker/1.0"


def check_latest_release(timeout=4.0):
    """Consulta la API de GitHub por el ultimo release publicado.

    Returns:
        dict con keys {tag_name, name, html_url, body, published_at}
        o None si:
        - no hay internet,
        - no hay ningun release publicado (404),
        - la respuesta no parsea como JSON con los campos esperados,
        - el release esta marcado como draft (no es publico todavia).

    NUNCA lanza excepciones. Es safe llamar desde un thread y dejar que
    el resultado se aplique en main thread via root.after.
    """
    url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/vnd.github+json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout,
                                     context=_ssl_context()) as resp:
            raw = resp.read().decode("utf-8")
        data = json.loads(raw)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError,
            json.JSONDecodeError, OSError):
        return None
    except Exception:
        # Cualquier otra cosa (timeout, ssl, etc.) — fallo silencioso.
        return None

    if not isinstance(data, dict):
        return None
    if data.get("draft") or data.get("prerelease"):
        return None
    tag = data.get("tag_name")
    if not tag:
        return None

    return {
        "tag_name":     tag,
        "name":         data.get("name") or tag,
        "html_url":     data.get("html_url") or "",
        "body":         data.get("body") or "",
        "published_at": data.get("published_at") or "",
    }


_VERSION_RE = re.compile(r"^v?(\d+(?:\.\d+)*)")


def parse_version(s):
    """Convierte 'v1.0.2', '1.0', 'v2.1-beta' → tupla de ints.
    Si la string no parece version, devuelve () (compara como menor a todo)."""
    if not s:
        return ()
    m = _VERSION_RE.match(str(s).strip())
    if not m:
        return ()
    try:
        return tuple(int(p) for p in m.group(1).split("."))
    except ValueError:
        return ()


def is_newer(remote, local):
    """True si remote > local segun parse_version. Si alguna no parsea
    (tupla vacia), retornamos False (no asumimos update)."""
    r = parse_version(remote)
    l = parse_version(local)
    if not r or not l:
        return False
    # Padding: ("1", "0") vs ("1", "0", "1") → (1,0,0) vs (1,0,1)
    n = max(len(r), len(l))
    r = r + (0,) * (n - len(r))
    l = l + (0,) * (n - len(l))
    return r > l


def is_frozen():
    """True si la app esta corriendo desde un binario empaquetado por
    PyInstaller (.app de Mac o .exe de Windows), False si esta corriendo
    desde codigo fuente (`python main.py`). Solo permitimos auto-update
    cuando esta frozen — en dev el usuario tiene git pull."""
    import sys
    return bool(getattr(sys, "frozen", False))


def asset_for_current_platform(release_info):
    """Identifica que asset del release bajar segun el SO actual.

    Args:
        release_info: dict de check_latest_release(), debe tener "assets"
                      list raw del json. Si no esta (porque check no lo
                      incluyo), devuelve None.

    Returns:
        dict con {name, browser_download_url, size} del asset apropiado,
        o None si no hay match.

    Convencion del proyecto:
        - Mac: archivo .zip que contiene un .app (ej. FotosProforma.app.zip)
        - Windows: archivo .exe directo (ej. FotosProforma.exe)
    """
    import sys
    assets = release_info.get("assets") if isinstance(release_info, dict) else None
    if not assets:
        return None
    is_mac = sys.platform == "darwin"
    is_win = sys.platform.startswith("win")
    for a in assets:
        name = (a.get("name") or "").lower()
        if is_mac and name.endswith(".app.zip"):
            return a
        if is_win and name.endswith(".exe"):
            return a
    return None


def fetch_release_with_assets(timeout=6.0):
    """Como check_latest_release pero conserva la lista de assets en el
    dict devuelto. Necesario para descargar el binario apropiado."""
    url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/vnd.github+json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout,
                                     context=_ssl_context()) as resp:
            raw = resp.read().decode("utf-8")
        data = json.loads(raw)
    except Exception:
        return None
    if not isinstance(data, dict) or data.get("draft") or data.get("prerelease"):
        return None
    if not data.get("tag_name"):
        return None
    return data  # dict crudo de la API, incluye "assets"


def download_asset(asset, dest_path, progress_cb=None):
    """Descarga un asset del release a dest_path. Llama progress_cb(done, total)
    periodicamente si esta presente.

    Returns:
        True si la descarga termino completa, False si fallo.
    """
    url = asset.get("browser_download_url") or asset.get("url")
    if not url:
        return False
    expected_size = int(asset.get("size") or 0)
    req = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/octet-stream"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30,
                                     context=_ssl_context()) as resp:
            total = int(resp.headers.get("Content-Length") or expected_size)
            done = 0
            chunk_size = 64 * 1024
            with open(dest_path, "wb") as f:
                while True:
                    chunk = resp.read(chunk_size)
                    if not chunk:
                        break
                    f.write(chunk)
                    done += len(chunk)
                    if progress_cb is not None:
                        try:
                            progress_cb(done, total)
                        except Exception:
                            pass
        # Verificacion minima: tamaño coincide con el header.
        import os
        if total > 0 and os.path.getsize(dest_path) != total:
            return False
        return True
    except Exception:
        try:
            import os
            os.remove(dest_path)
        except OSError:
            pass
        return False


def install_update_and_restart(downloaded_path):
    """Instala el update descargado y reinicia la app. La estrategia depende
    de la plataforma:

    - macOS: el binario actual es un .app (directorio). El descargado es un
      .zip que contiene el .app nuevo. Un shell script de fondo:
        1) espera ~2 segundos a que el proceso actual termine,
        2) descomprime el zip,
        3) reemplaza el .app viejo por el nuevo,
        4) abre el nuevo .app,
        5) se auto-borra.

    - Windows: equivalente con un .bat. El binario es un .exe directo,
      asi que el reemplazo es: timeout 2 → move /Y → start → del %0.

    Despues de lanzar el script, la app principal hace sys.exit() para
    liberar el binario.

    Args:
        downloaded_path: Path al archivo descargado (zip en Mac, exe en Win).

    Returns:
        True si el bootstrapper se lanzo OK. La app deberia cerrar despues.
        False si algo fallo antes de lanzar el bootstrapper.
    """
    import sys
    import os
    import subprocess
    import tempfile
    from pathlib import Path as _Path

    if sys.platform == "darwin":
        return _install_mac(downloaded_path)
    if sys.platform.startswith("win"):
        return _install_windows(downloaded_path)
    return False  # Linux y otros: por ahora no soportado


def _install_mac(zip_path):
    """Bootstrapper macOS. Lanza un shell script que reemplaza el .app
    y reinicia."""
    import sys
    import os
    import subprocess
    import tempfile
    from pathlib import Path as _Path

    # sys.executable apunta a .../FotosProforma.app/Contents/MacOS/FotosProforma
    # El .app es 3 niveles arriba.
    exe = _Path(sys.executable).resolve()
    if exe.parent.name != "MacOS":
        return False
    app_path = exe.parent.parent.parent  # FotosProforma.app
    if app_path.suffix != ".app":
        return False
    parent = app_path.parent
    app_name = app_path.name

    # Script shell que hace todo el reemplazo. Lo escribimos a /tmp
    # con permisos de ejecucion.
    script = f"""#!/bin/bash
set -e
# Esperar a que el proceso actual cierre (PID lo pasamos como arg).
PID="{os.getpid()}"
for i in {{1..30}}; do
    if ! kill -0 "$PID" 2>/dev/null; then break; fi
    sleep 0.2
done
sleep 0.5

ZIP="{zip_path}"
PARENT="{parent}"
OLD_APP="{app_path}"
APP_NAME="{app_name}"

# Descomprimir a tmp y mover el .app de adentro al PARENT.
WORK=$(mktemp -d)
unzip -q "$ZIP" -d "$WORK"
NEW_APP=$(find "$WORK" -maxdepth 2 -name "*.app" -type d | head -n 1)
if [ -z "$NEW_APP" ]; then
    osascript -e 'display dialog "El update bajado no contiene un .app valido." buttons {{"OK"}}'
    exit 1
fi

# Sacar quarantine attribute para evitar Gatekeeper preguntando de nuevo.
xattr -dr com.apple.quarantine "$NEW_APP" 2>/dev/null || true

# Reemplazar: rm viejo, mv nuevo.
rm -rf "$OLD_APP"
mv "$NEW_APP" "$PARENT/$APP_NAME"

# Limpiar tmp.
rm -rf "$WORK"
rm -f "$ZIP"

# Abrir la version nueva.
open "$PARENT/$APP_NAME"

# Auto-borrarse.
rm -f "$0"
"""
    fd, script_path = tempfile.mkstemp(suffix=".sh", prefix="fotosproforma_update_")
    with os.fdopen(fd, "w") as f:
        f.write(script)
    os.chmod(script_path, 0o755)

    # Lanzar en background totalmente desacoplado del proceso actual.
    subprocess.Popen(
        ["/bin/bash", script_path],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL, start_new_session=True,
    )
    return True


def _install_windows(exe_path):
    """Bootstrapper Windows: PowerShell script (no .bat) que reemplaza el
    .exe usando la estrategia "rename + move" en vez de "move directo".

    Estrategia:
    1. Espera 6 seg a que el proceso anterior termine.
    2. Unblock-File para sacar zone identifier.
    3. RENOMBRA el .exe actual a "<nombre>.old". El rename funciona en
       muchos casos donde move/delete fallan (file handle de read-only,
       Dropbox sync, etc).
    4. MOVE del .exe nuevo a la posicion final (ahora vacia).
    5. Intenta borrar el .old (best-effort; si Dropbox lo tiene lockeado,
       queda en disco hasta el proximo reinicio — no es bloqueante).
    6. Inicia la version nueva.
    7. Si algo falla, dialogo al usuario con el path del archivo nuevo.

    Todo el script corre con WindowStyle Hidden — sin ventana CMD visible.
    Loguea cada paso a %TEMP%\\fotosproforma_update.log para diagnostico.
    """
    import sys
    import os
    import subprocess
    import tempfile
    from pathlib import Path as _Path

    current_exe = _Path(sys.executable).resolve()
    new_exe = _Path(exe_path).resolve()
    old_path = current_exe.with_name(current_exe.stem + ".old")

    # Convertir Path a string Windows-friendly (con backslash).
    # Para PowerShell hay que escapar las comillas simples duplicandolas.
    def _esc(p):
        return str(p).replace("'", "''")
    cur_s = _esc(current_exe)
    new_s = _esc(new_exe)
    old_s = _esc(old_path)
    cur_dir_s = _esc(current_exe.parent)

    fail_msg = (
        f"No pude reemplazar el .exe automaticamente.\n\n"
        f"El archivo nuevo quedo en:\n{new_exe}\n\n"
        "Cerra Fotos Proforma y reemplazalo a mano por el .exe actual."
    )
    fail_msg_ps = fail_msg.replace("'", "''")

    ps = f"""$ErrorActionPreference = 'Continue'
$log = "$env:TEMP\\fotosproforma_update.log"
function Log($m) {{ Add-Content -LiteralPath $log -Value $m }}

Log ""
Log "=== $(Get-Date) ==="
Log "PID a esperar: {os.getpid()}"
Log "Source (nuevo): {new_s}"
Log "Target (actual): {cur_s}"
Log "Backup temporal: {old_s}"

# 1. Esperar al proceso anterior — chequeo activo del PID.
$pid_to_wait = {os.getpid()}
for ($i = 0; $i -lt 60; $i++) {{
    if (-not (Get-Process -Id $pid_to_wait -ErrorAction SilentlyContinue)) {{ break }}
    Start-Sleep -Milliseconds 200
}}
Start-Sleep -Seconds 2  # margen extra por si el handle tarda en soltarse

# 2. Sacar zone identifier del archivo descargado.
Log "Limpiando zone identifier..."
Unblock-File -LiteralPath '{new_s}' -ErrorAction SilentlyContinue

# 3. Borrar cualquier .old viejo de un update anterior.
if (Test-Path -LiteralPath '{old_s}') {{
    Log "Eliminando .old viejo"
    Remove-Item -LiteralPath '{old_s}' -Force -ErrorAction SilentlyContinue
}}

# 4. Renombrar el actual a .old. Estrategia "rename + move" funciona
#    en casos donde el move directo falla (lock de read, Dropbox sync).
$renamed = $false
for ($i = 0; $i -lt 30; $i++) {{
    try {{
        Rename-Item -LiteralPath '{cur_s}' -NewName '{old_path.name}' -Force -ErrorAction Stop
        $renamed = $true
        Log "Rename OK en intento $($i+1)"
        break
    }} catch {{
        if ($i -lt 29) {{
            Log "Rename intento $($i+1) fallo: $($_.Exception.Message)"
            Start-Sleep -Milliseconds 500
        }}
    }}
}}

if (-not $renamed) {{
    Log "FALLO rename despues de 30 intentos"
    Add-Type -AssemblyName PresentationFramework
    [System.Windows.MessageBox]::Show('{fail_msg_ps}', 'Fotos Proforma - Update fallo', 'OK', 'Warning') | Out-Null
    exit 1
}}

# 5. Mover el nuevo a la posicion del original.
$moved = $false
for ($i = 0; $i -lt 20; $i++) {{
    try {{
        Move-Item -LiteralPath '{new_s}' -Destination '{cur_s}' -Force -ErrorAction Stop
        $moved = $true
        Log "Move OK en intento $($i+1)"
        break
    }} catch {{
        Log "Move intento $($i+1) fallo: $($_.Exception.Message)"
        Start-Sleep -Milliseconds 500
    }}
}}

if (-not $moved) {{
    Log "FALLO move despues del rename — intentando revertir"
    # Si rename funciono pero move fallo, revertir para que el usuario
    # tenga al menos el .exe viejo de vuelta.
    Rename-Item -LiteralPath '{old_s}' -NewName '{current_exe.name}' -Force -ErrorAction SilentlyContinue
    Add-Type -AssemblyName PresentationFramework
    [System.Windows.MessageBox]::Show('{fail_msg_ps}', 'Fotos Proforma - Update fallo', 'OK', 'Warning') | Out-Null
    exit 1
}}

# 6. Borrar el .old (best-effort, no critico si falla).
Remove-Item -LiteralPath '{old_s}' -Force -ErrorAction SilentlyContinue
Log "Reemplazo completado"

# 6.5 Sacar el zone identifier del archivo recien movido (Windows
# marca como "bajado de internet" — puede impedir ejecucion automatica
# por SmartScreen sin dialogo visible).
Log "Limpiando zone identifier del exe final..."
Unblock-File -LiteralPath '{cur_s}' -ErrorAction SilentlyContinue

# 7. Iniciar la version nueva — intentamos 3 metodos en secuencia
# porque Start-Process puede fallar silenciosamente con .exe sin
# firmar (SmartScreen). Si todos fallan, MessageBox al usuario.
Log "Iniciando version nueva..."
$launched = $false

# Metodo A: Start-Process con WorkingDirectory explicito.
try {{
    Start-Process -FilePath '{cur_s}' -WorkingDirectory '{cur_dir_s}' -ErrorAction Stop
    $launched = $true
    Log "Launch metodo A (Start-Process) OK"
}} catch {{
    Log "Launch metodo A fallo: $($_.Exception.Message)"
}}

# Metodo B: Invoke-Item (equivalente a doble-click en Explorer).
if (-not $launched) {{
    try {{
        Invoke-Item -LiteralPath '{cur_s}' -ErrorAction Stop
        $launched = $true
        Log "Launch metodo B (Invoke-Item) OK"
    }} catch {{
        Log "Launch metodo B fallo: $($_.Exception.Message)"
    }}
}}

# Metodo C: cmd /c start (mas tolerante).
if (-not $launched) {{
    try {{
        Start-Process -FilePath 'cmd.exe' -ArgumentList '/c','start','""','"{cur_s}"' -WindowStyle Hidden -ErrorAction Stop
        $launched = $true
        Log "Launch metodo C (cmd start) OK"
    }} catch {{
        Log "Launch metodo C fallo: $($_.Exception.Message)"
    }}
}}

if (-not $launched) {{
    Log "FALLO TODOS los launches — avisar al usuario"
    Add-Type -AssemblyName PresentationFramework
    [System.Windows.MessageBox]::Show(
        'La actualizacion termino correctamente pero no pude abrir la version nueva automaticamente.' + "`n`n" +
        'Hace doble click en el icono de Fotos Proforma para abrirla.',
        'Fotos Proforma - Actualizacion lista', 'OK', 'Information'
    ) | Out-Null
}}

# 8. Auto-borrar este script.
Remove-Item -LiteralPath $MyInvocation.MyCommand.Path -Force -ErrorAction SilentlyContinue
"""

    fd, ps_path = tempfile.mkstemp(suffix=".ps1", prefix="fotosproforma_update_")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(ps)

    # Lanzar PowerShell hidden y desacoplado. -WindowStyle Hidden + Popen
    # con DETACHED_PROCESS asegura que no aparezca ninguna ventana.
    CREATE_NEW_PROCESS_GROUP = 0x00000200
    DETACHED_PROCESS         = 0x00000008
    CREATE_NO_WINDOW         = 0x08000000
    subprocess.Popen(
        [
            "powershell.exe",
            "-NoProfile",
            "-WindowStyle", "Hidden",
            "-ExecutionPolicy", "Bypass",
            "-File", ps_path,
        ],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        creationflags=CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS | CREATE_NO_WINDOW,
    )
    return True


# Smoke tests cuando se corre directo.
if __name__ == "__main__":
    tests = [
        ("v1.1",   "1.0",   True),
        ("v1.0",   "v1.0",  False),
        ("1.0",    "1.0.1", False),
        ("1.0.1",  "1.0",   True),
        ("v2.0",   "1.5.3", True),
        ("v1.0",   "",      False),  # local invalido
        ("",       "1.0",   False),  # remote invalido
    ]
    for r, l, expected in tests:
        got = is_newer(r, l)
        status = "OK " if got == expected else "FAIL"
        print(f"  [{status}] is_newer({r!r}, {l!r}) = {got} (expected {expected})")

    print()
    print("=== Live test contra GitHub API ===")
    info = check_latest_release()
    if info is None:
        print("  (no hay release publicada o la API no respondio)")
    else:
        print(f"  Ultimo release: {info['tag_name']}")
        print(f"  URL: {info['html_url']}")
