# 🛠️ arcompile — Compilador remoto para ESP32

Este proyecto permite compilar y cargar sketches de Arduino para ESP32 desde cualquier ordenador, utilizando un servidor remoto con `arduino-cli`. Ideal para acelerar el desarrollo cuando tu PC es lento al compilar.

## 🚀 Instalación rápida

Asegúrate de tener Python ≥ 3.6 y `pip` instalado. Luego ejecuta:

```bash
pip install git+https://github.com/jaestefaniah27/online_compiler.git
```

Si quieres desinstalarlo, ejecuta esto:

```bash
pip uninstall arcompile
```

Para actualizar/reinstalar:

```bash
pip uninstall -y arcompile && pip install --no-cache-dir git+https://github.com/jaestefaniah27/online_compiler.git
```

Si ya lo tenías y quieres actualizar a la última versión:

```bash
arcompile update
```

---

### Comandos disponibles

| Comando                  | Descripción                                                                                          |
| ------------------------ | ----------------------------------------------------------------------------------------------------- |
| `arcompile`            | Compila tu sketch actual y, si cambia, lo sube al servidor, descarga los binarios y lo flashea.       |
| `arcompile min_spiffs` | Fuerza el uso del esquema de particiones **minimal + SPIFFS **(útil si tu firmware supera 1.3 MB). |
| `arcompile help`       | Muestra esta guía de uso en la consola.                                                              |
| `arcompile update`     | Comprueba la última versión y, si existe, desinstala la antigua e instala la nueva.                 |

---

### Ejemplos

1. **Compilación y flasheo estándar**

   ```bash
   cd MiProyectoESP32
   arcompile
   ```

   * Detecta el puerto serie (p.ej. `COM3` o `/dev/ttyUSB0`)
   * Sube tu sketch al servidor remoto
   * Compila y descarga los `.bin` a `./binarios/`
   * Flashea el ESP32 en serie
2. **Forzar particiones pequeñas + SPIFFS**

   ```bash
   arcompile min_spiffs
   ```

   Útil cuando el compilado estándar da error “Sketch too big”.
3. **Ver la ayuda detallada**

   ```bash
   arcompile help
   ```

   Muestra un resumen de todos los comandos y opciones disponibles.
4. **Actualizar `arcompile`**

   ```bash
   arcompile update
   ```

   Descarga e instala la última versión desde GitHub.

---

### ¿Cómo funciona por debajo?

* **Detección de cambios** : sólo recompila si tu sketch ha cambiado (hash de archivos `.ino`, `.cpp`, `.h` o `.txt`).
* **Particiones OTA** : si tu binario supera 1.3 MB, reintenta automáticamente con `min_spiffs`.
* **Descarga masiva de binarios** : copia cada `.bin` generado al directorio local `./binarios/`.
* **Flasheo optimizado** : usa `esptool.py` para escribir sólo los sectores necesarios (bootloader, particiones, aplicación).

---

Con estos comandos tendrás un flujo de trabajo rápido y fiable para desarrollar en ESP32 sin complicaciones. ¡A compilar se ha dicho!
