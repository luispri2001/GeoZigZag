import odrive

odrv0 = odrive.find_any()

print("VBUS =", odrv0.vbus_voltage)

for name, axis in [("axis0", odrv0.axis0), ("axis1", odrv0.axis1)]:
    print("\n", name)

    try:
        print("phase_resistance =", axis.motor.config.phase_resistance)
    except:
        print("phase_resistance = N/A")

    try:
        print("phase_inductance =", axis.motor.config.phase_inductance)
    except:
        print("phase_inductance = N/A")

    print("axis error   =", hex(axis.error))
    print("motor error  =", hex(axis.motor.error))
    print("encoder error=", hex(axis.encoder.error))
