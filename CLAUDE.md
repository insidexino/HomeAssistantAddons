# HomeAssistantAddons Project Memory

## HA SSH Access
- SSH: `ssh root@192.168.50.181 -p 22222`
- This connects to HA Terminal & SSH addon

## HA Addon Management Commands
- Addon slug: `8aeab66b_masterhills_wallpad`
- `ha store refresh` - refresh addon repository from GitHub
- `ha apps update <slug>` - update addon to latest version
- `ha apps rebuild <slug>` - rebuild addon (same version only)
- `ha apps start/stop <slug>` - start/stop addon
- `ha apps logs <slug> --lines N` - view addon logs
- `ha apps info <slug>` - check addon state/version
- `ha supervisor logs` - check supervisor/build logs
- Note: `ha addons` is deprecated, use `ha apps` instead

## Network
- HA IP: 192.168.50.181
- RS485 Bridge IP: 192.168.50.185:8899
- MQTT Broker: 192.168.50.181:1883

## Versioning
- Version is in `MasterHillsRS485/config.json` (`"version"` field)
- **코드 수정 시 반드시 patch 버전을 올려야 함** (예: 0.0.24 → 0.0.25)
- HA는 version이 변경되어야 업데이트를 감지함 (`ha store refresh` → `ha apps update`)
- 버전을 안 올리면 `ha apps rebuild`로만 반영 가능 (같은 버전 재빌드)

## Key Architecture Decisions
- `"init": false` in config.json - required because HA base image s6-overlay v3 doesn't pass CMD correctly
- `paho-mqtt<2` pinned - v2 breaks mqtt.Client() API (requires CallbackAPIVersion)
- `--break-system-packages` for pip - Alpine PEP 668 compliance
- Shebang: `#!/bin/bash` (not bashio)

## RS485 Protocol
- Packet: 21 bytes (42 hex chars), prefix AA55, suffix 0D0D
- Type 30b = send/command, Type 309 = notify/state
- Light groups: kitchen(0e040100), livingroom(0e000100), bedroom(0e010100), studyroom(0e020100), guestroom(0e030100)
- Thermostat groups: livingroom(36000100/39000100), bedroom(36010100/39010100), studyroom(36020100/39020100), guestroom(36030100/39030100)
- Thread safety: _send_lock on RS485Sock, _state_lock per DeviceLightGroup
- ProcessGroupPacket filters out send-type (30b) echo packets
