import enum
from calendar import timegm
from datetime import date, datetime as Datetime, time, timedelta as Timedelta
from datetime import timezone as Timezone
from json import dumps
from typing import Any

from .utils import (
    d_pack, d_unpack, dii_pack,

    min_int4, max_int4, min_int8, max_int8,
    q_pack, q_unpack, qii_pack,
)

ZERO: Timedelta = Timedelta(0)
class DBAPITypeObject:
    """Category comparable to every provider OID belonging to it."""

    def __init__(self, *oids: int) -> None:
        self.oids = frozenset(oids)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, DBAPITypeObject):
            return self.oids == other.oids
        return isinstance(other, int) and other in self.oids

    def __ne__(self, other: object) -> bool:
        return not self == other

    def __instancecheck__(self, value: object) -> bool:
        # Preserve the historical isinstance(value, BINARY) extension.
        return self.oids == frozenset({17}) and isinstance(value, bytes)


STRING = DBAPITypeObject(19, 25, 1042, 1043, 2522, 2530)
NUMBER = DBAPITypeObject(20, 21, 23, 700, 701, 1700, 2500)
DATETIME = DBAPITypeObject(1082, 1083, 1114, 1184, 1266)
ROWID = DBAPITypeObject(26)
BINARY = DBAPITypeObject(17)


class LogOptions(enum.IntFlag):
    Disabled = 0
    Inherit = enum.auto()
    Logfile = enum.auto()


class Interval:
    def __init__(self, microseconds: int = 0, days: int = 0, months: int = 0) -> None:
        self.microseconds = microseconds
        self.days = days
        self.months = months

    def _setMicroseconds(self, value: int) -> None:
        if not isinstance(value, int):
            raise TypeError("microseconds must be an integer type")
        elif not (min_int8 < value <= max_int8):
            raise OverflowError(
                "microseconds must be representable as a 64-bit integer")
        else:
            self._microseconds = value

    def _setDays(self, value: int) -> None:
        if not isinstance(value, int):
            raise TypeError("days must be an integer type")
        elif not (min_int4 < value <= max_int4):
            raise OverflowError(
                "days must be representable as a 32-bit integer")
        else:
            self._days = value

    def _setMonths(self, value: int) -> None:
        if not isinstance(value, int):
            raise TypeError("months must be an integer type")
        elif not (min_int4 < value <= max_int4):
            raise OverflowError(
                "months must be representable as a 32-bit integer")
        else:
            self._months = value

    microseconds = property(lambda self: self._microseconds, _setMicroseconds)
    days = property(lambda self: self._days, _setDays)
    months = property(lambda self: self._months, _setMonths)

    def __repr__(self) -> str:
        return "<Interval %s months %s days %s microseconds>" % (
            self.months, self.days, self.microseconds)

    def __eq__(self, other: object) -> bool:
        return other is not None and isinstance(other, Interval) and \
               self.months == other.months and self.days == other.days and \
               self.microseconds == other.microseconds

    def __ne__(self, other: object) -> bool:
        return not self.__eq__(other)


class PGType:
    value: object

    def __init__(self, value: object) -> None:
        self.value = value

    def encode(self, encoding: str) -> bytes:
        return str(self.value).encode(encoding)


class PGEnum(PGType):
    value: str

    def __init__(self, val: str | enum.Enum) -> None:
        if isinstance(val, str):
            self.value = val
        else:
            self.value = val.value  # pyright: ignore


class PGJson(PGType):
    def encode(self, encoding: str) -> bytes:
        return dumps(self.value).encode(encoding)


class PGJsonb(PGType):
    def encode(self, encoding: str) -> bytes:
        return dumps(self.value).encode(encoding)


class PGTsvector(PGType):
    pass


class PGVarchar(str):
    pass


class PGText(str):
    pass


def Date(year: int, month: int, day: int) -> date:
    return date(year, month, day)


def Time(hour: int, minute: int, second: int) -> time:
    return time(hour, minute, second)


