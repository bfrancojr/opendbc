import unittest

from opendbc.car import Bus, DT_CTRL, gen_empty_fingerprint, structs
from opendbc.car.car_helpers import interfaces
from opendbc.car.structs import CarParams
from opendbc.car.fw_versions import build_fw_dict
from opendbc.car.toyota.fingerprints import FW_VERSIONS
from opendbc.car.toyota.values import CAR, DBC, MIN_ACC_SPEED, STATIC_DSU_MSGS, ToyotaFlags, ToyotaSafetyFlags, FW_QUERY_CONFIG, PLATFORM_CODE_ECUS, \
                                                  FUZZY_EXCLUDED_PLATFORMS, get_platform_codes
from opendbc.testing import fuzzy_test

Ecu = CarParams.Ecu


def check_fw_version(fw_version: bytes) -> bool:
  # TODO: just use the FW patterns, need to support all chunks
  return b'?' not in fw_version and b'!' not in fw_version


def cars_with(flags):
  return {c for c in CAR if c.config.flags & flags}


class TestToyotaInterfaces(unittest.TestCase):
  def test_car_flags(self):
    # Angle and radar-ACC cars are always TSS2 cars
    assert not (cars_with(ToyotaFlags.ANGLE_CONTROL | ToyotaFlags.RADAR_ACC) - cars_with(ToyotaFlags.TSS2))

  def test_lta_platforms(self):
    # At this time, only RAV4 2023 is expected to use LTA/angle control
    assert cars_with(ToyotaFlags.ANGLE_CONTROL) == {CAR.TOYOTA_RAV4_TSS2_2023}

  def test_tss2_dbc(self):
    # We make some assumptions about TSS2 platforms,
    # like looking up certain signals only in this DBC
    for car_model, dbc in DBC.items():
      if car_model.config.flags & ToyotaFlags.TSS2 and not (car_model.config.flags & ToyotaFlags.SECOC):
        assert dbc[Bus.pt] == "toyota_nodsu_pt_generated"

  def test_essential_ecus(self):
    # Asserts standard ECUs exist for each platform
    common_ecus = {Ecu.fwdRadar, Ecu.fwdCamera}
    for car_model, ecus in FW_VERSIONS.items():
      with self.subTest(car_model=car_model.value):
        present_ecus = {ecu[0] for ecu in ecus}
        missing_ecus = common_ecus - present_ecus
        assert len(missing_ecus) == 0

        # Some exceptions for other common ECUs
        if car_model not in (CAR.TOYOTA_ALPHARD_TSS2,):
          assert Ecu.abs in present_ecus

        if car_model not in (CAR.TOYOTA_MIRAI,):
          assert Ecu.engine in present_ecus

        if car_model not in (CAR.TOYOTA_PRIUS_V, CAR.LEXUS_CTH):
          assert Ecu.eps in present_ecus


