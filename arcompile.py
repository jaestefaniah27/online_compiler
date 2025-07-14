#!/usr/bin/env python3

import os
import sys
import subprocess
import shlex
import time
import shutil
import hashlib
from pathlib import Path
import serial.tools.list_ports
from arcompile_version import __version__ as VERSION

# ======== CONFIGURACIÓN ========
REMOTE      = "minecraft_server"
REMOTE_DIR  = "/home/ubuntu/compilacion_esp32"
FQBN        = "esp32:esp32:esp32da"
BAUD        = 921600
MAX_SIZE    = 1310720   # 1.3 MB
# ===============================

def run(cmd, **kw):
    print(f"» {cmd}")
    subprocess.run(cmd, shell=True, check=True, **kw)

def run_capture(cmd):
    p = subprocess.run(cmd, shell=True, text=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return p.returncode, p.stdout, p.stderr

def puerto_esp32():
    print("🔍 Buscando puerto ESP32 …")
    for p in serial.tools.list_ports.comports():
        if any(t in p.description for t in ("CP210", "Silicon", "USB", "ESP32")):
            print(f"✔ Detectado {p.device}")
            return p.device
    sys.exit("❌ ESP32 no encontrada")

def leer_libraries():
    f = Path("libraries.txt")
    return [l.strip() for l in f.read_text(encoding="utf8").splitlines() if l.strip()] if f.exists() else []

def instalar_librerias(libs):
    if not libs:
        return
    print("• Instalando/actualizando librerías en servidor …")
    for lib in libs:
        run(f"ssh {REMOTE} arduino-cli lib install {shlex.quote(lib)} --no-overwrite")

def subir_proyecto(remote_proj):
    run(f"ssh {REMOTE} rm -rf {shlex.quote(remote_proj)}")
    run(f"ssh {REMOTE} mkdir -p {shlex.quote(remote_proj)}")
    run(f"scp -r * {REMOTE}:{remote_proj}/")

def mostrar_ayuda():
    print(f"""
arcompile v{VERSION}

Uso:
  arcompile              → Compila y flashea el proyecto automáticamente
  arcompile min_spiffs   → Fuerza el uso del esquema de particiones min_spiffs
  arcompile help         → Muestra esta ayuda
  arcompile update       → Actualiza arcompile a la última versión

""")
    sys.exit(0)

def realizar_update():
    print("🔄 Actualizando arcompile …")
    run("pip uninstall -y arcompile")
    run("pip install --no-cache-dir --force-reinstall git+https://github.com/jaestefaniah27/online_compiler.git")
    print("✅ arcompile actualizado")
    sys.exit(0)

def compilar_en_servidor(remote_proj, libs, particion=None):
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
            return out + err
        if intento == 1 and "No such file or directory" in err:
            print("⚠ Faltan librerías → instalando y reintentando …")
            instalar_librerias(libs)
            continue
        print(out + err)
        sys.exit("❌ Compilación abortada")

def binario_excede_tamano(salida):
    for linea in salida.splitlines():
        if "Sketch uses" in linea and "Maximum is" in linea:
            try:
                usado = int(linea.split("Sketch uses")[1].split("bytes")[0].strip().replace(",", ""))
                print(f"• Binario ocupa {usado} bytes")
                return usado > MAX_SIZE
            except:
                pass
    return False

def descargar_binarios(remote_proj, build_remote, sketch_base):
    out_dir = Path("binarios")
    out_dir.mkdir(exist_ok=True)

    # Descarga todos los .bin generados
    run(f"ssh {REMOTE} 'ls {build_remote}/*.bin'")  # para debug
    run(f"scp {REMOTE}:{build_remote}/*.bin binarios/")

    local_files = {}
    for archivo in out_dir.glob("*.bin"):
        nombre = archivo.name.lower()
        if "bootloader" in nombre:
            local_files["bootloader"] = archivo
        elif "partition" in nombre:
            local_files["partitions"] = archivo
        elif "app0" in nombre:
            local_files["boot_app0"] = archivo
        elif nombre.endswith(".ino.bin"):
            local_files["application"] = archivo

    # Asegura boot_app0
    if "boot_app0" not in local_files:
        print("• Descargando boot_app0.bin …")
        ruta = subprocess.check_output(
            f"ssh {REMOTE} find ~/.arduino15 -name boot_app0.bin | head -n1",
            shell=True, text=True
        ).strip()
        if ruta:
            run(f"scp {REMOTE}:{ruta} binarios/boot_app0.bin")
            local_files["boot_app0"] = out_dir / "boot_app0.bin"
        else:
            sys.exit("❌ No se encontró boot_app0.bin en el servidor")

    print("✅ Binarios descargados en ./binarios/")
    return local_files

def hash_proyecto():
    sha = hashlib.sha256()
    for path in sorted(Path().rglob("*")):
        if path.is_file() and path.suffix in {".ino", ".cpp", ".h", ".txt"}:
            sha.update(path.read_bytes())
    return sha.hexdigest()

def main():
    args = [a.lower() for a in sys.argv[1:]]

    # 1) Help
    if any(a in ("help", "-h", "--help") for a in args):
        mostrar_ayuda()

    # 2) Update
    if "update" in args:
        realizar_update()

    # 3) Forzar partición si lo pide el usuario
    particion = "min_spiffs" if "min_spiffs" in args else None

    # 4) Preparar paths
    sketch_dir  = Path.cwd()
    sketch_name = sketch_dir.name + ".ino"
    ino_path    = sketch_dir / sketch_name
    if not ino_path.exists():
        sys.exit(f"❌ No se encontró {sketch_name}")

    # 5) Detectar bibliotecas y puerto serie
    libs = leer_libraries()
    com  = puerto_esp32()

    # 6) Compilación condicional por hash
    hash_actual = hash_proyecto()
    hash_file   = Path(".build_hash")
    hash_anterior = hash_file.read_text() if hash_file.exists() else ""

    if hash_actual != hash_anterior:
        print("🛠 Compilación necesaria")
        remote_proj = f"{REMOTE_DIR}/{sketch_dir.name}"
        subir_proyecto(remote_proj)

        salida = compilar_en_servidor(remote_proj, libs, particion)

        # Si no forzó y excede tamaño, reintentar con min_spiffs
        if not particion and binario_excede_tamano(salida):
            print("⚠ Binario >1.3MB → reintentando con min_spiffs")
            salida = compilar_en_servidor(remote_proj, libs, "min_spiffs")
            particion = "min_spiffs"

        build_remote = f"{remote_proj}/build/{FQBN.replace(':','.')}"
        bin_files    = descargar_binarios(remote_proj, build_remote, sketch_dir.name)
        hash_file.write_text(hash_actual)
    else:
        print("⚡ Usando binarios ya compilados")
        # Asume ya están en ./binarios
        out_dir = Path("binarios")
        bin_files = {
            "bootloader":   out_dir / f"{sketch_dir.name}.ino.bootloader.bin",
            "partitions":   out_dir / f"{sketch_dir.name}.ino.partitions.bin",
            "application":  out_dir / f"{sketch_dir.name}.ino.bin",
            "boot_app0":    out_dir / "boot_app0.bin",
        }
        for k, f in bin_files.items():
            if not f.exists():
                sys.exit(f"❌ Falta el binario requerido: {f}")

    # 7) Flasheo
    esptool = shutil.which("esptool.py") or f"{sys.executable} -m esptool"
    flash_cmd = (
        f"{esptool} --chip esp32 --port {com} --baud {BAUD} write_flash -z "
        f"0x1000 {bin_files['bootloader']} "
        f"0x8000 {bin_files['partitions']} "
        f"0xe000 {bin_files['boot_app0']} "
        f"0x10000 {bin_files['application']}"
    )
    run(flash_cmd)

    print(f"✅ Terminado en {time.time() - start:.1f} s")

if __name__ == "__main__":
    start = time.time()
    main()
