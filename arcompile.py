#!/usr/bin/env python3

import os, sys, subprocess, shlex, glob, json, time, shutil, hashlib
from pathlib import Path
import serial.tools.list_ports
import urllib.request

# ======== CONFIGURACIÓN ========
REMOTE      = "minecraft_server"
REMOTE_DIR  = "/home/ubuntu/compilacion_esp32"
FQBN        = "esp32:esp32:esp32da"
BAUD        = 921600
MAX_SIZE    = 1310720
PACKAGE     = "arcompile"
REPO_URL    = "https://raw.githubusercontent.com/jaestefaniah27/online_compiler/main/arcompile.py"
VERSION     = "1.0.3"
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
Uso:
  arcompile              → compila y flashea el proyecto automáticamente
  arcompile min_spiffs   → fuerza el uso del esquema de particiones min_spiffs
  arcompile help         → muestra esta ayuda
  arcompile update       → actualiza arcompile si hay una nueva versión

Versión instalada: {VERSION}
""")
    sys.exit(0)

def check_update():
    try:
        with urllib.request.urlopen(REPO_URL) as resp:
            content = resp.read().decode("utf-8")
        for line in content.splitlines():
            if "VERSION" in line and "=" in line:
                latest = line.split("=")[1].strip().strip('"').strip("'")
                break
        else:
            print("⚠ No se pudo obtener la versión remota.")
            return
        if latest != VERSION:
            print(f"📦 Nueva versión disponible: {latest} → Actualizando …")
            run(f"pip install --upgrade --no-cache-dir git+https://github.com/jaestefaniah27/online_compiler.git")
        else:
            print("✔ Ya tienes la última versión instalada.")
    except Exception as e:
        print(f"❌ Error al verificar la versión: {e}")
    sys.exit(0)

def compilar_en_servidor(remote_proj, libs, particion=None):
    print("🏗️  Iniciando compilación")
    props = f"--build-property build.partitions={particion}" if particion else ""
    if particion:
        print(f"• Usando partición: {particion}")

    compile_cmd = (
        f"ssh {REMOTE} /usr/local/bin/arduino-cli compile "
        f"--fqbn {FQBN} {remote_proj} --export-binaries {props}"
    )

    for intento in (1, 2):
        code, out, err = run_capture(compile_cmd)
        if code == 0:
            print("✓ Compilación exitosa")
            return out + err
        if intento == 1 and "No such file or directory" in err:
            print("⚠ Faltan librerías → se instalan y se reintenta …")
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
            except Exception:
                pass
    return False

def descargar_binarios(remote_proj, build_remote, sketch_base):
    nombres = {
        "bootloader":   f"{sketch_base}.ino.bootloader.bin",
        "partitions":   f"{sketch_base}.ino.partitions.bin",
        "application":  f"{sketch_base}.ino.bin",
    }
    local_files = {}

    for key, fname in nombres.items():
        remote_path = f"{build_remote}/{fname}"
        local_path = Path(fname)
        run(f"scp {REMOTE}:{remote_path} \"{local_path}\"")
        local_files[key] = local_path

    boot_app0_local = Path("boot_app0.bin")
    if not boot_app0_local.exists():
        print("• Descargando boot_app0.bin …")
        out = subprocess.check_output(
            f"ssh {REMOTE} find ~/.arduino15 -name boot_app0.bin | head -n1",
            shell=True, text=True
        ).strip()
        if out:
            run(f"scp {REMOTE}:{out} \"{boot_app0_local}\"")
        else:
            sys.exit("❌ No se encontró boot_app0.bin en el servidor")
    local_files["boot_app0"] = boot_app0_local

    return local_files

def hash_proyecto():
    sha = hashlib.sha256()
    for path in sorted(Path().rglob("*")):
        if path.is_file() and path.suffix in {".ino", ".cpp", ".h", ".txt"}:
            sha.update(path.read_bytes())
    return sha.hexdigest()

def main():
    args = [arg.lower() for arg in sys.argv[1:]]
    particion = None
    if not args:
        pass  # comportamiento normal
    elif "help" in args or "-h" in args or "--help" in args:
        mostrar_ayuda()
    elif "update" in args:
        check_update()
    elif "min_spiffs" in args:
        particion = "min_spiffs"
    else:
        particion = None

    inicio = time.time()
    sketch_dir   = Path.cwd()
    sketch_name  = sketch_dir.name + ".ino"
    ino_path     = sketch_dir / sketch_name

    if not ino_path.exists():
        sys.exit(f"❌ No se encontró {sketch_name}")

    libs = leer_libraries()
    com  = puerto_esp32()

    hash_actual = hash_proyecto()
    hash_file = Path(".build_hash")
    hash_anterior = hash_file.read_text() if hash_file.exists() else ""

    if hash_actual != hash_anterior:
        print("🛠  Compilación necesaria")
        remote_proj  = f"{REMOTE_DIR}/{sketch_dir.name}"
        subir_proyecto(remote_proj)
        salida = compilar_en_servidor(remote_proj, libs, particion)

        if not particion and binario_excede_tamano(salida):
            print("⚠ El binario excede 1.3MB → Reintentando con partición min_spiffs …")
            salida = compilar_en_servidor(remote_proj, libs, "min_spiffs")
            particion = "min_spiffs"

        build_remote = f"{remote_proj}/build/{FQBN.replace(':','.')}"
        bin_files = descargar_binarios(remote_proj, build_remote, sketch_dir.name)
        hash_file.write_text(hash_actual)
    else:
        print("⚡ Usando binarios ya compilados")
        bin_files = {
            "bootloader":   Path(f"{sketch_dir.name}.ino.bootloader.bin"),
            "partitions":   Path(f"{sketch_dir.name}.ino.partitions.bin"),
            "application":  Path(f"{sketch_dir.name}.ino.bin"),
            "boot_app0":    Path("boot_app0.bin"),
        }
        for f in bin_files.values():
            if not f.exists():
                sys.exit(f"❌ Falta el binario requerido: {f}")

    esptool = shutil.which("esptool.py") or f"{sys.executable} -m esptool"
    flash_cmd = (
        f"{esptool} --chip esp32 --port {com} --baud {BAUD} write_flash -z "
        f"0x1000 {bin_files['bootloader']} "
        f"0x8000 {bin_files['partitions']} "
        f"0xe000 {bin_files['boot_app0']} "
        f"0x10000 {bin_files['application']}"
    )
    run(flash_cmd)

    print(f"✅ Terminado en {time.time() - inicio:.1f} s")

if __name__ == "__main__":
    main()
