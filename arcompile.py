#!/usr/bin/env python3

import os
import sys
import subprocess
import shlex
import time
import atexit
import platform
import shutil
import hashlib
from pathlib import Path
from typing import Optional, Tuple, Dict

import requests
import serial.tools.list_ports
from arcompile_version import __version__ as VERSION

# ======== CONFIGURACIÓN POR DEFECTO ========
REMOTE           = "minecraft_server"
REMOTE_DIR       = "/home/ubuntu/compilacion_esp32"

# FQBN por defecto (puedes sobreescribirlo desde CLI: esp32c3 / s3 / dev / da / micro / fqbn=...)
FQBN_DEFAULT     = "esp32:esp32:esp32"

BAUD             = 921600
MAX_SIZE         = 1310720   # 1.3 MB (límite heurístico para decidir min_spiffs)
REPO_VERSION_URL = "https://raw.githubusercontent.com/jaestefaniah27/online_compiler/main/arcompile_version.py"
# Estimación basada en líneas de código (en segundos por línea)
TIME_PER_LINE    = 0.02
# Archivos de log
COMPILE_LOG      = Path("compile.log")
ERROR_LOG        = Path("error.log")
# ==========================================

# Mapeos útiles
BOARD_ALIASES = {
    "dev":       "esp32:esp32:esp32",
    "da":        "esp32:esp32:esp32da",
    "c3":        "esp32:esp32:esp32c3",
    "esp32c3":   "esp32:esp32:esp32c3",
    "s3":        "esp32:esp32:esp32s3",
    "esp32s3":   "esp32:esp32:esp32s3",
    # Arduino Micro (ATmega32U4)
    "micro":     "arduino:avr:micro",
}

# Offsets de flasheo por familia ESP32
FLASH_LAYOUT = {
    # ESP32 clásico / DA
    "esp32": {
        "bootloader": 0x1000,
        "partitions": 0x8000,
        "boot_app0":  0xE000,
        "application":0x10000,
        "use_boot_app0": True
    },
    # ESP32-C3 (RISC-V)
    "esp32c3": {
        "bootloader": 0x0000,
        "partitions": 0x8000,
        "application":0x10000,
        "use_boot_app0": False
    },
    # ESP32-S3
    "esp32s3": {
        "bootloader": 0x0000,
        "partitions": 0x8000,
        "application":0x10000,
        "use_boot_app0": False
    },
    # Nota: familia "avr" no usa esptool ni offsets
}

# Tiempo de inicio para cálculo de elapsed
_start_time = time.time()

@atexit.register
def _print_elapsed():
    elapsed = time.time() - _start_time
    print(f"⏱ Tiempo transcurrido: {elapsed:.1f} s")


# ------------------------------ Utilidades -------------------------------- #

def run(cmd, **kw):
    # Permite cmd como str (usa shell) o como list (sin shell, más seguro en Windows)
    if isinstance(cmd, (list, tuple)):
        print("»", " ".join(shlex.quote(str(x)) for x in cmd))
        subprocess.run(cmd, shell=False, check=True, **kw)
    else:
        print(f"» {cmd}")
        subprocess.run(cmd, shell=True, check=True, **kw)