def Timestamp(year: int, month: int, day: int, hour: int, minute: int, second: int) -> Datetime:
    return Datetime(year, month, day, hour, minute, second)


def DateFromTicks(ticks: int) -> date:
    from time import localtime
    return Date(*localtime(ticks)[:3])


def TimeFromTicks(ticks: int) -> time:
    from time import localtime
    return Time(*localtime(ticks)[3:6])


def TimestampFromTicks(ticks: int) -> Datetime:
    from time import localtime
    return Timestamp(*localtime(ticks)[:6])


def Binary(value: bytes | bytearray | memoryview) -> bytes:
    return bytes(value)


FC_TEXT: int = 0
FC_BINARY: int = 1

J2000_OFFSET: int = 2451545

EPOCH: Datetime = Datetime(2000, 1, 1)
EPOCH_TZ: Datetime = EPOCH.replace(tzinfo=Timezone.utc)
EPOCH_SECONDS: float = timegm(EPOCH.timetuple())
INFINITY_MICROSECONDS: int = 2 ** 63 - 1
MINUS_INFINITY_MICROSECONDS: int = -1 * INFINITY_MICROSECONDS - 1


def timestamp_recv_integer(data: bytes, offset: int, length: int) -> Datetime | str | int:
    micros = q_unpack(data, offset)[0]
    try:
        return EPOCH + Timedelta(microseconds=micros)
    except OverflowError:
        if micros == INFINITY_MICROSECONDS:
            return 'infinity'
        elif micros == MINUS_INFINITY_MICROSECONDS:
            return '-infinity'
        else:
            return micros  # type: ignore[no-any-return]


def timestamp_recv_float(data: bytes, offset: int, length: int) -> Datetime:
    return Datetime.fromtimestamp(EPOCH_SECONDS + d_unpack(data, offset)[0], tz=Timezone.utc).replace(tzinfo=None)


def timestamp_send_integer(v: Datetime) -> bytes:
    return q_pack(
        int((timegm(v.timetuple()) - EPOCH_SECONDS) * 1e6) + v.microsecond)


def timestamp_send_float(v: Datetime) -> bytes:
    return d_pack(timegm(v.timetuple()) + v.microsecond / 1e6 - EPOCH_SECONDS)


def timestamptz_send_integer(v: Datetime) -> bytes:
    return timestamp_send_integer(
        v.astimezone(Timezone.utc).replace(tzinfo=None))


def timestamptz_send_float(v: Datetime) -> bytes:
    return timestamp_send_float(
        v.astimezone(Timezone.utc).replace(tzinfo=None))


def timestamptz_recv_integer(data: bytes, offset: int, length: int) -> str:
    return (data[offset:offset + length]).decode("utf-8")


def timestamptz_recv_float(data: bytes, offset: int, length: int) -> str:
    return (data[offset:offset + length]).decode("utf-8")


def interval_send_integer(v: Any) -> bytes:
    microseconds = 0
    try:
        microseconds += int(v.seconds * 1e6)
    except AttributeError:
        pass

    try:
        microseconds += v.microseconds
    except AttributeError:
        pass

    try:
        months = v.months
    except AttributeError:
        months = 0

    try:
        days = v.days
    except AttributeError:
        days = 0

    return qii_pack(microseconds, days, months)


def interval_send_float(v: Any) -> bytes:
    seconds = 0.0
    try:
        seconds += v.microseconds / 1000.0 / 1000.0
    except AttributeError:
        pass
    try:
        seconds += v.seconds
    except AttributeError:
        pass

    try:
        months = v.months
    except AttributeError:
        months = 0

    try:
        days = v.days
    except AttributeError:
        days = 0

    return dii_pack(seconds, days, months)


def interval_recv_integer(data: bytes, offset: int, length: int) -> Interval:
    return _parse_interval_text(data[offset:offset + length].decode("utf-8"))


