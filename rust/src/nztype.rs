//! DBOS field type codes. Values mirror `nzpy_extended/protocol.py`.

pub const DOUBLE: i64 = 2;
pub const INT: i64 = 3;
pub const FLOAT: i64 = 4;
pub const DATE: i64 = 6;
pub const NUMERIC: i64 = 7;
pub const TIME: i64 = 8;
pub const TIMESTAMP: i64 = 9;
pub const INTERVAL: i64 = 10;
pub const TIMETZ: i64 = 11;
pub const BOOL: i64 = 12;
pub const INT1: i64 = 13;
pub const CHAR: i64 = 15;
pub const VARCHAR: i64 = 16;
pub const UNKNOWN: i64 = 18;
pub const INT2: i64 = 19;
pub const INT8: i64 = 20;
pub const VARFIXEDCHAR: i64 = 21;
pub const GEOMETRY: i64 = 22;
pub const VARBINARY: i64 = 23;
pub const NCHAR: i64 = 25;
pub const NVARCHAR: i64 = 26;
pub const JSON: i64 = 30;
pub const JSONB: i64 = 31;
pub const JSONPATH: i64 = 32;
pub const VECTOR: i64 = 33;

/// Types whose storage is a `u16` length prefix (which counts itself).
pub fn is_length_prefixed(ftype: i64) -> bool {
    matches!(
        ftype,
        NCHAR
            | NVARCHAR
            | VARCHAR
            | VARFIXEDCHAR
            | GEOMETRY
            | VARBINARY
            | JSON
            | JSONB
            | JSONPATH
            | VECTOR
    )
}
