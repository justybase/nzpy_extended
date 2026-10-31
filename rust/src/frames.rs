//! Framing of consecutive `Y` (DBOS row) messages. Pure Rust.
//!
//! Frame layout: 1 type byte, 8 skipped bytes, big-endian u32 length, payload.

/// Returns `(payload_start, payload_len)` of the next complete `Y` frame at `pos`,
/// or `None` for a non-`Y` byte, an incomplete header, or an incomplete payload.
pub fn next_row_frame(buf: &[u8], pos: usize) -> Option<(usize, usize)> {
    if pos + 13 > buf.len() || buf[pos] != b'Y' {
        return None;
    }
    let len =
        u32::from_be_bytes([buf[pos + 9], buf[pos + 10], buf[pos + 11], buf[pos + 12]]) as usize;
    if len > buf.len() - pos - 13 {
        return None;
    }
    Some((pos + 13, len))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn frame(payload: &[u8]) -> Vec<u8> {
        let mut v = vec![b'Y'];
        v.extend_from_slice(&[0, 0, 0, 1]);
        v.extend_from_slice(&[0; 4]);
        v.extend_from_slice(&(payload.len() as u32).to_be_bytes());
        v.extend_from_slice(payload);
        v
    }

    #[test]
    fn reads_complete_frame_and_stops_at_partial() {
        let mut buf = frame(b"abc");
        let complete = buf.len();
        buf.extend_from_slice(&frame(b"partial")[..15]);
        assert_eq!(next_row_frame(&buf, 0), Some((13, 3)));
        assert_eq!(next_row_frame(&buf, complete), None);
    }

    #[test]
    fn non_row_message_stops_the_scan() {
        assert_eq!(
            next_row_frame(b"C\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00", 0),
            None
        );
    }
}
