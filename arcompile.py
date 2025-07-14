#!/usr/bin/env python3

import os, sys, subprocess, shlex, glob, json, time, shutil, hashlib
from pathlib import Path
import serial.tools.list_ports

REMOTE      = "minecraft_server"
REMOTE_DIR  = "/home/ubuntu/compilacion_esp32"
FQBN        = "esp32:esp32:esp32da"
BAUD        = 921600

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
        run(f'ssh {REMOTE} "bash -c \'export PATH=\\\"$HOME/bin:$PATH\\\"; arduino-cli lib install {lib} --no-overwrite\'"')

def subir_proyecto(remote_proj):
    run(f"ssh {REMOTE} rm -rf {shlex.quote(remote_proj)}")
    run(f"ssh {REMOTE} mkdir -p {shlex.quote(remote_proj)}")
    run(f"scp -r * {REMOTE}:{remote_proj}/")

def compilar_en_servidor(remote_proj, libs):
    partition = "min_spiffs"
    compile_cmd = (
        f'ssh {REMOTE} "export PATH=\\\"$HOME/bin:$PATH\\\" && '
        f'arduino-cli compile --fqbn {FQBN} {remote_proj}"'
    )
    for intento in (1, 2):
        code, out, err = run_capture(compile_cmd)
        if code == 0:
            print("✓ Compilación exitosa")
            return partition
        if intento == 1 and "No such file or directory" in err:
            print("⚠ Faltan librerías → se instalan y se reintenta …")
            instalar_librerias(libs)
            continue
        print(out + err)
        sys.exit("❌ Compilación abortada")

def descargar_binarios(remote_proj, build_remote):
    print("📥 Descargando archivos .bin compilados …")
    output_dir = Path("binarios")
    output_dir.mkdir(exist_ok=True)

    # listar todos los binarios en el servidor
    cmd = f"ssh {REMOTE} 'find {build_remote} -name \"*.bin\"'"
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if result.returncode != 0:
        sys.exit("❌ No se pudo listar binarios en el servidor")

    bin_paths = result.stdout.strip().splitlines()
    if not bin_paths:
        sys.exit("❌ No se encontraron archivos .bin en el servidor")

    downloaded = []
    for path in bin_paths:
        fname = Path(path).name
        local_path = output_dir / fname
        run(f"scp {REMOTE}:{shlex.quote(path)} \"{local_path}\"")
        downloaded.append(local_path)

    print(f"✅ Todos los archivos .bin fueron descargados en la carpeta '{output_dir}/'")
    return downloaded

def hash_proyecto():
    sha = hashlib.sha256()
    for path in sorted(Path().rglob("*")):
        if path.is_file() and path.suffix in {".ino", ".cpp", ".h", ".txt"}:
            sha.update(path.read_bytes())
    return sha.hexdigest()

def main():
    inicio = time.time()

    sketch_dir  = Path.cwd()
    sketch_name = sketch_dir.name + ".ino"
    ino_path    = sketch_dir / sketch_name

    if not ino_path.exists():
        sys.exit(f"❌ No se encontró {sketch_name}")

    libs = leer_libraries()
    com  = puerto_esp32()
    hash_actual = hash_proyecto()
    hash_file = Path(".build_hash")
    hash_anterior = hash_file.read_text() if hash_file.exists() else ""

    if hash_actual != hash_anterior:
        print("🛠  Compilación necesaria")
        remote_proj = f"{REMOTE_DIR}/{sketch_dir.name}"
        subir_proyecto(remote_proj)
        partition = compilar_en_servidor(remote_proj, libs)
        build_remote = f"{remote_proj}/build/{FQBN.replace(':','.')}"
        bin_files = descargar_binarios(remote_proj, build_remote)
        hash_file.write_text(hash_actual)
    else:
        print("⚡ Usando binarios ya compilados")
        bin_files = list(Path("binarios").glob("*.bin"))
        if not bin_files:
            sys.exit("❌ No se encontraron binarios locales en la carpeta 'binarios'")

    print(f"✅ Finalizado en {time.time() - inicio:.1f} s")

if __name__ == "__main__":
    main()
