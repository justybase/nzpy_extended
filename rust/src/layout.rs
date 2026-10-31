//! Row layout validation. Pure Rust, no Python.
//!
//! Mirrors `DbosParser._validate_row_layout` / `_validate_field_span` in
//! `nzpy_extended/_dbos.py`. Every field's start is resolved and
//! bounds-checked before any value is decoded.

use crate::nztype as t;

/// Per-column metadata, one entry per field in descriptor order.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct FieldMeta {
    pub phys: i64,
    pub ftype: i64,
    pub size: i64,
    pub true_size: i64,
    pub offset: i64,
    pub fixed: i64,
}

/// Row-level shape: header values from the tuple descriptor.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct RowShape {
    pub num_fields: i64,
    pub nulls_allowed: bool,
    pub fixed_fields_size: i64,
    pub num_varying: i64,
}

/// A layout fault. The message is embedded in the Python-facing error.
#[derive(Debug, PartialEq, Eq)]
pub struct LayoutError(pub String);

fn fail<T>(msg: impl Into<String>) -> Result<T, LayoutError> {
    Err(LayoutError(msg.into()))
}

#[inline]
fn u16_le(row: &[u8], at: usize) -> u16 {
    u16::from_le_bytes([row[at], row[at + 1]])
}

/// Resolve every field's start offset (`None` for NULL) or fail.
pub fn resolve_starts(
    shape: &RowShape,
    fields: &[FieldMeta],
    row: &[u8],
) -> Result<Vec<Option<usize>>, LayoutError> {
    let len = row.len() as i64;
    let n = shape.num_fields;
    let bitmaplen = (n + 7) / 8;
    if 2 + bitmaplen > len {
        return fail("row is too short for the NULL bitmap");
    }
    if shape.fixed_fields_size < 0 {
        return fail("fixed-field area offset is invalid");
    }
    if shape.num_varying < 0 {
        return fail("varying field count is invalid");
    }

    // Each varying entry takes at least two bytes, so the row length bounds the
    // count; the header count alone must not drive the allocation.
    let mut var_offsets: Vec<i64> =
        Vec::with_capacity((shape.num_varying as usize).min(row.len() / 2));
    let mut cur = shape.fixed_fields_size;
    for _ in 0..shape.num_varying {
        var_offsets.push(cur);
        // `len >= 2` here (checked above), so the subtraction cannot underflow.
        if cur > len - 2 {
            return fail("varying field length is truncated");
        }
        let vlen = u16_le(row, cur as usize) as i64;
        if vlen < 2 {
            return fail("varying field length is invalid");
        }
        let next = cur + vlen + (vlen & 1);
        if next > len {
            return fail("varying field extends beyond the row");
        }
        cur = next;
    }

    let mut starts = Vec::with_capacity(fields.len());
    for (i, f) in fields.iter().enumerate() {
        if is_null(shape, n, f, i, row)? {
            starts.push(None);
            continue;
        }
        let start = if f.fixed != 0 {
            if f.fixed < 0 {
                return fail(format!("field {i} fixed size is invalid"));
            }
            f.offset
        } else if !var_offsets.is_empty() {
            if f.offset < 0 || f.offset >= var_offsets.len() as i64 {
                return fail(format!("field {i} varying index is invalid"));
            }
            var_offsets[f.offset as usize]
        } else {
            shape.fixed_fields_size
        };
        if start < 0 || start > len {
            return fail(format!("field {i} starts outside the row"));
        }
        check_span(i, f, start, len, row)?;
        starts.push(Some(start as usize));
    }
    Ok(starts)
}

fn is_null(
    shape: &RowShape,
    n: i64,
    f: &FieldMeta,
    i: usize,
    row: &[u8],
) -> Result<bool, LayoutError> {
    if !shape.nulls_allowed {
        return Ok(false);
    }
    if f.phys < 0 || f.phys >= n {
        return fail(format!("field {i} physical index is invalid"));
    }
    let byte = row[(2 + (f.phys >> 3)) as usize];
    Ok((byte >> (f.phys & 7)) & 1 == 1)
}

