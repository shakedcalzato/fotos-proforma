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
