#!/usr/bin/env python3

import sys
import time
import odrive
from odrive.enums import *

print("Buscando ODrive...")
odrv0 = odrive.find_any()

if len(sys.argv) != 2:
    print("Uso:")
    print("  python calibrate.py 0")
    print("  python calibrate.py 1")
    print("  python calibrate.py all")
    sys.exit(1)

arg = sys.argv[1].lower()

if arg == "0":
    axes = [("axis0", odrv0.axis0)]
elif arg == "1":
    axes = [("axis1", odrv0.axis1)]
elif arg == "all":
    axes = [
        ("axis0", odrv0.axis0),
        ("axis1", odrv0.axis1)
    ]
else:
    print("Argumento inválido")
    sys.exit(1)

for name, axis in axes:
    print(f"Iniciando calibración de {name}")
    axis.clear_errors()
    axis.requested_state = AXIS_STATE_FULL_CALIBRATION_SEQUENCE

# Esperar a que TODOS vuelvan a IDLE
while True:
    busy = False

    for _, axis in axes:
        if axis.current_state != AXIS_STATE_IDLE:
            busy = True
            break

    if not busy:
        break

    time.sleep(0.5)

print("\nResultados:\n")

calibration_ok = True

for name, axis in axes:
    print(name)
    print("  axis error   :", hex(axis.error))
    print("  motor error  :", hex(axis.motor.error))
    print("  encoder error:", hex(axis.encoder.error))
    print("  calibrated   :", axis.motor.is_calibrated)
    print("  encoder ready:", axis.encoder.is_ready)
    print()

print("Guardando configuración...")

try:
    odrv0.save_configuration()
except:
    pass

# Marcar como pre-calibrado
if calibration_ok:
    print("Marcando como pre-calibrado...")

    if arg in ("0", "all"):
        odrv0.axis0.motor.config.pre_calibrated = True
        odrv0.axis0.encoder.config.pre_calibrated = True

    if arg in ("1", "all"):
        odrv0.axis1.motor.config.pre_calibrated = True
        odrv0.axis1.encoder.config.pre_calibrated = True

    try:
        odrv0.save_configuration()
        print("Pre-calibración guardada correctamente.")
    except Exception as e:
        print("Error guardando pre-calibración:", e)
else:
    print("No se marca como pre-calibrado porque la calibración falló.")
    
print("Listo.")