/// Bytes the field's storage spans, bounds-checked against the row.
fn check_span(
    i: usize,
    f: &FieldMeta,
    start: i64,
    len: i64,
    row: &[u8],
) -> Result<(), LayoutError> {
    let ftype = if f.ftype == t::UNKNOWN {
        t::VARCHAR
    } else {
        f.ftype
    };
    let need: i64 = match ftype {
        t::CHAR => {
            if f.size < 0 {
                return fail(format!("field {i} size is invalid"));
            }
            f.size
        }
        ft if t::is_length_prefixed(ft) => {
            if start + 2 > len {
                return fail(format!("field {i} length prefix is truncated"));
            }
            let total = u16_le(row, start as usize) as i64;
            if total < 2 || start + total > len {
                return fail(format!("field {i} extends beyond the row"));
            }
            return Ok(());
        }
        t::INT8 | t::DOUBLE => 8,
        t::INT | t::FLOAT => 4,
        t::INT2 => 2,
        t::INT1 | t::BOOL => 1,
        t::DATE | t::TIME | t::TIMESTAMP => {
            if f.size < 0 {
                return fail(format!("field {i} size is invalid"));
            }
            if f.size >= 8 {
                8
            } else {
                f.size
            }
        }
        t::INTERVAL | t::TIMETZ => {
            if f.size < 4 {
                return fail(format!("field {i} size is invalid"));
            }
            f.size
        }
        t::NUMERIC => {
            let chunk = f.true_size;
            if !(4..=16).contains(&chunk) || chunk % 4 != 0 {
                return fail(format!("field {i} numeric width is invalid"));
            }
            chunk
        }
        _ => 0,
    };
    // `start` is within the row here, so `len - start` cannot underflow; adding
    // `need` could overflow for a hostile size.
    if need > len - start {
        return fail(format!("field {i} extends beyond the row"));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn int_field(phys: i64, offset: i64) -> FieldMeta {
        FieldMeta {
            phys,
            ftype: t::INT,
            size: 4,
            true_size: 4,
            offset,
            fixed: 4,
        }
    }

    #[test]
    fn fixed_int_with_bitmap_null() {
        // header(2) + bitmap(1) + i32(4) = 7 bytes; field 0 phys 0 is NULL.
        let row = [0u8, 0, 0x01, 0, 0, 0, 0];
        let shape = RowShape {
            num_fields: 1,
            nulls_allowed: true,
            fixed_fields_size: 7,
            num_varying: 0,
        };
        let starts = resolve_starts(&shape, &[int_field(0, 3)], &row).unwrap();
        assert_eq!(starts, vec![None]);
    }

    #[test]
    fn truncated_bitmap_is_a_layout_fault() {
        let shape = RowShape {
            num_fields: 9,
            nulls_allowed: true,
            fixed_fields_size: 0,
            num_varying: 0,
        };
        let err = resolve_starts(&shape, &[], &[0u8, 0]).unwrap_err();
        assert_eq!(err.0, "row is too short for the NULL bitmap");
    }

    #[test]
    fn varying_length_must_be_at_least_two() {
        let shape = RowShape {
            num_fields: 1,
            nulls_allowed: false,
            fixed_fields_size: 3,
            num_varying: 1,
        };
        let row = [0u8, 0, 0, 1, 0];
        let err = resolve_starts(
            &shape,
            &[FieldMeta {
                phys: 0,
                ftype: t::VARCHAR,
                size: 8,
                true_size: 8,
                offset: 0,
                fixed: 0,
            }],
            &row,
        )
        .unwrap_err();
        assert_eq!(err.0, "varying field length is invalid");
    }

    #[test]
    fn huge_varying_count_is_bounded_by_the_row() {
        // Regression (found by fuzzing): the header count sized an allocation
        // before any bounds check. A count of i32::MAX must fail cleanly.
        let shape = RowShape {
            num_fields: 0,
            nulls_allowed: false,
            fixed_fields_size: 2,
            num_varying: i64::from(i32::MAX),
        };
        let err = resolve_starts(&shape, &[], &[0u8, 0]).unwrap_err();
        assert_eq!(err.0, "varying field length is truncated");
    }

    #[test]
    fn huge_fixed_offset_does_not_overflow() {
        // Regression (found by review): `cur + 2` wrapped for a near-i64::MAX offset.
        let shape = RowShape {
            num_fields: 0,
            nulls_allowed: false,
            fixed_fields_size: i64::MAX,
            num_varying: 1,
        };
        let err = resolve_starts(&shape, &[], &[0u8, 0]).unwrap_err();
        assert_eq!(err.0, "varying field length is truncated");
    }

    #[test]
    fn huge_field_size_does_not_overflow() {
        // Regression (found by review): `start + need` wrapped for a hostile size.
        let row = [0u8; 16];
        let shape = RowShape {
            num_fields: 1,
            nulls_allowed: false,
            fixed_fields_size: 2,
            num_varying: 0,
        };
        let f = FieldMeta {
            phys: 0,
            ftype: t::CHAR,
            size: i64::MAX,
            true_size: 0,
            offset: 2,
            fixed: 0,
        };
        let err = resolve_starts(&shape, &[f], &row).unwrap_err();
        assert_eq!(err.0, "field 0 extends beyond the row");
    }

    #[test]
    fn negative_fixed_size_is_rejected() {
        let row = [0u8; 16];
        let shape = RowShape {
            num_fields: 1,
            nulls_allowed: false,
            fixed_fields_size: 3,
            num_varying: 0,
        };
        let f = FieldMeta {
            phys: 0,
            ftype: t::INT,
            size: 4,
            true_size: 4,
            offset: 3,
            fixed: -1,
        };
        assert!(resolve_starts(&shape, &[f], &row).is_err());
    }
}