class TestToyotaFingerprint(unittest.TestCase):
  def test_non_essential_ecus(self):
    # Ensures only the cars that have multiple engine ECUs are in the engine non-essential ECU list
    for car_model, ecus in FW_VERSIONS.items():
      with self.subTest(car_model=car_model.value):
        engine_ecus = {ecu for ecu in ecus if ecu[0] == Ecu.engine}
        assert (len(engine_ecus) > 1) == (car_model in FW_QUERY_CONFIG.non_essential_ecus[Ecu.engine]), \
          f"Car model unexpectedly {'not ' if len(engine_ecus) > 1 else ''}in non-essential list"

  def test_valid_fw_versions(self):
    # Asserts all FW versions are valid
    for car_model, ecus in FW_VERSIONS.items():
      with self.subTest(car_model=car_model.value):
        for fws in ecus.values():
          for fw in fws:
            assert check_fw_version(fw), fw

  # Tests for part numbers, platform codes, and sub-versions which Toyota will use to fuzzy
  # fingerprint in the absence of full FW matches:
  @fuzzy_test(max_examples=100)
  def test_platform_codes_fuzzy_fw(self, fuzzy):
    get_platform_codes(fuzzy.list(fuzzy.binary))

  def test_platform_code_ecus_available(self):
    # Asserts ECU keys essential for fuzzy fingerprinting are available on all platforms
    for car_model, ecus in FW_VERSIONS.items():
      with self.subTest(car_model=car_model.value):
        for platform_code_ecu in PLATFORM_CODE_ECUS:
          if platform_code_ecu == Ecu.eps and car_model in (CAR.TOYOTA_PRIUS_V, CAR.LEXUS_CTH,):
            continue
          if platform_code_ecu == Ecu.abs and car_model in (CAR.TOYOTA_ALPHARD_TSS2,):
            continue
          assert platform_code_ecu in [e[0] for e in ecus]

  def test_fw_format(self):
    # Asserts:
    # - every supported ECU FW version returns one platform code
    # - every supported ECU FW version has a part number
    # - expected parsing of ECU sub-versions

    for car_model, ecus in FW_VERSIONS.items():
      with self.subTest(car_model=car_model.value):
        for ecu, fws in ecus.items():
          if ecu[0] not in PLATFORM_CODE_ECUS:
            continue

          codes = dict()
          for fw in fws:
            result = get_platform_codes([fw])
            # Check only one platform code and sub-version
            assert 1 == len(result), f"Unable to parse FW: {fw}"
            assert 1 == len(list(result.values())[0]), f"Unable to parse FW: {fw}"
            codes |= result

          # Toyota places the ECU part number in their FW versions, assert all parsable
          # Note that there is only one unique part number per ECU across the fleet, so this
          # is not important for identification, just a sanity check.
          assert all(code.count(b"-") > 1 for code in codes), f"FW does not have part number: {fw} {codes}"

  def test_platform_codes_spot_check(self):
    # Asserts basic platform code parsing behavior for a few cases
    results = get_platform_codes([
      b"F152607140\x00\x00\x00\x00\x00\x00",
      b"F152607171\x00\x00\x00\x00\x00\x00",
      b"F152607110\x00\x00\x00\x00\x00\x00",
      b"F152607180\x00\x00\x00\x00\x00\x00",
    ])
    assert results == {b"F1526-07-1": {b"10", b"40", b"71", b"80"}}

    results = get_platform_codes([
      b"\x028646F4104100\x00\x00\x00\x008646G5301200\x00\x00\x00\x00",
      b"\x028646F4104100\x00\x00\x00\x008646G3304000\x00\x00\x00\x00",
    ])
    assert results == {b"8646F-41-04": {b"100"}}

    # Short version has no part number
    results = get_platform_codes([
      b"\x0235870000\x00\x00\x00\x00\x00\x00\x00\x00A0202000\x00\x00\x00\x00\x00\x00\x00\x00",
      b"\x0235883000\x00\x00\x00\x00\x00\x00\x00\x00A0202000\x00\x00\x00\x00\x00\x00\x00\x00",
    ])
    assert results == {b"58-70": {b"000"}, b"58-83": {b"000"}}

    results = get_platform_codes([
      b"F152607110\x00\x00\x00\x00\x00\x00",
      b"F152607140\x00\x00\x00\x00\x00\x00",
      b"\x028646F4104100\x00\x00\x00\x008646G5301200\x00\x00\x00\x00",
      b"\x0235879000\x00\x00\x00\x00\x00\x00\x00\x00A4701000\x00\x00\x00\x00\x00\x00\x00\x00",
    ])
    assert results == {b"F1526-07-1": {b"10", b"40"}, b"8646F-41-04": {b"100"}, b"58-79": {b"000"}}

  def test_fuzzy_excluded_platforms(self):
    # Asserts a list of platforms that will not fuzzy fingerprint with platform codes due to them being shared.
    platforms_with_shared_codes = set()
    for platform, fw_by_addr in FW_VERSIONS.items():
      car_fw = []
      for ecu, fw_versions in fw_by_addr.items():
        ecu_name, addr, sub_addr = ecu
        for fw in fw_versions:
          car_fw.append(CarParams.CarFw(ecu=ecu_name, fwVersion=fw, address=addr,
                                        subAddress=0 if sub_addr is None else sub_addr))

      CP = CarParams(carFw=car_fw)
      matches = FW_QUERY_CONFIG.match_fw_to_car_fuzzy(build_fw_dict(CP.carFw), CP.carVin, FW_VERSIONS)
      if len(matches) == 1:
        assert list(matches)[0] == platform
      else:
        # If a platform has multiple matches, add it and its matches
        platforms_with_shared_codes |= {str(platform), *matches}

    assert platforms_with_shared_codes == FUZZY_EXCLUDED_PLATFORMS, (len(platforms_with_shared_codes), len(FW_VERSIONS))


