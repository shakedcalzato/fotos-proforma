# Cómo actualizar la app

Cada vez que se hace `git push` a `main`, GitHub Actions compila
automáticamente:

- **`FotosProforma.exe`** — para Cyndi (Windows)
- **`FotosProforma.app`** (dentro de un `.zip`) — para Kobi (Mac Apple Silicon)

Los dos quedan disponibles para descargar durante 90 días desde la pestaña
**Actions** del repo.

URL de Actions: https://github.com/shakedcalzato/fotos-proforma/actions

---

## Cómo conseguir el .exe de Windows (para Cyndi)

1. Abrí https://github.com/shakedcalzato/fotos-proforma/actions
2. Hacé click en el último run del workflow **"Build Windows EXE"**
   (el de arriba con check verde ✅).
3. Bajá hasta la sección **Artifacts** al final de la página.
4. Click en **`FotosProforma-Windows`** → baja un `.zip`.
5. Descomprimí → adentro está **`FotosProforma.exe`**.
6. Mandale el `.exe` a Cyndi (Dropbox, Drive, o WhatsApp).
7. Cyndi reemplaza el `.exe` viejo del escritorio por el nuevo y listo.

### Primera vez en Windows (SmartScreen)

La primera vez que Cyndi abra el `.exe`, Windows va a mostrar una
ventana azul "Windows protegió tu PC". Es porque el binario no está
firmado con un certificado de Microsoft (cuesta caro y no hace falta).

Pasos:
- Click en **"Más información"**.
- Click en **"Ejecutar de todas formas"**.

Eso queda recordado: no vuelve a aparecer para el mismo `.exe`.

---

## Cómo conseguir el .app de Mac (para Kobi)

1. Abrí https://github.com/shakedcalzato/fotos-proforma/actions
2. Hacé click en el último run del workflow **"Build Mac .app"**
   (el de arriba con check verde ✅).
3. Bajá hasta la sección **Artifacts** al final de la página.
4. Click en **`FotosProforma-Mac`** → baja un `.zip` (ej. `FotosProforma-Mac.zip`
   que contiene otro `.zip` adentro). GitHub mete los artifacts dentro de un
   zip propio, así que vas a tener un zip-dentro-de-zip.
5. Descomprimí el zip exterior → vas a tener `FotosProforma-Mac.zip`.
6. Descomprimí ese también (doble click) → aparece `FotosProforma.app`.
7. Subilo a la carpeta de Dropbox que uses con Kobi (NO mandes por WhatsApp:
   WhatsApp puede corromper el bundle al comprimirlo otra vez).

### Cómo Kobi instala el .app

1. En Dropbox, baja `FotosProforma.app` a su Mac.
2. Lo arrastra a `~/Applications/` (su carpeta de Aplicaciones del usuario)
   o a `/Applications/` (global).
3. **Primera vez (importante):** la app no está firmada con certificado
   de Apple Developer ($99/año, no lo pagamos). Si Kobi le hace doble
   click directo, macOS la va a bloquear con un mensaje tipo
   *"No se puede abrir porque Apple no puede comprobar que esté libre
   de malware"*.

   **Solución (solo la primera vez):**
   - Click DERECHO sobre `FotosProforma.app` → **"Abrir"**.
   - Aparece otro popup, ahora con un botón **"Abrir"** habilitado.
   - Click en **"Abrir"**.
   - La app arranca y queda autorizada para siempre. Las próximas veces
     doble click normal funciona.

4. Si en macOS Sequoia el doble click directo no da la opción de "Abrir"
   ni con click derecho, ir a:
   - **Ajustes del Sistema** → **Privacidad y Seguridad**.
   - Bajar hasta el final, va a haber un mensaje
     *"Se bloqueó FotosProforma porque no se pudo verificar..."*.
   - Click en **"Abrir de todas formas"**.

---

## Cómo forzar un build sin hacer push

Si querés generar `.exe` o `.app` sin tocar código (ej. para entregar
una versión limpia recién compilada):

1. Andá a https://github.com/shakedcalzato/fotos-proforma/actions
2. Click en el workflow del lado izquierdo: **"Build Windows EXE"** o
   **"Build Mac .app"**.
3. Botón **"Run workflow"** arriba a la derecha → seleccionar branch
   `main` → **"Run workflow"** verde.
4. Esperá a que termine (~3–5 min) y bajá el artifact como en las
   instrucciones de arriba.

---

## Si el build falla

- Click en el run con la X roja ❌ para ver el log.
- El error suele estar en el último paso ejecutado.
- Avisame con el mensaje de error o un screenshot del log y lo miramos.
