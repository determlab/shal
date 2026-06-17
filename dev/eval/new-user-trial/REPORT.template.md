# SHAL new-user trial — <DEVICE> · <DATE>

> Filled in by the evaluator agent. Be specific and honest. A PARTIAL/NO with crisp
> friction is worth more than a vague YES. Never report a read you didn't actually get
> off the real device.

## 1. Outcome
**<YES | PARTIAL | NO>.** <one line: what real value you read, off which real device/
address — or exactly where you stopped and why.>

- Confirmed real (not the simulator) by: <how you know it wasn't sim>
- Read-only confirmed: <the ops you called were all reads; nothing was actuated>

## 2. Time
Total **<~N min>** (<start> → <end>).
- Install (venv + `pip install` from wheel): <time>
- Figuring-it-out (help/docs/discovery): <time>
- To first real read: <time>  ·  (or **time-to-wall** if you stalled: <time>)

## 3. UX (end-user feel)
<Was the next step obvious? Where did you have to guess? What did `--help` / the docs /
error messages tell you — or fail to? Did it feel magical, confusing, fiddly? What would
a *non-engineer* have experienced at each step?>

## 4. DX (engineer / setup feel)
<Install + extras. Pointing at / authenticating to the device. Error messages. Any
documented path to add or use the device. What tribal knowledge did you have to
reverse-engineer that no shipped doc told you?>

## 5. Friction points (ranked, worst first)
1. **<worst>** — *what happened:* <…>  ·  *what would remove it:* <…>
2. **<next>** — *what happened:* <…>  ·  *what would remove it:* <…>
3. **<…>** — …

## 6. Single biggest improvement
<The one change that would most help the next new user with this device.>

---
*Artifacts:* <paths to the throwaway venv + the script(s) that produced the real read,
left as evidence. Nothing outside the temp folder was modified.>
