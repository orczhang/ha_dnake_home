import asyncio
import logging

from homeassistant.components.fan import FanEntity, FanEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .core.assistant import assistant
from .core.constant import DOMAIN, MANUFACTURER

_LOGGER = logging.getLogger(__name__)


def load_fans(device_list):
    fans = []
    seen = set()

    for device in device_list:
        ty = device.get("ty")
        name = str(device.get("na", ""))
        name_lower = name.lower()
        is_fan_name = "新风" in name or "xin feng" in name_lower
        if ty == 1792 or is_fan_name:
            dev_no = device.get("nm")  # 提取设备编号
            dev_ch = device.get("ch")  # 提取设备通道
            dev_key = (dev_no, dev_ch)
            if dev_key not in seen:
                seen.add(dev_key)
                if ty != 1792:
                    _LOGGER.warning(
                        "Load device as fan even though type is unexpected: %s (ty=%s)",
                        name,
                        ty,
                    )
                fans.append(DnakeFan(assistant, name, dev_no, dev_ch))
            else:
                _LOGGER.warning(
                    "Duplicate fan device found: %s (nm=%s, ch=%s)",
                    device.get("na"),
                    device.get("nm"),
                    device.get("ch"),
                )

    _LOGGER.info("find fan num: %s", len(fans))
    for fan in fans:
        _LOGGER.info(
            "Fan entity loaded: name=%s, nm=%s, ch=%s",
            fan._name,
            fan._dev_no,
            fan._dev_ch,
        )
    assistant.entries["fan"] = fans


def update_fans_state(states):
    fans = assistant.entries.get("fan", [])
    for fan in fans:
        state = next(
            (
                state
                for state in states
                if state.get("devType") == 1792 and fan.is_hint_state(state)
            ),
            None,
        )
        if state:
            fan.update_state(state)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
):
    fan_list = assistant.entries.get("fan")
    if fan_list:
        async_add_entities(fan_list)


class DnakeFan(FanEntity):
    def __init__(self, assistant, name, dev_no, dev_ch):
        """Initialize the fan entity."""
        self._assistant = assistant
        self._name = name
        self._dev_no = dev_no
        self._dev_ch = dev_ch
        self._is_on = False
        self._preset_mode = "低速"  # 默认低速

    def is_hint_state(self, state):
        return str(state.get("devNo")) == str(self._dev_no) and str(state.get("devCh")) == str(self._dev_ch)

    @property
    def unique_id(self):
        return f"dnake_fan_{self._dev_no}_{self._dev_ch}"

    @property
    def device_info(self):
        return DeviceInfo(
            identifiers={(DOMAIN, f"fan_{self._dev_no}_{self._dev_ch}")},
            name=self._name,
            manufacturer=MANUFACTURER,
            model="新风控制",
        )

    @property
    def should_poll(self):
        return False

    @property
    def name(self):
        return self._name

    @property
    def is_on(self):
        return self._is_on

    @property
    def preset_mode(self):
        """返回当前风速模式（仅在开启时有效）"""
        return self._preset_mode if self._is_on else None

    @property
    def preset_modes(self):
        """支持低速、高速两种模式"""
        return ["低速", "高速"]

    @property
    def supported_features(self):
        """支持开关和风速预设模式"""
        return FanEntityFeature.TURN_OFF | FanEntityFeature.TURN_ON | FanEntityFeature.PRESET_MODE

    async def async_turn_on(self, percentage=None, preset_mode=None, **kwargs):
        """Turn on the fan with optional speed or preset mode."""
        # 只发送 powerOn 命令
        success = await self.hass.async_add_executor_job(
            self._send_command,
            {"oper": "powerOn", "param": 1},
        )
        if success:
            self._is_on = True
            self.async_write_ha_state()
            await self._refresh_state()
        else:
            _LOGGER.warning("Failed to turn on fan")

    async def async_turn_off(self, **kwargs):
        """Turn off the fan."""
        success = await self.hass.async_add_executor_job(
            self._send_command,
            {"oper": "powerOff", "param": 1},
        )
        if success:
            _LOGGER.info("Fan turned off successfully: %s", self._name)
            self._is_on = False
            self.async_write_ha_state()
            await asyncio.sleep(1)
            await self._refresh_state()

    async def async_set_preset_mode(self, preset_mode: str):
        """设置风速预设模式（低速/高速）"""
        speed_map = {
            "低速": 0,  # speed=0 表示低速
            "高速": 2,  # speed=2 表示高速
        }
        if preset_mode not in speed_map:
            _LOGGER.error("Invalid preset mode: %s", preset_mode)
            return

        speed = speed_map[preset_mode]
        # 使用新的 set_air_fresh_speed 函数控制风速
        success = await self.hass.async_add_executor_job(
            assistant.set_air_fresh_speed,
            self._dev_no,
            self._dev_ch,
            speed,
        )
        if success:
            self._preset_mode = preset_mode
            self._is_on = True
            self.async_write_ha_state()
            await self._refresh_state()
        else:
            _LOGGER.warning("Failed to set preset mode: %s", preset_mode)

    def _send_command(self, data_updates: dict):
        data = {
            "action": "ctrlDev",
            "cmd": "airFresh",
            "devNo": self._dev_no,
            "devCh": self._dev_ch,
        }
        data.update(data_updates)
        _LOGGER.debug("fan send payload: %s", data)
        result = assistant.do_action(data)
        if not result:
            _LOGGER.warning("fan control failed: payload=%s", data)
        return result

    async def _refresh_state(self):
        _LOGGER.debug(
            "Refreshing fan state: name=%s, devNo=%s, devCh=%s",
            self._name,
            self._dev_no,
            self._dev_ch,
        )
        state = await self.hass.async_add_executor_job(
            assistant.read_dev_state,
            self._dev_no,
            self._dev_ch,
        )
        if state and state.get("result") == "ok":
            _LOGGER.debug("Refresh fan state response: %s", state)
            self.update_state(state)
        else:
            _LOGGER.warning("Failed to refresh fan state: %s", state)

    def update_state(self, state):
        self._is_on = state.get("powerOn", 0) == 1
        # 根据 speed 值更新 preset_mode
        speed = state.get("speed", 0)
        if speed == 0:
            self._preset_mode = "低速"
        elif speed == 2:
            self._preset_mode = "高速"
        else:
            self._preset_mode = "低速"  # 默认低速
        self.async_write_ha_state()
