from datetime import datetime,timedelta,timezone
import importlib.util
from pathlib import Path
from types import SimpleNamespace

spec=importlib.util.spec_from_file_location('sensor_age',Path(__file__).parents[1]/'custom_components/hch_passivelink/sensor_age.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


def test_last_reported_has_priority_over_changed_or_updated_time():
    now=datetime(2026,11,24,tzinfo=timezone.utc)
    sensor=SimpleNamespace(last_reported=now-timedelta(seconds=61),last_updated=now-timedelta(days=2))
    assert m.reported_age_seconds(sensor,now)==61


def test_fallback_and_invalid_timestamps_are_never_freshened():
    now=datetime(2026,11,24,tzinfo=timezone.utc)
    assert m.reported_age_seconds(SimpleNamespace(last_updated=now-timedelta(minutes=5)),now)==300
    for reported in [None,now+timedelta(seconds=1),'invalid',now.replace(tzinfo=None)]:
        assert m.reported_age_seconds(SimpleNamespace(last_reported=reported),now) is None
