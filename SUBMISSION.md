# Submission candidate

Bot name: **Harbor**

Entrant: **Sankeerth S Banagar** (GitHub: [San245o](https://github.com/San245o)).

Repository: https://github.com/San245o/harbor-yeno-bot

Builderr source: `source=direct`.

Runtime: Python 3.12, standard library only.

Command: `python3 -m yeno.server`

Interface: `POST /decide`; default bind `127.0.0.1:8080`. Use `YENO_HOST=0.0.0.0` only when the runner
needs access across a container/network boundary. No external runtime services, keys or funds are needed.

The official ZIP, contract, sample observation and smoke test are in `research/`. The recorder and replay
utilities are development tools; the evaluator only needs `yeno/`.

Before sending an entry:

1. Freeze the exact source as a Git commit or container digest.
2. Supply a runnable repository URL (public hosting of an endpoint is optional).
3. Send from the entrant's contact email so the organizers can reply; no personal email is embedded in this public repository.
4. Include the runtime command and describe this as an experimental paper bot, without an official score.

Send only when authorized to submit. No email or competition submission has been made by this project.
