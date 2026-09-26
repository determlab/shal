"""sonos,speaker — the demo's driver is the ADK reference (#152).

The driver and its sim twin moved into the Authoring Kit as guide material:
``src/shal/adk/reference/sonos/`` (``shal docs --example sonos`` prints it). This
module only imports it, so there is one copy and the demo cannot drift from it.
The reference is not registered by ``import shal`` (D1); importing it here
registers ``sonos,speaker`` for the demo, the same as ``--drivers`` would.
"""
from shal.adk.reference.sonos.driver import SonosSpeaker  # noqa: F401  registers sonos,speaker
