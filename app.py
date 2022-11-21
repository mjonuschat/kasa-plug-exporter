#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import logging
import time

import typing as t
from enum import Enum
from ipaddress import IPv4Address

import click
import yaml
from asyncio_mqtt import Client as MqttClient, MqttError
from bestlog import logger as bestlog
from kasa import SmartPlug, SmartStrip
from pydantic import BaseModel, BaseSettings

logger = bestlog.get("kasa-plug-exporter")


class MQTTPublisher:
    reconnect_interval: float = 5.0

    def __init__(self, queue: asyncio.Queue, server: MQTTServer) -> None:
        self.__queue = queue
        self.__server = server

        logger.info("Using MQTT Server at %s:%s", server.address, server.port)

    async def start(self) -> None:
        while True:
            try:
                async with MqttClient(
                    hostname=str(self.__server.address), port=self.__server.port
                ) as client:
                    while True:
                        item: MqttMessage = await self.__queue.get()
                        await client.publish(item.topic, item.payload.json())
                        logger.debug(
                            "Published status update to MQTT topic %s: %s",
                            item.topic,
                            item.payload,
                        )
                        self.__queue.task_done()
            except MqttError as error:
                logger.error(
                    f'Error "{error}". Reconnecting in {self.reconnect_interval} seconds.'
                )
                await asyncio.sleep(self.reconnect_interval)


class MqttMessage(BaseModel):
    topic: str
    payload: BaseModel


class Payload(BaseModel):
    on: bool = False
    voltage: float = 0.0
    power: float = 0.0
    current: float = 0.0
    total_kwh: float = 0.0


class DeviceMonitor:
    def __init__(
        self,
        name: str,
        queue: asyncio.Queue,
        device: t.Union[SmartStrip, SmartPlug],
        interval: float = 10.0,
    ) -> None:
        self.__name = name
        self.__device = device
        self.__interval = interval - 0.0015
        self.__running = False
        self.__task: t.Optional[asyncio.Task] = None
        self.__queue = queue

    def start(self) -> None:
        logger.info("Starting to monitor %s@%s", self.__name, self.__device.host)
        self.__task = asyncio.create_task(self._run())
        self.__running = True

    async def _run(self) -> None:
        while True:
            if not self.__running:
                logger.info("Monitor is stopped, bailing!")
                return

            logger.debug("Updating information for %s", self.__name)
            start = time.monotonic()
            try:
                await self._update()
            except Exception:
                logger.exception("Error updating device data for %s", self.__name)
            duration = time.monotonic() - start
            logger.debug("Updated device %s in %0.6fs", self.__name, duration)
            # Reduce sleep delay by update execution duration
            await asyncio.sleep(
                delay=max(0.0, self.__interval - (time.monotonic() - start))
            )

    async def _update(self) -> None:
        logger.debug("Reading current state of %s", self.__name)
        await self.__device.update()

        # Main plug/strip
        payload = Payload(on=self.__device.is_on)
        if self.__device.has_emeter:
            payload = Payload(
                on=self.__device.is_on,
                current=self.__device.emeter_realtime.current or 0.0,
                voltage=self.__device.emeter_realtime.voltage or 0.0,
                power=self.__device.emeter_realtime.power or 0.0,
                total_kwh=self.__device.emeter_realtime.total or 0.0,
            )

        await self.__queue.put(
            MqttMessage(
                topic=f"kasa/{self.__name}/status",
                payload=payload,
            )
        )

        # Children (plugs on a strip)
        if self.__device.is_strip:
            for idx, plug in enumerate(self.__device.children):
                plug_id = idx + 1
                topic = f"kasa/{self.__name}/{plug_id:02}/status"
                payload = Payload(on=plug.is_on)
                if plug.has_emeter:
                    payload = Payload(
                        on=plug.is_on,
                        current=plug.emeter_realtime.current or 0.0,
                        voltage=plug.emeter_realtime.voltage or 0.0,
                        power=plug.emeter_realtime.power or 0.0,
                        total_kwh=plug.emeter_realtime.total or 0.0,
                    )

                await self.__queue.put(
                    MqttMessage(
                        topic=topic,
                        payload=payload,
                    )
                )

    async def _stop(self):
        self.__running = False
        await self.__task


class GenericDeviceType(str, Enum):
    PLUG = "plug"
    STRIP = "strip"


class MQTTServer(BaseModel):
    address: t.Union[IPv4Address, str] = IPv4Address("0.0.0.0")
    port: int = 1883
    enabled: bool = False


class Device(BaseModel):
    name: str
    type: GenericDeviceType
    address: IPv4Address


class Settings(BaseSettings):
    devices: t.List[Device]
    mqtt: MQTTServer = MQTTServer()


async def _main(settings: Settings, debug: bool = False) -> None:
    queue = asyncio.Queue(maxsize=500)
    publisher = MQTTPublisher(queue=queue, server=settings.mqtt)

    monitors = []
    for dev in settings.devices:
        if dev.type is GenericDeviceType.PLUG:
            device = SmartPlug(host=str(dev.address))
        elif dev.type is GenericDeviceType.STRIP:
            device = SmartStrip(host=str(dev.address))
        else:
            raise ValueError(f"{dev.type} is not yet supported")

        monitor = DeviceMonitor(name=dev.name, queue=queue, device=device)
        monitor.start()
        monitors.append(monitor)

    await publisher.start()


@click.command()
@click.option(
    "--config-file",
    default="config.yml",
    help="Path to configuration file",
    type=click.File("rb"),
)
@click.option("--debug", default=False, is_flag=True, help="Debug output")
def main(config_file: click.File, debug: bool = False) -> None:
    log_level = logging.DEBUG if debug else logging.INFO

    bestlog.init("kasa-plug-exporter", to_file=False, log_level=log_level)
    config = yaml.safe_load(config_file)
    settings = Settings(**config)

    asyncio.run(_main(settings=settings, debug=debug))


if __name__ == "__main__":
    main()
