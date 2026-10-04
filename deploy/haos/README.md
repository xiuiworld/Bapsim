# A06: existing Hyper-V Home Assistant OS

## Deployment boundary

Use commit `6839bfa` or a later reviewed commit containing this directory and the
A05 bridge. This deployment chooses **HA internal analysis mode**. B's normalizer,
analysis, output, state and notification code runs in the Bapsim HA integration;
Docker Desktop runs **only Mosquitto**. Do not run the repository root
`compose.yaml` or `compose.standalone.yaml` for this installation: both create
another Home Assistant container, and the latter also creates a worker.

The existing HA OS, Matter Server and A05 probe integration stay in place.
Installation of the Bapsim integration is additive. Never copy the repository's
whole `infrastructure/home_assistant/config` directory over the HA OS config.

## Values to collect

* HA URL: verify the HA OS URL and port in a browser. The prior A address was
  `http://192.168.1.13/`; use its actual working address. Internal mode does
  not need `BAPSIM_HA_TOKEN` or `BAPSIM_HA_URL` in Docker.
* Windows LAN IPv4: put it in `BAPSIM_MQTT_BIND_IP` in `deploy/haos/.env`.
  Keep it stable (DHCP reservation is suitable). It is the MQTT host entered
  in the HA MQTT integration. The HA VM must reach this host on TCP 1883.
* MQTT username: choose one for HA, e.g. `bapsim_ha`. Create its password
  interactively as below; do not put it in Git or `.env`. Configure the same
  username and password in the HA MQTT integration. There is no worker MQTT
  identity in this mode.
* `household_id`: choose a topic-safe ID (letters, digits, `_`, `-`). Set it
  under `bapsim:` in HA `configuration.yaml`; default is `household_01`.
* `device_id`: choose a stable logical cooker ID for `register_a05_snapshot`.
  It is not the Matter node ID. Record the actual ReportCounter entity ID,
  Matter entry ID and node ID from this HA installation; example values in
  `tools/a05_contract/ha_mapping.test.json` are not portable.
* HA timezone: `Asia/Seoul`. Set the HA OS timezone accordingly. Internal
  quiet-hours policy is KST and defaults to 22:00–08:00. This mode has no
  configuration service for changing that window; B must provide one if a
  different window is required.
* Optional mobile target: identify a registered `notify.mobile_app_*` service
  for a test phone. Set it through `bapsim.set_notification_target` only after
  validating its owner. Leave it unset for the A05 test-origin check.

## Install

From the repository root in PowerShell, with Docker Desktop running:

```powershell
Copy-Item deploy/haos/.env.example deploy/haos/.env
# Replace the documentation-only 192.0.2.10 with the Windows LAN IPv4 in deploy/haos/.env.
docker compose --env-file deploy/haos/.env -f deploy/haos/compose.yaml config
docker compose --env-file deploy/haos/.env -f deploy/haos/compose.yaml run --rm --no-deps -it mosquitto mosquitto_passwd -c /mosquitto/data/passwords bapsim_ha
docker compose --env-file deploy/haos/.env -f deploy/haos/compose.yaml up -d
docker compose --env-file deploy/haos/.env -f deploy/haos/compose.yaml ps
docker compose --env-file deploy/haos/.env -f deploy/haos/compose.yaml logs -f mosquitto
```

The password command prompts twice. Use `-c` only for initial creation; it
overwrites an existing password file. To change/add an account, omit `-c`.
Allow inbound TCP 1883 on Windows Firewall from the HA VM/local LAN only.
No router forwarding is needed. Broker data and the password hash persist at
`local-data/a06/mosquitto/`. Check that this directory is private to the host.

In HA OS, add/configure the MQTT integration using the **Windows LAN IPv4**,
port `1883`, and the credentials just created. Do not use `localhost` or
`mosquitto` as the host in HA OS. Check the integration reports connected.

The ZIP is generated locally and is not tracked by Git. On A's Windows
notebook, install **Git** and **Python 3.12** (the tested version is 3.12.10).
The build uses only Python's standard library, so no `pip install`, SDK,
Docker image or HA token is needed. Fetch the deployment branch and record
its exact revision:

```powershell
git fetch origin deploy/a06-existing-haos
git switch --track origin/deploy/a06-existing-haos
git rev-parse HEAD
```

From the root of that same checkout, generate the ZIP:

```powershell
python -m tools.build_ha_bundle
```

