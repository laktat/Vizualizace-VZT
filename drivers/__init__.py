"""
Vrstva driverů — jediné místo, kde se řeší, jakým protokolem zařízení mluví.

Dispečink i poller volají driver a nevědí, jestli je pod ním Modbus TCP nebo
BACnet/IP. Díky tomu může část závodu jet po jedné sběrnici a část po druhé,
aniž by se cokoli nad tím muselo měnit.
"""

from .base import Driver, for_device

__all__ = ["Driver", "for_device"]