def interval_recv_float(data: bytes, offset: int, length: int) -> Interval:
    return _parse_interval_text(data[offset:offset + length].decode("utf-8"))


def _parse_interval_text(s: str) -> Interval:
    import re
    months = 0
    days = 0
    microseconds = 0

    s = s.strip()
    normalized = re.sub(r'([+-])\s+(\d)', r'\1\2', s)
    parts = re.split(r'\s+', normalized)

    i = 0
    while i < len(parts):
        tok = parts[i]

        time_match = re.match(r'^([+-]?)(\d+):(\d+):(\d+(?:\.\d+)?)$', tok)
        if time_match:
            neg = time_match.group(1) == '-'
            h = int(time_match.group(2))
            m = int(time_match.group(3))
            sec = float(time_match.group(4))
            us = h * 3600000000 + m * 60000000 + int(sec * 1000000)
            if neg:
                us = -us
            microseconds += us
            i += 1
            continue

        try:
            val = int(tok)
        except ValueError:
            i += 1
            continue

        if i + 1 < len(parts):
            unit = parts[i + 1]
            if unit.startswith('year'):
                months += val * 12
                i += 2
                continue
            elif unit.startswith('mon'):
                months += val
                i += 2
                continue
            elif unit.startswith('day'):
                days += val
                i += 2
                continue

        i += 1

    return Interval(microseconds=microseconds, days=days, months=months)


def int8_recv(data: bytes, offset: int, length: int) -> int:
    return int(data[offset:offset + length])


def int2_recv(data: bytes, offset: int, length: int) -> int:
    return int(data[offset:offset + length])


def int4_recv(data: bytes, offset: int, length: int) -> int:
    return int(data[offset:offset + length])


def float4_recv(data: bytes, offset: int, length: int) -> float:
    return float(data[offset:offset + length])


def float8_recv(data: bytes, offset: int, length: int) -> float:
    return float(data[offset:offset + length])


def bytea_send(v: bytes | bytearray) -> bytes | bytearray:
    return v


def bytea_recv(data: bytes, offset: int, length: int) -> bytes:
    return data[offset:offset + length]


def uuid_send(v: object) -> bytes:
    from uuid import UUID
    if isinstance(v, UUID):
        return v.bytes
    return UUID(v).bytes  # type: ignore[arg-type]


def uuid_recv(data: bytes, offset: int, length: int) -> object:
    from uuid import UUID
    return UUID(bytes=data[offset:offset + length])


def bool_send(v: object) -> bytes:
    return b"\x01" if v else b"\x00"


def null_send(v: object) -> bytes:
    from .protocol import NULL
    return NULL


def int_in(data: bytes, offset: int, length: int) -> int:
    return int(data[offset: offset + length])


def timestamp_in(data: bytes, offset: int, length: int) -> Datetime | str:
    s = data[offset:offset + length].decode('utf-8')
    try:
        if ' ' in s:
            parts = s.split('.')
            date_time_parts = parts[0]
            microseconds = int(parts[1][:6].ljust(6, '0')) if len(parts) > 1 else 0
            dt_parts = date_time_parts.replace('-', ' ').replace(':', ' ').split()
            return Datetime(int(dt_parts[0]), int(dt_parts[1]), int(dt_parts[2]),
                          int(dt_parts[3]), int(dt_parts[4]), int(dt_parts[5]), microseconds)
        return Datetime(int(s[:4]), int(s[5:7]), int(s[8:10]), 0, 0, 0)
    except (ValueError, IndexError):
        return s


def timestamptz_in(data: bytes, offset: int, length: int) -> Datetime | str:
    import re
    s = data[offset:offset + length].decode('utf-8')
    try:
        text = s.strip()
        if ' ' not in text:
            return Datetime(int(text[:4]), int(text[5:7]), int(text[8:10]), 0, 0, 0)

        if text.endswith((' UTC', ' utc')):
            text = text[:-4] + '+00:00'
        text = re.sub(r'([+-]\d{2})$', r'\1:00', text)
        text = re.sub(r'([+-]\d{2})(\d{2})$', r'\1:\2', text)
        value = Datetime.fromisoformat(text.replace(' ', 'T', 1))
        if value.tzinfo is None:
            return value
        return value.astimezone(Timezone.utc)
    except (ValueError, IndexError):
        return s


