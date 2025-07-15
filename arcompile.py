#!/usr/bin/env python3

import os
import sys
import subprocess
import shlex
import time
import atexit
import shutil
import hashlib
from pathlib import Path

import requests
import serial.tools.list_ports
from arcompile_version import __version__ as VERSION

# ======== CONFIGURACIÓN ========
REMOTE             = "minecraft_server"
REMOTE_DIR         = "/home/ubuntu/compilacion_esp32"
FQBN               = "esp32:esp32:esp32da"
BAUD               = 921600
MAX_SIZE           = 1310720   # 1.3 MB
REPO_VERSION_URL   = "https://raw.githubusercontent.com/jaestefaniah27/online_compiler/main/arcompile_version.py"
# Estimación basada en líneas de código (en segundos por línea)
TIME_PER_LINE      = 0.02
# Archivos de log
COMPILE_LOG        = Path("compile.log")
ERROR_LOG          = Path("error.log")
# ===============================

# Tiempo de inicio para cálculo de elapsed
_start_time = time.time()

@atexit.register
def _print_elapsed():
    elapsed = time.time() - _start_time
    print(f"⏱ Tiempo transcurrido: {elapsed:.1f} s")


def run(cmd, **kw):
    print(f"» {cmd}")
    try:
        subprocess.run(cmd, shell=True, check=True, **kw)
    except subprocess.CalledProcessError as e:
        # Guardar stderr si está disponible
        if hasattr(e, 'stderr') and e.stderr:
            ERROR_LOG.write_text(e.stderr, encoding='utf8')
        raise


def run_capture(cmd):
    p = subprocess.run(cmd, shell=True, text=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    # Registrar errores en ERROR_LOG
    if p.stderr:
        ERROR_LOG.write_text(p.stderr, encoding='utf8')
    return p.returncode, p.stdout, p.stderr


def puerto_esp32():
    print("🔍 Buscando puerto ESP32 …")
    for p in serial.tools.list_ports.comports():
        if any(t in p.description for t in ("CP210", "Silicon", "USB", "ESP32")):
            print(f"✔ Detectado {p.device}")
            return p.device
    sys.exit("❌ ESP32 no encontrada")

# ... resto del código permanece igual hasta compilar_en_servidor

def compilar_en_servidor(remote_proj, libs, particion=None):
    # Limpiar error log antes de compilar
    ERROR_LOG.write_text("", encoding='utf8')
    # Mostrar estimación antes de compilar
    estimar_tiempo()
    print("🏗 Iniciando compilación")
    fqbn = FQBN
    if particion:
        print(f"• Forzando particiones: {particion}")
        fqbn = f"{FQBN}:PartitionScheme={particion}"

    compile_cmd = (
        f"ssh {REMOTE} /usr/local/bin/arduino-cli compile "
        f"--fqbn {shlex.quote(fqbn)} "
        f"{shlex.quote(remote_proj)} --export-binaries"
    )

    for intento in (1, 2):
        code, out, err = run_capture(compile_cmd)
        if code == 0:
            print("✓ Compilación exitosa")
            return fqbn, out + err
        if intento == 1 and "No such file or directory" in err:
            print("⚠ Faltan librerías → instalando y reintentando …")
            instalar_librerias(libs)
            continue
        print(out + err)
        # En caso de abortar, err ya grabado, salimos
        sys.exit("❌ Compilación abortada")

# ... resto del código sin cambios

def main():
    start = time.time()
    args = [a.lower() for a in sys.argv[1:]]

    if any(a in ("help", "-h", "--help") for a in args):
        mostrar_ayuda()

    if "update" in args:
        realizar_update()

    particion = "min_spiffs" if "min_spiffs" in args else None

    sketch_dir  = Path.cwd()
    sketch_name = sketch_dir.name + ".ino"
    ino_path    = sketch_dir / sketch_name
    if not ino_path.exists():
        sys.exit(f"❌ No se encontró {sketch_name}")

    libs = leer_libraries()
    com  = puerto_esp32()

    hash_actual   = hash_proyecto()
    hash_file     = Path(".build_hash")
    hash_anterior = hash_file.read_text() if hash_file.exists() else ""

    if hash_actual != hash_anterior:
        print("🛠 Compilación necesaria")
        remote_proj = f"{REMOTE_DIR}/{sketch_dir.name}"
        subir_proyecto(remote_proj)

        used_fqbn, salida = compilar_en_servidor(remote_proj, libs, particion)

        if not particion and binario_excede_tamano(salida):
            print("⚠ Binario >1.3MB → reintentando con min_spiffs")
            used_fqbn, salida = compilar_en_servidor(remote_proj, libs, "min_spiffs")
            particion = "min_spiffs"

        # Guardar salida de compilación en archivo log
        COMPILE_LOG.write_text(salida, encoding="utf8")
        print(f"ℹ Salida de compilación guardada en {COMPILE_LOG}")

        # ... continuar con descarga de binarios etc.

    # ... resto del main

if __name__ == "__main__":
    main()
