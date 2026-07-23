#!/usr/bin/env python3

import sys
import odrive
from odrive.enums import *

print("Buscando ODrive...")
odrv0 = odrive.find_any()

# --------------------------
# Selección de ejes
# --------------------------

if len(sys.argv) != 2:
    print("Uso:")
    print("  python config.py 0")
    print("  python config.py 1")
    print("  python config.py all")
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

# --------------------------
# Configuración
# --------------------------

for axis_name, axis in axes:

    print("Configurando {}...".format(axis_name))

    # MOTOR
    axis.motor.config.pole_pairs = 15
    axis.motor.config.current_lim = 16
    axis.motor.config.calibration_current = 5
    axis.motor.config.resistance_calib_max_voltage = 12

    # ENCODER
    axis.encoder.config.mode = ENCODER_MODE_INCREMENTAL
    axis.encoder.config.cpr = 16384
    axis.encoder.config.bandwidth = 300

    # CONTROLADOR
    axis.controller.config.vel_limit = 3.4

    axis.controller.config.pos_gain = 20
    axis.controller.config.vel_gain = 0.08
    axis.controller.config.vel_integrator_gain = 0.16

    # STARTUP
    axis.config.startup_motor_calibration = False
    axis.config.startup_encoder_index_search = False
    axis.config.startup_encoder_offset_calibration = False
    axis.config.startup_closed_loop_control = False

print("Guardando configuración...")
odrv0.save_configuration()

try:
    odrv0.reboot()
except:
    pass
    
print("Configuración completada.")
