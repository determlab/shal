# Cold-start read test — report

> Fill this in as you go. Follow `eval/cold-start/AGENT_BRIEF.md`. Stop at the first
> wall and record it honestly. Do **not** import `shal.drivers.*`, do **not** reference
> the `sonos,speaker` compatible, do **not** install a device extra, do **not** use
> `shal-mcp --device`, do **not** use `address: sim`, and do **not** actuate. A human
> runs `verify_run.py` on this run afterward.

- **Device:** `<sonos | deebot>`
- **Run id:** `<runs/<device>-<timestamp>>`
- **Date / evaluator:** `<ISO date> / <agent or human name>`
- **Verdict:** `<PASS | PARTIAL | FAIL>`  — `<one-sentence justification>`

---

## 1. Environment

The runner pre-filled the machine-readable `env` block in `metrics.json` (pyshal
version, wheel filename, git SHA, platform, python version). Mirror it here.

| Field | Value |
|-------|-------|
| OS / platform | `<e.g. Windows 11 / win32>` |
| Python version | `<e.g. 3.12.x — from metrics.env.python_version>` |
| pyshal version installed | `<metrics.env.pyshal_version / pip show pyshal>` |
| wheel filename | `<metrics.env.wheel_filename>` |
| git SHA | `<metrics.env.git_sha>` |
| Extras installed | `mcp` (only — confirm no device extra; see pip-freeze.install.txt) |
| venv path | `<runs/<device>-<timestamp>/venv>` |
| shal-mcp path | `<.../venv/(bin|Scripts)/shal-mcp>` |

---

## 2. Timeline (named checkpoints)

Offset-aware ISO-ms timestamps (`datetime.now().astimezone().isoformat(timespec="milliseconds")`,
e.g. `2026-06-16T14:03:21.250+12:00` — the offset is required). `t_created` and the
`t_install_*` pair are **runner-owned** — leave them. Set `t_wall_hit` **instead of**
`t_first_read_ok` if you stopped at a wall.

| Checkpoint | Timestamp | Notes |
|------------|-----------|-------|
| `t_created` | `<filled by runner>` | run dir created |
| `t_install_start` | `<filled by runner>` | install began |
| `t_install_done` | `<filled by runner>` | pyshal[mcp] importable |
| `t_first_read_attempt` | `<...>` | first call meant to read the real device |
| `t_first_read_ok` | `<... or —>` | a real device responded to a read |
| `t_wall_hit` | `<... or —>` | stopped at a wall |

- **`reached_checkpoint`:** `<installed | read_attempted | read_ok | wall>`
- **`install_ms`:** `<filled by runner — clean-install cost>`
- **`install_to_read_ms`:** `<t_first_read_ok − t_install_done, or — if walled>`

---

## 3. Per-step log

Number each step. Give the exact command/call and verbatim output/error. This is the
evidence trail — be specific.

1. `<command or call>` -> `<what happened; paste real error text where relevant>`
2. ...
3. ...

> If you claim a read succeeded, the actual returned data (track title, volume number,
> battery percent, clean-state string) AND the real device address you read it from
> MUST appear in one of these steps — the same values you put in
> `read_attempt.raw_response` / `read_attempt.device_address`.

---

## 4. Metrics

Mirror of `metrics.json` (must validate against `eval/cold-start/metrics.schema.json`).

| Key | Value |
|-----|-------|
| `finalized` | `true` |
| `did_it_work` | `<true | false>` |
| `reached_checkpoint` | `<installed | read_attempted | read_ok | wall>` |
| `install_ms` | `<number — runner-filled>` |
| `install_to_read_ms` | `<number or null>` |
| `wall_reason` | `<free text, or null if it worked>` |
| `verdict` | `<PASS | PARTIAL | FAIL>` |

Read attempt (SHAL audit convention + real-device evidence):

| Field | Value |
|-------|-------|
| `event` | `audit` |
| `op` | `<now_playing | get_volume | battery | clean_state | ...>` |
| `outcome` | `<ok | error | rejected | approved | denied>` |
| `delivered` | `<none | yes | no | unknown | null>` |
| `duration_ms` | `<number or null>` |
| `txn` | `<4-char id or null>` |
| `device_address` | `<real IP/host — never sim/localhost; null if no read landed>` |
| `raw_response` | `<verbatim returned value, or null if no read landed>` |

---

## 5. UX narrative (newcomer feel)

Write as the brand-new user. Was the next step obvious after `pip install`? What did
the README / `shal-mcp --help` / error messages tell you — or fail to tell you? Where
did you have to guess? Where did you stall as a non-expert?

`<...>`

---

## 6. EX narrative (engineer / driver-author feel)

Write as the engineer following the documented doc->driver path. Was `docs/SDK.md` +
the `shal-generate-driver` skill enough to author a driver (under your own compatible,
not `sonos,speaker`) from the device's own docs? Where did the authoring path break
down — missing bus/transport, missing credentials, the conformance gate,
installing/registering your local driver so `shal.load` finds it? Was the boundary
between "not bundled" and "not supported" clear?

`<...>`

---

## 7. Blockers

| # | Wall | What it was | What would have unblocked it |
|---|------|-------------|------------------------------|
| 1 | `<short label>` | `<detail>` | `<doc, feature, cred, bus, ...>` |
| 2 | ... | ... | ... |

---

## 8. Verdict

- **Verdict:** `<PASS | PARTIAL | FAIL>`
- **Justification:** `<one or two sentences against the rubric in AGENT_BRIEF.md verdict rubric>`

### Integrity self-check (answer each — see AGENT_BRIEF.md)

- [ ] I did **not** import `shal.drivers.*`, reference the `sonos,speaker` compatible,
      or use `shal-mcp --device`.
- [ ] I installed **only** `pyshal[mcp]` — no device extra (see pip-freeze.install.txt).
- [ ] I did **not** use `address: sim` or any simulator; the read targeted the real
      device's network address.
- [ ] I issued **read-only** ops; I never actuated.
- [ ] Every checkpoint timestamp reflects a real event (not backfilled); I left the
      runner-owned `t_install_*` timestamps untouched.
- [ ] A claimed read is backed by the device's actual returned data AND its real
      address in section 3 (and in `read_attempt`).
- [ ] If I hit a wall, I recorded it honestly and stopped (no fabricated read, no quiet
      use of the bundled driver or a simulator to "make it pass").
