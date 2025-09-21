#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
arcompile.py — Compilar/subir sketches para Arduino/ESP32 usando arduino-cli,
SIN depender de ningún archivo .json (solo flags de línea de comandos).

Características:
- Selección de placa por FQBN (--fqbn) o alias (--board).
- Opciones de menú (--menu cpu=atmega328old,psram=enabled,...).
- Instalación automática del core (--auto-core).
- Autodetección de puerto serie (si no pasas --port).
- Flags extra para compile y upload.
- Funciona con AVR (UNO, Nano, Mega...), SAMD, RP2040 y ESP32.

Requisitos:
- arduino-cli instalado y en PATH.
"""

from __future__ import annotations
import argparse
import json
import os
import sys
import subprocess
import shutil
from pathlib import Path
from typing import Dict, Any, Optional, List

# ------------ Mapa de alias -> FQBN ------------
BOARD_TO_FQBN: Dict[str, str] = {
    "uno":       "arduino:avr:uno",
    "nano":      "arduino:avr:nano",          # Usa --menu cpu=atmega328old si corresponde
    "mega":      "arduino:avr:mega",
    "leonardo":  "arduino:avr:leonardo",
    "mkr1000":   "arduino:samd:mkr1000",
    "zero":      "arduino:samd:arduino_zero",
    "pico":      "rp2040:rp2040:rpipico",
    "esp32":     "esp32:esp32:esp32",         # Genérico ESP32 Dev Module
}

DEFAULT_FQBN = BOARD_TO_FQBN["esp32"]  # Compatibilidad: si no se indica, usar ESP32


# ------------ Utilidades OS ------------
def eprint(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)


def run(cmd: List[str],
        check: bool = True,
        capture_output: bool = False,
        text: bool = True,
        env: Optional[Dict[str, str]] = None) -> subprocess.CompletedProcess:
    verbose = os.environ.get("ARCOMPILE_VERBOSE", "0") == "1"
    if verbose:
        eprint(f"[CMD] {' '.join(cmd)}")
    try:
        return subprocess.run(
            cmd,
            check=check,
            capture_output=capture_output,
            text=text,
            env=env
        )
    except subprocess.CalledProcessError as ex:
        if ex.stdout:
            eprint(ex.stdout)
        if ex.stderr:
            eprint(ex.stderr)
        raise


def ensure_arduino_cli() -> str:
    cli = shutil.which("arduino-cli")
    if not cli:
        eprint("❌ No se encontró 'arduino-cli' en PATH.")
        eprint("   Instálalo: https://arduino.github.io/arduino-cli/latest/installation/")
        sys.exit(127)
    return cli


# ------------ Lógica Arduino CLI ------------
def parse_core_from_fqbn(fqbn: str) -> str:
    # "vendor:arch:board[:menu=val,...]" -> "vendor:arch"
    parts = fqbn.split(":")
    if len(parts) < 2:
        raise ValueError(f"FQBN inválido: {fqbn}")
    return ":".join(parts[:2])


def apply_menu_options_to_fqbn(fqbn: str, menu_list: Optional[List[str]]) -> str:
    """
    Añade opciones de menú a la FQBN.
    menu_list, ej.: ["cpu=atmega328old", "flash=4MB", "psram=enabled"]
    Resultado: vendor:arch:board:cpu=atmega328old,flash=4MB,psram=enabled
    """
    if not menu_list:
        return fqbn
    parts = fqbn.split(":")
    if len(parts) < 3:
        raise ValueError(f"FQBN incompleto para opciones de menú: {fqbn}")
    vendor, arch, board = parts[:3]
    menu = ",".join(menu_list)
    return f"{vendor}:{arch}:{board}:{menu}"


def ensure_core_installed(cli: str, fqbn: str):
    core = parse_core_from_fqbn(fqbn)
    res = run([cli, "core", "list", "--format", "json"], capture_output=True)
    try:
        installed = json.loads(res.stdout or "[]")
    except json.JSONDecodeError:
        installed = []

    installed_ids = {entry.get("ID") for entry in installed if isinstance(entry, dict)}
    if core not in installed_ids:
        eprint(f"➡️  Instalando core '{core}' (primera vez en este host)...")
        run([cli, "core", "install", core])


def autodetect_port(cli: str, fqbn: str) -> Optional[str]:
    """
    Usa `arduino-cli board list --format json` y:
      1) Busca coincidencias con el mismo vendor:arch del fqbn objetivo.
      2) Si no hay, devuelve el primer puerto serie disponible.
    """
    res = run([cli, "board", "list", "--format", "json"], capture_output=True)
    ports_json = {}
    try:
        ports_json = json.loads(res.stdout or "{}")
    except Exception:
        pass

    # Estructuras posibles según versión
    serial_items = ports_json.get("serialBoards") or ports_json.get("ports") or []
    target_vendor_arch = parse_core_from_fqbn(fqbn)

    first_serial = None
    for item in serial_items:
        # Soportar dos formatos
        address = item.get("address") or item.get("port", {}).get("address")
        if not address:
            continue
        if not first_serial:
            first_serial = address

        boards = item.get("boards") or item.get("matchingBoards") or []
        for b in boards:
            # b puede tener "FQBN" o "platform"
            bfqbn = b.get("FQBN")
            platform = b.get("platform")
            if bfqbn and parse_core_from_fqbn(bfqbn) == target_vendor_arch:
                return address
            if platform and platform.get("id") == target_vendor_arch:
                return address

    return first_serial  # si no hay match, al menos un puerto


def find_sketch_dir(user_path: Optional[str]) -> Path:
    """
    Determina el directorio del sketch:
    - Si user_path es archivo .ino/.cpp => usa su carpeta.
    - Si es carpeta => úsala (debe contener .ino principal).
    - Si None => carpeta actual.
    """
    if user_path:
        p = Path(user_path).resolve()
    else:
        p = Path.cwd().resolve()

    if p.is_file():
        return p.parent
    return p


def default_build_path(sketch_dir: Path) -> Path:
    # Ubica los binarios en <sketch_dir>/build
    return sketch_dir / "build"


def compile_sketch(cli: str,
                   fqbn: str,
                   sketch_dir: Path,
                   build_path: Path,
                   extra_flags: Optional[List[str]] = None) -> None:
    cmd = [
        cli, "compile",
        "--fqbn", fqbn,
        "--build-path", str(build_path),
        "--export-binaries",
        str(sketch_dir)
    ]
    if extra_flags:
        cmd.extend(extra_flags)
    eprint(f"🛠️  Compilando para {fqbn} ...")
    run(cmd)


def upload_sketch(cli: str,
                  fqbn: str,
                  sketch_dir: Path,
                  port: Optional[str],
                  extra_upload_flags: Optional[List[str]] = None) -> None:
    cmd = [
        cli, "upload",
        "--fqbn", fqbn,
        str(sketch_dir)
    ]
    if port:
        cmd.extend(["-p", port])
    if extra_upload_flags:
        cmd.extend(extra_upload_flags)
    eprint(f"⬆️  Subiendo a {port or '(auto)'} ...")
    run(cmd)


# ------------ CLI ------------
def build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Compilador/subidor en línea para placas Arduino/ESP32 usando arduino-cli (sin .json)."
    )
    g_board = p.add_argument_group("Selección de placa")
    g_board.add_argument("--fqbn", help="FQBN completo (p.ej. arduino:avr:uno)")
    g_board.add_argument("--board",
                         choices=list(BOARD_TO_FQBN.keys()),
                         help="Alias de placa (convierte a FQBN).")
    g_board.add_argument("--menu", nargs="*", default=None,
                         help="Opciones de menú: 'clave=valor' separadas por espacio (ej: --menu cpu=atmega328old).")

    g_paths = p.add_argument_group("Rutas")
    g_paths.add_argument("--sketch-dir", help="Directorio del sketch o ruta a un .ino/.cpp. Por defecto, cwd.")
    g_paths.add_argument("--build-path", help="Directorio de build. Por defecto, <sketch>/build")

    g_serial = p.add_argument_group("Serial/Upload")
    g_serial.add_argument("--port", help="Puerto serie (ej: /dev/ttyACM0, COM3). Si se omite, intenta autodetectar.")
    g_serial.add_argument("--no-upload", action="store_true", help="Compila sin subir al dispositivo.")

    g_auto = p.add_argument_group("Automatización")
    g_auto.add_argument("--auto-core", action="store_true",
                        help="Instala automáticamente el core del FQBN si falta.")

    g_extra = p.add_argument_group("Flags extra")
    g_extra.add_argument("--extra-arduino-flags", nargs="*", default=None,
                         help="Flags extra para 'arduino-cli compile'.")
    g_extra.add_argument("--extra-upload-flags", nargs="*", default=None,
                         help="Flags extra para 'arduino-cli upload'.")

    return p


def main():
    args = build_argparser().parse_args()

    cli = ensure_arduino_cli()

    # Resolver FQBN
    fqbn = args.fqbn
    if not fqbn and args.board:
        fqbn = BOARD_TO_FQBN.get(args.board)
    if not fqbn:
        fqbn = DEFAULT_FQBN  # compat: ESP32 por defecto

    # Aplicar opciones de menú a la FQBN (si las hay)
    try:
        fqbn = apply_menu_options_to_fqbn(fqbn, args.menu)
    except ValueError as ex:
        eprint(f"❌ {ex}")
        sys.exit(2)

    # Instalar core si se pide
    if args.auto_core:
        try:
            ensure_core_installed(cli, fqbn)
        except subprocess.CalledProcessError:
            eprint("❌ Falló la instalación del core. Revisa los package indexes y tu conexión.")
            sys.exit(1)

    # Rutas
    sketch_dir = find_sketch_dir(args.sketch_dir)
    if not sketch_dir.exists():
        eprint(f"❌ Sketch no encontrado: {sketch_dir}")
        sys.exit(2)

    build_path = Path(args.build_path).resolve() if args.build_path else default_build_path(sketch_dir)
    build_path.mkdir(parents=True, exist_ok=True)

    # Compilar
    try:
        compile_sketch(cli, fqbn, sketch_dir, build_path, args.extra_arduino_flags)
    except subprocess.CalledProcessError:
        eprint("❌ Error de compilación.")
        sys.exit(1)

    if args.no_upload:
        eprint("✅ Compilación finalizada (sin subir).")
        return

    # Puerto
    port = args.port
    if not port:
        try:
            port = autodetect_port(cli, fqbn)
        except subprocess.CalledProcessError:
            port = None

    if not port:
        eprint("⚠️  No se pudo autodetectar el puerto. "
               "Conecta la placa y usa --port /dev/ttyACM0 (o COMx).")

    # Subir
    try:
        upload_sketch(cli, fqbn, sketch_dir, port, args.extra_upload_flags)
        eprint("✅ Subida completada.")
    except subprocess.CalledProcessError:
        eprint("❌ Error al subir al dispositivo.")
        sys.exit(1)


if __name__ == "__main__":
    main()
