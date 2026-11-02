# skt

A standalone command-line journey planner for Skånetrafiken.

```sh
skt stops Jupitergatan
skt trips 'Lund Jupitergatan' 'Malmö Kungsgatan' --line 171 --json
skt trips 'Lund C' 'Malmö C' --at '08:00'
skt trip 'LEG_ID'
```

Use `skt --help` and each command's `--help` for options and exit codes. Development: `uv sync --locked`, then `just check`.
