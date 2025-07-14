import os
import sys
import shlex
import subprocess
from pathlib import Path

VERSION = "1.2.0"
REMOTE = "minecraft_server"

def run(cmd, **kw):
    print(f"» {cmd}")
    subprocess.run(cmd, shell=True, check=True, **kw)

def mostrar_ayuda():
    print(f"""\
arcompile v{VERSION}
Uso:
  arcompile                    → Compila normalmente
  arcompile min_spiffs        → Usa partición min_spiffs si supera 1.3MB o la fuerzas
  arcompile help              → Muestra esta ayuda
  arcompile update            → Actualiza arcompile desde el repositorio
""")
    sys.exit(0)

def actualizar_arcompile():
    print("🔄 Buscando última versión de arcompile...")
    url_repo = "https://github.com/jaestefaniah27/online_compiler.git"
    try:
        subprocess.run([sys.executable, "-m", "pip", "uninstall", "-y", "arcompile"], check=True)
        subprocess.run([sys.executable, "-m", "pip", "install", "--no-cache-dir", f"git+{url_repo}"], check=True)
        print("✅ arcompile actualizado correctamente.")
    except subprocess.CalledProcessError:
        print("❌ Error actualizando arcompile.")
    sys.exit(0)

def instalar_librerias(libs):
    if not libs:
        return
    print("• Instalando/actualizando librerías en servidor …")
    for lib in libs:
        run(f"ssh {REMOTE} 'export PATH=\"$HOME/bin:$PATH\" && arduino-cli lib install {shlex.quote(lib)} --no-overwrite'")

def compilar_en_servidor(remote_proj, libs, particion=None):
    fqbn = "esp32:esp32:esp32da"
    build_cmd = f"arduino-cli compile --fqbn {fqbn} "

    if particion:
        build_cmd += f"--build-property build.partitions={particion} "

    build_cmd += "--export-binaries"

    remote_cmd = f'cd "{remote_proj}" && export PATH="$HOME/bin:$PATH" && {build_cmd} .'

    try:
        run(f"ssh {REMOTE} {shlex.quote(remote_cmd)}")
    except subprocess.CalledProcessError:
        if not particion:
            print("⚠ Sketch excede los 1.3MB. Reintentando con partición min_spiffs …")
            return compilar_en_servidor(remote_proj, libs, particion="min_spiffs")
        raise

    return particion or "default"

def descargar_binarios(remote_proj):
    build_path = f"{remote_proj}/build"
    local_dir = Path("binarios")
    local_dir.mkdir(exist_ok=True)

    # Buscar binarios en el subdirectorio más reciente
    result = subprocess.run(f"ssh {REMOTE} 'find {build_path} -type f -name \"*.bin\"'", shell=True, capture_output=True, text=True)
    archivos = result.stdout.strip().splitlines()

    bin_files = {}
    for remote_path in archivos:
        nombre = Path(remote_path).name
        local_path = local_dir / nombre
        run(f"scp {REMOTE}:{remote_path} \"{local_path}\"")
        key = nombre.replace(".bin", "")
        bin_files[key] = str(local_path)

    if not bin_files:
        print("⚠ No se encontraron binarios.")
        sys.exit(1)

    print("✅ Todos los archivos .bin fueron descargados en la carpeta 'binarios/'")
    return bin_files

def main():
    args = [arg.lower() for arg in sys.argv[1:]]

    if any(a in ("-h", "--help", "help") for a in args):
        mostrar_ayuda()

    if "update" in args:
        actualizar_arcompile()

    particion = None
    if "min_spiffs" in args:
        particion = "min_spiffs"

    print("🔍 Buscando puerto ESP32 …")
    puerto = "COM3"  # Puedes personalizar esto si lo deseas
    print(f"✔ Detectado {puerto}")

    sketch_dir = Path.cwd()
    remote_proj = f"/home/ubuntu/compilacion_esp32/{sketch_dir.name}"

    print("🛠  Compilación necesaria")
    run(f"ssh {REMOTE} rm -rf {remote_proj}")
    run(f"ssh {REMOTE} mkdir -p {remote_proj}")
    run(f"scp -r * {REMOTE}:{remote_proj}/")

    # Detectar librerías necesarias
    libs = []
    lib_file = sketch_dir / "libraries.txt"
    if lib_file.exists():
        libs = [line.strip() for line in lib_file.read_text().splitlines() if line.strip()]

    if libs:
        print("⚠ Faltan librerías → se instalan y se reintenta …")

    particion = compilar_en_servidor(remote_proj, libs, particion)
    bin_files = descargar_binarios(remote_proj)

    print("\n🧪 Puedes ahora flashear con:")
    print("esptool.py --chip esp32 --port COM3 --baud 460800 write_flash -z \\")
    print(f"  0x1000 {bin_files.get('homespan_test.ino.bootloader', 'bootloader.bin')} \\")
    print(f"  0x8000 {bin_files.get('homespan_test.ino.partitions', 'partitions.bin')} \\")
    print(f"  0xe000 binarios/boot_app0.bin \\")
    print(f"  0x10000 {bin_files.get('homespan_test.ino', 'firmware.bin')}")
    print()

if __name__ == "__main__":
    main()
