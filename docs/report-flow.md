# Report flow

`tools/run report_flow <report>` loads a review report, counts findings by
severity in `scripts/report_flow/core.py`, and prints the totals. With
`--channel`, the same totals are sent through the channel helper.
