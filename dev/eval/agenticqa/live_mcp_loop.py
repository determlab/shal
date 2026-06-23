"""Tier-2 LIVE loop over the shipped `shal mcp` adapter (issue #88, DoD step 7).

Drives a REAL device through SHAL's actuation gate **over the MCP wire** — the same
protocol a Claude/host MCP client speaks — without needing the host to mount the server
or a session reload. It launches the cold-installed `shal mcp <topology> --drivers ...`
as a stdio MCP server and talks to it as a client, so what it exercises is the *shipped
bridge*, not the dev tree.

It is **manuscript-driven** (same specs as run.py's in-process loop): a new device = a
new manuscript, zero code change. Tool names in the manuscript are the runtime
`<node>__<op>` handles (e.g. `cleaner__start_cleaning`).

Role separation is enforced **in code**: the OPERATOR may call reads + actuators but
NOT `shal_approve`/`shal_deny`; the APPROVER may call ONLY `shal_approve`/`shal_deny`.
Either crosses its allowlist => the run aborts. This is one process (one shared
pending-ticket table, so approve finds the operator's ticket) — faithful to the gate,
one notch short of two separate *live agents* (that variant needs the host to mount the
server + a session reconnect; see LIVE-MCP-LOOP.md).

Verdict is a deterministic device read-back (`expected.becomes` / `deny_path.stays`),
never an opinion. A green that moved pre-approval, self-approved, or skipped the gate is RED.

Run it WITH THE COLD-INSTALLED VENV PYTHON (it needs `mcp` + `yaml` from `pyshal[mcp]`):

    /path/to/rig/.venv/Scripts/python.exe dev/eval/agenticqa/live_mcp_loop.py \
        --manuscript /path/to/rig/deebot_n20.manuscript.yaml \
        --deny --out /path/to/rig/evidence

Exit code is 0 only if every run passed — usable as the pre-publish live gate.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

import anyio
import yaml
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# robot motion is slow; poll the read-back instead of guessing a fixed wait.
SETTLE_TRIES = 20
SETTLE_GAP_S = 2.0


# ---- manuscript -------------------------------------------------------------
def load_manuscript(path: Path) -> dict:
    m = yaml.safe_load(path.read_text(encoding="utf-8"))
    m["_dir"] = path.parent
    for k in ("device", "topology", "actuation", "expected", "liveness_reads"):
        if k not in m:
            raise SystemExit(f"manuscript missing required key: {k}")
    return m


def server_params(m: dict, server_python: Path) -> StdioServerParameters:
    """Launch the cold-installed `shal mcp` for this manuscript's topology + drivers."""
    shal = server_python.parent / ("shal.exe" if sys.platform == "win32" else "shal")
    if not shal.exists():
        raise SystemExit(f"shal launcher not found next to python: {shal}")
    d = m["_dir"]
    topo = str((d / m["topology"]).resolve())
    args = ["mcp", topo]
    drivers = m.get("drivers") or []
    if drivers:
        args.append("--drivers")
        args += [str((d / drv).resolve()) for drv in drivers]
    # creds resolve from the .env beside the topology (#73) — we pass the ambient env only.
    return StdioServerParameters(command=str(shal), args=args, env=dict(os.environ))


# ---- MCP result helpers -----------------------------------------------------
def _text(result) -> str:
    parts = []
    for c in getattr(result, "content", []) or []:
        t = getattr(c, "text", None)
        if t:
            parts.append(t)
    return "\n".join(parts).strip()


def _payload(result) -> dict:
    """Best-effort structured view of a tool result (structuredContent or parsed text)."""
    sc = getattr(result, "structuredContent", None)
    if isinstance(sc, dict):
        return sc
    txt = _text(result)
    try:
        obj = json.loads(txt)
        return obj if isinstance(obj, dict) else {"_text": txt}
    except Exception:
        return {"_text": txt}


_APPROVAL_RE = re.compile(r"approval[_-]?id['\"]?\s*[:=]\s*['\"]?([A-Za-z0-9._\-]+)", re.I)


def extract_approval_id(result) -> str | None:
    p = _payload(result)
    for key in ("approval_id", "approvalId", "id", "ticket", "ticket_id"):
        v = p.get(key)
        if isinstance(v, str) and v:
            return v
    mt = _APPROVAL_RE.search(_text(result))
    return mt.group(1) if mt else None


