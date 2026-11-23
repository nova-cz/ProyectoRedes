#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NOVA MOTORS · Consola de Administración del Servidor de Planta
Proyecto ADR 2026 — Administración de Redes

Sector: Armadora automotriz (empresa ficticia "Nova Motors").
Sistema objetivo: AlmaLinux 9 (también funciona en otras distros Linux).

Ejecutar como root:
    sudo python3 nova_admin.py
"""

import datetime as dt
import grp
import ipaddress
import json
import os
import pwd
import re
import shutil
import signal
import socket
import subprocess
import sys
import time

try:
    import questionary
    from questionary import Choice, Separator, Style
    from rich import box
    from rich.align import Align
    from rich.console import Console, Group
    from rich.live import Live
    from rich.markup import escape
    from rich.panel import Panel
    from rich.progress import (BarColumn, MofNCompleteColumn, Progress,
                               SpinnerColumn, TextColumn, TimeElapsedColumn)
    from rich.table import Table
    from rich.text import Text
except ImportError:
    print("\n[!] Faltan librerías de Python. Instálalas con:\n")
    print("    sudo pip3 install rich questionary\n")
    sys.exit(1)

# ─────────────────────────────────────────────────────────────────────────────
#  CONFIGURACIÓN GENERAL (cámbiala para adaptar el proyecto)
# ─────────────────────────────────────────────────────────────────────────────
EMPRESA = "NOVA MOTORS"
LEMA = "Planta de Ensamble Automotriz · Servidor de Planta"
DEPARTAMENTOS = ["produccion", "ensamble", "pintura", "soldadura",
                 "calidad", "logistica", "ingenieria", "mantenimiento"]
DIR_PLANTA = "/srv/planta"
DIR_RESPALDOS = "/respaldos"
PUNTO_USB = "/mnt/usb_respaldo"
LOG = "/var/log/nova_admin.log"
UID_MIN = 1000

console = Console(highlight=False)

COLOR = "deep_sky_blue1"   # color principal (azul "corporativo")
ACENTO = "orange1"         # color de acento (naranja industrial)

ESTILO = Style([
    ("qmark", "fg:#ff8700 bold"),
    ("question", "bold"),
    ("answer", "fg:#00afff bold"),
    ("pointer", "fg:#ff8700 bold"),
    ("highlighted", "fg:#ff8700 bold"),
    ("selected", "fg:#00afff"),
    ("separator", "fg:#585858"),
    ("instruction", "fg:#808080 italic"),
    ("text", ""),
])


class Cancelado(Exception):
    """El usuario canceló (Ctrl+C o Esc) una pregunta."""


# ─────────────────────────────────────────────────────────────────────────────
#  UTILIDADES
# ─────────────────────────────────────────────────────────────────────────────
def registrar(accion):
    """Guarda en bitácora cada acción administrativa."""
    try:
        usuario = os.environ.get("SUDO_USER", "root")
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"{dt.datetime.now():%Y-%m-%d %H:%M:%S} [{usuario}] {accion}\n")
    except OSError:
        pass


def ejecutar(cmd, entrada=None):
    """Ejecuta un comando y regresa (codigo, salida, error)."""
    try:
        r = subprocess.run(cmd, input=entrada, capture_output=True, text=True)
        return r.returncode, r.stdout, r.stderr.strip()
    except FileNotFoundError:
        return 127, "", f"No se encontró el comando '{cmd[0]}'. ¿Está instalado?"


def _hijo_con_ctrl_c():
    signal.signal(signal.SIGINT, signal.SIG_DFL)


def interactivo(cmd, cwd=None):
    """Ejecuta un programa que usa toda la terminal (iftop, top, nano...)."""
    viejo = signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        return subprocess.call(cmd, cwd=cwd, preexec_fn=_hijo_con_ctrl_c)
    except FileNotFoundError:
        error(f"No se encontró '{cmd[0]}'. Instálalo con instalar_servidor.sh")
        return 127
    finally:
        signal.signal(signal.SIGINT, viejo)


def en_vivo(cmd, titulo, cwd=None, largo=True):
    """Muestra la salida de un comando línea por línea dentro de un marco."""
    console.rule(f"[bold {ACENTO}]{escape(titulo)}[/]" + ("  [dim](Ctrl+C para detener)[/]" if largo else ""))
    viejo = signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, bufsize=1, cwd=cwd, preexec_fn=_hijo_con_ctrl_c)
        for linea in p.stdout:
            console.print("  " + escape(linea.rstrip()))
        p.wait()
        console.rule(style="dim")
        return p.returncode
    except FileNotFoundError:
        error(f"No se encontró '{cmd[0]}'. Instálalo con instalar_servidor.sh")
        return 127
    finally:
        signal.signal(signal.SIGINT, viejo)


def existe(programa):
    return shutil.which(programa) is not None


def ok(msg):
    console.print(f"\n  [bold green]✔[/] {msg}")


def error(msg):
    console.print(f"\n  [bold red]✘[/] {msg}")


def aviso(msg):
    console.print(f"\n  [bold yellow]![/] {msg}")


def info(msg):
    console.print(f"  [{COLOR}]›[/] {msg}")


def pausa():
    console.print()
    try:
        questionary.press_any_key_to_continue(
            "  Presiona cualquier tecla para continuar...", style=ESTILO).ask()
    except Exception:
        input("  Presiona Enter para continuar...")


def _r(respuesta):
    if respuesta is None:
        raise Cancelado
    return respuesta


def pregunta(msg, default="", validar=None):
    return _r(questionary.text(msg, default=default, validate=validar,
                               qmark="  ✎", style=ESTILO).ask()).strip()


def clave(msg):
    return _r(questionary.password(msg, qmark="  🔑", style=ESTILO).ask())


def confirmar(msg, default=False):
    return _r(questionary.confirm(msg, default=default, qmark="  ?", style=ESTILO).ask())


def elegir(msg, opciones, default=None):
    return _r(questionary.select(msg, choices=opciones, default=default, qmark="  ▸",
                                 pointer="❯", style=ESTILO,
                                 instruction="(↑↓ y Enter)").ask())


def varios(msg, opciones):
    return _r(questionary.checkbox(msg, choices=opciones, qmark="  ▸", pointer="❯",
                                   style=ESTILO,
                                   instruction="(Espacio marca, Enter acepta)").ask())


def ruta(msg, default="", solo_dirs=True):
    return _r(questionary.path(msg, default=default, only_directories=solo_dirs,
                               qmark="  📁", style=ESTILO).ask()).strip()


def tamano(n):
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {u}" if u != "B" else f"{n} B"
        n /= 1024
    return f"{n:.1f} PB"


NOMBRE_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")


def valida_nombre(t):
    return True if NOMBRE_RE.match(t or "") else \
        "Usa minúsculas, números, '-' o '_' (máx. 32, sin espacios ni acentos)"


def valida_hora(t):
    return True if re.match(r"^([01]?\d|2[0-3]):[0-5]\d$", t or "") else "Formato HH:MM (24 h), ej. 23:30"


def valida_fecha(t):
    try:
        dt.datetime.strptime(t, "%Y-%m-%d")
        return True
    except ValueError:
        return "Formato AAAA-MM-DD, ej. 2026-11-30"


def valida_entero(minimo, maximo):
    def v(t):
        return True if t.isdigit() and minimo <= int(t) <= maximo else f"Número entre {minimo} y {maximo}"
    return v


# ─────────────────────────────────────────────────────────────────────────────
#  ENCABEZADOS Y BANNER
# ─────────────────────────────────────────────────────────────────────────────
GLIFOS = {
    "N": ["█   █", "██  █", "█ █ █", "█  ██", "█   █"],
    "O": [" ███ ", "█   █", "█   █", "█   █", " ███ "],
    "V": ["█   █", "█   █", "█   █", " █ █ ", "  █  "],
    "A": [" ███ ", "█   █", "█████", "█   █", "█   █"],
    "M": ["█   █", "██ ██", "█ █ █", "█   █", "█   █"],
    "T": ["█████", "  █  ", "  █  ", "  █  ", "  █  "],
    "R": ["████ ", "█   █", "████ ", "█  █ ", "█   █"],
    "S": [" ████", "█    ", " ███ ", "    █", "████ "],
    "E": ["█████", "█    ", "████ ", "█    ", "█████"],
    "L": ["█    ", "█    ", "█    ", "█    ", "█████"],
    "I": ["█████", "  █  ", "  █  ", "  █  ", "█████"],
    "C": [" ████", "█    ", "█    ", "█    ", " ████"],
    "D": ["████ ", "█   █", "█   █", "█   █", "████ "],
    "U": ["█   █", "█   █", "█   █", "█   █", " ███ "],
    "G": [" ████", "█    ", "█  ██", "█   █", " ████"],
    "P": ["████ ", "█   █", "████ ", "█    ", "█    "],
    "B": ["████ ", "█   █", "████ ", "█   █", "████ "],
    "K": ["█   █", "█  █ ", "███  ", "█  █ ", "█   █"],
    "H": ["█   █", "█   █", "█████", "█   █", "█   █"],
    "F": ["█████", "█    ", "████ ", "█    ", "█    "],
    "Y": ["█   █", " █ █ ", "  █  ", "  █  ", "  █  "],
    " ": ["   ", "   ", "   ", "   ", "   "],
}
DEGRADADO = ["#00d7ff", "#00afff", "#0087ff", "#5f5fff", "#8787ff"]

AUTO = [
    "        ______________",
    "   ____//__][__][___|_\\____",
    "  |  _     NOVA     _      |",
    "  '-(_)-------------(_)----'",
]


def titulo_grande(texto):
    lineas = []
    for fila in range(5):
        t = Text()
        for letra in texto.upper():
            t.append(GLIFOS.get(letra, GLIFOS[" "])[fila] + " ", style=f"bold {DEGRADADO[fila]}")
        lineas.append(t)
    return Group(*lineas)


def datos_sistema():
    host = socket.gethostname()
    ip = "sin red"
    c, s, _ = ejecutar(["hostname", "-I"])
    if c == 0 and s.split():
        ip = s.split()[0]
    try:
        with open("/proc/uptime") as f:
            seg = int(float(f.read().split()[0]))
        uptime = f"{seg // 86400}d {seg % 86400 // 3600}h {seg % 3600 // 60}m"
    except OSError:
        uptime = "?"
    distro = "Linux"
    try:
        with open("/etc/os-release") as f:
            for l in f:
                if l.startswith("PRETTY_NAME="):
                    distro = l.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return host, ip, uptime, distro


def banner():
    console.clear()
    host, ip, uptime, distro = datos_sistema()
    auto = Text("\n".join(AUTO), style=f"bold {ACENTO}")
    cuerpo = Table.grid(padding=(0, 3))
    cuerpo.add_column()
    cuerpo.add_column(vertical="middle")
    cuerpo.add_row(titulo_grande(EMPRESA), auto)
    info_t = Text.assemble(
        ("🖥  ", ""), (host, f"bold {COLOR}"), ("   🌐 ", ""), (ip, "bold"),
        ("   🐧 ", ""), (distro, "bold"), ("   ⏱  ", ""), (uptime, "bold"),
        ("   👤 ", ""), (os.environ.get("SUDO_USER", "root"), "bold"),
        ("   📅 ", ""), (dt.datetime.now().strftime("%d/%m/%Y %H:%M"), "bold"),
    )
    console.print(Panel(
        Group(Align.center(cuerpo), Text(""), Align.center(Text(LEMA, style=f"italic {ACENTO}")),
              Align.center(info_t)),
        border_style=COLOR, box=box.HEAVY, padding=(1, 2),
        title=f"[bold white on {COLOR}]  CONSOLA DE ADMINISTRACIÓN  [/]",
        subtitle="[dim]Proyecto ADR 2026 · Administración de Redes[/]"))


def cabecera(ruta_menu, descripcion=""):
    console.clear()
    migas = Text()
    migas.append(f" {EMPRESA} ", style=f"bold white on {COLOR}")
    for i, parte in enumerate(ruta_menu):
        migas.append("  ›  ", style="dim")
        migas.append(parte, style=f"bold {ACENTO}" if i == len(ruta_menu) - 1 else "bold")
    contenido = Group(migas, Text(descripcion, style="dim italic")) if descripcion else migas
    console.print(Panel(contenido, border_style=COLOR, box=box.ROUNDED, padding=(0, 1)))


class _Volver(Exception):
    """Regresa al menú anterior sin pedir 'presiona una tecla'."""


def sin_pausa(funcion):
    def envoltura():
        funcion()
        raise _Volver
    return envoltura


def submenu(ruta_menu, opciones, descripcion=""):
    while True:
        cabecera(ruta_menu, descripcion)
        lista = [Choice(e, value=i) for i, (e, _) in enumerate(opciones)]
        lista += [Separator("  ─────────────────────────"), Choice("⬅  Regresar", value=-1)]
        sel = questionary.select("Elige una opción:", choices=lista, qmark="  ▸", pointer="❯",
                                 style=ESTILO, instruction="(↑↓ para moverte, Enter para elegir)").ask()
        if sel is None or sel == -1:
            return
        etiqueta, funcion = opciones[sel]
        cabecera(ruta_menu + [re.sub(r"^\W+\s*", "", etiqueta)])
        try:
            funcion()
        except _Volver:
            continue
        except Cancelado:
            aviso("Operación cancelada.")
        except KeyboardInterrupt:
            aviso("Operación interrumpida.")
        pausa()


def tabla(titulo, columnas, filas, vacio="Sin registros"):
    t = Table(title=f"[bold {ACENTO}]{titulo}[/]", box=box.ROUNDED, border_style=COLOR,
              header_style=f"bold white on {COLOR}", row_styles=["", "on grey11"], expand=False)
    for c in columnas:
        if isinstance(c, tuple):
            t.add_column(c[0], **c[1])
        else:
            t.add_column(c)
    for f in filas:
        t.add_row(*[escape(str(x)) for x in f])
    if not filas:
        t.add_row(*([f"[dim]{vacio}[/]"] + [""] * (len(columnas) - 1)))
    console.print(t)


# ─────────────────────────────────────────────────────────────────────────────
#  1. GESTIÓN · USUARIOS
# ─────────────────────────────────────────────────────────────────────────────
def usuarios_humanos():
    return sorted([u for u in pwd.getpwall() if u.pw_uid >= UID_MIN and u.pw_uid != 65534],
                  key=lambda u: u.pw_uid)


def grupos_de(usuario):
    try:
        prim = grp.getgrgid(pwd.getpwnam(usuario).pw_gid).gr_name
    except KeyError:
        prim = "?"
    sec = [g.gr_name for g in grp.getgrall() if usuario in g.gr_mem]
    return prim, sec


def grupos_planta():
    return sorted([g for g in grp.getgrall() if g.gr_gid >= UID_MIN and g.gr_gid != 65534],
                  key=lambda g: g.gr_gid)


def elegir_usuario(msg="Selecciona el usuario:"):
    us = usuarios_humanos()
    if not us:
        aviso("No hay usuarios creados todavía.")
        raise Cancelado
    return elegir(msg, [Choice(f"{u.pw_name:<16} {u.pw_gecos.split(',')[0]}", value=u.pw_name) for u in us])


def usuario_alta():
    console.print(Panel("Registra a un nuevo colaborador de la planta.", border_style="green"))
    nombre = pregunta("Nombre de usuario (login):", validar=valida_nombre)
    try:
        pwd.getpwnam(nombre)
        error(f"El usuario '{nombre}' ya existe.")
        return
    except KeyError:
        pass
    completo = pregunta("Nombre completo:")
    puesto = pregunta("Puesto (ej. Operador de línea, Supervisor):")
    existentes = [g.gr_name for g in grupos_planta()]
    grupos = [d for d in DEPARTAMENTOS if d in existentes] + \
             [g for g in existentes if g not in DEPARTAMENTOS]
    depto = None
    if grupos:
        depto = elegir("Departamento (grupo):", ["(ninguno)"] + grupos)
        depto = None if depto == "(ninguno)" else depto
    shell = elegir("Shell de inicio:", [
        Choice("/bin/bash  (acceso normal)", value="/bin/bash"),
        Choice("/sbin/nologin  (sin acceso a terminal)", value="/sbin/nologin")])
    p1 = clave("Contraseña:")
    p2 = clave("Repite la contraseña:")
    if p1 != p2 or not p1:
        error("Las contraseñas no coinciden o están vacías.")
        return

    resumen = Table.grid(padding=(0, 2))
    for k, v in [("Login", nombre), ("Nombre", completo), ("Puesto", puesto),
                 ("Departamento", depto or "—"), ("Shell", shell)]:
        resumen.add_row(f"[dim]{k}[/]", f"[bold]{escape(v)}[/]")
    console.print(Panel(resumen, title="Resumen", border_style=COLOR))
    if not confirmar("¿Crear el usuario?", True):
        raise Cancelado

    cmd = ["useradd", "-m", "-c", f"{completo},{puesto}", "-s", shell]
    if depto:
        cmd += ["-G", depto]
    c, _, e = ejecutar(cmd + [nombre])
    if c != 0:
        error(f"No se pudo crear: {e}")
        return
    c, _, e = ejecutar(["chpasswd"], entrada=f"{nombre}:{p1}\n")
    if c != 0:
        aviso(f"Usuario creado pero falló la contraseña: {e}")
    registrar(f"ALTA usuario {nombre} ({depto})")
    ok(f"Usuario [bold]{nombre}[/] creado con su carpeta /home/{nombre}.")


def usuario_baja():
    nombre = elegir_usuario("¿Qué usuario darás de baja?")
    borrar_home = confirmar(f"¿Borrar también su carpeta personal (/home/{nombre})?", False)
    console.print(f"\n  [bold red]Se eliminará la cuenta '{nombre}'.[/]")
    if not confirmar("¿Seguro que deseas continuar?", False):
        raise Cancelado
    ejecutar(["pkill", "-KILL", "-u", nombre])
    c, _, e = ejecutar(["userdel"] + (["-r"] if borrar_home else []) + [nombre])
    if c not in (0, 12):  # 12 = no pudo borrar home (ya no existía)
        error(f"No se pudo eliminar: {e}")
        return
    registrar(f"BAJA usuario {nombre} (home borrado={borrar_home})")
    ok(f"Usuario [bold]{nombre}[/] eliminado.")


def usuario_modificar():
    nombre = elegir_usuario("¿Qué usuario quieres modificar?")
    accion = elegir("¿Qué deseas modificar?", [
        Choice("📝 Nombre completo / puesto", value="gecos"),
        Choice("🔑 Contraseña", value="pass"),
        Choice("👥 Agregar a departamentos (grupos)", value="addg"),
        Choice("➖ Quitar de un departamento", value="delg"),
        Choice("💻 Shell de inicio", value="shell"),
        Choice("🔒 Bloquear / desbloquear cuenta", value="lock"),
        Choice("🏷  Cambiar nombre de login", value="login"),
    ])
    if accion == "gecos":
        comp = pregunta("Nuevo nombre completo:")
        puesto = pregunta("Nuevo puesto:")
        c, _, e = ejecutar(["usermod", "-c", f"{comp},{puesto}", nombre])
    elif accion == "pass":
        p1, p2 = clave("Nueva contraseña:"), clave("Repítela:")
        if p1 != p2 or not p1:
            error("No coinciden.")
            return
        c, _, e = ejecutar(["chpasswd"], entrada=f"{nombre}:{p1}\n")
    elif accion == "addg":
        _, actuales = grupos_de(nombre)
        disp = [g.gr_name for g in grupos_planta() if g.gr_name not in actuales]
        if not disp:
            aviso("No hay más grupos disponibles.")
            return
        sel = varios("Departamentos a agregar:", disp)
        if not sel:
            raise Cancelado
        c, _, e = ejecutar(["usermod", "-aG", ",".join(sel), nombre])
    elif accion == "delg":
        _, actuales = grupos_de(nombre)
        if not actuales:
            aviso("El usuario no pertenece a grupos secundarios.")
            return
        g = elegir("¿De cuál grupo lo quitas?", actuales)
        c, _, e = ejecutar(["gpasswd", "-d", nombre, g])
    elif accion == "shell":
        sh = elegir("Nueva shell:", ["/bin/bash", "/bin/sh", "/sbin/nologin"])
        c, _, e = ejecutar(["usermod", "-s", sh, nombre])
    elif accion == "lock":
        _, est, _ = ejecutar(["passwd", "-S", nombre])
        bloqueado = len(est.split()) > 1 and est.split()[1] in ("L", "LK")
        if bloqueado:
            c, _, e = ejecutar(["usermod", "-U", nombre])
            accion = "desbloqueo"
        else:
            c, _, e = ejecutar(["usermod", "-L", nombre])
            accion = "bloqueo"
    elif accion == "login":
        nuevo = pregunta("Nuevo login:", validar=valida_nombre)
        c, _, e = ejecutar(["usermod", "-l", nuevo, "-d", f"/home/{nuevo}", "-m", nombre])
        if c == 0:
            ejecutar(["groupmod", "-n", nuevo, nombre])
    if c != 0:
        error(f"No se pudo modificar: {e}")
        return
    registrar(f"MODIFICAR usuario {nombre}: {accion}")
    ok(f"Usuario [bold]{nombre}[/] actualizado ({accion}).")


def usuario_consulta():
    filas = []
    for u in usuarios_humanos():
        prim, sec = grupos_de(u.pw_name)
        gecos = (u.pw_gecos.split(",") + ["", ""])[:2]
        _, est, _ = ejecutar(["passwd", "-S", u.pw_name])
        estado = "🔒 Bloq." if len(est.split()) > 1 and est.split()[1] in ("L", "LK") else "✅ Activo"
        filas.append([u.pw_name, u.pw_uid, gecos[0], gecos[1], ", ".join(sec) or "—", u.pw_shell, estado])
    tabla("Colaboradores registrados en el servidor",
          ["Login", ("UID", {"justify": "right"}), "Nombre", "Puesto", "Departamentos", "Shell", "Estado"],
          filas)
    if filas and confirmar("¿Ver el detalle de un usuario?", False):
        nombre = elegir_usuario()
        _, idt, _ = ejecutar(["id", nombre])
        _, chg, _ = ejecutar(["chage", "-l", nombre])
        _, ult, _ = ejecutar(["last", "-n", "3", nombre])
        console.print(Panel(Group(Text(idt.strip(), style="bold"), Text(""), Text(chg.strip()),
                                  Text(""), Text("Últimos accesos:", style=f"bold {ACENTO}"),
                                  Text(ult.strip() or "Sin registros")),
                            title=f"Detalle de {nombre}", border_style=COLOR))


# ─────────────────────────────────────────────────────────────────────────────
#  1. GESTIÓN · GRUPOS
# ─────────────────────────────────────────────────────────────────────────────
def elegir_grupo(msg="Selecciona el grupo:"):
    gs = grupos_planta()
    if not gs:
        aviso("No hay grupos creados.")
        raise Cancelado
    return elegir(msg, [Choice(f"{g.gr_name:<18} (GID {g.gr_gid}, {len(g.gr_mem)} miembros)",
                               value=g.gr_name) for g in gs])


def grupo_alta():
    nombre = pregunta("Nombre del grupo / departamento:", validar=valida_nombre)
    try:
        grp.getgrnam(nombre)
        error(f"El grupo '{nombre}' ya existe.")
        return
    except KeyError:
        pass
    gid = pregunta("GID (deja vacío para asignar automático):",
                   validar=lambda t: True if t == "" or t.isdigit() else "Solo números")
    c, _, e = ejecutar(["groupadd"] + (["-g", gid] if gid else []) + [nombre])
    if c != 0:
        error(f"No se pudo crear: {e}")
        return
    registrar(f"ALTA grupo {nombre}")
    ok(f"Grupo [bold]{nombre}[/] creado.")
    us = [u.pw_name for u in usuarios_humanos()]
    if us and confirmar("¿Agregar miembros ahora?", False):
        for u in varios("Usuarios a agregar:", us):
            ejecutar(["gpasswd", "-a", u, nombre])
        ok("Miembros agregados.")


def grupo_baja():
    nombre = elegir_grupo("¿Qué grupo darás de baja?")
    if not confirmar(f"¿Eliminar el grupo '{nombre}'?", False):
        raise Cancelado
    c, _, e = ejecutar(["groupdel", nombre])
    if c != 0:
        error(f"No se pudo eliminar: {e}"
              + ("\n    (Es grupo principal de algún usuario; elimina o cambia ese usuario primero.)"
                 if c == 8 else ""))
        return
    registrar(f"BAJA grupo {nombre}")
    ok(f"Grupo [bold]{nombre}[/] eliminado.")


def grupo_modificar():
    nombre = elegir_grupo("¿Qué grupo quieres modificar?")
    accion = elegir("¿Qué deseas hacer?", [
        Choice("➕ Agregar miembros", value="add"),
        Choice("➖ Quitar miembros", value="del"),
        Choice("🏷  Renombrar grupo", value="ren"),
        Choice("🔢 Cambiar GID", value="gid"),
    ])
    g = grp.getgrnam(nombre)
    if accion == "add":
        disp = [u.pw_name for u in usuarios_humanos() if u.pw_name not in g.gr_mem]
        if not disp:
            aviso("Todos los usuarios ya son miembros.")
            return
        for u in varios("Usuarios a agregar:", disp):
            ejecutar(["gpasswd", "-a", u, nombre])
    elif accion == "del":
        if not g.gr_mem:
            aviso("El grupo no tiene miembros.")
            return
        for u in varios("Usuarios a quitar:", g.gr_mem):
            ejecutar(["gpasswd", "-d", u, nombre])
    elif accion == "ren":
        nuevo = pregunta("Nuevo nombre:", validar=valida_nombre)
        c, _, e = ejecutar(["groupmod", "-n", nuevo, nombre])
        if c != 0:
            error(e)
            return
    elif accion == "gid":
        nuevo = pregunta("Nuevo GID:", validar=valida_entero(1000, 60000))
        c, _, e = ejecutar(["groupmod", "-g", nuevo, nombre])
        if c != 0:
            error(e)
            return
    registrar(f"MODIFICAR grupo {nombre}: {accion}")
    ok(f"Grupo [bold]{nombre}[/] actualizado.")


def grupo_consulta():
    usuarios = usuarios_humanos()
    filas = []
    for g in grupos_planta():
        prim = [u.pw_name for u in usuarios if u.pw_gid == g.gr_gid]
        miembros = sorted(set(g.gr_mem) | set(prim))
        filas.append([g.gr_name, g.gr_gid, len(miembros), ", ".join(miembros) or "—"])
    tabla("Grupos / departamentos de la planta",
          ["Grupo", ("GID", {"justify": "right"}), ("Miembros", {"justify": "center"}), "Usuarios"], filas)


def inicializar_planta():
    console.print(Panel(
        f"Crea los grupos de cada departamento de la armadora:\n[bold]{', '.join(DEPARTAMENTOS)}[/]\n"
        f"y la estructura de carpetas [bold]{DIR_PLANTA}/<depto>[/] con archivos de ejemplo\n"
        "(órdenes de producción, inspecciones, inventario...). Útil para probar los respaldos.",
        title="🏭 Inicializar estructura de la planta", border_style=ACENTO))
    if not confirmar("¿Continuar?", True):
        raise Cancelado
    for d in DEPARTAMENTOS:
        try:
            grp.getgrnam(d)
        except KeyError:
            ejecutar(["groupadd", d])
        carpeta = os.path.join(DIR_PLANTA, d)
        os.makedirs(carpeta, exist_ok=True)
        ejecutar(["chgrp", d, carpeta])
        os.chmod(carpeta, 0o2770)
        with open(os.path.join(carpeta, f"reporte_{d}_{dt.date.today()}.csv"), "w") as f:
            f.write("folio,modelo,linea,turno,estado\n")
            for i in range(1, 51):
                f.write(f"{d[:3].upper()}-{i:04d},Nova {['Sedan', 'SUV', 'Pickup'][i % 3]},"
                        f"L{i % 4 + 1},{['Matutino', 'Vespertino', 'Nocturno'][i % 3]},"
                        f"{['OK', 'En proceso', 'Retrabajo'][i % 3]}\n")
        with open(os.path.join(carpeta, "LEEME.txt"), "w") as f:
            f.write(f"Carpeta compartida del departamento de {d} - {EMPRESA}\n")
    os.makedirs(DIR_RESPALDOS, exist_ok=True)
    registrar("Inicializó estructura de la planta")
    ok(f"Departamentos y carpetas creados en {DIR_PLANTA}")
    en_vivo(["ls", "-l", DIR_PLANTA], "Contenido de " + DIR_PLANTA, largo=False)


# ─────────────────────────────────────────────────────────────────────────────
#  1. GESTIÓN · PROCESOS
# ─────────────────────────────────────────────────────────────────────────────
def leer_ps(args, campos):
    c, s, e = ejecutar(["ps"] + args)
    if c != 0 and e:
        error(e)
        return []
    return [l.split(None, campos - 1) for l in s.splitlines() if l.strip()]


def procesos_usuario():
    _, s, _ = ejecutar(["who"])
    sesiones = [l.split() for l in s.splitlines() if l.strip()]
    if sesiones:
        tabla("Sesiones activas (who)", ["Usuario", "Terminal", "Desde"],
              [[x[0], x[1], " ".join(x[2:4]) + (" " + x[4] if len(x) > 4 else "")] for x in sesiones])
    activos = sorted({x[0] for x in sesiones})
    otros = sorted({u.pw_name for u in usuarios_humanos()} | {"root"} - set(activos))
    opciones = [Choice(f"🟢 {u}  (sesión activa)", value=u) for u in activos] + \
               [Choice(f"⚪ {u}", value=u) for u in otros]
    usuario = elegir("¿De qué usuario quieres ver los procesos?", opciones)
    filas = leer_ps(["-u", usuario, "-o", "pid=,ppid=,pcpu=,pmem=,stat=,etime=,args=",
                     "--sort=-pcpu"], 7)
    tabla(f"Procesos del usuario {usuario}  ({len(filas)})",
          [("PID", {"justify": "right"}), ("PPID", {"justify": "right"}), ("%CPU", {"justify": "right"}),
           ("%MEM", {"justify": "right"}), "Estado", "Tiempo", ("Comando", {"overflow": "ellipsis", "no_wrap": True,
                                                                           "max_width": 60})],
          [f for f in filas[:40] if len(f) == 7])
    acciones_proceso()


def procesos_sistema():
    filas = leer_ps(["-eo", "pid=,user=,pcpu=,pmem=,stat=,etime=,args=", "--sort=-pcpu"], 7)
    _, cuenta, _ = ejecutar(["sh", "-c", "ps -e --no-headers | wc -l"])
    tabla(f"Procesos del sistema — top 30 por CPU  (total: {cuenta.strip()})",
          [("PID", {"justify": "right"}), "Usuario", ("%CPU", {"justify": "right"}),
           ("%MEM", {"justify": "right"}), "Estado", "Tiempo",
           ("Comando", {"overflow": "ellipsis", "no_wrap": True, "max_width": 60})],
          [f for f in filas[:30] if len(f) == 7])
    acciones_proceso()


def acciones_proceso():
    acc = elegir("¿Qué deseas hacer?", [
        Choice("Nada, regresar", value=None),
        Choice("📺 Ver en tiempo real (top)", value="top"),
        Choice("🌳 Ver árbol de procesos (pstree)", value="tree"),
        Choice("⛔ Terminar un proceso (kill)", value="kill"),
    ])
    if acc == "top":
        interactivo(["htop"] if existe("htop") else ["top"])
    elif acc == "tree":
        en_vivo(["pstree", "-pu"] if existe("pstree") else ["ps", "-ejH"], "Árbol de procesos")
    elif acc == "kill":
        pid = pregunta("PID del proceso:", validar=valida_entero(2, 4194304))
        sen = elegir("Señal:", [Choice("SIGTERM (cerrar amablemente)", value="TERM"),
                                Choice("SIGKILL (forzar)", value="KILL")])
        c, _, e = ejecutar(["kill", f"-{sen}", pid])
        if c == 0:
            registrar(f"KILL -{sen} {pid}")
            ok(f"Señal {sen} enviada al proceso {pid}.")
        else:
            error(e)


# ─────────────────────────────────────────────────────────────────────────────
#  2. AUTOMATIZACIÓN DE TAREAS · CRON / AT
# ─────────────────────────────────────────────────────────────────────────────
SCRIPT_ESTADO = "/usr/local/bin/planta-estado.sh"
SCRIPT_RESPALDO = "/usr/local/bin/planta-respaldo.sh"

PLANTILLAS = [
    ("📊 Reporte de estado del servidor (CPU, RAM, disco) → /var/log/planta/estado.log",
     SCRIPT_ESTADO),
    ("💾 Respaldo de producción (tar.gz) → /respaldos/automaticos", SCRIPT_RESPALDO),
    ("🧹 Limpiar archivos de /tmp con más de 7 días",
     "find /tmp -type f -mtime +7 -delete"),
    ("📢 Aviso de cambio de turno a todas las terminales",
     "wall 'NOVA MOTORS: Cambio de turno en 15 minutos. Guarde su trabajo.'"),
    ("🔄 Actualizar paquetes del sistema (dnf)", "dnf -y update"),
    ("⏻  Reiniciar el servidor", "/sbin/shutdown -r now"),
]


def crear_scripts_ejemplo():
    os.makedirs("/var/log/planta", exist_ok=True)
    with open(SCRIPT_ESTADO, "w") as f:
        f.write("""#!/bin/bash
