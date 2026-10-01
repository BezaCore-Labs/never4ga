"""Never4gA port interfaces.

core/05 section 20 and core/06 section 5: core logic depends on these contracts,
never on a concrete storage engine or provider SDK. Signatures are minimal:
speculative methods are deliberately absent and are added when a caller needs
them.

Ports are ``typing.Protocol`` classes rather than ABCs so that an adapter need
not inherit from Never4gA to satisfy one -- important for adapters wrapping
third-party clients.
"""