def is_error(result) -> bool:
    return bool(getattr(result, "isError", False))


# ---- roles (allowlists enforced in code) ------------------------------------
class Role:
    def __init__(self, session: ClientSession, name: str, allow, forbid):
        self.s, self.name, self.allow, self.forbid = session, name, allow, forbid

    async def call(self, tool: str, args: dict | None = None):
        if tool in self.forbid or (self.allow is not None and tool not in self.allow):
            raise SystemExit(
                f"ROLE VIOLATION: {self.name} tried to call {tool!r} outside its allowlist "
                f"— aborting (this would invalidate the gate test)."
            )
        return await self.s.call_tool(tool, arguments=args or {})


def approve_arg_name(tools: dict, approve_tool: str) -> str:
    schema = (tools[approve_tool].inputSchema or {}) if approve_tool in tools else {}
    props = list((schema.get("properties") or {}).keys())
    req = schema.get("required") or props
    return (req[0] if req else (props[0] if props else "approval_id"))


# ---- one run ----------------------------------------------------------------
async def settle(role: Role, read_tool: str, want: str) -> str:
    last = ""
    for _ in range(SETTLE_TRIES):
        r = await role.call(read_tool)
        last = _text(r)
        if want.lower() in last.lower():
            return last
        await anyio.sleep(SETTLE_GAP_S)
    return last


async def run_control(op: Role, ap: Role, tools: dict, m: dict, *, decision: str) -> dict:
    actuation = m["actuation"]["tool"]
    act_args = m["actuation"].get("args") or {}
    exp_read = m["expected"]["read"]
    becomes = str(m["expected"]["becomes"])
    stays = str((m.get("deny_path") or {}).get("stays", ""))
    approve_tool = next((t for t in tools if t.endswith("shal_approve") or t == "shal_approve"), "shal_approve")
    deny_tool = next((t for t in tools if t.endswith("shal_deny") or t == "shal_deny"), "shal_deny")
    arg = approve_arg_name(tools, approve_tool)

    ev: dict = {"device": m["device"], "decision": decision, "actuation": actuation}

    # 1. liveness reads — must return live data (a raised/error read is a hard fail)
    reads = {}
    for rt in m["liveness_reads"]:
        r = await op.call(rt)
        if is_error(r):
            return {**ev, "passed": False, "reason": f"liveness read {rt} errored: {_text(r)}"}
        reads[rt] = _text(r)
    ev["liveness_reads"] = reads
    ev["state_before"] = _text(await op.call(exp_read))

    # 2. drive ONE actuation — gate MUST intercept, nothing sent yet
    req = await op.call(actuation, act_args)
    approval_id = extract_approval_id(req)
    ev["approval_id"] = approval_id
    ev["actuation_result"] = _text(req)
    if not approval_id:
        return {**ev, "passed": False,
                "reason": f"gate did NOT defer: no approval_id from {actuation} (gate may be bypassed)"}
    # pre-approval the device must NOT have moved
    pre = _text(await op.call(exp_read))
    ev["state_after_request"] = pre
    if becomes.lower() in pre.lower():
        return {**ev, "passed": False, "reason": f"device moved BEFORE approval (read={pre}) — RED"}

    # 3. APPROVER decides (separate role; operator cannot reach these tools)
    if decision == "approve":
        dec = await ap.call(approve_tool, {arg: approval_id})
        ev["approver_result"] = _text(dec)
        after = await settle(op, exp_read, becomes)
        ev["state_after"] = after
        passed = becomes.lower() in after.lower()
        ev["reason"] = (f"approved → {exp_read} became {after!r} (== {becomes})" if passed
                        else f"approved but {exp_read}={after!r} never became {becomes!r}")
    else:  # deny
        dec = await ap.call(deny_tool, {arg: approval_id})
        ev["approver_result"] = _text(dec)
        after = _text(await op.call(exp_read))
        ev["state_after"] = after
        passed = (stays.lower() in after.lower()) and (becomes.lower() not in after.lower())
        ev["reason"] = (f"denied → {exp_read} stayed {after!r} (device did NOT move)" if passed
                        else f"denied but {exp_read}={after!r} (expected to stay {stays!r}) — RED")
    ev["passed"] = passed
    return ev


