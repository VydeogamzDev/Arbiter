# app

A small service.

## Configuration

| option | default | meaning |
|---|---|---|
| timeout_s | 30 | request timeout in seconds (`timeout` still works, deprecated) |
| log_level | info | logging level |
| max_retries | 3 | retries per request, 0 to 10 |

## Development

Run `python -m pytest -q`.
