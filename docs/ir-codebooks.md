# Optional local IR codebooks (proposal)

Some stateful IR controllers acknowledge climate DP writes without emitting IR.
A locally supplied codebook can attach a full `send_ir` packet to a matching DP
update. This is opt-in and does not fetch Tuya libraries or decode cloud rules.

Put a JSON file in `config/tuya_local_ir/<physical-device-id>.json`. The filename
binds it to that controller. Do not publish your IDs, credentials or raw logs.
The file is read on the executor for each parent-device write, so reloads do not
require restarting. A missing or disabled file leaves existing behavior intact.
Malformed enabled files fail before changes are queued. Subdevices bypass this
feature; their routing is not verified by this implementation.

```json
{
  "version": 1,
  "enabled": false,
  "trigger_dps": ["1", "3", "4", "5"],
  "key_dps": ["4", "3", "5"],
  "output_dp": "201",
  "required_state": {"11": "c"},
  "off": {"dp": "1", "value": false, "key": "off"},
  "commands": {}
}
```

Fill `commands` with full packets captured from successful appliance commands,
then enable the file. For this selector, heat/26/low is `warm|26|low`; off is
`off`. A captured version-3 packet for that heat state contained:

```json
{
  "head": "020ed800000000000500150016003c00a700c0",
  "key1": {"data": "02%%0030B24D9F60DC23@^", "data_type": 0, "key": "M1_T26_S1"},
  "devid": "",
  "ver": "3",
  "delay": 300,
  "control": "send_ir",
  "v_devid": "REPLACE_WITH_YOUR_PAIRED_REMOTE_ID",
  "key_num": 1
}
```

`key_dps` select values from the cached state overlaid with all incoming changes.
Integers and integer-valued floats use the same spelling. Unknown states fail
instead of guessing a mode. All required-state values must match, for example
to prevent treating Fahrenheit as Celsius. If an update contains output_dp
explicitly, it is left alone. Non-trigger updates are also unchanged.

The packet and climate changes are queued together through the existing local
transport. There is no background transmission or additional cloud access.
Repeated identical climate actions can still emit repeated IR; suppress these
in automations when appropriate. Do not mix libraries or assume every model
from a brand uses the same protocol. No full cloud library is distributed here.

The user reported that all actions worked after installing an earlier,
device-specific prototype. The generalized configurable implementation in this
proposal needs hardware retesting. An internet-blocked test has not been
reported. Device acknowledgements and cloud Publish logs do not establish that
an appliance physically received an IR signal.

This manual JSON interface is proposed for maintainer feedback; UI configuration
and additional transport adapters are outside this change.
