#![no_main]

//! Row layout validation: `resolve_starts` must never panic on any header or
//! metadata, and every reported field start must lie inside the row.

use libfuzzer_sys::fuzz_target;
use rust_ext::layout::{resolve_starts, FieldMeta, RowShape};

/// Reads little-endian values from the input; past the end it yields zeros.
struct Reader<'a> {
    data: &'a [u8],
    at: usize,
}

impl Reader<'_> {
    fn byte(&mut self) -> u8 {
        let b = self.data.get(self.at).copied().unwrap_or(0);
        self.at += 1;
        b
    }

    fn bytes<const N: usize>(&mut self) -> [u8; N] {
        let mut out = [0u8; N];
        for slot in out.iter_mut() {
            *slot = self.byte();
        }
        out
    }

    /// Full-range `i64`, or an `i32` sign-extended, as the metadata may be either.
    fn value(&mut self, wide: bool) -> i64 {
        if wide {
            i64::from_le_bytes(self.bytes())
        } else {
            i64::from(i32::from_le_bytes(self.bytes::<4>()))
        }
    }
}

fuzz_target!(|data: &[u8]| {
    let mut r = Reader { data, at: 0 };
    let mode = r.byte();
    let wide = mode & 1 == 1;

    let shape = RowShape {
        num_fields: i64::from(r.byte()),
        nulls_allowed: mode & 2 == 2,
        fixed_fields_size: r.value(wide),
        num_varying: r.value(wide),
    };
    let count = shape.num_fields as usize;
    let mut fields = Vec::with_capacity(count);
    for _ in 0..count {
        fields.push(FieldMeta {
            phys: r.value(wide),
            ftype: r.value(wide),
            size: r.value(wide),
            true_size: r.value(wide),
            offset: r.value(wide),
            fixed: r.value(wide),
        });
    }
    let row = &data[r.at.min(data.len())..];

    if let Ok(starts) = resolve_starts(&shape, &fields, row) {
        assert_eq!(starts.len(), fields.len());
        for start in starts.iter().flatten() {
            assert!(*start <= row.len());
        }
    }
});
