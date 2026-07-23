# ODrive configuration exports

Exports for serial `335C33513235`:

- `original-335C33513235-20260723T110941Z.json`: byte-preserved configuration
  before any writes.
- `safety-interim-335C33513235-20260723T111049Z.json`: only automatic
  closed-loop startup disabled on both axes.
- `working-safe-335C33513235-20260723T113547Z.json`: validated 2 A,
  0.2 turn/s working configuration.

The second ODrive is not connected and therefore has no export. Create its
original backup before changing it:

```bash
stamp=$(date -u +%Y%m%dT%H%M%SZ)
odrivetool -s SECOND_SERIAL backup-config \
  "odrive_setup/configuration_exports/original-${SECOND_SERIAL}-${stamp}.json"
```

Never copy the primary controller export onto a different serial without
inspecting every axis mapping and hardware parameter.
