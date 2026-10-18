# Stop log

Append-only record. `honoured` records a refused wake attempt or a session
started under a marker. `removed` records an operator removal and the number
of honoured rows for that marker. Removal records preserve the full marker
and the operator's reason beneath the table.

| Date and time (UTC) | Type | Detail |
| --- | --- | --- |