def run_capture(cmd):
    p = subprocess.run(cmd, shell=True, text=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if p.stderr:
        ERROR_LOG.write_text(p.stderr, encoding='utf8')
    return p.returncode, p.stdout, p.stderr


def puerto_esp32():
    print("🔍 Buscando puerto ESP32 …")
    for p in serial.tools.list_ports.comports():
        if any(t in p.description for t in ("CP210", "Silicon", "USB", "ESP32", "CH340", "CDC")):
            print(f"✔ Detectado {p.device}")
            return p.device
    sys.exit("❌ ESP32 no encontrada")


def puerto_esp32_optional():
    print("🔍 Buscando puerto (opcional) …")
    for p in serial.tools.list_ports.comports():
        if any(t in p.description for t in ("CP210", "Silicon", "USB", "ESP32", "CH340", "CDC", "Arduino")):
            print(f"✔ Detectado {p.device}")
            return p.device
    print("⚠ No se detectó puerto. Se continuará sin flashear.")
    return None


def detectar_puerto_serial_optional():
    """
    Detector GENÉRICO (opcional) de puerto serie válido tanto para ESP32 como Arduino AVR.
    No aborta si no encuentra nada: devuelve None.
    """
    print("🔍 Buscando puerto (opcional) …")
    patrones = (
        "CP210", "Silicon", "USB", "ESP32", "CH340", "CDC",
        "Arduino", "Caterina", "ATmega32U4", "ATm32U4",
    )
    for p in serial.tools.list_ports.comports():
        desc = f\"{p.description or ''} {p.hwid or ''}\"
        if any(t.lower() in desc.lower() for t in patrones):
            print(f\"✔ Detectado {p.device}\")
            return p.device
    print(\"⚠ No se detectó puerto compatible.\")
    return None


def leer_libraries():
    f = Path(\"libraries.txt\")
    if not f.exists():
        return []
    return [l.strip() for l in f.read_text(encoding=\"utf8\").splitlines() if l.strip()]


def instalar_librerias(libs):
    if not libs:
        return
    print(\"• Instalando/actualizando librerías en servidor …\")
    for lib in libs:
        run(f\"ssh {REMOTE} arduino-cli lib install {shlex.quote(lib)} --no-overwrite\")


def subir_proyecto(remote_proj):
    run(f\"ssh {REMOTE} rm -rf {shlex.quote(remote_proj)}\")
    run(f\"ssh {REMOTE} mkdir -p {shlex.quote(remote_proj)}\")
    run(f\"scp -r * {REMOTE}:{remote_proj}/\")


def mostrar_ayuda():
    print(f\"\"\"
arcompile v{VERSION}

Uso:
  arcompile                    → compila y flashea (FQBN por defecto)
  arcompile dev|da|esp32c3|c3|esp32s3|s3|micro
                               → selecciona FQBN rápido por alias (incluye Arduino Micro)
  arcompile fqbn=<VENDOR:ARCH:BOARD>
                               → usa un FQBN exacto
  arcompile min_spiffs         → (ESP32) fuerza particiones min_spiffs
  arcompile update             → actualiza arcompile
  arcompile help               → esta ayuda

Ejemplos:
  arcompile esp32c3
  arcompile fqbn=esp32:esp32:esp32c3
  arcompile micro
  arcompile fqbn=arduino:avr:micro
\"\"\")
    sys.exit(0)


def get_remote_version():
    try:
        resp = requests.get(REPO_VERSION_URL, timeout=5)
        resp.raise_for_status()
        for line in resp.text.splitlines():
            if line.startswith(\"__version__\"):
                return line.split(\"=\")[1].strip().strip('\"').strip(\"'\")
    except Exception as e:
        print(f\"⚠ No se pudo obtener versión remota: {e}\")
    return None


def realizar_update():
    remote = get_remote_version()
    if not remote:
        sys.exit(\"❌ No se pudo comprobar la versión remota.\")
    if remote == VERSION:
        print(f\"✔ Ya tienes la última versión ({VERSION}).\")
        sys.exit(0)
    print(f\"🔄 Nueva versión disponible: {remote} → actualizando …\")
    run(\"pip uninstall -y arcompile\")
    run(\"pip install --no-cache-dir --force-reinstall git+https://github.com/jaestefaniah27/online_compiler.git\")
    print(f\"✅ arcompile actualizado a {remote}\")
    sys.exit(0)


def estimar_tiempo():
    total_lineas = 0
    for ext in (\".ino\", \".cpp\", \".h\"):
        for file in Path.cwd().rglob(f\"*{ext}\"):
            try:
                total_lineas += sum(1 for _ in file.open(encoding='utf8', errors='ignore'))
            except Exception:
                continue
    estimado = total_lineas * TIME_PER_LINE
    print(f\"⏳ Estimación de compilación basada en {total_lineas} líneas: ~{estimado:.1f} s\")


def compilar_en_servidor(remote_proj, libs, particion=None, fqbn_base=None):
    ERROR_LOG.write_text(\"\", encoding='utf8')
    estimar_tiempo()
    print(\"🏗 Iniciando compilación\")
    fqbn = fqbn_base or FQBN_DEFAULT

    # Particiones sólo aplican a ESP32
    if particion and fqbn.startswith(\"esp32:\"):
        print(f\"• Forzando particiones: {particion}\")
        fqbn = f\"{fqbn}:PartitionScheme={particion}\"

    compile_cmd = (
        f\"ssh {REMOTE} /usr/local/bin/arduino-cli compile \"
        f\"--fqbn {shlex.quote(fqbn)} \"
        f\"{shlex.quote(remote_proj)} --export-binaries\"
    )

    for intento in (1, 2):
        code, out, err = run_capture(compile_cmd)
        if code == 0:
            print(\"✓ Compilación exitosa\")
            return fqbn, (out + err)
        if intento == 1 and (\"No such file or directory\" in err or \"not found\" in err):
            print(\"⚠ Faltan librerías → instalando y reintentando …\")
            instalar_librerias(libs)
            continue
        print(out + err)
        sys.exit(\"❌ Compilación abortada\")


def binario_excede_tamano(salida):
    for linea in salida.splitlines():
        if \"Sketch uses\" in linea and \"Maximum is\" in linea:
            try:
                usado = int(linea.split(\"Sketch uses\")[1].split(\"bytes\")[0].strip().replace(\",\", \"\"))
                print(f\"• Binario ocupa {usado} bytes\")
                return usado > MAX_SIZE
            except:
                pass
    return False



def descargar_binarios(build_remote, sketch_name) -> Dict[str, Path]:
    out_dir = Path("binarios")
    out_dir.mkdir(exist_ok=True)

    # Descargar todo y luego filtramos cuidadosamente
    scp_cmd = f"scp {REMOTE}:{shlex.quote(build_remote)}/*.* binarios/"
    run(scp_cmd)

    local_files: Dict[str, Path] = {}

    def is_exact_bootloader(name: str) -> bool:
        # aceptamos exactamente "*.bootloader.bin", NO "*.with_bootloader.bin"
        return name.endswith(".bootloader.bin") and "with_bootloader" not in name

    def is_exact_partitions(name: str) -> bool:
        return name.endswith(".partitions.bin")

    def is_boot_app0(name: str) -> bool:
        return name.endswith("app0.bin") and "with_bootloader" not in name and "merged" not in name

    def is_application_bin(name: str) -> bool:
        # aceptamos *.ino.bin o <sketch>.bin, pero NO "with_bootloader" ni "merged"
        if "with_bootloader" in name or "merged" in name:
            return False
        if name.endswith(".ino.bin"):
            return True
        base = sketch_name.lower().removesuffix(".ino")
        return name == f"{base}.bin"

    # Recorremos archivos y asignamos con reglas estrictas
    for archivo in out_dir.glob("*.*"):
        name = archivo.name.lower()
        if name.endswith(".hex"):
            local_files["application_hex"] = archivo
        elif is_exact_bootloader(name):
            local_files["bootloader"] = archivo
        elif is_exact_partitions(name):
            local_files["partitions"] = archivo
        elif is_boot_app0(name):
            local_files["boot_app0"] = archivo
        elif name.endswith(".bin") and is_application_bin(name):
            local_files["application"] = archivo

    print("✅ Artefactos descargados en ./binarios/")
    # Log defensivo si detectamos imágenes problemáticas
    bad = [p.name for p in out_dir.glob("*.bin") if ("with_bootloader" in p.name.lower() or "merged" in p.name.lower())]
    if bad:
        print("ℹ Ignorando imágenes combinadas:", ", ".join(bad))
    return local_files


def hash_proyecto():
    sha = hashlib.sha256()
    for path in sorted(Path.cwd().rglob(\"*\")):
        if path.is_file() and path.suffix in {\".ino\", \".cpp\", \".h\", \".txt\"}:
            sha.update(path.read_bytes())
    return sha.hexdigest()


def resolver_fqbn_desde_args(args_list):
    \"\"\"
    Soporta:
      - aliases: dev | da | esp32c3 | c3 | esp32s3 | s3 | micro
      - fqbn=VENDOR:ARCH:BOARD
      - si no hay nada, usa FQBN_DEFAULT
    \"\"\"
    fqbn = None
    for a in args_list:
        if a.startswith(\"fqbn=\"):
            fqbn = a.split(\"=\", 1)[1]
        elif a in BOARD_ALIASES:
            fqbn = BOARD_ALIASES[a]
    return fqbn or FQBN_DEFAULT


def familia_chip_de_fqbn(fqbn: str) -> str:
    \"\"\"
    Devuelve la familia: 'esp32', 'esp32c3', 'esp32s3' o 'avr' (Arduino AVR).
    \"\"\"
    try:
        vendor, arch, board = fqbn.split(\":\")
        vendor = vendor.lower()
        arch   = arch.lower()
        board  = board.lower()
    except Exception:
        vendor, arch, board = \"\", \"\", \"\"

    if arch == \"avr\" or vendor == \"arduino\":
        return \"avr\"
    if \"c3\" in board:
        return \"esp32c3\"
    if \"s3\" in board:
        return \"esp32s3\"
    return \"esp32\"


def construir_flash_cmd(esptool, com, baud, files, family, add_reset_flags: bool = True, no_stub: bool = False):
    \"\"\"
    Construye el comando esptool write_flash con offsets correctos por familia (ESP32*).
    Para AVR no se usa esptool: se sube con arduino-cli upload --input-dir.
    \"\"\"
    if family == \"avr\":
        raise RuntimeError(\"construir_flash_cmd no aplica a AVR\")
    layout = FLASH_LAYOUT.get(family, FLASH_LAYOUT[\"esp32\"])

    parts = []
    # bootloader
    if \"bootloader\" in files and files[\"bootloader\"].exists():
        parts += [f\"0x{layout['bootloader']:x}\", str(files[\"bootloader\"])]

    # partitions
    if \"partitions\" in files and files[\"partitions\"].exists():
        parts += [f\"0x{layout['partitions']:x}\", str(files[\"partitions\"])]

    # boot_app0 (solo si la familia lo usa y existe el archivo)
    if layout.get(\"use_boot_app0\", False) and \"boot_app0\" in files and files[\"boot_app0\"].exists():
        parts += [f\"0x{layout['boot_app0']:x}\", str(files[\"boot_app0\"])]

    # aplicación
    if \"application\" in files and files[\"application\"].exists():
        parts += [f\"0x{layout['application']:x}\", str(files[\"application\"])]

    if not parts:
        sys.exit(\"❌ No se encontraron binarios para flashear (ESP32).\")

    # Sin --chip → esptool auto-detecta (evita 'Wrong --chip argument?')
    flags = []
    if add_reset_flags:
        flags += [\"--before\", \"default_reset\", \"--after\", \"hard_reset\"]
    if no_stub:
        flags += [\"--no-stub\"]

    flash_cmd = (
        f\"{esptool} --port {com} --baud {baud} \"
        + \" \".join(flags)
        + \" write_flash -z \"
        + \" \".join(parts)
    )
    return flash_cmd


def try_flash_esptool(esptool, com, files, family):
    \"\"\"
    Flasheo robusto con reintentos: 921600 → 460800 → 460800 --no-stub.
    \"\"\"
    attempts = [
        (BAUD, False),
        (460800, False),
        (460800, True),
    ]
    last_err = None
    for baud, no_stub in attempts:
        try:
            cmd = construir_flash_cmd(esptool, com, baud, files, family, add_reset_flags=True, no_stub=no_stub)
            run(cmd)
            return True
        except subprocess.CalledProcessError as e:
            print(f\"⚠ Falló el flasheo a {baud} bps{' (no-stub)' if no_stub else ''}: {e}\")
            last_err = e
            time.sleep(1.5)  # pequeña pausa entre intentos
            continue
    if last_err:
        raise last_err
    return False

# ===== Releases (guardar/flash sin recompilar) =====

def releases_dir() -> Path:
    d = Path(\"releases\")
    d.mkdir(exist_ok=True)
    return d


def write_meta(dirpath: Path, fqbn: str, family: str):
    meta = dirpath / \".meta\"
    ts = time.strftime(\"%Y-%m-%d %H:%M:%S\")
    lines = [
        f\"NAME={dirpath.name}\",
        f\"DATE={ts}\",
        f\"FQBN={fqbn}\",
        f\"FAMILY={family}\",
    ]
    meta.write_text(\"\\n\".join(lines) + \"\\n\", encoding=\"utf8\")


def read_meta(dirpath: Path) -> dict:
    meta = dirpath / \".meta\"
    if not meta.exists():
        info = {}
        # heurística mínima por presencia de archivos
        info[\"FAMILY\"] = \"avr\" if any(p.suffix == \".hex\" for p in dirpath.glob(\"*.*\")) else \"esp32\"
        info[\"FQBN\"] = \"\"
        return info
    d = {}
    for line in meta.read_text(encoding=\"utf8\").splitlines():
        if \"=\" in line:
            k, v = line.split(\"=\", 1)
            d[k.strip()] = v.strip()
    return d


def extraer_fqbn_de_compile_log() -> Optional[str]:
    \"\"\"
    Intenta extraer el FQBN real del último compile (arduino-cli) leyendo compile.log.
    Busca patrones comunes como '--fqbn <...>' o 'FQBN:'.
    \"\"\"
    if not COMPILE_LOG.exists():
        return None
    txt = COMPILE_LOG.read_text(encoding=\"utf8\", errors=\"ignore\")
    # Patrones habituales
    # 1) ... --fqbn vendor:arch:board ...
    for token in (\"--fqbn\", \"FQBN:\", \"fqbn=\"):
        idx = txt.find(token)
        if idx != -1:
            frag = txt[idx: idx+200]  # ventana corta
            # intenta capturar vendor:arch:board
            import re
            m = re.search(r\"([a-zA-Z0-9_]+:[a-zA-Z0-9_]+:[a-zA-Z0-9_]+)\", frag)
            if m:
                return m.group(1)
    return None


def save_release(name: str, fqbn: str, family: str):
    src = Path(\"binarios\")
    if not src.exists():
        sys.exit(\"❌ No hay carpeta ./binarios. Compila primero.\")
    dst = releases_dir() / name
    if dst.exists():
        sys.exit(f\"❌ Ya existe releases/{name}. Elige otro nombre.\")
    dst.mkdir(parents=True)

    # Copia archivos relevantes
    copied = 0
    # ESP32 típicos:
    for fn in [\"main.ino.bootloader.bin\", \"main.ino.partitions.bin\", \"main.ino.bin\", \"boot_app0.bin\",
               f\"{Path.cwd().name}.ino.bootloader.bin\",
               f\"{Path.cwd().name}.ino.partitions.bin\",
               f\"{Path.cwd().name}.ino.bin\"]:
        f = src / fn
        if f.exists():
            shutil.copy2(f, dst / f.name)
            copied += 1

    # AVR: .hex
    for fn in [\"main.ino.hex\", f\"{Path.cwd().name}.ino.hex\", f\"{Path.cwd().name}.hex\"]:
        f = src / fn
        if f.exists():
            shutil.copy2(f, dst / f.name)
            copied += 1

    if copied == 0:
        sys.exit(\"❌ No se encontraron artefactos en ./binarios para guardar.\")

    write_meta(dst, fqbn, family)
    print(f\"✅ Guardado en releases/{name}\")


def load_release_bins(name: str) -> Tuple[Dict[str, Path], str]:
    rdir = releases_dir() / name
    if not rdir.exists():
        sys.exit(f\"❌ No existe releases/{name}\")
    meta = read_meta(rdir)
    family = meta.get(\"FAMILY\", \"esp32\")

    files: Dict[str, Path] = {}
    candidates = list(rdir.glob(\"*.*\"))
    for archivo in candidates:
        low = archivo.name.lower()
        if low.endswith(\".hex\"):
            files[\"application_hex\"] = archivo
        elif \"bootloader\" in low and low.endswith(\".bin\"):
            files[\"bootloader\"] = archivo
        elif \"partition\" in low and low.endswith(\".bin\"):
            files[\"partitions\"] = archivo
        elif \"app0\" in low and low.endswith(\".bin\"):
            files[\"boot_app0\"] = archivo
        elif low.endswith(\".ino.bin\"):
            files[\"application\"] = archivo
        elif low.endswith(\".bin\") and all(t not in low for t in (\"partition\", \"bootloader\", \"app0\" )):
            # fallback .bin de aplicación
            files[\"application\"] = archivo

    return files, family


def resolve_arduino_cli() -> str:
    \"\"\"
    Localiza arduino-cli de forma portátil.
    Prioriza ARDUINO_CLI, luego which/where y ubicaciones típicas de Windows.
    \"\"\"
    env_cli = os.environ.get(\"ARDUINO_CLI\")
    if env_cli and Path(env_cli).exists():
        return str(Path(env_cli))

    cand = shutil.which(\"arduino-cli\") or shutil.which(\"arduino-cli.exe\")
    if cand:
        return cand

    if platform.system().lower().startswith(\"win\"):
        home = Path.home()
        candidates = [
            home / \"AppData/Local/Programs/arduino-cli/arduino-cli.exe\",
            home / \"AppData/Local/Arduino CLI/arduino-cli.exe\",
            Path(\"C:/Program Files/Arduino CLI/arduino-cli.exe\"),
            Path(\"C:/Program Files (x86)/Arduino CLI/arduino-cli.exe\"),
        ]
        for c in candidates:
            if c.exists():
                return str(c)

    raise FileNotFoundError(
        \"No se encontró 'arduino-cli'. Instálalo o define ARDUINO_CLI con la ruta completa al ejecutable.\"
    )


def flash_release(name: str, port: Optional[str], baud: int):
    files, family = load_release_bins(name)
    if family == \"avr\":
        cli = resolve_arduino_cli()
        # Para AVR usamos arduino-cli upload con --input-dir
        fqbn = \"arduino:avr:micro\"  # por defecto razonable; si .meta tiene FQBN lo usamos
        meta_path = releases_dir() / name / \".meta\"
        if meta_path.exists():
            meta = read_meta(releases_dir() / name)
            if meta.get(\"FQBN\"):
                fqbn = meta[\"FQBN\"]

        com = port or detectar_puerto_serial_optional()
        if not com:
            sys.exit(\"❌ No se detectó ningún puerto serie para Arduino. Conecta la placa y reintenta.\")

        cmd = [
            cli, \"upload\",
            \"--fqbn\", fqbn,
            \"-p\", com,
            \"--input-dir\", str((releases_dir() / name).resolve()),
            str(Path.cwd().resolve()),
        ]
        run(cmd)
        print(f\"✅ Flash AVR de release '{name}' completado en {com}\")
        return

    # ESP32
    esptool = shutil.which(\"esptool.py\") or f\"{sys.executable} -m esptool\"
    com = port or detectar_puerto_serial_optional()
    if not com:
        sys.exit(\"❌ No se detectó ningún puerto serie para ESP32. Conecta la placa y reintenta.\")
    try:
        try_flash_esptool(esptool, com, files, family)
        print(f\"✅ Flash de release '{name}' completado en {com}\")
    except subprocess.CalledProcessError:
        sys.exit(\"❌ Falló el flasheo de la release tras varios intentos.\")


# --------------------------------- Main ----------------------------------- #

def main():
    start = time.time()
    args = [a.lower() for a in sys.argv[1:]]

    # --- Guardar release ---
    if args and args[0].lower() == \"save\":
        if len(args) < 2:
            sys.exit(\"Uso: arcompile save <nombre_release>\")

        # 1) Intenta extraer FQBN real del compile.log
        fqbn_detectado = extraer_fqbn_de_compile_log()

        # 2) Si no hay, deduce familia por artefactos en ./binarios
        if not fqbn_detectado:
            bin_dir = Path(\"binarios\")
            if any(p.suffix.lower() == \".hex\" for p in bin_dir.glob(\"*.*\")):
                # AVR por presencia de .hex ⇒ usa Micro como default razonable
                fqbn_detectado = \"arduino:avr:micro\"
            else:
                fqbn_detectado = FQBN_DEFAULT

        family = familia_chip_de_fqbn(fqbn_detectado)
        save_release(args[1], fqbn_detectado, family)
        return

    # --- Flashear release ---
    if args and args[0].lower() == \"flash\":
        if len(args) < 2:
            sys.exit(\"Uso: arcompile flash <nombre_release> [COMx]\")
        name = args[1]
        port = args[2] if len(args) >= 3 else None
        flash_release(name, port, BAUD)
        print(f\"✅ Terminado en {time.time() - start:.1f} s\")
        return
    
    if any(a in (\"help\", \"-h\", \"--help\") for a in args):
        mostrar_ayuda()

    if \"update\" in args:
        realizar_update()

    # Procesa selección de FQBN desde CLI
    fqbn_base = resolver_fqbn_desde_args(args)
    used_fqbn  = fqbn_base  # se actualizará en compilación si aplica
    used_family = familia_chip_de_fqbn(fqbn_base)

    particion = \"min_spiffs\" if (\"min_spiffs\" in args and used_family != \"avr\") else None

    sketch_dir  = Path.cwd()
    sketch_name = sketch_dir.name + \".ino\"
    ino_path    = sketch_dir / sketch_name
    if not ino_path.exists():
        sys.exit(f\"❌ No se encontró {sketch_name}\")

    libs = leer_libraries()

    # === Compilar SIEMPRE, sin requerir puerto conectado ===
    hash_actual   = hash_proyecto()
    hash_file     = Path(\".build_hash\")
    hash_anterior = hash_file.read_text() if hash_file.exists() else \"\"

    bin_files = None  # asegura visibilidad fuera de ramas

    if hash_actual != hash_anterior:
        print(\"🛠 Compilación necesaria\")
        remote_proj = f\"{REMOTE_DIR}/{sketch_dir.name}\"
        subir_proyecto(remote_proj)

        used_fqbn, salida = compilar_en_servidor(remote_proj, libs, particion, fqbn_base=fqbn_base)
        used_family = familia_chip_de_fqbn(used_fqbn)  # ⚠️ actualizar family inmediatamente tras compilar

        # Sólo aplica a ESP32
        if used_family != \"avr\" and not particion and binario_excede_tamano(salida):
            print(\"⚠ Binario >1.3MB → reintentando con min_spiffs\")
            used_fqbn, salida = compilar_en_servidor(remote_proj, libs, \"min_spiffs\", fqbn_base=fqbn_base)
            particion = \"min_spiffs\"
            used_family = familia_chip_de_fqbn(used_fqbn)

        COMPILE_LOG.write_text(salida, encoding=\"utf8\")
        print(f\"ℹ Salida de compilación guardada en {COMPILE_LOG}\")

        print(\"🔍 Detectando carpeta de build en el servidor…\")
        out = subprocess.check_output(
            f\"ssh {REMOTE} ls {shlex.quote(remote_proj)}/build\",
            shell=True, text=True
        ).split()
        if not out:
            sys.exit(\"❌ No se encontró ningún subdirectorio en build/\")
        carpeta_build = out[0].strip()
        build_remote = f\"{remote_proj}/build/{carpeta_build}\"

        bin_files = descargar_binarios(build_remote, sketch_name)
        hash_file.write_text(hash_actual)
    else:
        print(\"⚡ Usando artefactos ya compilados\")
        out_dir = Path(\"binarios\")
        if used_family == \"avr\":
            # AVR: verificar que exista un .hex; el upload se hará más abajo
            cand_hex = [
                out_dir / f\"{sketch_name}.hex\",
                out_dir / f\"{sketch_name}.ino.hex\",
                out_dir / f\"{Path.cwd().name}.ino.hex\",
                out_dir / f\"{Path.cwd().name}.hex\",
            ]
            app_hex = next((p for p in cand_hex if p.exists()), None)
            if not app_hex:
                sys.exit(\"❌ Falta el .hex necesario en ./binarios para AVR.\")
            # No hace falta guardar en bin_files para AVR; se usa --input-dir más abajo
        else:
            # ESP32: .bin
            bin_files = {
                \"bootloader\":   out_dir / f\"{sketch_name}.bootloader.bin\",
                \"partitions\":   out_dir / f\"{sketch_name}.partitions.bin\",
                \"application\":  out_dir / f\"{sketch_name}.bin\",
                \"boot_app0\":    out_dir / \"boot_app0.bin\",
            }
            for k, f in bin_files.items():
                if k != \"boot_app0\" and not f.exists():
                    sys.exit(f\"❌ Falta el binario requerido: {f}\")

    # === Flasheo (sólo si hay puerto disponible ahora) ===
    com_final = detectar_puerto_serial_optional()
    if not com_final:
        print(\"🚫 No se detectó puerto. La compilación/descarga de artefactos se han completado correctamente.\")
        print(\"📦 Artefactos listos en ./binarios/\")
        print(\"▶ Cuando conectes la placa, vuelve a ejecutar el comando para flashear directamente (sin recompilar).\" )
        sys.exit(2)

    if used_family == \"avr\":
        # Subida con arduino-cli usando artefactos .hex en ./binarios
        cli = resolve_arduino_cli()
        cmd = [
            cli, \"upload\",
            \"--fqbn\", used_fqbn,
            \"-p\", com_final,
            \"--input-dir\", str(Path(\"binarios\").resolve()),
            str(Path.cwd().resolve()),
        ]
        run(cmd)
    else:
        esptool = shutil.which(\"esptool.py\") or f\"{sys.executable} -m esptool\"
        try:
            try_flash_esptool(esptool, com_final, bin_files, used_family)
        except subprocess.CalledProcessError:
            sys.exit(\"❌ Falló el flasheo tras varios intentos. Revisa el cable/permiso del puerto USB y vuelve a intentar.\")

    print(f\"✅ Terminado en {time.time() - start:.1f} s\")


if __name__ == \"__main__\":
    main()
# EOF
