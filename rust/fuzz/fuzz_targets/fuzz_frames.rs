#![no_main]

//! Framing of `Y` messages: `next_row_frame` must never panic, must only
//! report frames that lie inside the buffer, and a scan must always advance.

use libfuzzer_sys::fuzz_target;
use rust_ext::frames::next_row_frame;

fuzz_target!(|data: &[u8]| {
    if data.len() < 8 {
        return;
    }
    // The first 8 bytes choose the start position (including values past the
    // end and near usize::MAX); the rest is the wire buffer.
    let raw = u64::from_le_bytes(data[..8].try_into().unwrap()) as usize;
    let buf = &data[8..];

    for pos in [raw, raw % (buf.len() + 1)] {
        if let Some((start, len)) = next_row_frame(buf, pos) {
            assert_eq!(start, pos + 13);
            assert_eq!(buf[pos], b'Y');
            assert!(start + len <= buf.len());
        }
    }

    let mut pos = 0usize;
    let mut frames = 0usize;
    while let Some((start, len)) = next_row_frame(buf, pos) {
        let next = start + len;
        assert!(next > pos);
        pos = next;
        frames += 1;
        assert!(frames <= buf.len());
    }
});