The output is `dist/bapsim_ha_bundle.zip`. Keep the recorded commit hash with
the ZIP so the source used for installation is reproducible. Back up HA OS
first. Transfer the ZIP into the HA OS
configuration directory and extract only `custom_components/bapsim/`. Add
the following to the **existing** HA `configuration.yaml`, merging with any
existing `bapsim:` block:

```yaml
bapsim:
  household_id: household_a_test
```

Do not set `external_service: true`. Validate HA configuration and restart
HA Core. Confirm Bapsim services and `sensor.bapsim_matter_input` exist.
Keep the existing `bapsim_matter_probe` installation that produces A05 sensors.

## A05 test-origin verification

Confirm `sensor.a05_reportcounter` (or its actual entity ID) updates and its
attributes show `input_kind: test`, `transport: matter`, the expected entry,
node and SnapshotPacked path. In HA Developer Tools, call:

```yaml
action: bapsim.register_a05_snapshot
data:
  device_id: cooker_a_test
  entity_id: sensor.a05_reportcounter
  matter_entry_id: <this HA's Matter entry ID>
  node_id: <this HA's node ID>
```

Use the actual IDs, including a numeric node ID. A valid registration should
show the bound A05 source in `sensor.bapsim_matter_input`. Its source is
verified, but `inputs_available` stays false and the overall readiness is
`connection_unverified` while reports have `input_kind=test`. No A05 raw
`lid_state`/`availability=online` event is forwarded to B's live personal
learning or live notifications. An incorrect entry/node/entity binding must
be rejected by the registration service. Do not change the probe's origin to
`live` or turn off provenance checks to make this test pass.

This is currently a **test-input rejection test**, not a full test-origin
ingestion test. B's existing bridge accepts only a verified `live` report.
If A05 needs test-origin data to traverse the B raw-event boundary, B must
implement and verify a separate test source/isolated storage and notification
path. A should not reimplement B's collection or normalization code.

For the remaining pipeline, use B's offline simulation command
`python -m tools.simulate_notification_flow` or the simulator in a separately
identified test household. Offline simulation sends nothing to HA/MQTT/phone.
Do not present it as Matter-to-phone proof.

## Persistence, stop, backup and restore

MQTT topics are `bapsim/v1/<household_id>/...` for Matter and
`bapsim/simulation/v1/<household_id>/...` for simulator. Use different
household IDs and mobile targets for separate installations. The HA internal
state store itself contains both sources, so topic separation alone is not
an independent data store. A05 test reports are rejected before that store.

HA OS stores Bapsim state in `.storage/bapsim.pipeline`, raw events in
`bapsim_events/`, normalized observations in `bapsim_observations/` (90 days),
and service records in `bapsim_records/` (180 days), all under its config
directory. Keep HA's own database/configuration and Matter state in the
normal HA backup. Mosquitto's persisted messages and password hash are in
`local-data/a06/mosquitto/` on Windows. `.env` is local and ignored by Git.

```powershell
docker compose --env-file deploy/haos/.env -f deploy/haos/compose.yaml stop
# For a cold broker backup, copy local-data/a06/mosquitto/ to a private backup location.
docker compose --env-file deploy/haos/.env -f deploy/haos/compose.yaml start
# To stop and remove only the broker container/network, preserving bind-mounted data:
docker compose --env-file deploy/haos/.env -f deploy/haos/compose.yaml down
```

Take a normal HA OS backup, then stop HA Core gracefully and stop Mosquitto
for a cold copy of the Mosquitto data directory and the installation's
non-secret configuration record. Start both again. Record the backup times;
the HA backup and broker copy are not an atomic cross-service snapshot.
Keep credentials and backup archives outside the repository. To restore,
stop both, restore the HA backup and Mosquitto directory to the same paths,
restore the local `.env` bind IP, then start Mosquitto and HA. Confirm the
MQTT connection, A05 binding, retained discovery/state and absence of
unexpected mobile delivery before resuming normal use. Do not run two active
HA instances from one restored Matter Fabric.

## Acceptance boundary

On the current test board, passing evidence is: HA receives A05 snapshots,
Bapsim recognizes the configured probe provenance, rejects `input_kind=test`
for live analysis, and MQTT/HA status infrastructure is reachable. Full
Matter→B live analysis→phone delivery requires a supported live producer and
separate end-to-end evidence. The A05 receiver polls every five seconds;
rapid intermediate lid transitions are not guaranteed to appear in HA.