async def teardown(op: Role, ap: Role, tools: dict, m: dict) -> list[dict]:
    """Leave the device safe: each teardown op is gated, so the approver approves it."""
    approve_tool = next((t for t in tools if t.endswith("shal_approve")), "shal_approve")
    arg = approve_arg_name(tools, approve_tool)
    out = []
    for tool in m.get("teardown") or []:
        r = await op.call(tool)
        aid = extract_approval_id(r)
        line = {"tool": tool, "approval_id": aid}
        if aid:
            line["approved"] = _text(await ap.call(approve_tool, {arg: aid}))
        out.append(line)
    return out


# ---- session orchestration --------------------------------------------------
async def drive(m: dict, deny: bool, server_python: Path) -> dict:
    params = server_params(m, server_python)
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as session:
            await session.initialize()
            tools = {t.name: t for t in (await session.list_tools()).tools}

            reads = set(m["liveness_reads"]) | {m["expected"]["read"]}
            actuators = {m["actuation"]["tool"], *(m.get("teardown") or [])}
            approve_tools = {t for t in tools if t.endswith(("shal_approve", "shal_deny"))}
            missing = [t for t in (reads | actuators) if t not in tools]
            if missing:
                raise SystemExit(f"served device is missing manuscript tools: {missing}")

            operator = Role(session, "operator", allow=(reads | actuators), forbid=approve_tools)
            approver = Role(session, "approver", allow=approve_tools, forbid=(reads | actuators) - {m["expected"]["read"]})
            # approver may read state to confirm (get_state allowed), but never actuate.

            runs = [await run_control(operator, approver, tools, m, decision="approve")]
            td = await teardown(operator, approver, tools, m)
            if deny and m.get("deny_path"):
                # teardown (go_charge) leaves the robot *returning*; wait for it to reach the
                # deny-path baseline before testing deny, else we'd compare against a transient.
                await settle(operator, m["expected"]["read"], str(m["deny_path"]["stays"]))
                runs.append(await run_control(operator, approver, tools, m, decision="deny"))
            return {"device": m["device"], "runs": runs, "teardown": td,
                    "tools": sorted(tools), "passed": all(r["passed"] for r in runs)}


# ---- report -----------------------------------------------------------------
def write_evidence(result: dict, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "evidence.json").write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    lines = [f"# Live MCP loop evidence — {result['device']}", ""]
    lines.append(f"**Verdict: {'GREEN' if result['passed'] else 'RED'}**")
    lines.append("")
    for run in result["runs"]:
        lines += [
            f"## {run['decision'].upper()} run — {'PASS' if run['passed'] else 'FAIL'}",
            f"- actuation: `{run['actuation']}`  approval_id: `{run.get('approval_id')}`",
            f"- state_before: `{run.get('state_before')}`",
            f"- state_after_request (pre-approval): `{run.get('state_after_request')}`",
            f"- state_after (post-decision): `{run.get('state_after')}`",
            f"- liveness_reads: `{run.get('liveness_reads')}`",
            f"- approver_result: `{run.get('approver_result')}`",
            f"- reason: {run.get('reason')}",
            "",
        ]
    lines += ["## teardown", f"```\n{json.dumps(result['teardown'], indent=2)}\n```", ""]
    md = out_dir / "evidence.md"
    md.write_text("\n".join(lines), encoding="utf-8")
    return md


def main(argv: list[str] | None = None) -> int:
    try:  # device/gate messages carry unicode (—, →); don't die on a cp1252 console
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(prog="live_mcp_loop", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--manuscript", required=True, help="path to the device manuscript yaml")
    ap.add_argument("--deny", action="store_true", help="also run the deny-path (gated devices)")
    ap.add_argument("--out", default=None, help="evidence dir (default: <manuscript dir>/evidence)")
    args = ap.parse_args(argv)

    mpath = Path(args.manuscript).resolve()
    m = load_manuscript(mpath)
    out_dir = Path(args.out).resolve() if args.out else mpath.parent / "evidence"

    result = anyio.run(drive, m, args.deny, Path(sys.executable))
    md = write_evidence(result, out_dir)

    for run in result["runs"]:
        print(f"  [{'PASS' if run['passed'] else 'FAIL'}] {run['device']} {run['decision']:<7}: "
              f"{run.get('state_before')!r} -> {run.get('state_after')!r}  {run.get('reason')}")
    print(f"\n  {'GREEN' if result['passed'] else 'RED'} — evidence: {md}")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
