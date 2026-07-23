# Fault handling

| Detection | Severity | Immediate action | Automatic recovery | Operator action |
|---|---|---|---|---|
| Expected serial missing / USB exception | Fault | Zero/IDLE every reachable axis; mark disconnected | No | Restore cable/power, diagnose, explicitly reinitialize and enable |
| Axis, motor, encoder or controller error | Fault | Zero all wheels, then IDLE | No | Record/decode cause; explicit one-time clear only after repair |
| Invalid calibration or encoder not ready | Startup block | Remain IDLE | No | Run safe encoder calibration after power cycle |
| Invalid/NaN command | Fault | Zero and IDLE | No | Fix publisher; inspect log |
| Stale `/cmd_vel` | Controlled stop | Slew to zero, then IDLE | No movement resume | Explicit enable after timeout |
| Closed-loop transition failure | Fault | Idle all armed axes | No | Diagnose error and calibration |
| Axis unexpectedly IDLE | Fault | Stop all wheels | No | Inspect controller and wiring |
| Persistent same-side velocity mismatch | Fault | Stop all wheels | No | Inspect tyre/load/encoder/motor |
| DC voltage outside configured limits | Fault | Controlled zero and IDLE when communication exists | No | Inspect battery, regen and resistor |
| Excess current or temperature | Fault | Zero and IDLE | No | Cool and inspect load/configuration |
| Motion while zero commanded | Emergency fault | Software zero/IDLE; use physical E-stop | Never | Electrically isolate and diagnose |
| Physical E-stop | Emergency stop | Electrical isolation by hardware | Never | Inspect robot before reset |

State transitions:

```text
DISCONNECTED -> INITIALIZING -> IDLE/READY -> ENABLED
ENABLED -> STOPPING -> READY
any active state -> FAULT
any state -> EMERGENCY_STOP
```

`FAULT` and `EMERGENCY_STOP` never resume motion automatically. USB reconnection
only restores communication; an explicit operator enable is still required.
