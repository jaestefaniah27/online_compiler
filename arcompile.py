#!/usr/bin/env python3

import os, sys, subprocess, shlex, glob, json, time, shutil, hashlib
from pathlib import Path
import serial.tools.list_ports

# ======== CONFIGURACIÓN ========
REMOTE      = "minecraft_server"             # alias ssh
REMOTE_DIR  = "/home/ubuntu/compilacion_esp32"
FQBN        = "esp32:esp32:esp32da"
BAUD        = 921600                         # velocidad esptool
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
    if not f.exists():
        return []
    return [l.strip() for l in f.read_text(encoding="utf8").splitlines() if l.strip()]

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
    #run(f"ssh {REMOTE} rm -f {remote_proj}/compile.py")

def compilar_en_servidor(remote_proj, libs):
    compile_cmd = (
        f"ssh {REMOTE} /usr/local/bin/arduino-cli compile "
        f"--fqbn {FQBN} {remote_proj} --export-binaries"
    )

    for intento in (1, 2):
        code, out, err = run_capture(compile_cmd)
        if code == 0:
            print("✓ Compilación exitosa")
            return
        if intento == 1 and "No such file or directory" in err:
            print("⚠ Faltan librerías → se instalan y se reintenta …")
            instalar_librerias(libs)
            continue
        print(out + err)
        sys.exit("❌ Compilación abortada")

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

    # buscar boot_app0.bin en servidor y descargarlo también
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
        compilar_en_servidor(remote_proj, libs)
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

    # ---------- flasheo optimizado ----------
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
