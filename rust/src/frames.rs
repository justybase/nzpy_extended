//! Framing of consecutive `Y` (DBOS row) messages. Pure Rust.
//!
//! Frame layout: 1 type byte, 8 skipped bytes, big-endian u32 length, payload.

/// Returns `(payload_start, payload_len)` of the next complete `Y` frame at `pos`,
/// or `None` for a non-`Y` byte, an incomplete header, or an incomplete payload.
pub fn next_row_frame(buf: &[u8], pos: usize) -> Option<(usize, usize)> {
    // `get` rejects `pos` past the end without any arithmetic on `pos`.
    let rest = buf.get(pos..)?;
    if rest.len() < 13 || rest[0] != b'Y' {
        return None;
    }
    let len = u32::from_be_bytes([rest[9], rest[10], rest[11], rest[12]]) as usize;
    if len > rest.len() - 13 {
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
    fn position_past_the_end_is_not_a_frame() {
        // Regression (found by review): `pos + 13` could wrap for a huge `pos`.
        let buf = frame(b"abc");
        assert_eq!(next_row_frame(&buf, usize::MAX), None);
        assert_eq!(next_row_frame(&buf, buf.len() + 1), None);
        assert_eq!(next_row_frame(&buf, buf.len()), None);
    }

    #[test]
    fn non_row_message_stops_the_scan() {
        assert_eq!(
            next_row_frame(b"C\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00", 0),
            None
        );
    }
}
