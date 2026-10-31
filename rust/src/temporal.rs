//! Date and time arithmetic. Pure Rust.
//!
//! Matches the Python reference in `nzpy_extended/types.py`: `j2date` uses
//! floor division, `time2struct` uses truncating division of the seconds,
//! and `timestamp2struct` splits with floor/modulo. For in-range values
//! (all DATE/TIME/TIMESTAMP values the appliance produces) the results are
//! exact; out-of-range garbage is decided only by the caller's range checks.

pub const J2000_OFFSET: i128 = 2_451_545;
pub const USECS_PER_DAY: i64 = 86_400_000_000;

/// Julian day number to (year, month, day), floor semantics.
pub fn j2date(jd: i128) -> (i128, i128, i128) {
    let mut l = jd + 68_569;
    let n = (4 * l).div_euclid(146_097);
    l -= (146_097 * n + 3).div_euclid(4);
    let i = (4_000 * (l + 1)).div_euclid(1_461_001);
    l += 31 - (1_461 * i).div_euclid(4);
    let j = (80 * l).div_euclid(2_447);
    let day = l - (2_447 * j).div_euclid(80);
    l = j.div_euclid(11);
    let month = (j + 2) - 12 * l;
    let year = 100 * (n - 49) + i + l;
    (year, month, day)
}

/// Calendar date for a DATE workspace value (days since 2000-01-01).
pub fn date_from_workspace(workspace: i64) -> (i128, i128, i128) {
    j2date(workspace as i128 + J2000_OFFSET)
}

/// (hour, minute, second, microsecond) for a TIME workspace value (microseconds).
/// Microseconds use floor modulo, seconds use truncation: as in `time2struct`.
pub fn time_parts(time_us: i64) -> (i64, i64, i64, i64) {
    let us = time_us.rem_euclid(1_000_000);
    let secs = time_us / 1_000_000;
    let second = secs.rem_euclid(60);
    let minutes = secs / 60;
    let minute = minutes.rem_euclid(60);
    let hour = minutes / 60;
    (hour, minute, second, us)
}

/// Calendar and clock parts for a TIMESTAMP workspace value, or `None` when the
/// day number lies before year 1 (the Python reference returns `None` there).
pub fn timestamp_parts(micros: i64) -> Option<(i128, i128, i128, i64, i64, i64, i64)> {
    let days = micros.div_euclid(USECS_PER_DAY);
    let time_us = micros.rem_euclid(USECS_PER_DAY);
    if days < -(J2000_OFFSET as i64) {
        return None;
    }
    let (y, m, d) = j2date(days as i128 + J2000_OFFSET);
    let fraction = time_us % 1_000_000;
    let mut rest = time_us / 1_000_000;
    let hour = rest / 3600;
    rest -= hour * 3600;
    let minute = rest / 60;
    let second = rest - minute * 60;
    Some((y, m, d, hour, minute, second, fraction))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn j2000_epoch_is_2000_01_01() {
        assert_eq!(date_from_workspace(0), (2000, 1, 1));
        assert_eq!(date_from_workspace(-1), (1999, 12, 31));
    }

    #[test]
    fn date_matches_python_for_leap_day() {
        // 2024-02-29 is 8825 days after 2000-01-01.
        assert_eq!(date_from_workspace(8825), (2024, 2, 29));
    }

    #[test]
    fn time_parts_within_a_day() {
        let t = 23 * 3_600_000_000 + 59 * 60_000_000 + 59 * 1_000_000 + 123_456;
        assert_eq!(time_parts(t), (23, 59, 59, 123_456));
        assert_eq!(time_parts(0), (0, 0, 0, 0));
    }

    #[test]
    fn timestamp_splits_days_and_clock() {
        // 2000-01-01 00:00:01.000002
        assert_eq!(timestamp_parts(1_000_002), Some((2000, 1, 1, 0, 0, 1, 2)));
        // one microsecond before 2000-01-01 is 1999-12-31 23:59:59.999999
        assert_eq!(
            timestamp_parts(-1),
            Some((1999, 12, 31, 23, 59, 59, 999_999))
        );
    }

    #[test]
    fn timestamp_before_year_one_is_none() {
        let far_past = -(J2000_OFFSET as i64) * USECS_PER_DAY - 1;
        assert_eq!(timestamp_parts(far_past), None);
    }
}
