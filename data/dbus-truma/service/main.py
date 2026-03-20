#!/usr/bin/env python3
"""Truma BLE service for Cerbo GX / Venus OS.

Connects to Truma iNetX heater via BLE, publishes state to D-Bus (auto-bridged
to MQTT for Home Assistant), and provides a local REST API on port 8090.
"""
import asyncio
import signal
import logging
import time

from .const import TOPIC_BATCHES
from .protocol import (
    build_register_frame, build_subscribe_frame,
    build_identity_frames, build_write_frame, parse_v3_frame
)
from .ble_transport import BleTransport
from .truma_state import TrumaState
from .rest_api import TrumaRestApi

# Try to import D-Bus service (only works on Venus OS)
try:
    from .dbus_service import TrumaDbusService
    HAS_DBUS = True
except Exception:
    HAS_DBUS = False

logger = logging.getLogger("truma")


class TrumaService:
    """Main service orchestrating BLE, state, D-Bus, and REST API."""

    def __init__(self):
        self.transport = BleTransport()
        self.state = TrumaState()
        self.dbus_service = None
        self.rest_api = None
        self._running = False
        self._reconnect_delay = 5  # seconds, increases on failure

    async def start(self):
        """Start the service."""
        self._running = True

        # Setup logging
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
            datefmt='%H:%M:%S'
        )

        logger.info("Truma BLE service starting...")

        # Register data callback
        self.transport.on_data(self._on_ble_data)

        # Start REST API
        self.rest_api = TrumaRestApi(
            state_getter=self.state.get_status,
            command_sender=self._handle_command,
            health_getter=self._get_health,
            port=8090
        )
        self.rest_api.start()

        # Start D-Bus service (if available)
        if HAS_DBUS:
            try:
                self.dbus_service = TrumaDbusService(
                    send_command_callback=self._handle_command_async
                )
                logger.info("D-Bus service started")
            except Exception as e:
                logger.warning("D-Bus service failed: %s", e)

        # Main loop: connect and maintain connection
        while self._running:
            try:
                await self._connect_and_run()
            except Exception as e:
                logger.error("Connection error: %s", e)
                self.state.connected = False
                if self.dbus_service:
                    self.dbus_service.update_from_state(self.state)

            if self._running:
                logger.info("Reconnecting in %ds...", self._reconnect_delay)
                await asyncio.sleep(self._reconnect_delay)
                self._reconnect_delay = min(self._reconnect_delay * 2, 60)

    async def _connect_and_run(self):
        """Connect to Truma and run the main data loop."""
        # 1. Connect BLE
        logger.info("Connecting to Truma...")
        await self.transport.connect()
        self._reconnect_delay = 5  # reset on successful connect

        # 2. Register — wait for assigned addr before proceeding
        logger.info("Registering (pv=[5,1])...")
        reg_frame = build_register_frame(self.transport.assigned_addr)
        await self.transport.send(reg_frame)
        # Wait for registration response (addr assignment)
        for _ in range(20):
            await asyncio.sleep(1)
            if self.transport.assigned_addr != 0x0500:
                break
        self.state.assigned_addr = self.transport.assigned_addr
        logger.info("Using addr: 0x%04X", self.transport.assigned_addr)

        # 3. Subscribe all topics in batches (using assigned addr)
        logger.info("Subscribing to %d topic batches...", len(TOPIC_BATCHES))
        for i, batch in enumerate(TOPIC_BATCHES):
            sub_frame = build_subscribe_frame(self.transport.assigned_addr, batch)
            await self.transport.send(sub_frame)
            await asyncio.sleep(0.5)
        await asyncio.sleep(3)

        # 4. Send identity sequence
        logger.info("Sending identity...")
        identity_frames = build_identity_frames(
            self.transport.assigned_addr,
            self.transport.identity
        )
        for frame in identity_frames:
            await self.transport.send(frame)
            await asyncio.sleep(0.5)

        # 5. Mark connected
        self.state.connected = True
        logger.info("Connected and subscribed! Listening for data...")

        # 6. Main loop: keep alive, update D-Bus
        while self._running and self.transport._connected:
            # Update D-Bus service with latest state
            if self.dbus_service:
                self.dbus_service.update_from_state(self.state)
            await asyncio.sleep(1)

        logger.warning("BLE connection lost")
        self.state.connected = False

    def _on_ble_data(self, parsed: dict):
        """Handle decoded V3 frame from BLE."""
        if not parsed:
            return

        control_raw = parsed.get('control_raw')
        sub_type = parsed.get('sub_type')
        cbor = parsed.get('cbor')

        if not cbor or not isinstance(cbor, dict):
            return

        # Registration response: ctrl=0x01, sub=0x02
        if control_raw == 0x01 and sub_type == 0x02:
            addr = cbor.get('addr')
            if addr:
                self.transport.assigned_addr = addr
                self.state.assigned_addr = addr
                logger.info("Registered with addr: 0x%04X", addr)
            return

        # Subscribe response: ctrl=0x03, sub=0x82
        if control_raw == 0x03 and sub_type == 0x82:
            topics = cbor.get('tn', [])
            logger.info("Subscribe confirmed: %s", topics)
            return

        # INFO_MESSAGE — parameter update: ctrl=0x03, sub=0x00
        if control_raw == 0x03 and sub_type == 0x00:
            tn = cbor.get('tn', '')
            pn = cbor.get('pn', '')
            v = cbor.get('v')
            if tn and pn and v is not None:
                self.state.update(tn, pn, v)
            return

    def _handle_command(self, topic: str, param: str, value: int) -> tuple:
        """Handle command from REST API (sync)."""
        # Validate
        ok, msg = self.state.validate_command(topic, param, value)
        if not ok:
            return False, msg

        if not self.state.connected:
            return False, "not connected"

        # Send via BLE (fire and forget from sync context)
        dest = self.state.get_command_dest(topic)
        frame = build_write_frame(self.transport.assigned_addr, dest, topic, param, value)
        asyncio.ensure_future(self.transport.send(frame))
        return True, f"sent {topic}.{param}={value}"

    def _handle_command_async(self, topic: str, param: str, value: int):
        """Handle command from D-Bus (fire and forget)."""
        ok, msg = self._handle_command(topic, param, value)
        if not ok:
            logger.warning("Command rejected: %s", msg)

    def _get_health(self) -> dict:
        """Get health status for REST API."""
        return {
            "connected": self.state.connected,
            "uptime": time.time() - self._start_time if hasattr(self, '_start_time') else 0,
            "last_update": self.state.last_update,
            "assigned_addr": f"0x{self.state.assigned_addr:04X}",
            "raw_param_count": len(self.state.raw_params),
        }

    async def stop(self):
        """Clean shutdown."""
        logger.info("Shutting down...")
        self._running = False
        if self.rest_api:
            self.rest_api.stop()
        await self.transport.disconnect()
        logger.info("Shutdown complete")


def main():
    """Entry point."""
    service = TrumaService()
    service._start_time = time.time()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    # Signal handlers
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda: asyncio.ensure_future(service.stop()))

    try:
        loop.run_until_complete(service.start())
    except KeyboardInterrupt:
        loop.run_until_complete(service.stop())
    finally:
        loop.close()


if __name__ == "__main__":
    main()