class DbosTupleDesc:
    def __init__(self) -> None:
        self.version: int | None = None
        self.nullsAllowed: int | None = None
        self.sizeWord: int | None = None
        self.sizeWordSize: int | None = None
        self.numFixedFields: int | None = None
        self.numVaryingFields: int | None = None
        self.fixedFieldsSize: int | None = None
        self.maxRecordSize: int | None = None
        self.numFields: int | None = None
        self.field_type: list[int] = []
        self.field_size: list[int] = []
        self.field_trueSize: list[int] = []
        self.field_offset: list[int] = []
        self.field_physField: list[int] = []
        self.field_logField: list[int] = []
        self.field_nullAllowed: list[int] = []
        self.field_fixedSize: list[int] = []
        self.field_springField: list[int] = []
        self.DateStyle: int | None = None
        self.EuroDates: int | None = None
        self.DBcharset: str | None = None
        self.EnableTime24: str | None = None


OID_BOOL: int = 16
OID_BYTEINT: int = 2500
OID_INT2: int = 21
OID_INT4: int = 23
OID_INT8: int = 20
OID_NUMERIC: int = 1700
OID_FLOAT4: int = 700
OID_FLOAT8: int = 701
OID_BPCHAR: int = 1042
OID_VARCHAR: int = 1043
OID_TEXT: int = 25
OID_DATE: int = 1082
OID_TIME: int = 1083
OID_TIMESTAMP: int = 1114
OID_TIMESTAMPTZ: int = 1184
OID_TIMETZ: int = 1266
OID_NCHAR: int = 2522
OID_NVARCHAR: int = 2530

NZ_TYPE_NUMERIC: int = 7


