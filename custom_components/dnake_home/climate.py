import asyncio
import logging

from homeassistant.components.climate import ClimateEntity
from homeassistant.components.climate.const import (
    FAN_LOW,
    FAN_MIDDLE,
    FAN_HIGH,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .core.assistant import assistant
from .core.constant import DOMAIN, MANUFACTURER
from .core.utils import get_key_by_value

_LOGGER = logging.getLogger(__name__)

_min_air_condition_temperature = 16
_max_air_condition_temperature = 32
_air_condition_hvac_table = {
    0: HVACMode.OFF,
    1: HVACMode.HEAT,
    2: HVACMode.COOL,
    3: HVACMode.FAN_ONLY,
    4: HVACMode.DRY,
}

_air_condition_swing_table = {
    0: "关闭摆动",
    1: "开启摆动",
    2: "横向摆风",
    3: "纵向摆风",
}

_air_condition_fan_table = {0: FAN_LOW, 1: FAN_MIDDLE, 2: FAN_HIGH}


CLIMATE_DEVICE_TYPES = {1536, 16640, 16671}


def load_climates(device_list):
    # 过滤空调设备并去重（基于设备编号和通道）
    seen = set()
    air_conditions = []
    candidates = []
    for device in device_list:
        ty = device.get("ty")
        name = device.get("na", "")
        if ty in CLIMATE_DEVICE_TYPES or "空调" in str(name):
            dev_key = (device.get("nm"), device.get("ch"))
            candidates.append((name, ty, dev_key))
            if dev_key not in seen:
                seen.add(dev_key)
                if ty not in CLIMATE_DEVICE_TYPES:
                    _LOGGER.warning(
                        f"Load device as climate even though type is unexpected: {name} (ty={ty})"
                    )
                air_conditions.append(DnakeAirCondition(device))
            else:
                _LOGGER.warning(
                    f"Duplicate climate device found: {name} (nm={device.get('nm')}, ch={device.get('ch')})"
                )

    _LOGGER.info(
        f"find air_condition num: {len(air_conditions)}, climate candidates: {[(n, t) for n, t, _ in candidates]}"
    )
    for climate in air_conditions:
        _LOGGER.info(
            "Climate entity loaded: name=%s, nm=%s, ch=%s, type=%s",
            climate._name,
            climate._dev_no,
            climate._dev_ch,
            climate._dev_type,
        )
    if not air_conditions and candidates:
        _LOGGER.warning(
            "Found potential climate devices but none were loaded as entities. "
            "Please check device types and fields."
        )
    assistant.entries["climate"] = air_conditions


def update_climates_state(states):
    climates = assistant.entries["climate"]
    for climate in climates:
        state = next((state for state in states if climate.is_hint_state(state)), None)
        if state:
            climate.update_state(state)


async def async_setup_entry(
        hass: HomeAssistant,
        entry: ConfigEntry,
        async_add_entities: AddEntitiesCallback,
):
    climate_list = assistant.entries["climate"]
    if climate_list:
        async_add_entities(climate_list)


class DnakeAirCondition(ClimateEntity):

    def __init__(self, device):
        self._name = device.get("na")
        self._dev_no = device.get("nm")
        self._dev_ch = device.get("ch")
        self._dev_type = device.get("ty")
        self._is_on = device.get("powerOn", 0) == 1
        self._hvac_mode = _air_condition_hvac_table.get(
            device.get("mode"), HVACMode.OFF
        )
        self._swing_mode = _air_condition_swing_table.get(
            device.get("swing"), "关闭摆动"
        )
        self._fan_mode = _air_condition_fan_table.get(device.get("speed"), FAN_LOW)
        self._current_temperature = device.get(
            "tempIndoor", _min_air_condition_temperature
        )
        self._target_temperature = device.get(
            "tempDesire", _min_air_condition_temperature
        )

    def is_hint_state(self, state):
        return state.get('devType') == self._dev_type and state.get("devNo") == self._dev_no and state.get(
            "devCh") == self._dev_ch

    @property
    def unique_id(self):
        return f"dnake_air_condition_{self._dev_no}_{self._dev_ch}"

    @property
    def device_info(self):
        return DeviceInfo(
            identifiers={(DOMAIN, f"air_condition_{self._dev_no}_{self._dev_ch}")},
            name=self._name,
            manufacturer=MANUFACTURER,
            model="空调控制",
        )

    @property
    def supported_features(self):
        return (
                ClimateEntityFeature.TURN_ON
                | ClimateEntityFeature.TURN_OFF
                | ClimateEntityFeature.TARGET_TEMPERATURE
                | ClimateEntityFeature.SWING_MODE
                | ClimateEntityFeature.FAN_MODE
        )

    @property
    def should_poll(self):
        return False

    @property
    def name(self):
        return self._name

    @property
    def min_temp(self):
        return _min_air_condition_temperature

    @property
    def max_temp(self):
        return _max_air_condition_temperature

    @property
    def target_temperature_step(self):
        return 1

    @property
    def temperature_unit(self):
        return UnitOfTemperature.CELSIUS

    @property
    def current_temperature(self):
        return self._current_temperature

    @property
    def target_temperature(self):
        return self._target_temperature

    @property
    def hvac_mode(self):
        return self._hvac_mode if self._is_on else HVACMode.OFF

    @property
    def hvac_modes(self):
        return list(_air_condition_hvac_table.values())

    @property
    def swing_mode(self):
        return self._swing_mode

    @property
    def swing_modes(self):
        return list(_air_condition_swing_table.values())

    @property
    def fan_mode(self):
        return self._fan_mode

    @property
    def fan_modes(self):
        return list(_air_condition_fan_table.values())

    async def _async_turn_to(self, is_open: bool):
        is_success = await self.hass.async_add_executor_job(
            assistant.set_air_condition_power,
            self._dev_no,
            self._dev_ch,
            is_open,
        )
        if is_success:
            self._is_on = is_open
            self.async_write_ha_state()

    async def async_turn_on(self, **kwargs):
        await self._async_turn_to(True)

    async def async_turn_off(self, **kwargs):
        await self._async_turn_to(False)

    async def async_set_hvac_mode(self, hvac_mode):
        if hvac_mode == HVACMode.OFF:
            await self.async_turn_off()
        else:
            switch_success = await self.hass.async_add_executor_job(
                assistant.set_air_condition_hvac_mode,
                self._dev_no,
                self._dev_ch,
                get_key_by_value(_air_condition_hvac_table, hvac_mode, 0),
            )
            if switch_success:
                self._hvac_mode = hvac_mode
                self.async_write_ha_state()
            if not self._is_on:
                await asyncio.sleep(2)
                await self.async_turn_on()

    async def async_set_temperature(self, **kwargs):
        temperature = kwargs.get("temperature")
        is_success = await self.hass.async_add_executor_job(
            assistant.set_air_condition_temperature,
            self._dev_no,
            self._dev_ch,
            temperature,
        )
        if is_success:
            self._target_temperature = temperature
            self.async_write_ha_state()

    async def async_set_swing_mode(self, swing_mode):
        is_success = await self.hass.async_add_executor_job(
            assistant.set_air_condition_swing_mode,
            self._dev_no,
            self._dev_ch,
            get_key_by_value(_air_condition_swing_table, swing_mode, 0),
        )
        if is_success:
            self._swing_mode = swing_mode
            self.async_write_ha_state()

    async def async_set_fan_mode(self, fan_mode):
        is_success = await self.hass.async_add_executor_job(
            assistant.set_air_condition_fan_mode,
            self._dev_no,
            self._dev_ch,
            get_key_by_value(_air_condition_fan_table, fan_mode, 0),
        )
        if is_success:
            self._fan_mode = fan_mode
            self.async_write_ha_state()

    def update_state(self, state):
        self._is_on = state.get("powerOn", 0) == 1
        self._hvac_mode = _air_condition_hvac_table.get(state.get("mode"), HVACMode.OFF)
        self._swing_mode = _air_condition_swing_table.get(
            state.get("swing"), "关闭摆动"
        )
        self._fan_mode = _air_condition_fan_table.get(state.get("speed"), FAN_LOW)
        self._current_temperature = state.get(
            "tempIndoor", _min_air_condition_temperature
        )
        self._target_temperature = state.get(
            "tempDesire", _min_air_condition_temperature
        )
        self.async_write_ha_state()
