# Simulator adapter

The Home Assistant scripts in `infrastructure/home_assistant/config/packages/bapsim_simulator.yaml`
are the current adapter. They publish `bapsim_raw_event` on the HA event bus and update
four HA helpers for state and History. The exact payload shape is in
`inputs/base/contracts.py`.

The Matter adapter should publish the same raw event shape. It may use
different raw `event_type` or `value` strings when the underlying device differs;
`domain/events/normalizer` maps those to canonical cooker events. No decision
logic belongs in either adapter.