def date2j(y: int, m: int, d: int) -> int:
    m12 = int((m - 14) / 12)
    return ((1461 * (y + 4800 + m12)) // 4 + (367 * (m - 2 - 12 * m12))
            // 12 - (3 * ((y + 4900 + m12) // 100)) // 4 + d - 32075)


def j2date(jd: int) -> list[int]:
    date_list: list[int] = []
    lval = jd + 68569
    nval = (4 * lval) // 146097
    lval -= (146097 * nval + 3) // 4
    ival = (4000 * (lval + 1)) // 1461001
    lval += 31 - (1461 * ival) // 4
    jval = (80 * lval) // 2447
    day = lval - (2447 * jval) // 80
    lval = jval // 11
    month = (jval + 2) - (12 * lval)
    year = 100 * (nval - 49) + ival + lval

    date_list.append(year)
    date_list.append(month)
    date_list.append(day)

    return date_list


def time2struct(time_val: int) -> list[int]:
    time_value: list[int] = []
    us = time_val % 1000000
    time_val = int(time_val / 1000000)
    second = time_val % 60
    time_val = int(time_val / 60)
    minute = time_val % 60
    hour = int(time_val / 60)

    time_value.append(hour)
    time_value.append(minute)
    time_value.append(second)
    time_value.append(us)

    return time_value


def decimalToBinary(dec: int, bitmaplen: int) -> list[int]:
    bin_list: list[int] = []
    while bitmaplen != 0:
        remainder = dec % 2
        dec = dec // 2
        bin_list.append(remainder)
        bitmaplen -= 1
    return bin_list


def timestamp2struct(dt: int) -> list[int] | bool:
    ts: list[int] = []
    date_val = int(dt // 86400000000)
    date0 = J2000_OFFSET

    time_val = dt % 86400000000

    if time_val < 0:
        time_val += 86400000000
        date_val -= 1

    if date_val < -date0:
        return False

    date_val += date0
    ts = j2date(date_val)
    fraction = (time_val % 1000000)

    time_val = int(time_val / 1000000)

    hour = int(time_val / 3600)
    time_val -= (hour * 3600)
    minute = int(time_val / 60)
    second = time_val - (minute * 60)

    ts.append(hour)
    ts.append(minute)
    ts.append(second)
    ts.append(fraction)

    return ts


def IntervalToText(interval_time: int, interval_month: int) -> str:
    fsec: int = 0
    tm, fsec = interval2tm(interval_time, interval_month, fsec)
    fsec_float = fsec / 1000000
    return EncodeTimeSpan(tm, fsec_float)


def interval2tm(interval_time: int, interval_month: int, fsec0: int) -> tuple[list[int], int]:
    tmpVal = 0
    time_list: list[int] = []

    if interval_month != 0:
        year = int(interval_month / 12)
        mon = interval_month % 12
    else:
        year = 0
        mon = 0

    tmpVal = interval_time // 86400000000

    if tmpVal != 0:
        interval_time -= tmpVal * 86400000000
        mday = tmpVal
    else:
        mday = 0

    tmpVal = interval_time // 3600000000

    if tmpVal != 0:
        interval_time -= tmpVal * 3600000000
        hour = tmpVal
    else:
        hour = 0

    tmpVal = interval_time // 60000000

    if tmpVal != 0:
        interval_time -= tmpVal * 60000000
        min_val = tmpVal
    else:
        min_val = 0

    tmpVal = interval_time // 1000000

    if tmpVal != 0:
        interval_time -= tmpVal * 1000000
        sec = tmpVal
    else:
        sec = 0

    time_list.append(year)
    time_list.append(mon)
    time_list.append(mday)
    time_list.append(hour)
    time_list.append(min_val)
    time_list.append(sec)

    fsec = interval_time

    return time_list, fsec


def _abs(n: int | float) -> int | float:
    if n < 0:
        return -n
    else:
        return n


def EncodeTimeSpan(tm: list[int], fsec: float) -> str:
    is_nonzero = is_before = minus = False
    result = ""

    if tm[0] != 0:
        result = "{} year"
        result = result.format(tm[0])
        if _abs(tm[0]) != 1:
            result = result + "s"

        if tm[0] < 0:
            is_before = True

        is_nonzero = True

    if tm[1] != 0:
        if is_nonzero:
            result = result + " "
        else:
            result = result + ""

        if is_before and tm[1] > 0:
            result = result + "+"

        str_mon = "{} mon"
        str_mon = str_mon.format(tm[1])

        if _abs(tm[1]) != 1:
            str_mon = str_mon + "s"

        result = result + str_mon

        if tm[1] < 0:
            is_before = True

        is_nonzero = True

    if tm[2] != 0:
        if is_nonzero:
            result = result + " "

        if is_before and tm[2] > 0:
            result = result + "+"

        str_day = "{} day"
        str_day = str_day.format(tm[2])

        if _abs(tm[2]) != 1:
            str_day = str_day + "s"

        result = result + str_day

        if tm[2] < 0:
            is_before = True

        is_nonzero = True

    if (not is_nonzero) or (tm[3] != 0) or \
            (tm[4] != 0) or (tm[5] != 0) or (fsec != 0):

        if tm[3] < 0 or tm[4] < 0 or tm[5] < 0 or fsec < 0:
            minus = True

        if is_nonzero:
            result = result + " "

        if minus:
            result = result + "-"
        else:
            if is_before:
                result = result + "+"

        str_hr_min = "{0:02d}:{1:02d}"
        result = result + str_hr_min.format(_abs(tm[3]), _abs(tm[4]))

        is_nonzero = True

        if fsec != 0:
            fsec += tm[5]
            str_hr_sec = ":{0:09.6f}"
            result = result + str_hr_sec.format(_abs(fsec))
            is_nonzero = True
        elif tm[5] != 0:
            str_hr_sec = ":{0:02d}"
            result = result + str_hr_sec.format(_abs(tm[5]))
            is_nonzero = True

    if not is_nonzero:
        result = result + "0"

    return result


def timetz_out_timetzadt(timetz_time: int, timetz_zone: int) -> str:
    tm: list[int] = []

    time_val = int(timetz_time / 1000000)
    fusec = timetz_time % 1000000

    hour = int(time_val / 3600)
    time_val = time_val % 3600
    min_val = int(time_val / 60)
    sec = time_val % 60

    tm.append(hour)
    tm.append(min_val)
    tm.append(sec)

    return EncodeTimeOnly(tm, fusec, timetz_zone)


def EncodeTimeOnly(tm: list[int], fusec: int, timetz_zone: int) -> str:
    if (tm[0] < 0) or (tm[0] > 24):
        return ""

    if (tm[1] < 0) or (tm[1] > 59):
        return ""

    fusec_float = fusec / 1000000

    if fusec_float != 0:
        fusec_float += tm[2]
        result = "{0:02d}:{1:02d}:{2:09.6f}"
        result = result.format(tm[0], tm[1], fusec_float)
        result = result.rstrip('0')
        if result.endswith('.'):
            result = result[:-1]
    else:
        result = "{0:02d}:{1:02d}:{2:02d}"
        result = result.format(tm[0], tm[1], tm[2])

    if timetz_zone != 0:
        display_zone = -timetz_zone
        tz_hour = _abs(display_zone) // 3600
        tz_min = (_abs(display_zone) % 3600) // 60
        sign = '+' if display_zone > 0 else '-'

        if tz_min != 0:
            str_tz = "{0}{1:02d}:{2:02d}"
            result = result + str_tz.format(sign, tz_hour, tz_min)
        else:
            str_tz = "{0}{1:02d}"
            result = result + str_tz.format(sign, tz_hour)

    return result


__all__ = [
    "ZERO", "BINARY", "Binary", "Date", "Time", "Timestamp",
    "DateFromTicks", "TimeFromTicks", "TimestampFromTicks",
    "Interval", "LogOptions",
    "PGType", "PGEnum", "PGJson", "PGJsonb", "PGText", "PGTsvector",
    "PGVarchar",
    "FC_TEXT", "FC_BINARY",
    "DbosTupleDesc",
    "timestamp_recv_integer", "timestamp_recv_float",
    "timestamp_send_integer", "timestamp_send_float",
    "timestamptz_send_integer", "timestamptz_send_float",
    "timestamptz_recv_integer", "timestamptz_recv_float",
    "interval_send_integer", "interval_send_float",
    "interval_recv_integer", "interval_recv_float",
    "_parse_interval_text",
    "int8_recv", "int2_recv", "int4_recv",
    "float4_recv", "float8_recv",
    "bytea_send", "bytea_recv",
    "uuid_send", "uuid_recv",
    "bool_send", "null_send",
    "int_in", "timestamp_in", "timestamptz_in",
    "EPOCH", "EPOCH_TZ", "EPOCH_SECONDS",
    "INFINITY_MICROSECONDS", "MINUS_INFINITY_MICROSECONDS",
    "J2000_OFFSET",
    "OID_BOOL", "OID_BYTEINT", "OID_INT2", "OID_INT4",
    "OID_INT8", "OID_NUMERIC", "OID_FLOAT4", "OID_FLOAT8",
    "OID_BPCHAR", "OID_VARCHAR", "OID_TEXT",
    "OID_DATE", "OID_TIME", "OID_TIMESTAMP", "OID_TIMESTAMPTZ",
    "OID_TIMETZ", "OID_NCHAR", "OID_NVARCHAR",
    "NZ_TYPE_NUMERIC",
    "date2j", "j2date", "time2struct", "timestamp2struct",
    "timetz_out_timetzadt", "EncodeTimeOnly", "EncodeTimeSpan",
    "IntervalToText", "interval2tm",
    "decimalToBinary",
]