# NOVA MOTORS - Reporte de estado del servidor de planta
LOG=/var/log/planta/estado.log
CPU=$(cut -d' ' -f1-3 /proc/loadavg)
RAM=$(free -m | awk '/Mem:/ {printf "%d/%d MB", $3, $2}')
DISCO=$(df -h / | awk 'NR==2 {print $5}')
echo "$(date '+%F %T') | Carga: $CPU | RAM: $RAM | Disco /: $DISCO | Usuarios: $(who | wc -l)" >> "$LOG"
""")
    with open(SCRIPT_RESPALDO, "w") as f:
        f.write(f"""#!/bin/bash
# NOVA MOTORS - Respaldo automático de la carpeta de producción
DESTINO={DIR_RESPALDOS}/automaticos
mkdir -p "$DESTINO"
tar -czf "$DESTINO/produccion_$(date +%F_%H%M).tar.gz" -C {DIR_PLANTA} produccion 2>/dev/null
# conservar solo los últimos 7 respaldos
ls -1t "$DESTINO"/produccion_*.tar.gz 2>/dev/null | tail -n +8 | xargs -r rm -f
echo "$(date '+%F %T') Respaldo de produccion OK" >> /var/log/planta/respaldos.log
""")
    os.chmod(SCRIPT_ESTADO, 0o755)
    os.chmod(SCRIPT_RESPALDO, 0o755)


def elegir_tarea():
    """Pregunta qué tarea programar: plantilla o comando propio."""
    opciones = [Choice(t, value=i) for i, (t, _) in enumerate(PLANTILLAS)]
    opciones += [Separator(), Choice("⌨  Escribir mi propio comando", value=-1)]
    sel = elegir("¿Qué tarea quieres programar?", opciones)
    if sel == -1:
        comando = pregunta("Comando a ejecutar (ruta completa recomendada):",
                           validar=lambda t: True if t.strip() else "Escribe un comando")
        desc = pregunta("Descripción corta de la tarea:") or "Tarea personalizada"
    else:
        desc, comando = PLANTILLAS[sel]
        desc = re.sub(r"^\W+\s*", "", desc).split("→")[0].strip()
        if comando.startswith("/usr/local/bin/planta-"):
            crear_scripts_ejemplo()
    return comando, desc


DIAS = ["domingo", "lunes", "martes", "miércoles", "jueves", "viernes", "sábado"]
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
         "septiembre", "octubre", "noviembre", "diciembre"]


def preguntar_horario_cron():
    tipo = elegir("¿Cada cuándo se ejecutará?", [
        Choice("⏱  Cada N minutos", value="min"),
        Choice("🕐 Cada hora", value="hora"),
        Choice("📅 Diario a una hora", value="dia"),
        Choice("🏭 Días hábiles (lun-vie) a una hora", value="habil"),
        Choice("🗓  Un día de la semana", value="semana"),
        Choice("📆 Un día de cada mes", value="mes"),
        Choice("🎯 En una fecha específica (día y mes)", value="fecha"),
        Choice("🛠  Expresión cron manual (5 campos)", value="manual"),
    ])
    if tipo == "min":
        n = pregunta("¿Cada cuántos minutos?", "5", valida_entero(1, 59))
        return f"*/{n} * * * *", f"cada {n} minutos"
    if tipo == "hora":
        m = pregunta("¿En qué minuto de cada hora? (0-59)", "0", valida_entero(0, 59))
        return f"{int(m)} * * * *", f"cada hora al minuto {m}"
    if tipo == "manual":
        e = pregunta("Expresión (min hora día mes díaSemana):", "30 23 * * *",
                     lambda t: True if len(t.split()) == 5 else "Deben ser 5 campos")
        return e, f"cron '{e}'"
    fe = None
    if tipo == "fecha":
        fe = dt.datetime.strptime(pregunta("Fecha (AAAA-MM-DD):", (dt.date.today() + dt.timedelta(days=1))
                                           .isoformat(), valida_fecha), "%Y-%m-%d")
    h, m = map(int, pregunta("Hora (HH:MM, 24 h):", "23:30", valida_hora).split(":"))
    hora = f"{h:02d}:{m:02d}"
    if tipo == "dia":
        return f"{m} {h} * * *", f"diario a las {hora}"
    if tipo == "habil":
        return f"{m} {h} * * 1-5", f"lunes a viernes a las {hora}"
    if tipo == "semana":
        d = elegir("Día de la semana:", [Choice(n.capitalize(), value=i) for i, n in enumerate(DIAS)])
        return f"{m} {h} * * {d}", f"cada {DIAS[d]} a las {hora}"
    if tipo == "mes":
        d = pregunta("Día del mes (1-31):", "1", valida_entero(1, 31))
        return f"{m} {h} {int(d)} * *", f"el día {d} de cada mes a las {hora}"
    return f"{m} {h} {fe.day} {fe.month} *", f"cada {fe.day} de {MESES[fe.month - 1]} a las {hora}"


def leer_crontab(usuario):
    c, s, _ = ejecutar(["crontab", "-l", "-u", usuario])
    return s.splitlines() if c == 0 else []


def escribir_crontab(usuario, lineas):
    contenido = "\n".join(lineas).strip("\n") + "\n"
    return ejecutar(["crontab", "-u", usuario, "-"], entrada=contenido)


def entradas_cron(lineas):
    """Regresa lista de (indice_linea, horario, comando, descripcion)."""
    res, desc = [], ""
    for i, l in enumerate(lineas):
        s = l.strip()
        if s.startswith("# ADR |"):
            desc = s[len("# ADR |"):].strip()
            continue
        if not s or s.startswith("#") or re.match(r"^[A-Z_]+=", s):
            desc = ""
            continue
        partes = s.split(None, 5)
        if s.startswith("@"):
            horario, cmd = s.split(None, 1)[0], s.split(None, 1)[1] if " " in s else ""
        elif len(partes) == 6:
            horario, cmd = " ".join(partes[:5]), partes[5]
        else:
            continue
        res.append((i, horario, cmd, desc))
        desc = ""
    return res


def elegir_usuario_cron():
    us = ["root"] + [u.pw_name for u in usuarios_humanos()]
    return elegir("¿Crontab de qué usuario?", us, default="root")


def cron_listar():
    usuario = elegir_usuario_cron()
    ent = entradas_cron(leer_crontab(usuario))
    filas = [[n + 1, h, d.split(" | ")[0] if d else "—", d.split(" | ")[1] if " | " in d else "—", c]
             for n, (_, h, c, d) in enumerate(ent)]
    tabla(f"Tareas cron de {usuario}",
          [("#", {"justify": "right"}), ("Horario", {"style": ACENTO}), "Tarea", "Cuándo",
           ("Comando", {"overflow": "fold", "max_width": 55})], filas)
    _, act, _ = ejecutar(["systemctl", "is-active", "crond"])
    if act.strip() and act.strip() != "active":
        aviso("El servicio crond no está activo. Actívalo con: systemctl enable --now crond")
    if ent and confirmar("¿Eliminar alguna tarea?", False):
        idx = elegir("¿Cuál?", [Choice(f"#{n + 1}  {h}  {c[:60]}", value=n)
                                for n, (_, h, c, _) in enumerate(ent)])
        lineas = leer_crontab(usuario)
        quitar = {ent[idx][0]}
        if ent[idx][0] > 0 and lineas[ent[idx][0] - 1].startswith("# ADR |"):
            quitar.add(ent[idx][0] - 1)
        escribir_crontab(usuario, [l for i, l in enumerate(lineas) if i not in quitar])
        registrar(f"CRON eliminado ({usuario}): {ent[idx][1]} {ent[idx][2]}")
        ok("Tarea eliminada.")


def cron_programar():
    comando, desc = elegir_tarea()
    horario, texto = preguntar_horario_cron()
    usuario = elegir_usuario_cron()
    console.print(Panel(f"[dim]Tarea:[/]   [bold]{escape(desc)}[/]\n"
                        f"[dim]Cuándo:[/]  [bold]{texto}[/]   [dim]({horario})[/]\n"
                        f"[dim]Usuario:[/] [bold]{usuario}[/]\n"
                        f"[dim]Comando:[/] [bold]{escape(comando)}[/]",
                        title="Nueva tarea cron", border_style=COLOR))
    if not confirmar("¿Programar?", True):
        raise Cancelado
    lineas = leer_crontab(usuario)
    lineas += [f"# ADR | {desc} | {texto}", f"{horario} {comando.replace('%', chr(92) + '%')}"]
    c, _, e = escribir_crontab(usuario, lineas)
    if c != 0:
        error(f"No se pudo guardar: {e}")
        return
    ejecutar(["systemctl", "enable", "--now", "crond"])
    registrar(f"CRON programado ({usuario}): {horario} {comando}")
    ok(f"Tarea programada: [bold]{texto}[/].")


def cron_ejemplos():
    console.print(Panel(
        "Se instalarán 2 tareas de ejemplo en el crontab de root:\n\n"
        f" 1. [bold]Reporte de estado del servidor[/] cada 30 minutos → {SCRIPT_ESTADO}\n"
        f" 2. [bold]Respaldo de producción[/] de lunes a sábado a las 23:30 → {SCRIPT_RESPALDO}",
        title="Tareas de ejemplo", border_style=ACENTO))
    if not confirmar("¿Instalar?", True):
        raise Cancelado
    crear_scripts_ejemplo()
    lineas = [l for l in leer_crontab("root") if SCRIPT_ESTADO not in l and SCRIPT_RESPALDO not in l
              and "Ejemplo:" not in l]
    lineas += ["# ADR | Ejemplo: reporte de estado del servidor | cada 30 minutos",
               f"*/30 * * * * {SCRIPT_ESTADO}",
               "# ADR | Ejemplo: respaldo de producción | lunes a sábado a las 23:30",
               f"30 23 * * 1-6 {SCRIPT_RESPALDO}"]
    c, _, e = escribir_crontab("root", lineas)
    if c != 0:
        error(e)
        return
    ejecutar(["systemctl", "enable", "--now", "crond"])
    ejecutar([SCRIPT_ESTADO])
    registrar("CRON: tareas de ejemplo instaladas")
    ok("Tareas de ejemplo instaladas. Se ejecutó el reporte una vez para probar:")
    en_vivo(["tail", "-n", "3", "/var/log/planta/estado.log"], "/var/log/planta/estado.log", largo=False)


def at_servicio():
    if not existe("at"):
        error("El comando 'at' no está instalado (dnf install at).")
        raise Cancelado
    _, act, _ = ejecutar(["systemctl", "is-active", "atd"])
    if act.strip() != "active":
        ejecutar(["systemctl", "enable", "--now", "atd"])


def at_trabajos():
    _, s, _ = ejecutar(["atq"])
    trabajos = []
    for l in s.splitlines():
        p = l.split()
        if len(p) < 8:
            continue
        jid, fecha, cola, usuario = p[0], " ".join(p[1:6]), p[6], p[7]
        _, det, _ = ejecutar(["at", "-c", jid])
        cuerpo = det.splitlines()
        cmd = ""
        for i in range(len(cuerpo) - 1, -1, -1):
            if cuerpo[i].strip() == "}":
                cmd = " ; ".join(x for x in cuerpo[i + 1:] if x.strip())
                break
        trabajos.append((jid, fecha, cola, usuario, cmd))
    return trabajos


def at_listar():
    at_servicio()
    tr = at_trabajos()
    tabla("Tareas programadas con at (una sola vez)",
          [("ID", {"justify": "right"}), ("Fecha de ejecución", {"style": ACENTO}), "Cola", "Usuario",
           ("Comando", {"overflow": "fold", "max_width": 60})], tr, "No hay tareas pendientes")
    if tr and confirmar("¿Cancelar alguna tarea?", False):
        jid = elegir("¿Cuál?", [Choice(f"#{t[0]}  {t[1]}  {t[4][:50]}", value=t[0]) for t in tr])
        c, _, e = ejecutar(["atrm", jid])
        if c == 0:
            registrar(f"AT cancelada #{jid}")
            ok(f"Tarea #{jid} cancelada.")
        else:
            error(e)


def at_programar():
    at_servicio()
    comando, desc = elegir_tarea()
    cuando = elegir("¿Cuándo se ejecutará?", [
        Choice("⏱  Dentro de N minutos", value="min"),
        Choice("📅 Hoy a una hora", value="hoy"),
        Choice("🌅 Mañana a una hora", value="man"),
        Choice("🎯 En una fecha y hora específicas", value="fecha"),
    ])
    ahora = dt.datetime.now().replace(second=0, microsecond=0)
    if cuando == "min":
        n = int(pregunta("¿Dentro de cuántos minutos?", "2", valida_entero(1, 100000)))
        momento = ahora + dt.timedelta(minutes=n)
    else:
        if cuando == "fecha":
            f = dt.datetime.strptime(pregunta("Fecha (AAAA-MM-DD):", ahora.date().isoformat(),
                                              valida_fecha), "%Y-%m-%d").date()
        else:
            f = ahora.date() + dt.timedelta(days=1 if cuando == "man" else 0)
        h, m = map(int, pregunta("Hora (HH:MM, 24 h):", (ahora + dt.timedelta(minutes=5))
                                 .strftime("%H:%M"), valida_hora).split(":"))
        momento = dt.datetime.combine(f, dt.time(h, m))
    if momento <= ahora:
        error(f"La fecha {momento:%d/%m/%Y %H:%M} ya pasó.")
        return
    console.print(Panel(f"[dim]Tarea:[/]   [bold]{escape(desc)}[/]\n"
                        f"[dim]Cuándo:[/]  [bold]{DIAS[(momento.weekday() + 1) % 7]} "
                        f"{momento:%d/%m/%Y a las %H:%M}[/]\n"
                        f"[dim]Comando:[/] [bold]{escape(comando)}[/]",
                        title="Nueva tarea at", border_style=COLOR))
    if not confirmar("¿Programar?", True):
        raise Cancelado
    c, s, e = ejecutar(["at", "-t", momento.strftime("%Y%m%d%H%M")], entrada=comando + "\n")
    if c != 0:
        error(f"No se pudo programar: {e}")
        return
    registrar(f"AT programado {momento}: {comando}")
    ok(f"Tarea programada para el [bold]{momento:%d/%m/%Y %H:%M}[/]  "
       f"[dim]({escape(e.splitlines()[-1] if e else '')})[/]")


# ─────────────────────────────────────────────────────────────────────────────
#  3. RESPALDO DE LA INFORMACIÓN
# ─────────────────────────────────────────────────────────────────────────────
def pedir_origen_destino():
    origen = os.path.abspath(ruta("¿Qué carpeta vas a respaldar?",
                                  DIR_PLANTA + "/" if os.path.isdir(DIR_PLANTA) else "/home/"))
    if not os.path.isdir(origen):
        error(f"La carpeta {origen} no existe.")
        raise Cancelado
    destino = os.path.abspath(ruta("¿En qué ruta se depositará el respaldo?", DIR_RESPALDOS + "/"))
    if os.path.commonpath([origen, destino]) == origen:
        error("El destino no puede estar dentro de la carpeta que respaldas.")
        raise Cancelado
    os.makedirs(destino, exist_ok=True)
    return origen, destino


def contar_archivos(carpeta):
    total = 0
    for _, dirs, archivos in os.walk(carpeta):
        total += len(dirs) + len(archivos)
    return total + 1


def respaldo_tar(compresion):
    ext, bandera, nombre = {"gz": (".tar.gz", "z", "gzip"), "bz2": (".tar.bz2", "j", "bzip2")}[compresion]
    accion = elegir("¿Qué deseas hacer?", [
        Choice(f"📦 Crear respaldo (tar + {nombre})", value="crear"),
        Choice("📂 Restaurar un respaldo", value="restaurar"),
        Choice("🔍 Ver contenido de un respaldo", value="ver"),
    ])
    if accion != "crear":
        return tar_restaurar(ext, accion)

    origen, destino = pedir_origen_destino()
    base = os.path.basename(origen.rstrip("/")) or "raiz"
    archivo = os.path.join(destino, f"respaldo_{base}_{dt.datetime.now():%Y%m%d_%H%M%S}{ext}")
    total = contar_archivos(origen)
    console.print(f"\n  Respaldando [bold]{escape(origen)}[/] ({total} elementos) → "
                  f"[bold]{escape(archivo)}[/]\n")
    inicio = time.time()
    cmd = ["tar", f"-c{bandera}vf", archivo, "-C", os.path.dirname(origen.rstrip("/")) or "/", base]
    with Progress(SpinnerColumn(), TextColumn("[bold]{task.description}"), BarColumn(bar_width=40),
                  MofNCompleteColumn(), TimeElapsedColumn(), console=console) as prog:
        tarea = prog.add_task(f"Comprimiendo con {nombre}", total=total)
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        for _ in p.stdout:
            prog.advance(tarea)
        p.wait()
        prog.update(tarea, completed=total)
    if p.returncode not in (0, 1):
        error(f"tar terminó con error: {p.stderr.read().strip()}")
        return
    peso_orig = sum(os.path.getsize(os.path.join(r, f)) for r, _, fs in os.walk(origen)
                    for f in fs if os.path.isfile(os.path.join(r, f)))
    peso = os.path.getsize(archivo)
    registrar(f"RESPALDO tar.{compresion} {origen} -> {archivo}")
    resumen = Table.grid(padding=(0, 2))
    resumen.add_row("[dim]Archivo[/]", f"[bold]{escape(archivo)}[/]")
    resumen.add_row("[dim]Tamaño original[/]", tamano(peso_orig))
    resumen.add_row("[dim]Tamaño comprimido[/]", f"[bold green]{tamano(peso)}[/]")
    if peso_orig:
        resumen.add_row("[dim]Ahorro[/]", f"{100 - peso * 100 / peso_orig:.1f} %")
    resumen.add_row("[dim]Tiempo[/]", f"{time.time() - inicio:.1f} s")
    console.print(Panel(resumen, title="✔ Respaldo completado", border_style="green"))


def tar_restaurar(ext, accion):
    carpeta = ruta("Carpeta donde están los respaldos:", DIR_RESPALDOS + "/")
    archivos = []
    for r, _, fs in os.walk(carpeta):
        archivos += [os.path.join(r, f) for f in fs if f.endswith(ext)]
    if not archivos:
        aviso(f"No se encontraron archivos {ext} en {carpeta}")
        return
    archivos.sort(key=os.path.getmtime, reverse=True)
    arch = elegir("Selecciona el respaldo:", [
        Choice(f"{os.path.relpath(a, carpeta):<55} {tamano(os.path.getsize(a)):>10}  "
               f"{dt.datetime.fromtimestamp(os.path.getmtime(a)):%d/%m/%Y %H:%M}", value=a)
        for a in archivos])
    if accion == "ver":
        en_vivo(["tar", "-tvf", arch], f"Contenido de {os.path.basename(arch)}")
        return
    destino = os.path.abspath(ruta("¿En qué carpeta se restaurará?", "/tmp/restauracion/"))
    os.makedirs(destino, exist_ok=True)
    if not confirmar(f"Se extraerá en {destino} (los archivos iguales se sobrescriben). ¿Continuar?", True):
        raise Cancelado
    with console.status("[bold]Extrayendo...", spinner="dots"):
        c, _, e = ejecutar(["tar", "-xf", arch, "-C", destino])
    if c != 0:
        error(e)
        return
    registrar(f"RESTAURAR {arch} -> {destino}")
    ok(f"Respaldo restaurado en [bold]{escape(destino)}[/]")
    en_vivo(["ls", "-la", destino], destino, largo=False)


def respaldo_rsync():
    if not existe("rsync"):
        error("rsync no está instalado (dnf install rsync).")
        return
    console.print(Panel("rsync sincroniza carpetas: la primera vez copia todo y después solo "
                        "los cambios.\nEl destino puede ser local o remoto (usuario@servidor:/ruta).",
                        border_style=COLOR))
    origen = os.path.abspath(ruta("¿Qué carpeta vas a respaldar?", DIR_PLANTA + "/"))
    if not os.path.isdir(origen):
        error("La carpeta no existe.")
        return
    remoto = elegir("¿Destino local o en otro servidor?", [
        Choice("💽 Local (disco, USB, otra carpeta)", value=False),
        Choice("🌐 Remoto por SSH", value=True)])
    if remoto:
        destino = pregunta("Destino remoto (usuario@ip:/ruta):",
                           validar=lambda t: True if re.match(r"^[\w.-]+@[\w.-]+:.+", t) else
                           "Formato usuario@ip:/ruta")
    else:
        destino = os.path.abspath(ruta("¿En qué ruta se depositará?", DIR_RESPALDOS + "/rsync/"))
        os.makedirs(destino, exist_ok=True)
    opciones = varios("Opciones:", [
        Choice("Modo espejo (--delete): borra en destino lo que ya no existe en origen", value="--delete"),
        Choice("Comprimir durante la transferencia (-z)", value="-z", checked=remoto),
        Choice("Simulación: mostrar qué haría sin copiar (--dry-run)", value="--dry-run"),
    ])
    cmd = ["rsync", "-avh", "--stats"] + opciones + [origen.rstrip("/"), destino.rstrip("/") + "/"]
    info("Comando: [bold]" + escape(" ".join(cmd)) + "[/]")
    c = en_vivo(cmd, "Sincronizando con rsync")
    if c == 0:
        registrar(f"RESPALDO rsync {origen} -> {destino} {opciones}")
        ok(f"Sincronización completada en {escape(destino)}")
    else:
        error(f"rsync terminó con código {c}")


# ── dump / restore con USB ─────────────────────────────────────────────────
def detectar_usb():
    c, s, _ = ejecutar(["lsblk", "-J", "-b", "-o", "NAME,PATH,SIZE,FSTYPE,MOUNTPOINT,TRAN,RM,LABEL,TYPE,MODEL"])
    if c != 0:
        return []
    usbs = []

    def recorrer(dev, es_usb, modelo):
        es_usb = es_usb or dev.get("tran") == "usb" or str(dev.get("rm")) in ("1", "True", "true")
        modelo = dev.get("model") or modelo
        if es_usb and dev.get("type") == "part" or (es_usb and dev.get("type") == "disk"
                                                     and not dev.get("children") and dev.get("fstype")):
            usbs.append({**dev, "model": modelo})
        for h in dev.get("children", []) or []:
            recorrer(h, es_usb, modelo)

    for d in json.loads(s).get("blockdevices", []):
        if d.get("type") in ("rom", "loop"):
            continue
        recorrer(d, False, "")
    return usbs


def preparar_usb():
    """Detecta, (opcionalmente formatea) y monta la USB. Regresa el punto de montaje."""
    usbs = detectar_usb()
    opciones = [Choice(f"🔌 {u['path']:<12} {tamano(int(u['size'] or 0)):>9}  {u.get('fstype') or 'sin formato':<6}"
                       f" {u.get('label') or ''} {u.get('model') or ''}"
                       + (f"  (montada en {u['mountpoint']})" if u.get("mountpoint") else ""), value=u)
                for u in usbs]
    opciones += [Choice("📁 Usar una carpeta/punto de montaje manual", value="manual"),
                 Choice("🔄 Volver a buscar USB", value="otra")]
    if not usbs:
        aviso("No se detectó ninguna USB. Conéctala (en VirtualBox: Dispositivos › USB) y elige 'Volver a buscar'.")
    sel = elegir("Selecciona la USB de respaldo:", opciones)
    if sel == "otra":
        return preparar_usb()
    if sel == "manual":
        return os.path.abspath(ruta("Ruta donde está montada la USB:", "/mnt/"))

    dev = sel["path"]
    if sel.get("fstype") in ("vfat", "exfat", None, ""):
        aviso("La USB está en FAT/exFAT o sin formato. FAT32 limita cada archivo a 4 GB y no guarda "
              "permisos de Linux;\n    para un respaldo completo del sistema se recomienda ext4.")
        if confirmar(f"¿Formatear {dev} como ext4? (SE BORRARÁ TODO SU CONTENIDO)", False):
            if pregunta("Escribe FORMATEAR para confirmar:") != "FORMATEAR":
                raise Cancelado
            if sel.get("mountpoint"):
                ejecutar(["umount", dev])
            with console.status(f"Formateando {dev}..."):
                c, _, e = ejecutar(["mkfs.ext4", "-F", "-L", "RESPALDO", dev])
            if c != 0:
                error(e)
                raise Cancelado
            registrar(f"USB {dev} formateada ext4")
            sel["mountpoint"] = None
    if sel.get("mountpoint"):
        return sel["mountpoint"]
    os.makedirs(PUNTO_USB, exist_ok=True)
    c, _, e = ejecutar(["mount", dev, PUNTO_USB])
    if c != 0:
        error(f"No se pudo montar {dev}: {e}")
        raise Cancelado
    ok(f"USB {dev} montada en {PUNTO_USB}")
    return PUNTO_USB


def sistemas_archivos():
    c, s, _ = ejecutar(["findmnt", "-J", "-b", "-t", "xfs,ext2,ext3,ext4", "-o", "TARGET,SOURCE,FSTYPE,SIZE,USED"])
    if c != 0:
        return []
    return json.loads(s).get("filesystems", [])


def respaldo_dump():
    accion = elegir("¿Qué deseas hacer?", [
        Choice("💾 Respaldar el sistema completo a la USB (dump)", value="dump"),
        Choice("♻  Restaurar desde la USB (restore)", value="restore"),
        Choice("🔍 Ver contenido de un respaldo de la USB", value="ver"),
        Choice("⏏  Desmontar / expulsar la USB", value="umount"),
    ])
    if accion == "umount":
        c, _, e = ejecutar(["umount", PUNTO_USB])
        return ok("USB desmontada, ya puedes retirarla.") if c == 0 else error(e)
    usb = preparar_usb()
    carpeta = os.path.join(usb, "respaldo_sistema")
    os.makedirs(carpeta, exist_ok=True)
    if accion == "dump":
        return dump_crear(carpeta)
    return dump_restaurar(carpeta, accion)


def dump_crear(carpeta):
    fss = sistemas_archivos()
    if not fss:
        error("No se encontraron sistemas de archivos xfs/ext montados.")
        return
    tabla("Sistemas de archivos del servidor",
          ["Montaje", "Dispositivo", "Tipo", ("Tamaño", {"justify": "right"}), ("Usado", {"justify": "right"})],
          [[f["target"], f["source"], f["fstype"], tamano(int(f["size"])), tamano(int(f["used"]))] for f in fss])
    sel = varios("¿Qué particiones respaldar? (sistema completo = / y /boot)",
                 [Choice(f"{f['target']:<12} ({f['fstype']}, {tamano(int(f['used']))} usados)", value=f,
                         checked=f["target"] in ("/", "/boot")) for f in fss])
    if not sel:
        raise Cancelado
    st = os.statvfs(carpeta)
    libre, necesario = st.f_bavail * st.f_frsize, sum(int(f["used"]) for f in sel)
    info(f"Espacio libre en la USB: [bold]{tamano(libre)}[/] · Datos a respaldar: [bold]{tamano(necesario)}[/]")
    if necesario > libre and not confirmar("⚠ Puede que no alcance el espacio. ¿Continuar de todos modos?", False):
        raise Cancelado
    if not confirmar("Esto puede tardar varios minutos. ¿Iniciar respaldo?", True):
        raise Cancelado
    marca = f"{dt.datetime.now():%Y%m%d_%H%M}"
    for f in sel:
        nombre = "raiz" if f["target"] == "/" else f["target"].strip("/").replace("/", "_")
        if f["fstype"] == "xfs":
            if not existe("xfsdump"):
                error("xfsdump no está instalado (dnf install xfsdump).")
                continue
            arch = os.path.join(carpeta, f"{nombre}_{marca}.xfsdump")
            cmd = ["xfsdump", "-l", "0", "-L", f"{EMPRESA} {nombre}", "-M", "USB", "-f", arch, f["target"]]
        else:
            if not existe("dump"):
                error("dump no está instalado (dnf install dump, desde EPEL).")
                continue
            arch = os.path.join(carpeta, f"{nombre}_{marca}.dump")
            cmd = ["dump", "-0u", "-f", arch, f["target"]]
        c = en_vivo(cmd, f"{cmd[0]} de {f['target']}  ({f['fstype']}) → {arch}")
        if c == 0:
            registrar(f"DUMP {f['target']} -> {arch}")
            ok(f"{f['target']} respaldado: {escape(arch)} ({tamano(os.path.getsize(arch))})")
        else:
            error(f"Falló el respaldo de {f['target']} (código {c}).")
    os.sync()


def dump_restaurar(carpeta, accion):
    archivos = sorted([f for f in os.listdir(carpeta) if f.endswith((".dump", ".xfsdump"))], reverse=True)
    if not archivos:
        aviso(f"No hay respaldos en {carpeta}")
        return
    arch = os.path.join(carpeta, elegir("Selecciona el respaldo:", [
        Choice(f"{a:<40} {tamano(os.path.getsize(os.path.join(carpeta, a))):>10}", value=a) for a in archivos]))
    xfs = arch.endswith(".xfsdump")
    if accion == "ver":
        if xfs:
            en_vivo(["sh", "-c", f"xfsrestore -t -f '{arch}' 2>/dev/null | head -n 60"], "Contenido (primeros 60)")
        else:
            en_vivo(["sh", "-c", f"restore -tf '{arch}' | head -n 60"], "Contenido (primeros 60)")
        return
    console.print(Panel("Por seguridad, la restauración se hace en una carpeta aparte.\n"
                        "Para recuperar el sistema completo se arranca desde un Live USB, se monta el\n"
                        "disco nuevo y se restaura ahí (ej. /mnt/sysimage).", border_style="yellow"))
    destino = os.path.abspath(ruta("Carpeta destino de la restauración:", "/restauracion/"))
    if destino == "/" and pregunta("⚠ Restaurar sobre / sobrescribe el sistema. Escribe CONFIRMAR:") != "CONFIRMAR":
        raise Cancelado
    os.makedirs(destino, exist_ok=True)
    if xfs:
        c = en_vivo(["xfsrestore", "-f", arch, destino], f"xfsrestore → {destino}")
    else:
        c = en_vivo(["restore", "-rvf", arch], f"restore → {destino}", cwd=destino)
    if c == 0:
        registrar(f"RESTORE {arch} -> {destino}")
        ok(f"Restauración completada en {escape(destino)}")
    else:
        error(f"La restauración terminó con código {c}")


def listar_respaldos():
    carpeta = ruta("Carpeta de respaldos:", DIR_RESPALDOS + "/")
    filas = []
    for r, _, fs in os.walk(carpeta):
        for f in fs:
            if f.endswith((".tar.gz", ".tar.bz2", ".dump", ".xfsdump", ".tgz")):
                p = os.path.join(r, f)
                filas.append([os.path.relpath(p, carpeta), tamano(os.path.getsize(p)),
                              dt.datetime.fromtimestamp(os.path.getmtime(p)).strftime("%d/%m/%Y %H:%M")])
    filas.sort(key=lambda x: x[2], reverse=True)
    tabla(f"Respaldos en {carpeta}", ["Archivo", ("Tamaño", {"justify": "right"}), "Fecha"], filas)


# ─────────────────────────────────────────────────────────────────────────────
#  4. SEGURIDAD · MONITOREO
# ─────────────────────────────────────────────────────────────────────────────
def usuario_grafico():
    u = os.environ.get("SUDO_USER")
    return u if u and u != "root" else None


def entorno_grafico():
    env = {k: os.environ[k] for k in ("DISPLAY", "XAUTHORITY", "WAYLAND_DISPLAY", "LANG") if k in os.environ}
    u = usuario_grafico()
    if u:
        run = f"/run/user/{pwd.getpwnam(u).pw_uid}"
        env.setdefault("XDG_RUNTIME_DIR", run)
        env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path={run}/bus")
        if "WAYLAND_DISPLAY" not in env and os.path.exists(f"{run}/wayland-0"):
            env["WAYLAND_DISPLAY"] = "wayland-0"
    env.setdefault("DISPLAY", ":0")
    return env


def hay_escritorio():
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")) or os.path.exists("/tmp/.X11-unix/X0")


def abrir_navegador(url):
    console.print(f"\n  🌐 Abriendo [bold underline]{url}[/] en el navegador...")
    if not hay_escritorio():
        aviso(f"No se detectó escritorio gráfico. Abre {url} desde un navegador "
              f"(cambia localhost por la IP del servidor).")
        return
    env = entorno_grafico()
    u = usuario_grafico()
    abridor = next((b for b in ("xdg-open", "firefox", "google-chrome") if existe(b)), None)
    if not abridor:
        aviso(f"No se encontró navegador. Abre manualmente: {url}")
        return
    cmd = ([abridor, url] if not u else
           ["sudo", "-u", u, "env"] + [f"{k}={v}" for k, v in env.items()] + [abridor, url])
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
                     env={**os.environ, **env})


def asegurar_servicios(servicios):
    """Revisa y arranca servicios systemd. Regresa True si todos quedaron activos."""
    todo_ok = True
    for s in servicios:
        _, est, _ = ejecutar(["systemctl", "is-active", s])
        if est.strip() != "active":
            with console.status(f"Iniciando {s}..."):
                c, _, e = ejecutar(["systemctl", "enable", "--now", s])
            if c != 0:
                error(f"No se pudo iniciar {s}: {e or 'servicio no instalado'}")
                todo_ok = False
                continue
        info(f"Servicio [bold]{s}[/]: [green]activo[/]")
    return todo_ok


def red_local():
    c, s, _ = ejecutar(["ip", "-o", "-f", "inet", "addr", "show", "scope", "global"])
    for l in s.splitlines():
        p = l.split()
        if "inet" in p:
            try:
                return str(ipaddress.ip_interface(p[p.index("inet") + 1]).network)
            except ValueError:
                pass
    return "192.168.1.0/24"


def interfaces():
    try:
        return [i for i in sorted(os.listdir("/sys/class/net")) if i != "lo"] or ["lo"]
    except OSError:
        return ["eth0"]


def leer_cpu():
    with open("/proc/stat") as f:
        v = list(map(int, f.readline().split()[1:]))
    return v[3] + v[4], sum(v)


def leer_red():
    datos = {}
    with open("/proc/net/dev") as f:
        for l in f.readlines()[2:]:
            nom, resto = l.split(":", 1)
            c = resto.split()
            datos[nom.strip()] = (int(c[0]), int(c[8]))
    return datos


def barra(pct, ancho=34):
    color = "green" if pct < 60 else "yellow" if pct < 85 else "red"
    llenos = int(pct / 100 * ancho)
    return Text.assemble(("█" * llenos, color), ("░" * (ancho - llenos), "grey30"), (f" {pct:5.1f}%", "bold"))


def panel_en_vivo():
    console.print("  Iniciando panel... [dim](Ctrl+C para salir)[/]")
    idle0, tot0 = leer_cpu()
    red0, t0 = leer_red(), time.time()
    hist = []

    def construir():
        nonlocal idle0, tot0, red0, t0
        idle, tot = leer_cpu()
        cpu = 100 * (1 - (idle - idle0) / max(tot - tot0, 1))
        idle0, tot0 = idle, tot
        mem = {}
        with open("/proc/meminfo") as f:
            for l in f:
                k, v = l.split(":")
                mem[k] = int(v.split()[0]) * 1024
        ram = 100 * (1 - mem["MemAvailable"] / mem["MemTotal"])
        st = os.statvfs("/")
        disco = 100 * (1 - st.f_bavail / st.f_blocks)
        red1, t1 = leer_red(), time.time()
        dtt = max(t1 - t0, 0.1)
        hist.append(cpu)
        del hist[:-40]
        chispa = "".join("▁▂▃▄▅▆▇█"[min(int(x / 12.5), 7)] for x in hist)

        rec = Table.grid(padding=(0, 2))
        rec.add_column(style="bold", width=12)
        rec.add_column()
        rec.add_row("🧠 CPU", barra(cpu))
        rec.add_row("", Text(chispa, style=COLOR))
        rec.add_row("💾 RAM", barra(ram))
        rec.add_row("", Text(f"{tamano(mem['MemTotal'] - mem['MemAvailable'])} de {tamano(mem['MemTotal'])}",
                             style="dim"))
        rec.add_row("🗄  Disco /", barra(disco))
        with open("/proc/loadavg") as f:
            carga = f.read().split()[:3]
        rec.add_row("📈 Carga", Text(" · ".join(carga) + f"   ({os.cpu_count()} núcleos)", style="bold"))

        tr = Table(box=box.SIMPLE_HEAD, header_style=f"bold {ACENTO}")
        tr.add_column("Interfaz")
        tr.add_column("⬇ Recibe", justify="right")
        tr.add_column("⬆ Envía", justify="right")
        for i, (rx, tx) in red1.items():
            if i == "lo":
                continue
            rx0, tx0 = red0.get(i, (rx, tx))
            tr.add_row(i, f"{tamano((rx - rx0) / dtt)}/s", f"{tamano((tx - tx0) / dtt)}/s")
        red0, t0 = red1, t1

        tp = Table(box=box.SIMPLE_HEAD, header_style=f"bold {ACENTO}")
        for col, j in [("PID", "right"), ("Usuario", "left"), ("%CPU", "right"), ("%MEM", "right"),
                       ("Proceso", "left")]:
            tp.add_column(col, justify=j)
        for f in leer_ps(["-eo", "pid=,user=,pcpu=,pmem=,comm=", "--sort=-pcpu"], 5)[:8]:
            tp.add_row(*[escape(x) for x in f])

        izq = Panel(rec, title="[bold]Recursos[/]", border_style=COLOR)
        der = Panel(tr, title="[bold]Red en tiempo real[/]", border_style=COLOR)
        arriba = Table.grid(expand=True)
        arriba.add_column(ratio=3)
        arriba.add_column(ratio=2)
        arriba.add_row(izq, der)
        return Panel(Group(arriba, Panel(tp, title="[bold]Procesos con más consumo[/]", border_style=COLOR)),
                     title=f"[bold white on {COLOR}]  {EMPRESA} · MONITOR EN VIVO  [/]",
                     subtitle=f"[dim]{dt.datetime.now():%H:%M:%S} · actualiza cada segundo · Ctrl+C para salir[/]",
                     border_style=ACENTO, box=box.HEAVY)

    time.sleep(0.5)
    try:
        with Live(construir(), console=console, screen=True, refresh_per_second=4) as live:
            while True:
                time.sleep(1)
                live.update(construir())
    except KeyboardInterrupt:
        pass


def mon_nmap():
    if not existe("nmap"):
        error("nmap no está instalado (dnf install nmap).")
        return
    objetivo = pregunta("Objetivo (IP, rango o red):", red_local())
    tipo = elegir("Tipo de escaneo:", [
        Choice("🔎 Descubrir equipos conectados en la red (-sn)", value=["-sn"]),
        Choice("⚡ Puertos más comunes (-F)", value=["-F"]),
        Choice("🧩 Servicios y versiones (-sV)", value=["-sV", "--top-ports", "200"]),
        Choice("🖥  Detectar sistema operativo (-O)", value=["-O", "--osscan-guess"]),
        Choice("🛡  Escaneo completo y agresivo (-A)", value=["-A", "-T4"]),
    ])
    cmd = ["nmap"] + tipo + [objetivo]
    registrar(f"NMAP {' '.join(cmd)}")
    en_vivo(cmd, " ".join(cmd))


def mon_iftop():
    if not existe("iftop"):
        error("iftop no está instalado (dnf install epel-release && dnf install iftop).")
        return
    iface = elegir("Interfaz de red a monitorear:", interfaces())
    console.print(Panel("[bold]Teclas útiles en iftop:[/]  [bold]q[/] salir · [bold]n[/] mostrar IP/nombres · "
                        "[bold]p[/] puertos · [bold]t[/] modo de vista", border_style=COLOR))
    time.sleep(1.5)
    interactivo(["iftop", "-i", iface, "-P"])


def mon_wireshark():
    iface = elegir("Interfaz de red a capturar:", interfaces())
    if existe("wireshark") and hay_escritorio():
        u = usuario_grafico()
        env = entorno_grafico()
        if u and existe("xhost"):
            # permite que root abra ventanas en el escritorio del usuario (X11/Wayland)
            ejecutar(["sudo", "-u", u, "env", f"DISPLAY={env['DISPLAY']}",
                      f"XDG_RUNTIME_DIR={env.get('XDG_RUNTIME_DIR', '')}", "xhost", "+si:localuser:root"])
        os.makedirs("/tmp/runtime-root", mode=0o700, exist_ok=True)
        subprocess.Popen(["wireshark", "-i", iface, "-k"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True, env={**os.environ, **env, "XDG_RUNTIME_DIR": "/tmp/runtime-root"})
        registrar(f"WIRESHARK en {iface}")
        ok(f"Wireshark abierto capturando en [bold]{iface}[/] (revisa la ventana del escritorio).")
    elif existe("tshark"):
        aviso("Sin escritorio gráfico: se usa tshark (Wireshark en modo texto).")
        registrar(f"TSHARK en {iface}")
        interactivo(["tshark", "-i", iface, "-c", "2000"])
    else:
        error("Wireshark no está instalado (dnf install wireshark).")


def mon_nagios():
    console.print(Panel("Nagios vigila servicios y equipos de la red (HTTP, SSH, carga, disco, ping...)\n"
                        "y muestra alertas en una página web.\n\n"
                        "Usuario: [bold]nagiosadmin[/]   Contraseña: la que definiste en instalar_servidor.sh",
                        title="Nagios Core", border_style=COLOR))
    if asegurar_servicios(["httpd", "nagios"]):
        registrar("NAGIOS abierto")
        abrir_navegador("http://localhost/nagios")
    else:
        aviso("Ejecuta primero: sudo bash instalar_servidor.sh nagios")


def mon_cacti():
    console.print(Panel("Cacti genera gráficas históricas (tráfico de red, CPU, memoria) vía SNMP.\n\n"
                        "Primer acceso: usuario [bold]admin[/] contraseña [bold]admin[/] (te pide cambiarla).",
                        title="Cacti", border_style=COLOR))
    if asegurar_servicios(["mariadb", "snmpd", "httpd"]):
        registrar("CACTI abierto")
        abrir_navegador("http://localhost/cacti")
    else:
        aviso("Ejecuta primero: sudo bash instalar_servidor.sh cacti")


def mon_servicios():
    servicios = [("crond", "Tareas cron"), ("atd", "Tareas at"), ("sshd", "Acceso remoto SSH"),
                 ("httpd", "Servidor web Apache"), ("nagios", "Nagios"), ("mariadb", "Base de datos (Cacti)"),
                 ("snmpd", "SNMP (Cacti)"), ("firewalld", "Firewall")]
    filas = []
    for s, d in servicios:
        _, a, _ = ejecutar(["systemctl", "is-active", s])
        _, e, _ = ejecutar(["systemctl", "is-enabled", s])
        a, e = a.strip() or "no instalado", e.strip() or "—"
        filas.append([s, d, ("🟢 " if a == "active" else "🔴 ") + a, e])
    tabla("Estado de los servicios del servidor", ["Servicio", "Descripción", "Estado", "Al arrancar"], filas)
    inactivos = [f[0] for f in filas if "active" not in f[2] or "inactive" in f[2]]
    if inactivos and confirmar("¿Iniciar los servicios detenidos?", False):
        asegurar_servicios(varios("¿Cuáles?", inactivos))


# ─────────────────────────────────────────────────────────────────────────────
#  MENÚS
# ─────────────────────────────────────────────────────────────────────────────
def menu_gestion():
    submenu(["Gestión"], [
        ("👤 Usuarios", sin_pausa(lambda: submenu(["Gestión", "Usuarios"], [
            ("➕ Alta de usuario", usuario_alta),
            ("➖ Baja de usuario", usuario_baja),
            ("✏  Modificar usuario", usuario_modificar),
            ("🔍 Consulta de usuarios", usuario_consulta),
        ], "Colaboradores con acceso al servidor de planta"))),
        ("👥 Grupos (departamentos)", sin_pausa(lambda: submenu(["Gestión", "Grupos"], [
            ("➕ Alta de grupo", grupo_alta),
            ("➖ Baja de grupo", grupo_baja),
            ("✏  Modificar grupo", grupo_modificar),
            ("🔍 Consulta de grupos", grupo_consulta),
        ], "Cada departamento de la armadora es un grupo"))),
        ("⚙  Procesos", sin_pausa(lambda: submenu(["Gestión", "Procesos"], [
            ("🟢 Por usuario activo", procesos_usuario),
            ("🖥  Del sistema", procesos_sistema),
        ]))),
        ("🏭 Inicializar estructura de la planta (demo)", inicializar_planta),
    ], "Usuarios, grupos y procesos del servidor")


def menu_tareas():
    submenu(["Automatización de tareas"], [
        ("🔁 Cron · Listar tareas", cron_listar),
        ("🔁 Cron · Programar tarea", cron_programar),
        ("🔁 Cron · Instalar 2 tareas de ejemplo", cron_ejemplos),
        ("⏰ At · Listar tareas", at_listar),
        ("⏰ At · Programar tarea", at_programar),
    ], "Cron = tareas repetitivas · At = tareas de una sola vez")


def menu_respaldo():
    submenu(["Respaldo de la información"], [
        ("📦 tar + gzip   (.tar.gz)", lambda: respaldo_tar("gz")),
        ("📦 tar + bzip2  (.tar.bz2)", lambda: respaldo_tar("bz2")),
        ("🔄 rsync        (sincronización)", respaldo_rsync),
        ("💽 dump / restore  (sistema completo a USB)", respaldo_dump),
        ("📋 Ver respaldos existentes", listar_respaldos),
    ], "Protege la información de producción de la planta")


def menu_seguridad():
    submenu(["Seguridad", "Monitoreo"], [
        ("📊 Panel en vivo del servidor", sin_pausa(panel_en_vivo)),
        ("🔎 Nmap   · escaneo de red y puertos", mon_nmap),
        ("📶 iftop  · tráfico de red en tiempo real", mon_iftop),
        ("🦈 Wireshark · captura de paquetes", mon_wireshark),
        ("🛰  Nagios · monitoreo de servicios (web)", mon_nagios),
        ("📈 Cacti  · gráficas de rendimiento (web)", mon_cacti),
        ("🩺 Estado de servicios", mon_servicios),
    ], "Monitoreo en tiempo real de la red y el servidor")


def verificar_dependencias():
    req = {"useradd": "shadow-utils", "crontab": "cronie", "at": "at", "tar": "tar", "gzip": "gzip",
           "bzip2": "bzip2", "rsync": "rsync", "xfsdump": "xfsdump", "nmap": "nmap", "iftop": "iftop (EPEL)",
           "wireshark": "wireshark", "tshark": "wireshark-cli"}
    faltan = [f"{p} ({k})" for k, p in req.items() if not existe(k)]
    if faltan:
        console.print(Panel("Faltan algunos programas: " + ", ".join(faltan) +
                            "\nEjecuta [bold]sudo bash instalar_servidor.sh[/] para instalarlos.",
                            border_style="yellow", title="⚠ Aviso"))


def principal():
    if os.geteuid() != 0:
        console.print(Panel("Esta consola administra usuarios, respaldos y servicios, por lo que\n"
                            "necesita permisos de administrador. Ejecútala así:\n\n"
                            "    [bold]sudo python3 nova_admin.py[/]", title="🔒 Se requiere root",
                            border_style="red"))
        sys.exit(1)

    menus = [("🗂  Actividades de gestión", "Usuarios · Grupos · Procesos", menu_gestion),
             ("⏰ Automatización de tareas", "Cron · At", menu_tareas),
             ("💾 Respaldo de la información", "tar-gzip · tar-bzip2 · rsync · dump/restore", menu_respaldo),
             ("🛡  Seguridad y monitoreo", "Nagios · Wireshark · Nmap · iftop · Cacti", menu_seguridad)]
    primera = True
    while True:
        banner()
        if primera:
            verificar_dependencias()
            primera = False
        opciones = [Choice(title=[("class:text", f"{t:<34}"), ("fg:#808080", d)], value=i)
                    for i, (t, d, _) in enumerate(menus)]
        opciones += [Separator("  ─────────────────────────"), Choice("🚪 Salir", value=-1)]
        sel = questionary.select("MENÚ PRINCIPAL", choices=opciones, qmark="  ▸", pointer="❯",
                                 style=ESTILO, instruction="(↑↓ para moverte, Enter para elegir)").ask()
        if sel is None or sel == -1:
            console.clear()
            console.print(Panel(Align.center(f"[bold {COLOR}]{EMPRESA}[/] · ¡Hasta luego! 🚗"),
                                border_style=COLOR))
            break
        menus[sel][2]()


if __name__ == "__main__":
    try:
        principal()
    except KeyboardInterrupt:
        console.print("\n  Saliendo...")
