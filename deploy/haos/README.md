# A06: existing Hyper-V Home Assistant OS

## Deployment boundary

Use the exact commit reported for PR #10's A05 connection-test delivery (run
`git rev-parse HEAD` after checkout). This deployment chooses **HA internal analysis mode**. B's normalizer,
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
  for a test phone. `bapsim.set_notification_target` accepts that full name or
  its `mobile_app_*` suffix. Validate its owner first. Leave it unset for the
  A05 test-origin check.

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
The generated ZIP contains the Bapsim integration code, **not** the lab
dashboard YAML. If A uses the repository lab dashboard, update the HA OS copy
of `dashboards/bapsim_lab.yaml` from
`infrastructure/home_assistant/config/dashboards/bapsim_lab.yaml` at the same
commit. Merge the simulator recorder entities from the repository
`configuration.yaml` only if A wants their history; do not overwrite the HA
OS configuration. The Simulator view now has separate analysis cards.

In internal mode, simulator evaluations update `sensor.bapsim_simulation_*`
cards and the simulation MQTT prefix; they do not replace the live
`sensor.bapsim_output_state`. Both output cards are created at startup.
Before a fresh evaluation, live displays `awaiting_matter`, simulation
displays `waiting`, and `sensor.bapsim_mqtt_output=waiting` means no result
has yet been published. Each output card switches to `expired` at its own
`valid_until` (180 seconds after generation). Both MQTT discovery sensors
also use `expire_after: 180`. Verify this with a simulator result and then
call B's bounded test service in HA Developer Tools:

```yaml
action: bapsim.test_pause_simulation_output
data:
  duration_seconds: 240
```

It pauses **only simulation result publication**; analysis and live output
continue. About 180 seconds after the last result, the simulation output card
must show `expired`, and the simulation MQTT discovery sensor must expire.
Publication resumes automatically after 240 seconds or an HA restart. This
checks the consumer expiry path using the B simulator, not A05 board ingestion.

## A05 real-board development test

The exact A deployment and button procedure is in
[`A05_CONNECTION_TEST.md`](A05_CONNECTION_TEST.md). It uses the existing A05
receiver and firmware. Its explicit ten-minute test run accepts verified
`input_kind=test` reports in a separate B contract processor, requires a fresh
closed→open board sequence, persists records under `bapsim_test_runs/`, and
attempts one notification to the chosen test phone. The live bridge still
rejects test reports, and the simulator's mobile block remains in force. This
is a connection test, not seven-day personal learning or AI performance proof.

For the next sensor-free integration, use
[`A07_BOARD_TEST.md`](A07_BOARD_TEST.md). It runs an explicitly started
synthetic raw replay on the real board, publishes A07 model results through
the verified test-origin A05 path, and selects `a07_phase_sequence` in B.
It does not qualify the model for live use.

## Persistence, stop, backup and restore

MQTT topics are `bapsim/v1/<household_id>/...` for Matter and
`bapsim/simulation/v1/<household_id>/...` for simulator. Use different
household IDs and mobile targets for separate installations. The HA internal
state store itself contains both sources, so topic separation alone is not
an independent data store. Physical separation is not a requirement of the
current development contract; independent deletion, backup or access control
of test and live data would require a separate B storage design. A05 test
reports are rejected before the live model store.

HA OS stores Bapsim state in `.storage/bapsim.pipeline`, raw events in
`bapsim_events/`, normalized observations in `bapsim_observations/` (90 days),
service records in `bapsim_records/` (180 days), live A05 contract records in
`bapsim_contract_observations/` (90 days), and explicit A05 test runs in
`bapsim_test_runs/` (7 days), all under its config
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

On the current test board, passing evidence is a matching run ID across two
real board reports, B test contract records, the test result, HA mobile service
acceptance, and A's observed phone receipt and detail navigation. Live
personal-analysis performance still requires supported live input and separate
long-term evidence. The A05 receiver polls every five seconds; brief lid
transitions are not guaranteed to appear in HA.
