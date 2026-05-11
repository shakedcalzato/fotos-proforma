# Fotos Proforma

App de escritorio para automatizar el armado de carpetas de fotos para
WhatsApp después de cerrar una proforma con un cliente mayorista.

Carga el PDF de la proforma generada por Pepperi o SAP Business One,
busca las fotos correspondientes a las referencias en una estructura
de Dropbox, y arma una carpeta lista para mandar al cliente.

## Características

- **Soporta 3 formatos de PDF**: Pepperi (Off-line Preview), SAP Business
  One (Proforma de Cliente), SAP Business One (Cotización de Cliente).
- **3 modos de búsqueda**:
  - *Solo grupales*: una grupal por referencia.
  - *Solo individuales*: una individual por cada color pedido.
  - *Grupal si está completa*: grupal cuando se pidieron todos los
    colores existentes; individuales cuando faltan algunos.
- **Procesamiento batch**: una o varias proformas a la vez.
- **Detecta surtidos** (códigos `SURT`, `MIX`, `SUR`, etc.) → grupal automática.
- **Fuzzy match** para resolver SKUs ambiguos (color abreviado en proforma
  vs. nombre largo en el archivo).
- **Drag & drop**: arrastrá un PDF al ícono y abre con esa proforma cargada.
- **Settings persistentes** entre sesiones.
- **Notificación nativa de macOS** al terminar.
- **Click sobre faltante** copia el SKU al portapapeles.

## Stack

- Python 3.11+
- tkinter (widgets clásicos, no ttk)
- pdfplumber para parsear PDFs

## Requisitos

- macOS (testeado en Sequoia 15.x)
- Python 3.11 o superior
- Dropbox sincronizado con la estructura:
  ```
  Dropbox/GRUPALES/[año]/[MARCA]/<archivo>.jpg
  Dropbox/INDIVIDUALES/[año]/[MARCA]/<archivo>.jpg
  ```

## Cómo correr en desarrollo

```bash
pip install -r requirements.txt
python3 main.py
```

## Build de Windows

Cada push a `main` dispara un workflow de GitHub Actions que compila
un `.exe` de Windows con PyInstaller. Ver `.github/workflows/build-windows.yml`.

## Regla de oro

La app **NUNCA modifica** la carpeta de Dropbox: solo lee. Las copias
salen DESDE Dropbox HACIA `~/Desktop/Fotos de Proformas/<Cliente>/`.
Hay un guard en `processor._is_under_dropbox()` que blinda este invariante.