class TestToyotaDisconnectedDsu(unittest.TestCase):
  """openpilot longitudinal on TSS-P cars when the DSU is unplugged (restored from commaai/opendbc#2931) or a smartDSU
  is inline at the DSU. Decided at every start: DSU answers the fw query and no smartDSU -> stock longitudinal;
  DSU absent -> openpilot with stand-in DSU messages; smartDSU (0x2FF on the bus) -> openpilot, DSU keeps AEB."""
  WITH_DSU = [Ecu.engine, Ecu.eps, Ecu.abs, Ecu.fwdRadar, Ecu.fwdCamera, Ecu.dsu]
  WITHOUT_DSU = [Ecu.engine, Ecu.eps, Ecu.abs, Ecu.fwdRadar, Ecu.fwdCamera]

  @staticmethod
  def params(platform, ecus, docs=False, smart_dsu=False):
    car_fw = [CarParams.CarFw(ecu=ecu, fwVersion=b"", address=0x700, subAddress=0, brand="toyota") for ecu in ecus]
    fingerprint = gen_empty_fingerprint()
    if smart_dsu:
      fingerprint[0][0x2FF] = 8
    return interfaces[platform].get_params(platform, fingerprint, car_fw, False, True, docs)

  @staticmethod
  def sent_addresses(CP, frames=100):
    CI = interfaces[CP.carFingerprint](CP)
    CC = structs.CarControl().as_reader()
    sent = set()
    for frame in range(frames):
      CI.update([])
      _, can_sends = CI.apply(CC, int(frame * DT_CTRL * 1e9))
      sent |= {msg[0] for msg in can_sends}  # (address, data, bus)
    return sent

  def test_sienna_smart_dsu_openpilot_longitudinal(self):
    CP = self.params(CAR.TOYOTA_SIENNA, self.WITH_DSU, smart_dsu=True)
    assert CP.flags & ToyotaFlags.SMART_DSU and not CP.enableDsu
    assert CP.openpilotLongitudinalControl and CP.autoResumeSng and CP.minEnableSpeed < 0
    assert not (CP.safetyConfigs[0].safetyParam & ToyotaSafetyFlags.STOCK_LONGITUDINAL)
    sent = self.sent_addresses(CP)
    static = {addr for addr, cars, _, _, _ in STATIC_DSU_MSGS if CAR.TOYOTA_SIENNA in cars}
    assert 0x343 in sent and not (static & sent), sorted(map(hex, sent))  # the real DSU is still there

  def test_stop_timer_cars_stay_stock(self):
    # TSS-P cars that need a resume press after a stop (no NO_STOP_TIMER flag) don't get openpilot longitudinal:
    # the standstill request handling they need was removed upstream in #3076 and is not restored here
    assert not (CAR.TOYOTA_COROLLA.config.flags & ToyotaFlags.NO_STOP_TIMER)
    for kwargs in ({"ecus": self.WITHOUT_DSU}, {"ecus": self.WITH_DSU, "smart_dsu": True}):
      CP = self.params(CAR.TOYOTA_COROLLA, **kwargs)
      assert not CP.enableDsu and not (CP.flags & ToyotaFlags.SMART_DSU) and not CP.openpilotLongitudinalControl, kwargs
      assert CP.safetyConfigs[0].safetyParam & ToyotaSafetyFlags.STOCK_LONGITUDINAL
    assert CAR.TOYOTA_SIENNA.config.flags & ToyotaFlags.NO_STOP_TIMER
    assert all(c.config.flags & ToyotaFlags.NO_STOP_TIMER for c in cars_with(ToyotaFlags.TSS2))

  def test_smart_dsu_ignored_where_the_dsu_does_not_do_long(self):
    CP = self.params(CAR.TOYOTA_RAV4_TSS2, self.WITHOUT_DSU, smart_dsu=True)
    assert not (CP.flags & ToyotaFlags.SMART_DSU) and CP.openpilotLongitudinalControl  # camera-based long, as before
    CP = self.params(CAR.LEXUS_IS, self.WITH_DSU, smart_dsu=True)
    assert not (CP.flags & ToyotaFlags.SMART_DSU) and not CP.openpilotLongitudinalControl

  def test_sienna_dsu_unplugged_openpilot_longitudinal(self):
    CP = self.params(CAR.TOYOTA_SIENNA, self.WITHOUT_DSU)
    assert CP.enableDsu and CP.openpilotLongitudinalControl and CP.autoResumeSng
    assert not (CP.safetyConfigs[0].safetyParam & ToyotaSafetyFlags.STOCK_LONGITUDINAL)
    assert CP.minEnableSpeed < 0

  def test_sienna_dsu_present_stock_longitudinal(self):
    CP = self.params(CAR.TOYOTA_SIENNA, self.WITH_DSU)
    assert not CP.enableDsu and not CP.openpilotLongitudinalControl
    assert CP.safetyConfigs[0].safetyParam & ToyotaSafetyFlags.STOCK_LONGITUDINAL

  def test_no_firmware_means_stock(self):
    # docs generation and a failed fw query must not claim the DSU is gone
    CP = self.params(CAR.TOYOTA_SIENNA, [])
    assert not CP.enableDsu and not CP.openpilotLongitudinalControl

  def test_tss2_and_unsupported_dsu_unaffected(self):
    CP = self.params(CAR.TOYOTA_RAV4_TSS2, self.WITHOUT_DSU)
    assert not CP.enableDsu and CP.openpilotLongitudinalControl  # camera-based long, as before
    CP = self.params(CAR.LEXUS_IS, self.WITHOUT_DSU)
    assert not CP.enableDsu and not CP.openpilotLongitudinalControl

  def test_sng_without_dsu_flag(self):
    assert abs(self.params(CAR.TOYOTA_RAV4H, self.WITH_DSU).minEnableSpeed - MIN_ACC_SPEED) < 1e-3  # capnp stores Float32
    assert self.params(CAR.TOYOTA_RAV4H, self.WITHOUT_DSU).minEnableSpeed < 0
    assert abs(self.params(CAR.TOYOTA_RAV4H, self.WITHOUT_DSU, docs=True).minEnableSpeed - MIN_ACC_SPEED) < 1e-3

  def test_static_dsu_msgs_only_when_dsu_unplugged(self):
    expected = {addr for addr, cars, _, _, _ in STATIC_DSU_MSGS if CAR.TOYOTA_SIENNA in cars}
    assert expected
    for ecus, should_send in ((self.WITHOUT_DSU, True), (self.WITH_DSU, False)):
      sent = self.sent_addresses(self.params(CAR.TOYOTA_SIENNA, ecus))
      assert (expected <= sent) == should_send, (ecus, sorted(map(hex, sent)))
      assert (0x343 in sent) == should_send  # ACC_CONTROL only when openpilot does long
