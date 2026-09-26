"""A broken first driver, for the failing `shal check` report in AGENT_GUIDE.md
(shal#154, ADK R9). Used only by tests/test_guide_failing_check.py.

The mistake: `get_volume` is public but has no `@op` line. Not in the reference set,
not registered by any entry point, never imported by `import shal`.
"""
import shal
from shal import Driver, idempotent, op


@shal.register
class MyThing(Driver):
    compatible = "community,my-thing"
    kind = None
    llm_ready = True

    @idempotent
    def get_volume(self) -> int:
        raise NotImplementedError

    @op("Set the volume (0-100).", side_effect="write",
        params={"level": {"minimum": 0, "maximum": 100}})
    def set_volume(self, level: int) -> None:
        raise NotImplementedError
