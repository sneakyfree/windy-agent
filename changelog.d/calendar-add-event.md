- `windy_calendar.add_event` (dark, `WINDY_CALENDAR_ADD_EVENT=1`, OWNER band): puts an event of the owner's own on
  their Windy Calendar (P136-5; tools-invoke.v1 1.3.0, windy-contracts #238). Calendar's outcome comes back as data;
  the audit row keeps the start and length only. The registry's strict-args check now also covers integer type,
  minimum / maximum and minLength.
