# WhatsApp Bridge (Baileys)

Local Express + Baileys bridge that lets the FastAPI backend send WhatsApp
messages and receives patient messages via webhook.

## Run

```bash
npm install
npm start                 # or: pm2 start server.js --name whatsapp-bridge
```

On first run it prints a QR code. Scan it with
**WhatsApp → Settings → Linked devices → Link a device**.

## Endpoints

| Method | Path      | Body                          | Response                          |
| ------ | --------- | ----------------------------- | --------------------------------- |
| POST   | `/send`   | `{ "phone": "...", "message": "..." }` | `{ "success": true, "id": "..." }` |
| GET    | `/health` | –                             | `{ status, connected, user, ... }` |

## Configuration (env vars)

| Var                      | Default                                                       | Purpose                                   |
| ------------------------ | ------------------------------------------------------------- | ----------------------------------------- |
| `BRIDGE_PORT`            | `3000`                                                        | HTTP port                                 |
| `WEBHOOK_URL`            | the clinic-autopilot Render URL                               | Where inbound messages are forwarded      |
| `AUTH_DIR`               | `./auth_info_baileys`                                         | Session/credentials directory             |
| `LOG_LEVEL`              | `warn`                                                        | Baileys internal log level                |
| `SEND_ATTEMPTS`          | `3`                                                           | Retries per outbound send                 |
| `AUTO_REPAIR_ON_LOGOUT`  | `false`                                                       | Wipe auth + re-pair automatically on 401  |

## Operational rules

1. **Run exactly one instance.** `useMultiFileAuthState` is not multi-process
   safe. Two bridges sharing `auth_info_baileys` corrupt the Signal keys and
   WhatsApp logs the device out (401/440). The bridge enforces this with a lock
   file (`.bridge.pid`) and by binding the port before connecting.

   Never leave a manual `node server.js` running while PM2 also runs it:

   ```bash
   pgrep -af "server.js"          # must show exactly one process
   pm2 save                       # persist the single PM2 process
   ```

2. **Log growth.** Baileys logs whole frames on every packet. Keep the level at
   `warn` (default) and cap PM2 logs:

   ```bash
   pm2 install pm2-logrotate
   pm2 set pm2-logrotate-max_size 10M
   pm2 set pm2-logrotate_retain 5
   ```

## Recovery

| Symptom | Cause | Fix |
| ------- | ----- | --- |
| `Connection Closed` (428) on send | send attempted on a dead socket | restart bridge; the watchdog + send retry now recover automatically |
| `code 401` / `logged out` | device revoked or two instances fought | stop duplicates, `rm -rf auth_info_baileys`, restart, rescan QR |
| `code 440` | another instance/device replaced this session | ensure single instance; re-pair if it persists |
| `Bad MAC` | corrupted Signal keys in a shared auth dir | stop duplicates, `rm -rf auth_info_baileys`, re-pair |
| PM2 restart loop with low count | crash before socket ready | `pm2 logs whatsapp-bridge --err` and read the first error |
